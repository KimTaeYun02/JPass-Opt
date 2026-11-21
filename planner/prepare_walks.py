"""Create directed routing records using a self-hosted Valhalla pedestrian graph."""

import argparse
import json
import math
from pathlib import Path
from typing import Annotated

import httpx
from pydantic import Field, TypeAdapter

from .mobility_data import MobilityData, MobilityFile, WalkRecord
from .models import DTO, Evidence, Location, Walking


class WalkingPair(DTO):
    origin: Location
    destination: Location


WALKING_PAIRS = TypeAdapter(Annotated[list[WalkingPair], Field(min_length=1, max_length=64)])


def collect_walk(client, pair, graph_version):
    locations = [{"lat": p.lat, "lon": p.lng, "search_cutoff": 80} for p in (pair.origin, pair.destination)]
    response = client.post("/route", json={"locations": locations, "costing": "pedestrian", "directions_type": "none"})
    if response.status_code == 400 and response.json().get("error_code") in (171, 442):
        return None
    response.raise_for_status()
    trip = response.json()["trip"]
    seconds = trip["summary"]["time"]
    if (trip["status"] != 0 or len(trip["legs"]) != 1
            or type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 86400
            or (seconds == 0 and pair.origin != pair.destination)):
        raise ValueError("보행 경로 엔진에서 유효한 보행 결과를 반환하지 않았습니다.")
    route = Walking(status="routed", minutes=math.ceil(seconds / 60),
                    evidence=Evidence(source="self-hosted-valhalla-osm", version=graph_version))
    return WalkRecord(origin=pair.origin, destination=pair.destination, route=route)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=Path, required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--source", choices=("fixture", "replay"), default="replay")
    parser.add_argument("--graph-version", required=True)
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or args.output.exists():
        parser.error("유효한 포트와 새 출력 파일이 필요합니다.")
    pairs = WALKING_PAIRS.validate_json(args.pairs.read_bytes())
    data = MobilityFile.model_validate_json(args.bundle.read_bytes()) if args.bundle else MobilityFile(source=args.source)
    records = {(row.origin.lat, row.origin.lng, row.destination.lat, row.destination.lng): row
               for row in data.walks}
    unavailable = 0
    with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=10,
                      trust_env=False, follow_redirects=False) as client:
        for pair in pairs:
            key = (pair.origin.lat, pair.origin.lng, pair.destination.lat, pair.destination.lng)
            if key in records and records[key].route.evidence.version == args.graph_version:
                continue
            record = collect_walk(client, pair, args.graph_version)
            if record is None:
                records.pop(key, None)
                unavailable += 1
                continue
            records[key] = record
    updated = data.model_copy(update={"walks": list(records.values())})
    MobilityData(updated)
    with args.output.open("x", encoding="utf-8") as file:
        file.write(updated.model_dump_json(indent=2) + "\n")
    print(json.dumps({"walkRoutes": len(records), "unavailable": unavailable}))


if __name__ == "__main__":
    main()
