"""Synthetic schedules and pass decisions. Every operation uses zero network."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path

from .models import Evidence, PassEvaluation, RailQuery, Ride, StationCandidate, instant, iso
from .provider import Capabilities, ProviderFailure


class FixtureProvider:
    id = "fixture"
    dataSource = "fixture"

    def __init__(self, path: str | Path):
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.version = self.data["version"]
        self.fixture_id = self.data["fixtureId"]
        self.capabilities = Capabilities(**self.data["capabilities"])
        self.events = []
        self.failures = deepcopy(self.data.get("failures", {}))

    def check_failure(self, key):
        if self.failures.get(key, 0):
            self.failures[key] -= 1
            raise ProviderFailure("fixture_transient_failure", retryable=True)

    async def stations(self, place):
        self.events.append(("station", place.clientPlaceId))
        self.check_failure("station:" + place.clientPlaceId)
        values = self.data["stations"].get(place.clientPlaceId)
        if values is None:
            raise ProviderFailure("fixture_place_unsupported")
        return [StationCandidate.model_validate(row) for row in values]

    async def rail(self, query: RailQuery):
        a = query.origin.providerStationId
        b = query.destination.providerStationId
        key = f"{a}>{b}"
        self.events.append(("rail", key, query.preparedAt))
        self.check_failure("rail:" + key)
        spec = self.data.get("edges", {}).get(key, self.data["defaultEdge"])
        if spec.get("unsupported"):
            raise ProviderFailure("fixture_rail_unsupported")
        alternatives = spec.get("choices", [spec])
        result = []
        for i, edge in enumerate(alternatives):
            ready = instant(query.preparedAt)
            interval = edge["headwayMinutes"]
            origin = ready.replace(hour=0, minute=0, second=0, microsecond=0)
            minutes = (ready - origin).total_seconds() / 60
            import math
            departure = origin + timedelta(minutes=math.ceil(minutes / interval) * interval)
            arrival = departure + timedelta(minutes=edge["rideMinutes"])
            token = f"{key}:{iso(departure)}:{i}"
            evidence = Evidence(source="synthetic-timetable", version=self.version).model_dump()
            result.append(Ride.model_validate({"rideId": token,
                "departureAt": None if edge.get("missingDates") else iso(departure),
                "arrivalAt": None if edge.get("missingDates") else iso(arrival),
                "displayedDurationMinutes": edge["rideMinutes"], "transfers": edge.get("transfers", 0),
                "segments": [{"segmentId": token + ":segment", "operator": "fixture-rail", "line": "시험 노선", "trainType": "fixture"}],
                "fareUnits": [{"fareUnitId": token + ":fare", "rideSegmentIds": [token + ":segment"],
                               "baseFare": edge["fare"], "supplement": edge.get("supplement", 0), "evidence": evidence}],
                "fareBasis": "independent_journey", "initialWaitIncluded": "no", "evidence": evidence}))
        return result

    async def passes(self, routes: list[dict]):
        self.events.append(("rag", len(routes)))
        self.check_failure("rag")
        result = []
        # Templates describe fixture responses; this is not a pass eligibility engine.
        for route in routes:
            for template in self.data.get("passResponses", []):
                units = route["fare"]["fareUnits"]
                total = template["purchasePrice"] + len(units) * (template["uncoveredFarePerUnit"] + template["supplementPerUnit"])
                result.append(PassEvaluation.model_validate({
                    "routeDigest": route["digest"], "passId": template["passId"], "documentVersion": self.version,
                    "eligibility": template["eligibility"], "coverage": template["coverage"], "validity": template["validity"],
                    "validFrom": template["validFrom"], "validUntil": template["validUntil"],
                    "purchasePrice": template["purchasePrice"], "reportedTotal": total,
                    "allocations": [{"fareUnitId": unit["fareUnitId"], "uncoveredFare": template["uncoveredFarePerUnit"],
                                     "requiredSupplement": template["supplementPerUnit"]} for unit in units],
                    "evidence": {"source": "fixture-rag-response", "version": self.version}}))
        return result
