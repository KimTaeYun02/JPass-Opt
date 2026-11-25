"""Build an offline example from the verified dated capture; no network calls."""

from datetime import timedelta
import hashlib
import json
from pathlib import Path

from planner.models import RailQuery, instant, iso
from planner.replay import ReplayDataset, record_from_capture

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def write(name, payload):
    with (HERE / name).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main():
    source = HERE / "capture.json"
    content = source.read_bytes()
    captured = json.loads(content)
    if captured["status"] != "captured" or captured["headless"] is not False:
        raise ValueError("날짜·시각 검증을 완료한 headed 수집 결과가 필요합니다.")
    source_hash = hashlib.sha256(content).hexdigest()
    prepared = instant(captured["query"]["preparedAt"])
    bindings = []
    for index, (name, code) in enumerate(((captured["query"]["departure"], "depCode"),
                                         (captured["query"]["arrival"], "arrCode"))):
        # Coordinates/bindings/walks are illustrative scenario inputs, not real POI lookups.
        location = {"lat": 35.0 + index * 0.01, "lng": 139.0}
        place = {"clientPlaceId": f"dated-{index}", "name": name + " (재생 입력)", "location": location,
                 "stayMinutes": 0, "anchorRole": "start" if index == 0 else "end", "openingWindows": None}
        station = {"namespace": "ekispert-roote", "sourceVersion": source_hash,
                   "providerStationId": captured["stationCodes"][code], "name": name,
                   "line": "미확인", "prefecture": "미확인", "location": location}
        walking = {"status": "estimated", "minutes": 1,
                   "evidence": {"source": "illustrative-one-minute-walking-assumption", "version": "1"}}
        bindings.append({"place": place, "candidates": [{"station": station, "access": walking, "egress": walking}]})
    query = RailQuery(origin=bindings[0]["candidates"][0]["station"], destination=bindings[1]["candidates"][0]["station"],
                      preparedAt=iso(prepared))
    record = record_from_capture(captured, query)
    dataset = ReplayDataset.model_validate({"schemaVersion": "ekispert-replay/1", "datasetId": "headed-dated-20261119",
        "provenance": {"kind": "captured-team-crawler", "sourceArtifact": str(source.relative_to(ROOT)).replace("\\", "/"),
                       "sourceSha256": source_hash,
                       "notice": "교통 시각·총운임은 실제 수집값. 좌표·관광지 바인딩·도보 각 1분은 재생 시나리오 가정. 단일 역간 연결 시험이며 관광 동선 품질 검증이 아님."},
        "bindings": bindings, "records": [record.model_dump(mode="json")]})
    write("recording.json", dataset.model_dump(mode="json"))
    write("input.json", {"inputVersion": "2", "sessionId": "issued-by-cli", "operationId": "dated-replay-example",
        "dataSource": "replay", "fixtureId": dataset.datasetId, "startAt": iso(prepared-timedelta(minutes=1)),
        "places": [binding["place"] for binding in bindings], "options": {"allowRepair": False, "allowPassEvaluation": False}})


if __name__ == "__main__":
    main()
