"""Evaluate one order at absolute times; never infer dated trips from duration."""

from copy import deepcopy
from datetime import timedelta

from .gateway import Context, Gateway
from .ledger import Stopped
from .models import PlanRequest, RailQuery, Ride, StationCandidate, digest, instant, iso
from .pricing import fare_summary, unit_total


def constraint(kind, status, reason=None, visit=None):
    return {"kind": kind, "status": status, "reason": reason, "visitId": visit}


def visit_at(place, arrival, exact, *, report_estimates=False):
    current = arrival
    constraints = []
    if place.fixedVisitAt:
        fixed = instant(place.fixedVisitAt)
        missed = current > fixed
        reason = "conditional_arrival" if not exact else "late_for_fixed_visit" if missed else None
        if report_estimates and not exact and missed:
            reason = "estimated_miss_fixed_visit"
        constraints.append(constraint("fixed_visit", "unknown" if not exact else "violated" if missed else "satisfied",
                                      reason, place.clientPlaceId))
        current = max(current, fixed)
    duration = timedelta(minutes=place.stayMinutes)
    if place.openingWindows is None:
        constraints.append(constraint("opening", "unknown", "opening_hours_missing", place.clientPlaceId))
        exact = False
    else:
        starts = [max(current, instant(w.startAt)) for w in place.openingWindows
                  if max(current, instant(w.startAt)) + duration <= instant(w.endAt)
                  and (place.fixedVisitAt is None or instant(w.startAt) <= current)]
        status = "unknown" if not exact and place.openingWindows else "satisfied" if starts else "violated"
        reason = "conditional_arrival" if status == "unknown" else None
        if report_estimates and status == "unknown" and not starts:
            reason = "estimated_miss_opening"
        constraints.append(constraint("opening", status, reason, place.clientPlaceId))
        if starts:
            current = min(starts)
    return {"clientPlaceId": place.clientPlaceId, "arrivalAt": iso(arrival), "visitStartAt": iso(current),
            "leaveAt": iso(current + duration), "visitWaitMinutes": (current - arrival).total_seconds() / 60,
            "stayMinutes": place.stayMinutes, "arrivalClockExact": exact if place.openingWindows is not None else False,
            "leaveClockExact": exact}, constraints


def known_fare(ride: Ride):
    covered = [s for u in ride.fareUnits for s in u.rideSegmentIds]
    if ride.fareBasis != "independent_journey" or set(covered) != {s.segmentId for s in ride.segments}:
        return None
    amounts = [unit_total(u.model_dump()) for u in ride.fareUnits]
    if any(v is None for v in amounts):
        return None
    return sum(amounts)


def choose_ride(rides: list[Ride], prepared):
    usable = [r for r in rides if r.departureAt and r.arrivalAt and instant(r.departureAt) >= prepared]
    if not usable:
        raise Stopped("missing_dated_trip" if any(r.departureAt is None or r.arrivalAt is None for r in rides) else "no_valid_dated_trip")
    return min(usable, key=lambda r: (instant(r.arrivalAt), known_fare(r) is None, known_fare(r) or 0, r.transfers is None, r.transfers or 0, r.rideId))


def finish_route(order, visits, legs, constraints, choices, complete, reason, exact):
    walking = []
    for leg in legs:
        if "steps" in leg:
            walking.extend(step["status"] for step in leg["steps"] if step["mode"] == "walk")
        else:
            walking.extend((leg["accessWalking"]["status"], leg["egressWalking"]["status"]))
    access = "unknown" if "unknown" in walking else "estimated" if "estimated" in walking else "routed"
    if not complete and len(legs) < len(order) - 1:
        access = "unknown"
    if reason == "required_walk_unknown":
        access = "unknown"
    elif reason == "strict_walk_unavailable" and len(legs) == len(order)-1:
        access = "estimated"
    fare = fare_summary(legs)
    if not complete:
        fare.update(quotedSubtotal=fare["noPassTotal"], noPassTotal=None, status="partial" if fare["fareUnits"] else "unknown")
    route = {"order": list(order), "visits": visits, "legs": legs, "constraints": constraints,
             "completion": "complete" if complete else "partial" if visits or legs else "failed",
             "timing": "unknown" if reason in ("missing_dated_trip", "required_walk_unknown") else "timetable" if complete and exact and all(c["status"] != "unknown" for c in constraints) else "reference",
             "accessWalking": access, "stopReason": reason, "finishAt": visits[-1]["leaveAt"] if complete else None,
             "stationChoices": {pid: choice.station.model_dump(mode="json") for pid, choice in sorted(choices.items())},
             "fare": fare, "transfers": sum(leg["transfers"] for leg in legs) if all(leg["transfers"] is not None for leg in legs) else None,
             "optimality": "not-guaranteed"}
    route["digest"] = digest(route)
    route["planId"] = "plan-" + route["digest"][:20]
    return route


