"""Explicit, bounded acquisition CLI; default mode only replays saved files."""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

from .adapters.ekispert import normalize_routes
from .evaluator import choose_ride, visit_at
from .ledger import Stopped
from .models import RailQuery, instant, iso
from .replay import ReplayDataset, record_from_capture
from .sequential_benchmark import (Scenario, compare_schedules, fixed_time_order, geometric_order,
                                    replay_selected_order, replay_v2)


def save(path: Path, value: dict):
    temporary = path.with_suffix(path.suffix+".writing")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    temporary.replace(path)


def new_json(path: Path, value: dict):
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2)+"\n")


def extraction_provenance(method: str) -> str:
    kinds = {"existing-team-crawler":"captured-team-crawler", "public-dom-layout-compat":"captured-public-dom",
             "synthetic-test-parser":"test-only"}
    if method not in kinds:
        raise ValueError("unsupported_extraction_method")
    return kinds[method]


async def acquire(scenario: Scenario, output: Path, crawler, *, max_searches=10, budget_seconds=300,
                  collector=None, monotonic=time.monotonic):
    if type(max_searches) is not int or not 0 <= max_searches <= 10:
        raise ValueError("검색 예약 상한은 0~10회입니다.")
    if not 0 < budget_seconds <= 300:
        raise ValueError("전역 수집 기한은 0~300초입니다.")
    if collector is None:
        from .capture import collect
        collector = collect
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output/"captures").mkdir()
    new_json(output/"scenario.json", scenario.model_dump(mode="json"))
    started = monotonic()
    deadline = started+budget_seconds
    report = {"schemaVersion":"sequential-acquisition/1", "status":"running", "stopReason":None,
              "capturedAt":iso(datetime.now(timezone.utc)), "headless":False,
              "limits":{"searchReservations":max_searches,"deadlineSeconds":budget_seconds},
              "searchReservations":0, "routeSearchAttempts":0, "httpRequestsObserved":0,
              "stationRequests":0,"walkRequests":0,"retries":0,
              "v2Order":geometric_order(scenario), "legacyFixed":None,"legacySharedWithV2":False,
              "providerOptions":None,"extractionMethod":None,"captures":[],"sequences":{}}
    choices = {binding.place.clientPlaceId:binding.candidates[0] for binding in scenario.bindings}
    places = {place.clientPlaceId:place for place in scenario.request.places}
    persist = lambda:save(output/"acquisition.json", report)
    persist()

    async def capture(profile, origin_id, destination_id, prepared):
        if monotonic() >= deadline:
            raise Stopped("deadline_exceeded")
        if report["searchReservations"] >= max_searches:
            raise Stopped("search_budget_exhausted")
        report["searchReservations"] += 1
        query = RailQuery(origin=choices[origin_id].station, destination=choices[destination_id].station, preparedAt=iso(prepared))
        entry = {"profile":profile,"from":origin_id,"to":destination_id,"query":query.model_dump(mode="json"),
                 "file":f"captures/{report['searchReservations']:02d}-{profile}.json", "status":"reserved","sha256":None}
        report["captures"].append(entry)
        persist()
        result = None
        try:
            result = await collector(crawler, departure=query.origin.name, arrival=query.destination.name,
                                     prepared_at=query.preparedAt, output=output/entry["file"],
                                     timeout_seconds=min(90, max(0.001,deadline-monotonic())), headless=False)
        finally:
            path = output/entry["file"]
            if path.exists():
                content = path.read_bytes()
                entry["sha256"] = hashlib.sha256(content).hexdigest()
                try:
                    stored = json.loads(content)
                    entry["status"] = stored.get("status","incomplete_capture_file")
                    report["routeSearchAttempts"] += stored.get("routeSearchAttempts",0)
                    report["httpRequestsObserved"] += stored.get("httpRequestsObserved",0)
                except (ValueError, TypeError):
                    entry["status"] = "incomplete_capture_file"
            else:
                entry["status"] = "capture_file_missing"
            persist()
        if monotonic() >= deadline:
            raise Stopped("deadline_exceeded")
        if result is None or result.get("status") not in ("captured","partial"):
            raise Stopped((result or {}).get("stopReason") or "capture_failed")
        if profile != "snapshot" and result["status"] != "captured":
            raise Stopped("sequential_capture_has_unresolved_times")
        record = record_from_capture(result, query)
        method = result.get("extractionMethod")
        if not isinstance(method,str) or not method:
            raise Stopped("missing_extraction_method")
        if report["extractionMethod"] is None:
            report["extractionMethod"] = method
        elif report["extractionMethod"] != method:
            raise Stopped("extraction_method_changed")
        options = result.get("providerOptions",{})
        if report["providerOptions"] is None:
            report["providerOptions"] = options
        elif options != report["providerOptions"]:
            raise Stopped("provider_options_changed")
        persist()
        return result, record

    async def sequence(profile, order):
        current = instant(scenario.request.startAt)
        trace = {"order":list(order),"visits":[],"legs":[],"completion":"partial","finishAt":None}
        report["sequences"][profile] = trace
        for index, pid in enumerate(order):
            visit, _ = visit_at(places[pid], current, False)
            trace["visits"].append(visit)
            persist()
            current = instant(visit["leaveAt"])
            if index == len(order)-1:
                break
            target = order[index+1]
            prepared = current+timedelta(minutes=choices[pid].access.minutes)
            _, record = await capture(profile, pid, target, prepared)
            rides = normalize_routes(record.payload, source_version="bounded-sequential-capture",
                                     absolute_times=record.absoluteTimes, provenance_kind=extraction_provenance(report["extractionMethod"]))
            ride = choose_ride(rides, prepared)
            current = instant(ride.arrivalAt)+timedelta(minutes=choices[target].egress.minutes)
            trace["legs"].append({"from":pid,"to":target,"preparedAt":iso(prepared),
                                  "departureAt":ride.departureAt,"arrivalAt":ride.arrivalAt,"placeArrivalAt":iso(current)})
            persist()
        trace.update(completion="complete", finishAt=iso(current))
        persist()

    async def work():
        # The local v2 order was committed above, before reading the fixed matrix.
        edges = []
        ids = sorted(places)
        for origin in ids:
            for destination in ids:
                if origin != destination:
                    result, _ = await capture("snapshot", origin, destination, instant(scenario.request.startAt))
                    edges.append({"from":origin,"to":destination,"payload":result["payload"]})
        report["legacyFixed"] = fixed_time_order(ids, edges)
        persist()
        await sequence("v2", report["v2Order"])
        old_order = report["legacyFixed"]["order"]
        if old_order == report["v2Order"]:
            report["legacySharedWithV2"] = True
            report["sequences"]["legacy-validation"] = deepcopy(report["sequences"]["v2"])
            persist()
        else:
            await sequence("legacy-validation", old_order)

    try:
        await asyncio.wait_for(work(), timeout=budget_seconds)
        report["status"] = "complete"
    except asyncio.CancelledError:
        report.update(status="cancelled", stopReason="cancelled")
        raise
    except TimeoutError:
        report.update(status="partial", stopReason="deadline_exceeded")
    except Stopped as error:
        report.update(status="partial", stopReason=error.reason)
    except Exception as error:
        report.update(status="partial", stopReason=type(error).__name__)
        report["errorDetail"] = str(error)[:512]
    finally:
        report["elapsedSeconds"] = round(monotonic()-started,3)
        persist()
    return report


