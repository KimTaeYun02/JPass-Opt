"""Exact, immutable offline records. No network or implicit temporal fallback."""

from copy import deepcopy
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .adapters.ekispert import AbsoluteTimes, RawSearch, normalize_routes
from .adapters.ekispert_times import resolve_calendar_times
from .models import DTO, Place, RailQuery, StationCandidate, Text, digest, instant, iso
from .provider import Capabilities, ProviderFailure


class Provenance(DTO):
    kind: Literal["legacy-team-crawler", "captured-team-crawler", "captured-public-dom", "test-only"]
    sourceArtifact: Text
    sourceSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    notice: Text


class Binding(DTO):
    place: Place
    candidates: list[StationCandidate] = Field(min_length=1, max_length=2)


class Recording(DTO):
    query: RailQuery
    payload: RawSearch
    absoluteTimes: list[AbsoluteTimes | None] | None = None
    providerOptions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def correspondence(self):
        if self.absoluteTimes is not None and len(self.absoluteTimes) != len(self.payload.routes):
            raise ValueError("저장된 화면 후보와 시각 개수가 다릅니다.")
        for course in self.payload.routes:
            if course.route[0].station != self.query.origin.name or course.route[-1].station != self.query.destination.name:
                raise ValueError("검색한 역과 반환된 경로의 역 이름이 다릅니다.")
        for course, dated in zip(self.payload.routes, self.absoluteTimes or []):
            if dated is not None and dated.evidence == "calendar_dom_clock":
                evidence = dated.calendarEvidence
                if (instant(evidence.preparedAt) != instant(self.query.preparedAt)
                        or evidence.departureName != self.query.origin.name
                        or evidence.arrivalName != self.query.destination.name
                        or evidence.displayedDurationMinutes != course.time
                        or len(evidence.pointTimes) != 2 * len(course.route) - 2
                        or resolve_calendar_times(evidence) != dated):
                    raise ValueError("달력·화면 시각 근거가 재생 요청이나 원본 경로와 다릅니다.")
        return self


def record_from_capture(report: dict, query: RailQuery) -> Recording:
    """Bind one successful capture to an explicitly supplied station query."""
    if report.get("schemaVersion") != "ekispert-capture/1" or report.get("status") not in ("captured", "partial"):
        raise ValueError("성공 또는 부분 수집 결과만 재생 자료로 가져올 수 있습니다.")
    original = report["query"]
    if original["departure"] != query.origin.name or original["arrival"] != query.destination.name or instant(original["preparedAt"]) != instant(query.preparedAt) or original.get("options", {}) != query.options:
        raise ValueError("수집한 원본 요청과 재생 요청이 다릅니다.")
    codes = report.get("stationCodes") or {}
    for station, code in ((query.origin, "depCode"), (query.destination, "arrCode")):
        if station.providerStationId is not None and (station.namespace != "ekispert-roote" or codes.get(code) != station.providerStationId):
            raise ValueError("제공자의 선택 역 ID가 재생 참조와 다릅니다.")
    return Recording(query=query, payload=RawSearch.model_validate(report["payload"]),
                     absoluteTimes=report.get("absoluteTimes"), providerOptions=report.get("providerOptions", {}))


class ReplayDataset(DTO):
    schemaVersion: Literal["ekispert-replay/1"]
    datasetId: Text
    provenance: Provenance
    bindings: list[Binding] = Field(min_length=1, max_length=8)
    records: list[Recording] = Field(max_length=128)

    @model_validator(mode="after")
    def legacy_dates_not_invented(self):
        if self.provenance.kind == "legacy-team-crawler" and any(any(record.absoluteTimes or []) for record in self.records):
            raise ValueError("날짜 없는 기존 실응답에 합성 시각을 붙이면 test-only로 구분해야 합니다.")
        return self


def query_key(query: RailQuery) -> str:
    raw = query.model_dump(mode="json")
    raw["preparedAt"] = iso(instant(query.preparedAt))
    return digest(raw)


def place_key(place: Place) -> str:
    return digest([place.clientPlaceId, place.name, place.location.model_dump(), place.googlePlaceId])


class ReplayProvider:
    dataSource = "replay"
    capabilities = Capabilities(reuseExactQuotes=False, reusePrefixInRequest=False, ragDeliveryAllowed=False)

    def __init__(self, path: str | Path):
        source = Path(path).read_bytes()
        dataset = ReplayDataset.model_validate(json.loads(source))
        self.fixture_id = dataset.datasetId
        self.version = digest(dataset.model_dump(mode="json"))
        self.id = "ekispert-replay:" + self.version
        self.provenance = dataset.provenance.model_dump()
        self._stations, self._records = {}, {}
        seen_ids = set()
        for binding in dataset.bindings:
            if binding.place.clientPlaceId in seen_ids:
                raise ValueError("장소 ID가 중복된 재생 자료입니다.")
            seen_ids.add(binding.place.clientPlaceId)
            self._stations[place_key(binding.place)] = deepcopy(binding.candidates)
        for record in dataset.records:
            key = query_key(record.query)
            if key in self._records:
                raise ValueError("동일한 요청의 재생 자료가 중복되었습니다.")
            self._records[key] = normalize_routes(record.payload, source_version=digest(record.payload.model_dump(mode="json")),
                                                  absolute_times=record.absoluteTimes, provenance_kind=dataset.provenance.kind)

    async def stations(self, place):
        key = place_key(place)
        if key not in self._stations:
            raise ProviderFailure("replay_place_miss")
        return deepcopy(self._stations[key])

    async def rail(self, query):
        key = query_key(query)
        if key not in self._records:
            raise ProviderFailure("replay_query_miss")
        if not self._records[key]:
            raise ProviderFailure("replay_walk_only_unsupported")
        return deepcopy(self._records[key])

    async def passes(self, routes):
        raise ProviderFailure("replay_rag_disabled")
