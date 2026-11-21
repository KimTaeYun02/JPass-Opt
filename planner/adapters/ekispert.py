"""Keep missing source facts missing; aggregate fare is not a fare breakdown."""

from typing import Literal

from pydantic import Field, StrictInt, model_validator

from ..models import DTO, Evidence, Minutes, Money, Ride, Text, digest, instant, iso


class RawPoint(DTO):
    station: Text
    line: Text | None


class RawCourse(DTO):
    time: Minutes | None
    fee: Money | None
    route: list[RawPoint] = Field(min_length=2, max_length=100)


class RawSearch(DTO):
    routes: list[RawCourse] = Field(max_length=32)


class PointClock(DTO):
    pointIndex: StrictInt = Field(ge=0, le=99)
    kind: Literal["departure", "arrival"]
    rawText: str = Field(min_length=1, max_length=256)
    selector: Literal['[data-testid="course-detail-point-departure-time"]',
                      '[data-testid="course-detail-point-arrival-time"]']

    @model_validator(mode="after")
    def selector_matches_kind(self):
        if self.selector != f'[data-testid="course-detail-point-{self.kind}-time"]':
            raise ValueError("시각 종류와 DOM 선택자가 다릅니다.")
        return self


class CalendarEvidence(DTO):
    departureName: Text
    arrivalName: Text
    preparedAt: str
    resultSummary: str = Field(min_length=1, max_length=2048)
    pointTimes: list[PointClock] = Field(min_length=2, max_length=198)
    displayedDurationMinutes: Minutes
    timezone: Literal["Asia/Tokyo"] = "Asia/Tokyo"
    summarySelector: Literal["body: exact result-summary line"] = "body: exact result-summary line"


class AbsoluteTimes(DTO):
    departureAt: str
    arrivalAt: str
    evidence: Literal["dom-time-datetime", "calendar_dom_clock"]
    calendarEvidence: CalendarEvidence | None = None

    @model_validator(mode="after")
    def chronology(self):
        if instant(self.arrivalAt) < instant(self.departureAt):
            raise ValueError("명시한 도착 시각이 출발보다 빠릅니다.")
        if (self.evidence == "calendar_dom_clock") != (self.calendarEvidence is not None):
            raise ValueError("달력·화면 시각 방식에는 원본 근거가 필요합니다.")
        return self


def parse_absolute_time(value: str) -> str:
    """Only an explicit ISO date and offset qualify; HH:mm alone is ambiguous."""
    return iso(instant(value))


def normalize_routes(raw: RawSearch | dict, *, source_version: str,
                     absolute_times: list[AbsoluteTimes | None] | None = None,
                     provenance_kind: Literal["unknown", "legacy-team-crawler", "captured-team-crawler", "captured-public-dom", "test-only"] = "unknown") -> list[Ride]:
    parsed = RawSearch.model_validate(raw)
    if absolute_times is not None and len(absolute_times) != len(parsed.routes):
        raise ValueError("화면 후보와 절대 시각 근거의 개수가 다릅니다.")
    result = []
    for i, course in enumerate(parsed.routes):
        dated = AbsoluteTimes.model_validate(absolute_times[i]) if absolute_times and absolute_times[i] is not None else None
        token = digest([source_version, i, course.model_dump(mode="json")])
        evidence = Evidence(source="ekispert-" + provenance_kind, version=source_version)
        # Transfer walks remain included in the quoted journey's absolute times,
        # but are not invented as fare-bearing ride segments.
        points = [p for p in course.route[:-1] if p.line not in ("徒歩", "도보")]
        if not points:
            continue
        segments = [{"segmentId": f"{token}:{j}", "operator": None, "line": p.line, "trainType": None}
                    for j, p in enumerate(points)]
        result.append(Ride.model_validate({
            "rideId": token, "departureAt": parse_absolute_time(dated.departureAt) if dated else None,
            "arrivalAt": parse_absolute_time(dated.arrivalAt) if dated else None,
            "displayedDurationMinutes": course.time, "transfers": None,
            "segments": segments, "fareUnits": [{"fareUnitId": token + ":gross",
                "rideSegmentIds": [s["segmentId"] for s in segments], "baseFare": None, "supplement": None,
                "quotedTotal": course.fee, "currency": "JPY", "evidence": evidence}],
            "fareBasis": "independent_journey", "initialWaitIncluded": "unknown", "evidence": evidence}))
    return result


def audit_matrix(rows: list[dict]) -> dict:
    candidates = []
    endpoints_valid = True
    for pair in rows:
        parsed = RawSearch.model_validate({"routes": pair["routes"]})
        for course in parsed.routes:
            endpoints_valid = endpoints_valid and course.route[0].station == pair["departure"] and course.route[-1].station == pair["arrival"]
            candidates.append(course)
    return {"stationPairs": len(rows), "candidates": len(candidates), "endpointsMatch": endpoints_valid,
            "displayedDurationKnown": sum(c.time is not None for c in candidates),
            "grossFareKnown": sum(c.fee is not None for c in candidates),
            "datedDepartureArrivalKnown": 0, "stationCodesKnown": 0,
            "fareBreakdownKnown": 0, "verifiedTransferCountKnown": 0,
            "sequentialTimeEvaluationSupported": False,
            "reason": "legacy_payload_has_no_dated_trips_or_component_fares",
            "comparisonScope": "fixed-query-time-station-journey-snapshots-only"}
