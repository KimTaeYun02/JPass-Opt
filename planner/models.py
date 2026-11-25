"""Versioned contracts kept independent of the existing station-only API."""

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Literal
from unicodedata import normalize

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


Text = Annotated[str, Field(min_length=1, max_length=256, pattern=r"\S")]
Minutes = Annotated[StrictInt, Field(ge=0, le=1440)]
Money = Annotated[StrictInt, Field(ge=0, le=10**9)]
Kind = Literal["rail", "station", "walk", "rag"]


def instant(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise ValueError("날짜와 시간대가 있는 ISO 시각이 필요합니다.")
    offset = re.search(r"[+-](\d{2}):(\d{2})$", value)
    if offset and (int(offset[1]) > 23 or int(offset[2]) > 59):
        raise ValueError("시간대 오프셋이 올바르지 않습니다.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("시간대가 필요합니다.")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as error:
        raise ValueError("지원 범위의 실제 날짜와 시각이 필요합니다.") from error


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class Evidence(DTO):
    source: Text
    version: Text


class Location(DTO):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Window(DTO):
    startAt: str
    endAt: str

    @model_validator(mode="after")
    def times(self):
        if instant(self.startAt) >= instant(self.endAt):
            raise ValueError("방문 가능 시간대의 종료는 시작 이후여야 합니다.")
        return self


class Place(DTO):
    clientPlaceId: Text
    name: Text
    location: Location
    googlePlaceId: Text | None = None
    stayMinutes: Minutes
    anchorRole: Literal["start", "end"] | None = None
    openingWindows: list[Window] | None = Field(default=None, max_length=16)
    fixedVisitAt: str | None = None

    @model_validator(mode="after")
    def times(self):
        if self.fixedVisitAt is not None:
            instant(self.fixedVisitAt)
        return self


class Options(DTO):
    allowMultimodal: bool = False
    allowCache: bool = False
    allowPrefixReuse: bool = False
    allowStationAlternates: bool = True
    allowOrderAlternates: bool = False
    orderAlternatePolicy: Literal["diverse", "reverse_first", "deadline_first", "pass_hint"] = "diverse"
    allowRepair: bool = True
    allowPassEvaluation: bool = True


class BudgetOverride(DTO):
    railAttempts: Annotated[StrictInt, Field(ge=0, le=10)] | None = None


class PlanRequest(DTO):
    inputVersion: Literal["2"] = "2"
    sessionId: Text
    operationId: Text
    dataSource: Literal["fixture", "replay", "live"] = "fixture"
    fixtureId: Text = "tokyo-demo"
    startAt: str
    endBy: str | None = None
    places: list[Place] = Field(min_length=1, max_length=8)
    mode: Literal["reference", "strict"] = "reference"
    budget: BudgetOverride = Field(default_factory=BudgetOverride)
    options: Options = Field(default_factory=Options)
    candidateIndex: Annotated[StrictInt, Field(ge=0, le=2)] = 0

    @model_validator(mode="after")
    def valid_request(self):
        start = instant(self.startAt)
        if self.endBy is not None and instant(self.endBy) < start:
            raise ValueError("종료 제한은 시작 시각보다 빠를 수 없습니다.")
        if len({p.clientPlaceId for p in self.places}) != len(self.places):
            raise ValueError("방문 ID는 고유해야 합니다. 같은 좌표의 다른 방문은 별도 ID를 사용합니다.")
        for role in ("start", "end"):
            if sum(p.anchorRole == role for p in self.places) > 1:
                raise ValueError("시작·종료 앵커는 각각 최대 1개입니다.")
        return self


class StationRef(DTO):
    namespace: Text
    sourceVersion: Text
    providerStationId: Text | None = None
    name: Text
    line: Text
    prefecture: Text
    location: Location

    def identity(self) -> tuple:
        if self.providerStationId is not None:
            return (self.namespace, self.sourceVersion, "id", self.providerStationId)
        return (self.namespace, self.sourceVersion, "composite", normalize("NFKC", self.name),
                normalize("NFKC", self.line), self.prefecture, self.location.lat, self.location.lng)

    def same_physical(self, other) -> bool:
        return self.providerStationId is not None and other.providerStationId is not None and self.identity() == other.identity()


class Walking(DTO):
    status: Literal["routed", "estimated", "unknown"]
    minutes: Minutes | None
    evidence: Evidence

    @model_validator(mode="after")
    def known(self):
        if (self.status == "unknown") != (self.minutes is None):
            raise ValueError("도보 미확인은 null이며 알려진 도보는 명시적인 분이 필요합니다.")
        return self


class StationCandidate(DTO):
    station: StationRef
    access: Walking
    egress: Walking


class RailQuery(DTO):
    origin: StationRef
    destination: StationRef
    preparedAt: str
    options: dict[str, str | int | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def time(self):
        instant(self.preparedAt)
        return self


class RideSegment(DTO):
    segmentId: Text
    operator: Text | None
    line: Text | None
    trainType: Text | None


class FareUnit(DTO):
    fareUnitId: Text
    rideSegmentIds: list[Text] = Field(min_length=1)
    baseFare: Money | None
    supplement: Money | None
    quotedTotal: Money | None = None
    currency: Literal["JPY"] = "JPY"
    evidence: Evidence

    @model_validator(mode="after")
    def totals(self):
        if self.quotedTotal is not None and self.baseFare is not None and self.supplement is not None and self.quotedTotal != self.baseFare + self.supplement:
            raise ValueError("표시 총운임과 운임 구성의 합계가 다릅니다.")
        if self.quotedTotal is not None and any(v is not None and v > self.quotedTotal for v in (self.baseFare, self.supplement)):
            raise ValueError("알려진 운임 구성은 총운임을 초과할 수 없습니다.")
        return self


class Ride(DTO):
    rideId: Text
    departureAt: str | None
    arrivalAt: str | None
    displayedDurationMinutes: Minutes | None = None
    transfers: Annotated[StrictInt, Field(ge=0, le=20)] | None = None
    segments: list[RideSegment] = Field(min_length=1)
    fareUnits: list[FareUnit]
    fareBasis: Literal["independent_journey", "unknown"] = "unknown"
    initialWaitIncluded: Literal["yes", "no", "unknown"] = "unknown"
    evidence: Evidence

    @model_validator(mode="after")
    def chronology(self):
        if self.departureAt is not None:
            instant(self.departureAt)
        if self.arrivalAt is not None:
            instant(self.arrivalAt)
        if self.departureAt is not None and self.arrivalAt is not None and instant(self.arrivalAt) < instant(self.departureAt):
            raise ValueError("도착이 출발보다 빠릅니다.")
        segment_ids = [s.segmentId for s in self.segments]
        covered = [s for unit in self.fareUnits for s in unit.rideSegmentIds]
        if len(set(segment_ids)) != len(segment_ids) or len(set(covered)) != len(covered) or not set(covered) <= set(segment_ids):
            raise ValueError("승차 구간의 중복·중복 운임 또는 잘못된 운임 참조입니다.")
        if len({u.fareUnitId for u in self.fareUnits}) != len(self.fareUnits):
            raise ValueError("운임 단위 ID가 중복됩니다.")
        return self


class PassAllocation(DTO):
    fareUnitId: Text
    uncoveredFare: Money | None
    requiredSupplement: Money | None


class PassEvaluation(DTO):
    routeDigest: Text
    passId: Text
    documentVersion: Text
    eligibility: Literal["eligible", "ineligible", "unknown"]
    coverage: Literal["known", "unknown"]
    validity: Literal["valid", "invalid", "unknown"]
    validFrom: str | None
    validUntil: str | None
    purchasePrice: Money | None
    allocations: list[PassAllocation]
    reportedTotal: Money | None
    evidence: Evidence

    @model_validator(mode="after")
    def dates(self):
        if self.validFrom is not None:
            instant(self.validFrom)
        if self.validUntil is not None:
            instant(self.validUntil)
        if self.validFrom and self.validUntil and instant(self.validFrom) >= instant(self.validUntil):
            raise ValueError("패스 유효기간이 올바르지 않습니다.")
        return self


def rail_budget(n: int) -> int:
    return n - 1 + min(3, n - 1)