def read_records(run: Path, scenario: Scenario, acquisition: dict, profile: str):
    records = []
    for entry in acquisition["captures"]:
        if entry["profile"] != profile or entry["status"] not in ("captured","partial"):
            continue
        path = (run/entry["file"]).resolve()
        if not path.is_relative_to(run.resolve()/"captures"):
            raise ValueError("capture_path_outside_run")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("capture_hash_mismatch")
        records.append(record_from_capture(json.loads(content), RailQuery.model_validate(entry["query"])))
    return records


async def analyze(run: Path, output: Path):
    """Only saved bytes and the existing offline evaluator; no crawler import."""
    run, output = Path(run), Path(output)
    scenario = Scenario.model_validate_json((run/"scenario.json").read_text(encoding="utf-8"))
    acquisition_bytes = (run/"acquisition.json").read_bytes()
    acquisition = json.loads(acquisition_bytes)
    if acquisition["v2Order"] != geometric_order(scenario):
        raise ValueError("recorded_geometric_order_mismatch")
    output.mkdir(parents=True, exist_ok=False)
    provenance = {"sourceArtifact":"acquisition.json",
                  "sourceSha256":hashlib.sha256(acquisition_bytes).hexdigest(),
                  "notice":"실제 검색 시각·운임. 관광지 좌표는 근사 입력, 도보·체류는 시험 가정. 최근접 역·영업시간·패스 절감액 검증이 아님."}
    results = {}
    for profile in ("v2","legacy-validation"):
        source_profile = "v2" if profile == "legacy-validation" and acquisition["legacySharedWithV2"] else profile
        records = read_records(run, scenario, acquisition, source_profile)
        kinds = {extraction_provenance(json.loads((run/entry["file"]).read_text(encoding="utf-8"))["extractionMethod"])
                 for entry in acquisition["captures"]
                 if entry["profile"] == source_profile and entry["status"] in ("captured","partial")}
        if len(kinds) > 1:
            raise ValueError("mixed_extraction_provenance")
        # An empty failed run has no extracted rides. Preserve its failed team-search origin.
        provenance["kind"] = next(iter(kinds), "captured-team-crawler")
        if provenance["kind"] == "test-only":
            provenance["notice"] = "합성 수집기를 사용한 테스트 전용 자료. 실제 시간표 검증이 아님."
        dataset = ReplayDataset(schemaVersion="ekispert-replay/1", datasetId=scenario.request.fixtureId,
                                provenance=provenance, bindings=scenario.bindings, records=records)
        dataset_path = output/(profile+"-recording.json")
        new_json(dataset_path, dataset.model_dump(mode="json"))
        if profile == "v2":
            result = await replay_v2(scenario, dataset_path)
        elif acquisition["legacyFixed"] is not None:
            result = await replay_selected_order(scenario, dataset_path, acquisition["legacyFixed"]["order"])
        else:
            continue
        new_json(output/(profile+"-result.json"), result)
        results[profile] = result["selected"]
        trace = acquisition["sequences"].get(profile)
        if trace and trace["completion"] == "complete":
            if result["selected"]["completion"] != "complete" or trace["finishAt"] != result["selected"]["finishAt"]:
                raise ValueError("captured_schedule_replay_mismatch")
            actual = [{key:leg[key] for key in ("from","to","preparedAt","departureAt","arrivalAt","placeArrivalAt")}
                      for leg in result["selected"]["legs"]]
            if actual != trace["legs"]:
                raise ValueError("captured_leg_replay_mismatch")
    comparison = compare_schedules(acquisition["legacyFixed"],results.get("legacy-validation"),results.get("v2"))
    comparison.update(schemaVersion="sequential-comparison/1", acquisitionStatus=acquisition["status"],
                      stopReason=acquisition["stopReason"], assumptions=scenario.assumptions,
                      acquisition={key:acquisition[key] for key in ("searchReservations","routeSearchAttempts","httpRequestsObserved","stationRequests","walkRequests","retries")},
                      searchesByProfile={name:sum(entry["profile"]==name for entry in acquisition["captures"])
                                         for name in ("snapshot","v2","legacy-validation")},
                      legacySharedWithV2=acquisition["legacySharedWithV2"], replayHttpRequests=0,
                      legacyStaticClaim="station-leg-duration-only; excludes initial wait, stays and access walks")
    new_json(output/"comparison.json", comparison)
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="명시적으로 실제 headed 검색을 실행한다")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--backend", type=Path)
    parser.add_argument("--run", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-searches", type=int, default=10)
    parser.add_argument("--budget-seconds", type=float, default=300)
    args = parser.parse_args()
    if not 0 <= args.max_searches <= 10 or not 0 < args.budget_seconds <= 300:
        parser.error("검색 예약은 최대 10회, 전역 수집 기한은 최대 300초입니다.")
    if args.collect or args.dry_run:
        if args.scenario is None or args.run is not None or (args.collect and args.dry_run):
            parser.error("--scenario와 --collect 또는 --dry-run을 지정하세요.")
        scenario = Scenario.model_validate_json(args.scenario.read_text(encoding="utf-8"))
        if args.dry_run:
            print(json.dumps({"mode":"offline-dry-run","v2Order":geometric_order(scenario),"maxSearches":args.max_searches,
                              "snapshotSearches":6,"v2SequentialSearches":2,"extraLegacyValidationUpperBound":2},ensure_ascii=False))
            return
        if args.backend is None or args.output is None:
            parser.error("실제 수집에는 --backend와 새로운 --output 디렉터리가 필요합니다.")
        backend = args.backend.resolve()
        if not (backend/"app/crawling/ekispert.py").is_file():
            parser.error("기존 크롤러 backend 경로가 아닙니다.")
        from .capture import load_team_crawler
        crawler=load_team_crawler(backend)
        async def collect_and_analyze():
            await acquire(scenario,args.output,crawler,max_searches=args.max_searches,budget_seconds=args.budget_seconds)
            return await analyze(args.output,args.output/"analysis")
        result=asyncio.run(collect_and_analyze())
    else:
        if args.run is None or args.output is None or args.backend is not None:
            parser.error("기본 모드는 --run 저장 디렉터리와 새로운 --output 디렉터리의 오프라인 재생입니다.")
        result=asyncio.run(analyze(args.run,args.output))
    print(json.dumps({key:result[key] for key in ("status","acquisitionStatus","stopReason","timingOutcome","v2ElapsedMinutesSaved","acquisition")},ensure_ascii=False))
    if result["status"] != "complete":
        sys.exit(2)


if __name__ == "__main__":
    main()
