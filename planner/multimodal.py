"""Select one leg from a small set of directed, time-dependent alternatives."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import product
from typing import Literal
from urllib.parse import urlencode

from .evaluator import known_fare, visit_at
from .ledger import Stopped
from .models import Evidence, FareUnit, RailQuery, RideSegment, StationRef, Walking, digest, instant, iso
from .ordering import distance


ZERO_WALK = Walking(status="routed", minutes=0, evidence=Evidence(source="no-walk-step", version="1"))


@dataclass(frozen=True)
class Step:
    mode: str
    start: datetime
    end: datetime
    detail: dict

    def dump(self):
        return {"mode": self.mode, "startAt": iso(self.start), "endAt": iso(self.end), **self.detail}


@dataclass(frozen=True)
class Journey:
    steps: tuple[Step, ...]
    access: Walking = ZERO_WALK
    egress: Walking = ZERO_WALK
    origin: StationRef | None = None
    destination: StationRef | None = None
    segments: tuple = ()
    fare_units: tuple = ()
    fare_basis: str = "independent_journey"
    transfers: int | None = 0
    fare: int | None = 0
    same_station: bool = False

    @property
    def arrival(self):
        return self.steps[-1].end

    @property
    def routed(self):
        return all(s.detail["status"] == "routed" for s in self.steps if s.mode == "walk")

    def leg(self, origin_id, destination_id, index, exact, bus_status, bus_link):
        travel = [s for s in self.steps if s.mode in ("bus", "rail")]
        prepared = self.steps[0].start + timedelta(minutes=self.access.minutes)
        departure = travel[0].start if travel else prepared
        arrival = travel[-1].end if travel else prepared
        # Scope fare and ride IDs to the leg just as legacy v2 does.
        ids = {row["segmentId"]: digest([origin_id, destination_id, index, row["segmentId"]])
               for row in self.segments}
        units = [{**row, "fareUnitId": digest([origin_id, destination_id, index, row["fareUnitId"]]),
                  "rideSegmentIds": [ids[sid] for sid in row["rideSegmentIds"]]}
                 for row in self.fare_units]
        evidence = (travel[0] if travel else self.steps[0]).detail["evidence"]
        transit_link = None
        if bus_link:
            points = (self.steps[0].detail["origin"], self.steps[-1].detail["destination"])
            origin, destination = (f'{p["lat"]},{p["lng"]}' for p in points)
            transit_link = "https://www.google.com/maps/dir/?" + urlencode(
                {"api": 1, "origin": origin, "destination": destination, "travelmode": "transit"})
        return {
            "from": origin_id, "to": destination_id, "requestedAt": iso(prepared), "preparedAt": iso(prepared),
            "departureAt": iso(departure), "arrivalAt": iso(arrival), "placeArrivalAt": iso(self.arrival),
            "initialWaitMinutes": (departure - prepared).total_seconds() / 60,
            "rideMinutes": (arrival - departure).total_seconds() / 60,
            "accessWalking": self.access.model_dump(), "egressWalking": self.egress.model_dump(),
            "samePhysicalStation": self.same_station, "transfers": self.transfers,
            "rideSegmentIds": list(ids.values()), "segments": [{**s, "segmentId": ids[s["segmentId"]]} for s in self.segments],
            "fareUnits": units, "fareBasis": self.fare_basis, "providerEvidence": evidence,
            "clockExact": exact and self.routed, "steps": [s.dump() for s in self.steps],
            "boardStation": self.origin.model_dump() if self.origin else None,
            "alightStation": self.destination.model_dump() if self.destination else None,
            "busStatus": bus_status, "transitLink": transit_link,
        }


def walking_step(start, route, origin, destination):
    return Step("walk", start, start + timedelta(minutes=route.minutes),
                {**route.model_dump(), "origin": origin.model_dump(), "destination": destination.model_dump()})


def wait_step(start, end, evidence):
    return Step("wait", start, end, {"evidence": evidence})


def short_walk(data, origin, destination):
    walk = data.walk(origin.location, destination.location)
    return walk if walk and walk.minutes <= 12 and distance(origin, destination) <= 800 else None


def valid_walk(route, strict):
    return route.status == "routed" or (not strict and route.status == "estimated")


def visit_misses(checks, leave, end_by):
    misses = sum(c["status"] == "violated" or (c["reason"] or "").startswith("estimated_miss") for c in checks)
    return misses + int(end_by is not None and leave > instant(end_by))


class MultimodalSegment(RideSegment):
    mode: Literal["bus", "rail"]


def make_bus(connection, access, egress, start, trip, evidence):
    walk_in = walking_step(start, access, connection.origin, connection.boarding.location)
    departure, arrival = instant(trip.departureAt), instant(trip.arrivalAt)
    token = "bus:" + digest([connection.boarding.identity(), connection.alighting.identity(), iso(departure), trip.line])
    bus = Step("bus", departure, arrival, {"line": trip.line, "fareYen": trip.fareYen,
               "boarding": connection.boarding.model_dump(), "alighting": connection.alighting.model_dump(),
               "evidence": evidence})
    walk_out = walking_step(arrival, egress, connection.alighting.location, connection.destination)
    unit = FareUnit(fareUnitId=token, rideSegmentIds=[token], baseFare=None, supplement=None,
                    quotedTotal=trip.fareYen, evidence=evidence).model_dump()
    segment = MultimodalSegment(segmentId=token, operator=connection.boarding.operator,
                                line=trip.line, trainType=None, mode="bus").model_dump()
    return Journey((walk_in, wait_step(walk_in.end, departure, evidence), bus, walk_out), access, egress,
                   segments=(segment,), fare_units=(unit,), fare=trip.fareYen)


class LegSelector:
    def __init__(self, data, request, stations, gateway, ctx):
        self.data, self.request, self.stations = data, request, stations
        self.gateway, self.ctx = gateway, ctx

    def short_walk_order(self, order, by_id):
        current = instant(self.request.startAt)
        exact = True
        try:
            for index, place_id in enumerate(order):
                place = by_id[place_id]
                visit, checks = visit_at(place, current, exact, report_estimates=True)
                leave = instant(visit["leaveAt"])
                final = index == len(order) - 1
                if visit_misses(checks, leave, self.request.endBy if final else None):
                    return False
                if final:
                    return True
                walk = short_walk(self.data, place, by_id[order[index + 1]])
                if walk is None:
                    return False
                current = leave + timedelta(minutes=walk.minutes)
                exact = visit["leaveClockExact"]
        except OverflowError:
            return False
        return False

    def arrivals(self, destination):
        results = []
        for end in self.station_candidates(destination):
            egress = self.data.walk(end.station.location, destination.location) or end.egress
            if valid_walk(egress, self.request.mode == "strict"):
                results.append((end.station, egress))
        return sorted(results, key=lambda row: (row[1].minutes, row[0].identity()))

    def station_candidates(self, place):
        candidates = self.stations[place.clientPlaceId]
        return candidates if self.request.options.allowStationAlternates else candidates[:1]

    def pairs(self, origin, destination):
        pairs = []
        for start, (end, egress) in product(self.station_candidates(origin), self.arrivals(destination)):
            access = self.data.walk(origin.location, start.station.location) or start.access
            if valid_walk(access, self.request.mode == "strict"):
                pairs.append((start.station, end, access, egress))
        return sorted(pairs, key=lambda p: (p[2].minutes + p[3].minutes, p[0].identity(), p[1].identity()))

    async def rail(self, pair, origin, destination, start, feeder=None):
        board, alight, access, egress = pair
        walk_in = None if feeder else walking_step(start, access, origin.location, board.location)
        prepared = feeder.arrival if feeder else walk_in.end
        steps = feeder.steps if feeder else (walk_in,)
        if board.same_physical(alight):
            if feeder:
                return []
            walk_out = walking_step(prepared, egress, alight.location, destination.location)
            return [Journey(steps + (walk_out,), access, egress, board, alight, same_station=True)]

        query = RailQuery(origin=board, destination=alight, preparedAt=iso(prepared))
        rides = await self.gateway.rail(self.ctx, query, retry_allowed=False)
        journeys = []
        for ride in rides:
            if ride.departureAt is None or ride.arrivalAt is None or instant(ride.departureAt) < prepared:
                continue
            departure, arrival = instant(ride.departureAt), instant(ride.arrivalAt)
            evidence = ride.evidence.model_dump()
            out = walking_step(arrival, egress, alight.location, destination.location)
            rail = Step("rail", departure, arrival, {"boarding": board.model_dump(), "alighting": alight.model_dump(),
                                                   "evidence": evidence})
            segments = tuple(MultimodalSegment(**s.model_dump(), mode="rail").model_dump() for s in ride.segments)
            units = tuple(u.model_dump() for u in ride.fareUnits)
            fare = known_fare(ride)
            basis = ride.fareBasis
            if feeder:
                segments = feeder.segments + segments
                units = feeder.fare_units + units
                fare = None if fare is None or feeder.fare is None else feeder.fare + fare
                if feeder.fare_basis != "independent_journey":
                    basis = "unknown"
            transfers = None if ride.transfers is None else ride.transfers + int(feeder is not None)
            journeys.append(Journey(steps + (wait_step(prepared, departure, evidence), rail, out),
                                    feeder.access if feeder else access, egress, board, alight, segments, units,
                                    basis, transfers, fare))
        if not journeys:
            reason = "missing_dated_trip" if any(r.departureAt is None or r.arrivalAt is None for r in rides) else "no_valid_dated_trip"
            raise Stopped(reason)
        return journeys

    def bus_options(self, origin, destination):
        rail_destinations = self.arrivals(destination)
        options = []
        for connection in self.data.connections:
            if connection.origin != origin.location:
                continue
            pair = None
            if connection.station is None:
                if connection.destination != destination.location:
                    continue
            else:
                if not self.request.options.allowStationAlternates:
                    continue
                alighting = next((p for p in rail_destinations if not connection.station.same_physical(p[0])), None)
                if alighting is None:
                    continue
                pair = (connection.station, alighting[0], ZERO_WALK, alighting[1])
            access = self.data.walk(connection.origin, connection.boarding.location)
            egress = self.data.walk(connection.alighting.location, connection.destination)
            if access and egress:
                options.append((connection, pair, access, egress))
        return sorted(options, key=lambda o: (o[1] is not None, o[2].minutes + o[3].minutes,
                                              o[0].boarding.identity(), o[0].alighting.identity()))

    async def bus(self, option, origin, destination, start):
        connection, pair, access, egress = option
        prepared = start + timedelta(minutes=access.minutes)

        async def lookup():
            self.ctx.bus_attempts += 1
            return self.data.search(connection, prepared)

        search = await self.gateway.call(self.ctx, "rail", lookup, retry_allowed=False)
        if search is None or search.timetable == "unknown":
            return [], "unknown"
        if not search.trips:
            return [], "no_service"
        journeys = [make_bus(connection, access, egress, start, t, search.evidence.model_dump()) for t in search.trips]
        if pair:
            # Only the earliest feeder is continued, so one branch needs one more rail attempt.
            first = min(journeys, key=lambda j: (j.arrival, j.fare is None, j.fare or 0))
            return await self.rail(pair, origin, destination, start, feeder=first), "checked"
        return journeys, "checked"

    async def select(self, origin, destination, start, clock_exact, reserve):
        def score(journey):
            visit, constraints = visit_at(destination, journey.arrival, clock_exact and journey.routed, report_estimates=True)
            end_by = self.request.endBy if reserve == 0 else None
            leave = instant(visit["leaveAt"])
            return (visit_misses(constraints, leave, end_by), leave,
                    journey.fare is None, journey.fare or 0, journey.arrival)

        walk = self.data.walk(origin.location, destination.location)
        direct = Journey((walking_step(start, walk, origin.location, destination.location),), egress=walk) if walk else None
        if short_walk(self.data, origin, destination) and score(direct)[0] == 0:
            return direct, "not_checked", False
        candidates = [direct] if direct else []
        pairs = self.pairs(origin, destination)
        base = pairs[0] if pairs else None
        problem = None
        if base:
            try:
                candidates.extend(await self.rail(base, origin, destination, start))
            except Stopped as error:
                if error.reason in ("deadline_exceeded", "operation_not_running"):
                    raise
                problem = error
        base_best = min(candidates, key=score) if candidates else None
        base_time = (base_best.arrival - start).total_seconds() / 60 if base_best else 0
        risky_connection = base_best is not None and ((base_best.transfers or 0) >= 2 or score(base_best)[0] > 0)
        verify_extra = (base is None or problem is not None or base[2].minutes + base[3].minutes >= 15
                        or base_time >= 35 or risky_connection)
        bus_status = "unknown" if verify_extra else "not_checked"
        alternates = self.bus_options(origin, destination)
        tried_bus = False
        if self.ctx.bus_attempts < 2:
            eligible = next((o for o in alternates if self.ctx.remaining() - reserve >= (2 if o[1] else 1)), None)
            if eligible:
                tried_bus = True
                try:
                    journeys, bus_status = await self.bus(eligible, origin, destination, start)
                    candidates.extend(journeys)
                except Stopped as error:
                    if error.reason in ("deadline_exceeded", "operation_not_running"):
                        raise
                    bus_status = "unknown"
                    problem = problem or error
        if ((not tried_bus or bus_status in ("unknown", "no_service"))
                and verify_extra and len(pairs) > 1 and self.ctx.remaining() > reserve
                and self.request.options.allowStationAlternates and self.request.options.allowRepair):
            try:
                candidates.extend(await self.rail(pairs[1], origin, destination, start))
            except Stopped as error:
                if error.reason in ("deadline_exceeded", "operation_not_running"):
                    raise
                problem = problem or error
        if not candidates:
            raise problem or Stopped("bus_timetable_unconfirmed" if bus_status == "unknown" else "no_multimodal_leg")
        bus_link = bus_status == "unknown" or (bool(alternates) and not tried_bus)
        return min(candidates, key=score), bus_status, bus_link
