"""Build replay evidence from existing public team-crawler snapshots, offline."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from planner.adapters.ekispert import audit_matrix, normalize_routes
from planner.models import iso
from planner.pricing import unit_total

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def write(name, data):
    with (OUT / name).open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def main():
    source = ROOT / "sample/team-crawler-route-matrix.json"
    raw = source.read_bytes()
    rows = json.loads(raw)
    source_sha = hashlib.sha256(raw).hexdigest()
    audit = audit_matrix(rows)
    rides = [ride for row in rows for ride in normalize_routes({"routes": row["routes"]}, source_version=source_sha, provenance_kind="legacy-team-crawler")]
    audit.update(sourceArtifact=str(source.relative_to(ROOT)).replace("\\", "/"), sourceSha256=source_sha,
                 normalizedCandidates=len(rides), normalizedGrossFareKnown=sum(all(unit_total(u.model_dump()) is not None for u in r.fareUnits) for r in rides),
                 networkRequestsDuringAudit=0, qualityComparison="not_measurable_without_dated_sequential_quotes")
    write("legacy-audit.json", audit)

    comparison = json.loads((ROOT / "sample/team-crawler-comparison.json").read_text(encoding="utf-8"))
    prepared = datetime.fromisoformat(comparison["referenceDepartureAt"].replace("Z", "+00:00"))
    pair = next(r for r in rows if (r["departure"], r["arrival"]) == ("東大前", "後楽園"))
    evidence = {"source": "manual-replay-scenario-one-minute-walk-assumption", "version": "1"}
    bindings = []
    for i, name in enumerate(("東大前", "後楽園")):
        # These are illustrative coordinates and explicit scenario assumptions,
        # not a captured nearest-station lookup or measured walking route.
        location = {"lat": 35.0 + i * 0.01, "lng": 139.0}
        place = {"clientPlaceId": f"legacy-{i}", "name": name + " (재생 입력)", "location": location,
                 "stayMinutes": 0, "anchorRole": "start" if i == 0 else "end", "openingWindows": None}
        station = {"namespace": "manual-replay-example", "sourceVersion": source_sha, "providerStationId": None,
                   "name": name, "line": "미확인", "prefecture": "미확인", "location": location}
        walking = {"status": "estimated", "minutes": 1, "evidence": evidence}
        bindings.append({"place": place, "candidates": [{"station": station, "access": walking, "egress": walking}]})
    dataset = {"schemaVersion": "ekispert-replay/1", "datasetId": "team-crawler-legacy-20261017",
               "provenance": {"kind": "legacy-team-crawler", "sourceArtifact": str(source.relative_to(ROOT)).replace("\\", "/"),
                              "sourceSha256": source_sha, "notice": "교통 응답은 저장된 실응답. 입력 좌표·역 바인딩·도보 1분은 재생 시나리오 가정. 실제 관광 동선 검증이 아님."},
               "bindings": bindings, "records": [{"query": {"origin": bindings[0]["candidates"][0]["station"],
                   "destination": bindings[1]["candidates"][0]["station"], "preparedAt": iso(prepared), "options": {}},
                   "payload": {"routes": pair["routes"]}, "absoluteTimes": None}]}
    write("legacy-recording.json", dataset)
    request = {"inputVersion": "2", "dataSource": "replay", "fixtureId": dataset["datasetId"],
               "sessionId": "issued-by-cli", "operationId": "legacy-replay-example", "startAt": iso(prepared - timedelta(minutes=1)),
               "places": [b["place"] for b in bindings], "options": {"allowRepair": False, "allowPassEvaluation": False}}
    write("legacy-input.json", request)
    probe = ROOT / "tmp/ekispert-time-probe.log"
    if probe.exists() and "HTTP: 403" in probe.read_text(encoding="utf-8-sig"):
        write("capture-blocked.json", {"schemaVersion": "ekispert-capture-probe/1", "dataSource": "live-capture",
              "recordedAt": iso(datetime.now(timezone.utc)), "status": "blocked", "stopReason": "http_403",
              "routeSearchAttempts": 1, "stationRequests": 0, "httpRequestsObserved": None,
              "confirmedMainDocumentStatus": 403, "retries": 0,
              "query": {"departure": "東京", "arrival": "上野", "preparedAt": "2026-11-19T09:00:00+09:00"},
              "evidence": "Initial existing-crawler probe returned HTTP 403 before result DOM; observer totals were not persisted. No further network collection attempted.",
              "probeLogSha256": hashlib.sha256(probe.read_bytes()).hexdigest()})
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    main()