async def evaluate(request: PlanRequest, order: list[str], choices: dict[str, StationCandidate],
                   gateway: Gateway, ctx: Context, prefix: dict | None = None, start_index=0, leg_selector=None):
    by_id = {p.clientPlaceId: p for p in request.places}
    if leg_selector is not None:
        choices = {}
    if prefix is None:
        start_index = 0
    visits = deepcopy(prefix["visits"][:start_index]) if prefix else []
    legs = deepcopy(prefix["legs"][:start_index]) if prefix else []
    copied_ids = {v["clientPlaceId"] for v in visits}
    constraints = [deepcopy(c) for c in prefix["constraints"] if c["visitId"] in copied_ids] if prefix else []
    current = instant(prefix["visits"][start_index]["arrivalAt"]) if prefix and start_index else instant(request.startAt)
    exact = prefix["visits"][start_index]["arrivalClockExact"] if prefix and start_index else True
    try:
        for i in range(start_index, len(order)):
            ctx.time_left()
            place = by_id[order[i]]
            visit, checks = visit_at(place, current, exact, report_estimates=leg_selector is not None)
            visits.append(visit)
            constraints.extend(checks)
            current = instant(visit["leaveAt"])
            exact = visit["leaveClockExact"]
            if i == len(order) - 1:
                break
            if leg_selector is not None:
                destination_place = by_id[order[i + 1]]
                journey, bus_status, bus_link = await leg_selector.select(
                    place, destination_place, current, exact, len(order) - i - 2)
                leg = journey.leg(order[i], order[i + 1], i, exact, bus_status, bus_link)
                legs.append(leg)
                current, exact = journey.arrival, leg["clockExact"]
                continue
            origin, destination = choices[order[i]], choices[order[i + 1]]
            access, egress = origin.access, destination.egress
            if access.status == "unknown" or (request.mode == "strict" and access.status != "routed"):
                raise Stopped("required_walk_unknown" if access.status == "unknown" else "strict_walk_unavailable")
            exact = exact and access.status == "routed"
            prepared = current + timedelta(minutes=access.minutes)
            if origin.station.same_physical(destination.station):
                ride = None
                departure = arrival = prepared
            else:
                query = RailQuery(origin=origin.station, destination=destination.station, preparedAt=iso(prepared))
                ride = choose_ride(await gateway.rail(ctx, query), prepared)
                departure, arrival = instant(ride.departureAt), instant(ride.arrivalAt)
            segment_map = {s.segmentId: digest([order[i], order[i+1], i, s.segmentId]) for s in ride.segments} if ride else {}
            units = []
            if ride:
                for unit in ride.fareUnits:
                    row = unit.model_dump(mode="json")
                    row["fareUnitId"] = digest([order[i], order[i+1], i, unit.fareUnitId])
                    row["rideSegmentIds"] = [segment_map[s] for s in unit.rideSegmentIds]
                    units.append(row)
            leg = {"from": order[i], "to": order[i+1], "requestedAt": iso(prepared), "preparedAt": iso(prepared),
                   "departureAt": iso(departure), "arrivalAt": iso(arrival), "placeArrivalAt": None,
                   "initialWaitMinutes": (departure-prepared).total_seconds()/60,
                   "rideMinutes": (arrival-departure).total_seconds()/60,
                   "accessWalking": access.model_dump(mode="json"), "egressWalking": egress.model_dump(mode="json"),
                   "samePhysicalStation": ride is None, "transfers": ride.transfers if ride else 0,
                   "rideSegmentIds": list(segment_map.values()),
                   "segments": [{**s.model_dump(mode="json"), "segmentId": segment_map[s.segmentId]} for s in ride.segments] if ride else [],
                   "fareUnits": units, "fareBasis": ride.fareBasis if ride else "independent_journey",
                   "providerEvidence": ride.evidence.model_dump() if ride else {"source": "verified-station-id", "version": origin.station.sourceVersion}}
            legs.append(leg)
            if egress.status == "unknown" or (request.mode == "strict" and egress.status != "routed"):
                raise Stopped("required_walk_unknown" if egress.status == "unknown" else "strict_walk_unavailable")
            exact = exact and egress.status == "routed"
            current = arrival + timedelta(minutes=egress.minutes)
            leg["placeArrivalAt"] = iso(current)
            leg["clockExact"] = exact
        if request.endBy:
            late = current > instant(request.endBy)
            reason = "conditional_arrival" if not exact else None
            if leg_selector is not None and not exact and late:
                reason = "estimated_miss_end_by"
            constraints.append(constraint("end_by", "unknown" if not exact else "violated" if late else "satisfied", reason))
        result = finish_route(order, visits, legs, constraints, choices, True, None, exact)
        result["elapsedMinutes"] = (current - instant(request.startAt)).total_seconds()/60
        # elapsedMinutes is part of the immutable digest too.
        result.pop("digest")
        result.pop("planId")
        result["digest"] = digest(result)
        result["planId"] = "plan-" + result["digest"][:20]
        return result
    except OverflowError:
        reason = "time_out_of_range"
        constraints.append(constraint("evaluation", "unsupported", reason))
        return finish_route(order, visits, legs, constraints, choices, False, reason, exact)
    except Stopped as stop:
        constraints.append(constraint("evaluation", "unsupported" if "unsupported" in stop.reason else "unknown", stop.reason))
        return finish_route(order, visits, legs, constraints, choices, False, stop.reason, exact)
