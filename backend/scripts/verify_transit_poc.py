"""One stop-pair WALK + official transit + WALK diagnostic, separate from the app.

Default: credential preflight only. --live opts into at most 3 Ekispert and
2 Google calls. No retries, crawler fallback, raw traffic or exception logging.
"""

import argparse
import json
import math
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.clients.google_routes import walking_route  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
LIMITS = {"geo": 2, "course": 1, "walk": 2}
ENDPOINTS = {"geo": "geo/station", "course": "search/course/extreme"}
ORIGIN = {"lat": 34.6865, "lng": 135.5253}  # Osaka Castle park area
DESTINATION = {"lat": 34.6935, "lng": 135.5030}  # Nakanoshima area


class PocError(Exception):
    """Only locally defined classification codes may be surfaced."""


def rows(value):
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    raise PocError("invalid_collection")


def result_set(data):
    if not isinstance(data, dict) or not isinstance(data.get("ResultSet"), dict):
        raise PocError("invalid_result_set")
    result = data["ResultSet"]
    if "Error" in result:
        raise PocError("ekispert_error_result")
    return result


def transport(value):
    if isinstance(value, str):
        return value, ""
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        return value["text"], value.get("detail", "")
    raise PocError("invalid_transport_type")


def stop(point):
    station, geo = point.get("Station", {}), point.get("GeoPoint", {})
    code = station.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]+", code):
        raise PocError("invalid_stop_id")
    if geo.get("gcs") != "wgs84":
        raise PocError("invalid_coordinate_system")
    try:
        lat, lng = float(geo["lati_d"]), float(geo["longi_d"])
    except (KeyError, TypeError, ValueError):
        raise PocError("invalid_stop_coordinates") from None
    if not math.isfinite(lat + lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise PocError("invalid_stop_coordinates")
    return {"code": code, "location": {"lat": lat, "lng": lng}}


def first_bus_stop(data):
    points = rows(result_set(data).get("Point", []))
    if not points:
        raise PocError("no_local_bus_stop")
    # The first provider-ranked candidate only; no hidden candidate sweep.
    mode, detail = transport(points[0].get("Station", {}).get("Type"))
    if mode == "bus" and not detail:
        raise PocError("bus_detail_missing")
    if mode != "bus" or "local" not in str(detail).split(":"):
        raise PocError("no_local_bus_stop")
    return stop(points[0])


def timestamp(state):
    value = state.get("Datetime") if isinstance(state, dict) else None
    if isinstance(value, dict):
        value = value.get("text")
    try:
        instant = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise PocError("missing_timetable_timestamp") from None
    if instant.utcoffset() is None:
        raise PocError("missing_timetable_timezone")
    return instant.astimezone(JST)


def component_minutes(value):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise PocError("invalid_course_duration")
    return int(value)


def validate_course(data, origin, destination, ready_at):
    courses = rows(result_set(data).get("Course", []))
    if not courses:
        raise PocError("no_course")
    route = courses[0].get("Route")
    if not isinstance(route, dict):
        raise PocError("invalid_course")
    points, lines = rows(route.get("Point", [])), rows(route.get("Line", []))
    if not lines or len(points) != len(lines) + 1:
        raise PocError("invalid_course_chain")
    endpoints = stop(points[0]), stop(points[-1])
    for actual, expected in zip(endpoints, (origin, destination)):
        if actual["code"] != expected["code"]:
            raise PocError("endpoint_id_mismatch")
        # Same ID, with only representation/rounding drift allowed. No ID merging.
        if any(
            abs(actual["location"][k] - expected["location"][k]) > 0.00001
            for k in ("lat", "lng")
        ):
            raise PocError("endpoint_coordinates_mismatch")
    modes, has_local_bus, previous = [], False, ready_at
    step_minutes = gap_minutes = 0.0
    first_departure = None
    for line in lines:
        mode, detail = transport(line.get("Type"))
        if mode == "bus" and not detail:
            raise PocError("bus_detail_missing")
        if mode not in {"walk", "bus", "train"}:
            raise PocError("unsupported_transport_mode")
        has_local_bus |= mode == "bus" and "local" in str(detail).split(":")
        if mode != "walk" and line.get("TimeReliability") != "onTimetable":
            raise PocError("timetable_not_verified")
        departure, arrival = (
            timestamp(line.get("DepartureState")),
            timestamp(line.get("ArrivalState")),
        )
        if departure < previous or arrival < departure:
            raise PocError("invalid_timetable_order")
        if first_departure is None:
            first_departure = departure
        gap_minutes += (departure - previous).total_seconds() / 60
        step_minutes += (arrival - departure).total_seconds() / 60
        previous = arrival
        modes.append(mode)
    if not has_local_bus:
        raise PocError("no_local_bus_leg")
    elapsed = (previous - ready_at).total_seconds() / 60
    provider_minutes = sum(
        component_minutes(route.get(field))
        for field in ("timeOnBoard", "timeWalk", "timeOther")
    )
    delta = (previous - first_departure).total_seconds() / 60 - provider_minutes
    if abs(delta) > 1:
        raise PocError("provider_duration_mismatch")
    return {
        "stopCodes": [origin["code"], destination["code"]],
        "modes": modes,
        "requestedTransitDepartureAt": ready_at.isoformat(),
        "transitArrivalAt": previous.isoformat(),
        "transitElapsedMinutes": elapsed,
        "transitStepMinutes": step_minutes,
        "transitWaitingAndTransferMinutes": gap_minutes,
        "providerComponentMinutes": provider_minutes,
        "providerElapsedDeltaMinutes": delta,
    }


class LiveCalls:
    def __init__(self, ekispert_key, routes_key):
        self.ekispert_key, self.routes_key = ekispert_key, routes_key
        self.counts = dict.fromkeys(LIMITS, 0)

    def charge(self, kind):
        if self.counts[kind] >= LIMITS[kind]:
            raise PocError("request_budget_exceeded")
        self.counts[kind] += 1

    def get(self, kind, params):
        endpoint = ENDPOINTS[kind]
        self.charge(kind)
        # Ekispert documents require the viaList ':' separator to stay unescaped.
        query = urlencode({**params, "key": self.ekispert_key}, safe=":")
        try:
            response = requests.get(
                f"https://api.ekispert.jp/v1/json/{endpoint}?{query}",
                headers={"Accept": "application/json"},
                timeout=15,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise PocError("ekispert_connection_error") from None
        status = response.status_code
        if status != 200:
            if type(status) is int and 100 <= status <= 599:
                raise PocError(f"ekispert_http_{status}")
            raise PocError("ekispert_invalid_http_status")
        try:
            return response.json()
        except ValueError:
            raise PocError("ekispert_invalid_json") from None

    def walk(self, origin, destination):
        self.charge("walk")
        try:
            route = walking_route(origin, destination, self.routes_key)
        except Exception:
            raise PocError("google_walk_error") from None
        if route is None:
            raise PocError("walk_unavailable")
        return route


def run(live, departure, env, calls=None, origin=ORIGIN, destination=DESTINATION):
    configured = {
        key: bool(env.get(key, "").strip())
        for key in ("EKISPERT_API_KEY", "GOOGLE_ROUTES_API_KEY")
    }
    report = {
        "status": "PREFLIGHT",
        "credentialsConfigured": configured,
        "callLimits": LIMITS,
        "calls": dict.fromkeys(LIMITS, 0),
    }
    if not all(configured.values()):
        report["status"] = "MISSING_CREDENTIALS"
        return 1, report
    if not live:
        return 0, report
    if departure is None:
        report["status"] = "DEPARTURE_REQUIRED"
        return 1, report
    calls = calls or LiveCalls(env["EKISPERT_API_KEY"], env["GOOGLE_ROUTES_API_KEY"])
    try:
        stops = []
        for place in (origin, destination):
            raw = calls.get(
                "geo",
                {
                    "geoPoint": f"{place['lat']},{place['lng']},wgs84,1000",
                    "gcs": "wgs84",
                    "type": "bus.local",
                    "stationCount": 2,
                },
            )
            stops.append(first_bus_stop(raw))
        if stops[0]["code"] == stops[1]["code"]:
            raise PocError("same_stop_pair")
        outward = calls.walk(origin, stops[0]["location"])
        ready = departure + timedelta(minutes=outward.minutes)
        data = calls.get(
            "course",
            {
                "viaList": ":".join(s["code"] for s in stops),
                "date": ready.strftime("%Y%m%d"),
                "time": ready.strftime("%H%M"),
                "searchType": "departure",
                "sort": "time",
                "answerCount": 1,
                "gcs": "wgs84",
            },
        )
        course = validate_course(data, *stops, ready)
        inward = calls.walk(stops[1]["location"], destination)
        report.update(course)
        report.update(
            status="PASS",
            startAt=departure.isoformat(),
            totalTravelMinutes=outward.minutes
            + course["transitElapsedMinutes"]
            + inward.minutes,
            accessWalkingMinutes=[outward.minutes, inward.minutes],
            accessWalkingMeters=[outward.distance, inward.distance],
            accessPathPoints=[len(outward.path), len(inward.path)],
        )
        code = 0
    except PocError as exc:
        report.update(status="FAIL", reason=str(exc))
        code = 2
    except Exception:
        report.update(status="FAIL", reason="unexpected_verification_error")
        code = 2
    report["calls"] = dict(calls.counts)
    return code, report


def parse_departure(value):
    try:
        if not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00\+09:00", value
        ):
            raise ValueError
        instant = datetime.fromisoformat(value)
        return instant.astimezone(JST)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError(
            "Use a minute-aligned RFC 3339 instant with +09:00."
        ) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--departure", type=parse_departure)
    args = parser.parse_args()
    load_dotenv(BACKEND.parent / ".env")
    env = {
        key: os.getenv(key, "") for key in ("EKISPERT_API_KEY", "GOOGLE_ROUTES_API_KEY")
    }
    code, report = run(args.live, args.departure, env)
    # A final guard even if future report fields accidentally contain credentials.
    output = json.dumps(report, ensure_ascii=False, indent=2)
    for key in env.values():
        if key:
            output = output.replace(key, "[REDACTED]")
    print(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
