"""Import a dated Ekispert capture for a verified single bus connection."""

import argparse
import hashlib
import json
from pathlib import Path

from .adapters.ekispert import AbsoluteTimes, RawSearch
from .adapters.ekispert_times import resolve_calendar_times
from .mobility_data import BusSearch, BusTrip, MobilityData, MobilityFile
from .models import Evidence, instant, iso


def import_search(raw, connection, verified_lines):
    capture = json.loads(raw)
    query = capture["query"]
    codes = capture.get("stationCodes") or {}
    board, alight = connection.boarding, connection.alighting
    if (capture.get("schemaVersion") != "ekispert-capture/1" or capture.get("dataSource") != "live-capture"
            or capture.get("status") not in ("captured", "partial")
            or (query["departure"], query["arrival"]) != (board.name, alight.name)
            or (codes.get("depCode"), codes.get("arrCode")) != (board.stopId, alight.stopId)):
        raise ValueError("캡처한 양 끝 정류장 이름·코드와 바인딩이 일치해야 합니다.")
    payload = RawSearch.model_validate(capture["payload"])
    trips = []
    for course, times in zip(payload.routes, capture["absoluteTimes"], strict=True):
        if (times is None or len(course.route) != 2
                or (course.route[0].station, course.route[-1].station) != (board.name, alight.name)
                or course.route[0].line not in verified_lines):
            continue
        dated = AbsoluteTimes.model_validate(times)
        calendar = dated.calendarEvidence
        if calendar and (
            instant(calendar.preparedAt) != instant(query["preparedAt"])
            or [calendar.departureName, calendar.arrivalName] != capture.get("calendarAliases", [board.name, alight.name])
            or resolve_calendar_times(calendar) != dated
        ):
            raise ValueError("캡처된 버스 시각과 달력 근거가 일치하지 않습니다.")
        if course.time is not None and (instant(dated.arrivalAt) - instant(dated.departureAt)).total_seconds() != 60 * course.time:
            continue
        trips.append(BusTrip(departureAt=iso(instant(dated.departureAt)), arrivalAt=iso(instant(dated.arrivalAt)),
                             line=course.route[0].line, fareYen=course.fee))
    return BusSearch(boarding=board, alighting=alight, preparedAt=iso(instant(query["preparedAt"])),
                     timetable="verified" if trips else "unknown", trips=trips,
                     evidence=Evidence(source="ekispert-captured-verified-bus", version=hashlib.sha256(raw).hexdigest()))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--connection", type=int, default=0)
    parser.add_argument("--verified-line", action="append", required=True,
                        help="Exact line label visually checked as scheduled bus service in the source result.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    data = MobilityFile.model_validate_json(args.bundle.read_bytes())
    if data.source != "replay" or not 0 <= args.connection < len(data.connections) or args.output.exists():
        parser.error("재생용 바인딩 번호와 새 출력 경로가 필요합니다.")
    search = import_search(args.capture.read_bytes(), data.connections[args.connection], set(args.verified_line))
    updated = data.model_copy(update={"searches": [*data.searches, search]})
    MobilityData(updated)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(updated.model_dump_json(indent=2) + "\n")
    print(json.dumps({"timetable": search.timetable, "trips": len(search.trips)}))


if __name__ == "__main__":
    main()
