"""Resolve verified same-day DOM clocks; never infer an unmarked day rollover."""

from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from .ekispert import AbsoluteTimes, CalendarEvidence
from ..models import instant, iso

JAPAN = ZoneInfo("Asia/Tokyo")
SUMMARY_SELECTOR = "body: exact result-summary line"
POINT_SELECTORS = {
    "departure": '[data-testid="course-detail-point-departure-time"]',
    "arrival": '[data-testid="course-detail-point-arrival-time"]',
}


class TimeResolutionError(ValueError):
    pass


def result_summary(body: str, departure: str, arrival: str) -> str:
    """Read one complete result-summary line, including its full calendar date."""
    prefix = re.escape(departure) + r"\s*⇒\s*" + re.escape(arrival) + r"\s*\(.*\)"
    matches = [line.strip() for line in body.splitlines() if re.fullmatch(prefix, line.strip())]
    if len(matches) != 1:
        raise TimeResolutionError("missing_or_ambiguous_result_calendar")
    return matches[0]


def resolve_calendar_times(snapshot: CalendarEvidence | dict) -> AbsoluteTimes:
    """Require a matching result calendar, all point clocks, and duration agreement.

    Duration only checks independently read clocks; it never creates a timestamp.
    Next-day/extended clocks remain unsupported until their DOM semantics are verified.
    """
    source = CalendarEvidence.model_validate(snapshot)
    prepared = instant(source.preparedAt).astimezone(JAPAN)
    pattern = (re.escape(source.departureName) + r"\s*⇒\s*" + re.escape(source.arrivalName)
               + r"\s*\((\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2})時(\d{1,2})分\s+出発\)")
    match = re.fullmatch(pattern, source.resultSummary.strip())
    if match is None:
        raise TimeResolutionError("unsupported_result_calendar")
    try:
        selected = datetime(*(int(part) for part in match.groups()), tzinfo=JAPAN)
    except ValueError as error:
        raise TimeResolutionError("invalid_result_calendar") from error
    if selected != prepared.replace(second=0, microsecond=0):
        raise TimeResolutionError("result_calendar_query_mismatch")

    clocks = source.pointTimes
    count = clocks[-1].pointIndex + 1
    expected = [(0, "departure")]
    for index in range(1, count):
        expected.append((index, "arrival"))
        if index < count - 1:
            expected.append((index, "departure"))
    if count < 2 or [(clock.pointIndex, clock.kind) for clock in clocks] != expected:
        raise TimeResolutionError("missing_or_ambiguous_point_clocks")
    dated = []
    for clock in clocks:
        label = "(?:出発|発)" if clock.kind == "departure" else "(?:到着|着)"
        matched = re.fullmatch(label + r"\s*(\d{2}):(\d{2})", clock.rawText.strip())
        if matched is None:
            raise TimeResolutionError("unsupported_clock_or_day_marker")
        hour, minute = (int(value) for value in matched.groups())
        if hour > 23 or minute > 59:
            raise TimeResolutionError("unsupported_clock_or_day_marker")
        value = selected.replace(hour=hour, minute=minute)
        if dated and value < dated[-1]:
            raise TimeResolutionError("unmarked_midnight_or_non_monotonic")
        dated.append(value)
    if dated[0] < prepared:
        raise TimeResolutionError("departure_before_query_or_unmarked_next_day")
    if dated[-1] - dated[0] != timedelta(minutes=source.displayedDurationMinutes):
        raise TimeResolutionError("displayed_duration_disagrees_with_clocks")
    return AbsoluteTimes(departureAt=iso(dated[0]), arrivalAt=iso(dated[-1]),
                         evidence="calendar_dom_clock", calendarEvidence=source)


# Explicitly read DOM text/attributes, never executable site state or network internals.
DOM_SNAPSHOT_SCRIPT = """() => ({
    bodyText: document.body.innerText,
    courses: [...document.querySelectorAll('[data-testid="course-detail"]')].map(section => ({
        pointTimes: [...section.querySelectorAll('[data-testid="course-detail-point"]')].flatMap((point, pointIndex) => {
            const fields = [];
            for (const kind of ['arrival', 'departure']) {
                for (const node of point.querySelectorAll('[data-testid="course-detail-point-' + kind + '-time"]')) {
                    fields.push({pointIndex, kind, rawText:node.innerText, selector:'[data-testid="course-detail-point-' + kind + '-time"]'});
                }
            }
            return fields;
        }),
        pointCount: section.querySelectorAll('[data-testid="course-detail-point"]').length,
        stationNames: [...section.querySelectorAll('[data-testid="course-detail-point-station-name"]')].map(n=>n.innerText.trim())
    }))
})"""
