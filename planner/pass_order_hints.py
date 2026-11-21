"""Trusted, local pass evidence for ordering candidates before timetable evaluation."""

from itertools import permutations
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import DTO, Evidence, PlanRequest, StationCandidate, Text, instant


class PassOrderHints(DTO):
    schemaVersion: Literal["pass-order-hints/1"]
    passId: Text
    evidence: Evidence
    stationNamespace: Text
    stationSourceVersion: Text
    effectiveFrom: str
    effectiveUntil: str
    stationPairs: tuple[tuple[Text, Text], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope(self):
        if instant(self.effectiveFrom) >= instant(self.effectiveUntil):
            raise ValueError("패스 참고 자료의 유효 범위를 확인하세요.")
        if any(origin == destination for origin, destination in self.stationPairs):
            raise ValueError("패스 참고 역 쌍의 두 역은 서로 달라야 합니다.")
        if len(set(self.stationPairs)) != len(self.stationPairs):
            raise ValueError("같은 방향의 패스 참고 역 쌍이 중복됐습니다.")
        return self

    def match_visit_pairs(self, request: PlanRequest, choices: dict[str, StationCandidate]):
        """Map matching visit edges to stable directed station pairs for distinct counts."""
        start = instant(request.startAt)
        lower, upper = instant(self.effectiveFrom), instant(self.effectiveUntil)
        if not lower <= start < upper or (request.endBy is not None and instant(request.endBy) > upper):
            return {}, "outside_evidence_window"
        stations = {}
        matching_dataset = False
        for pid, candidate in choices.items():
            station = candidate.station
            if (station.namespace, station.sourceVersion) != (self.stationNamespace, self.stationSourceVersion):
                continue
            matching_dataset = True
            if station.providerStationId is not None:
                stations[pid] = station.providerStationId
        if choices and not matching_dataset:
            return {}, "station_dataset_mismatch"

        directed_pairs = set(self.stationPairs)
        matches = {}
        for first, second in permutations(stations, 2):
            pair = stations[first], stations[second]
            if pair in directed_pairs:
                matches[first, second] = pair
        return matches, None if matches else "no_matching_station_pairs"


def load_pass_order_hints(path: Path | None) -> PassOrderHints | None:
    return PassOrderHints.model_validate_json(path.read_bytes()) if path is not None else None
