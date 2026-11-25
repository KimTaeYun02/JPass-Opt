"""Compare bounded order selection against exhaustive fixture schedules and saved results."""

import argparse
import asyncio
from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch

from .evaluator import evaluate
from .fixture_provider import FixtureProvider
from .gateway import Context
from .ledger import Ledger
from .models import PlanRequest, StationCandidate, rail_budget
from .order_alternates import can_select, feasible, schedule_score
from .replay import ReplayDataset, ReplayProvider, query_key
from .service import Planner


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "planner/fixtures/tokyo-demo.json"
INPUT = ROOT / "sample/low-call/eight-node-input.json"
CASES = (("dense", 3), ("sparse", 4), ("rail_detour", 3), ("directional", 3),
         ("long_headway", 4), ("long_headway_later", 4), ("opening_window", 4),
         ("fixed_visit", 4), ("no_feasible", 3), ("five_visits", 5))


def prepare_case(name, n):
    raw = json.loads(INPUT.read_text(encoding="utf-8"))
    raw["places"] = raw["places"][:n]
    raw["mode"] = "strict"
    raw["options"] = {"allowRepair": False, "allowPassEvaluation": False}
    provider = FixtureProvider(FIXTURE)
    for i, place in enumerate(raw["places"]):
        place.pop("anchorRole", None)
        candidates = provider.data["stations"][place["clientPlaceId"]]
        del candidates[1:]
        for side in ("access", "egress"):
            candidates[0][side]["status"] = "routed"  # Exact within the synthetic timetable only.
        if name == "dense":
            place["location"]["lat"] = 35.666 + i * 0.0004
            place["location"]["lng"] = 139.694
        elif name == "sparse":
            place["location"]["lat"] = 35.666 + i * 0.075
            place["location"]["lng"] = 139.694 + (i % 2) * 0.05
    if name == "dense":
        provider.data["defaultEdge"] = {"headwayMinutes": 5, "rideMinutes": 2, "fare": 120}
    elif name == "sparse":
        provider.data["defaultEdge"] = {"headwayMinutes": 25, "rideMinutes": 35, "fare": 710}
        provider.data["edges"]["s2>s3"] = {"headwayMinutes": 30, "rideMinutes": 68, "fare": 910}
    elif name == "rail_detour":
        provider.data["edges"]["s2>s3"] = {"headwayMinutes": 10, "rideMinutes": 55, "fare": 900}
        provider.data["edges"]["s3>s2"] = {"headwayMinutes": 10, "rideMinutes": 55, "fare": 900}
    elif name == "directional":
        provider.data["edges"]["s2>s3"] = {"headwayMinutes": 10, "rideMinutes": 55, "fare": 900}
        provider.data["edges"]["s1>s3"] = {"headwayMinutes": 10, "rideMinutes": 65, "fare": 930}
    elif name.startswith("long_headway"):
        raw["places"][0]["anchorRole"] = "start"
        provider.data["edges"]["s1>s2"] = {"headwayMinutes": 70, "rideMinutes": 12, "fare": 180}
        if name == "long_headway_later":
            raw["startAt"] = "2026-11-17T09:37:00+09:00"
    elif name == "opening_window":
        raw["places"][2]["openingWindows"][0]["endAt"] = "2026-11-17T10:02:00+09:00"
    elif name == "fixed_visit":
        raw["places"][0]["anchorRole"] = "start"
        raw["places"][2]["fixedVisitAt"] = "2026-11-17T10:25:00+09:00"
    elif name == "no_feasible":
        raw["endBy"] = "2026-11-17T10:42:00+09:00"
    return raw, provider


def metrics(route):
    if route is None:
        return None
    return {
        "order": route["order"], "completion": route["completion"], "feasible": feasible(route),
        "knownViolations": sum(c["status"] == "violated" for c in route["constraints"]),
        "unknownConstraints": sum(c["status"] == "unknown" for c in route["constraints"]),
        "elapsedMinutes": route.get("elapsedMinutes"), "grossFare": route["fare"]["noPassTotal"],
        "stopReason": route["stopReason"],
    }


async def run_policy(ledger, provider, raw, operation, *, alternate, prefix=False):
    payload = deepcopy(raw)
    payload.update(sessionId=ledger.create_session()["sessionId"], operationId=operation)
    payload["options"].update(allowOrderAlternates=alternate, allowPrefixReuse=prefix)
    planner = Planner(ledger, provider, retries=0)
    return await planner.plan(PlanRequest.model_validate(payload), payload)


def summarize(result):
    ledger = result["ledger"]
    audit = result["candidates"].get("orderEvaluation", {})
    return {**metrics(result["selected"]), "railAttempts": ledger["railAttempts"],
            "railLimit": ledger["limits"]["rail"], "stationAttempts": ledger["stationAttempts"],
            "httpRequestsObserved": ledger["httpRequestsObserved"], "sessionRail": ledger["sessionUsage"]["rail"],
            "alternateStatus": audit.get("status"), "alternate": audit.get("alternate"),
            "alternateMetrics": metrics(audit.get("plan"))}


async def exhaustive(ledger, provider, raw):
    payload = {**raw, "sessionId": ledger.create_session()["sessionId"]}
    request = PlanRequest.model_validate(payload)
    n = len(request.places)
    choices = {p.clientPlaceId: StationCandidate.model_validate(provider.data["stations"][p.clientPlaceId][0])
               for p in request.places}
    planner = Planner(ledger, provider, retries=0)
    routes = {}
    for order in permutations(request.places):
        if any(p.anchorRole and p != order[0 if p.anchorRole == "start" else -1] for p in order):
            continue
        ids = tuple(p.clientPlaceId for p in order)
        operation = "reference-" + "-".join(ids)
        ledger.begin(request.sessionId, operation, operation, {"rail": n-1, "station": 0, "walk": 0, "rag": 0})
        context = Context(ledger, request.sessionId, operation, time.monotonic()+300)
        try:
            routes[ids] = await evaluate(request, ids, choices, planner.gateway, context)
        finally:
            ledger.finish(request.sessionId, operation)
    return routes


def add_gaps(row, fastest, cheapest):
    elapsed, fare = row["elapsedMinutes"], row["grossFare"]
    row["minutesBehindFastest"] = elapsed - fastest["elapsedMinutes"] if row["feasible"] and fastest else None
    row["yenAboveFastest"] = (
        fare - fastest["grossFare"] if row["feasible"] and fare is not None and fastest
        and fastest["grossFare"] is not None else None
    )
    row["yenAboveCheapest"] = fare - cheapest if row["feasible"] and fare is not None and cheapest is not None else None


async def compare_fixture(ledger, name, n):
    raw, provider = prepare_case(name, n)
    single = await run_policy(ledger, provider, raw, "single", alternate=False)
    diverse = await run_policy(ledger, provider, raw, "diverse", alternate=True, prefix=True)
    routes = await exhaustive(ledger, provider, raw)
    eligible = [route for route in routes.values() if feasible(route)]
    fastest = metrics(min(eligible, key=schedule_score, default=None))
    cheapest = min((route["fare"]["noPassTotal"] for route in eligible if route["fare"]["noPassTotal"] is not None),
                   default=None)
    ranked = single["candidates"]["orders"]
    consecutive = [routes[tuple(row["order"])] for row in ranked[:2]]
    numeric = consecutive[0] if consecutive else single["selected"]
    if len(consecutive) == 2 and can_select(consecutive[1], numeric):
        numeric = consecutive[1]
    arms = {"first": summarize(single), "diverse": summarize(diverse), "geometricTopTwoUncapped": metrics(numeric)}
    for arm in arms.values():
        add_gaps(arm, fastest, cheapest)
    return {"case": name, "places": n, "ordersEnumerated": len(routes), "feasibleOrders": len(eligible),
            "referenceFastest": fastest, "referenceCheapestFare": cheapest, "policies": arms}


def merged_recordings(paths, output):
    datasets = [ReplayDataset.model_validate_json(path.read_bytes()) for path in paths]
    primary = datasets[0]
    records = {}
    for dataset in datasets:
        if (dataset.datasetId, dataset.provenance, dataset.bindings) != (primary.datasetId, primary.provenance, primary.bindings):
            raise ValueError("replay_sources_do_not_match")
        for record in dataset.records:
            key = query_key(record.query)
            if key in records and records[key] != record:
                raise ValueError("conflicting_exact_replay_records")
            records[key] = record
    raw = primary.model_dump(mode="json")
    raw["records"] = [record.model_dump(mode="json") for record in records.values()]
    output.write_text(ReplayDataset.model_validate(raw).model_dump_json(), encoding="utf-8")


async def compare_replay(ledger, folder, name, run):
    scenario = json.loads((run/"scenario.json").read_text(encoding="utf-8"))
    folder.mkdir()
    analysis = run/"analysis"
    paths = [analysis/"v2-recording.json"]
    if name == "different_orders":
        paths.append(analysis/"legacy-validation-recording.json")
    merged = folder/"recording.json"
    merged_recordings(paths, merged)
    provider = ReplayProvider(merged)
    return {"case": name, "scope": "exact-saved-time-and-station-queries",
            "first": summarize(await run_policy(ledger, provider, scenario["request"], "single", alternate=False)),
            "diverse": summarize(await run_policy(ledger, provider, scenario["request"], "diverse", alternate=True))}


async def benchmark():
    with tempfile.TemporaryDirectory() as tmp, patch("socket.socket.connect", side_effect=AssertionError("offline only")):
        folder = Path(tmp)
        ledger = Ledger(folder/"ledger.sqlite3", {"rail": 1000, "station": 16, "walk": 0, "rag": 0})
        synthetic = []
        for name, n in CASES:
            synthetic.append(await compare_fixture(ledger, name, n))
        scale_ledger = Ledger(folder/"scale.sqlite3")
        scaling = []
        for n in range(3, 9):
            raw, provider = prepare_case("scaling", n)
            without = summarize(await run_policy(scale_ledger, provider, raw, "full", alternate=True))
            with_prefix = summarize(await run_policy(scale_ledger, provider, raw, "prefix", alternate=True, prefix=True))
            scaling.append({"places": n, "cap": rail_budget(n), "fullOnly": without, "withPrefix": with_prefix})
        base = ROOT/"sample/low-call"
        replay = [
            await compare_replay(ledger, folder/"different", "different_orders",
                                 base/"divergent-revalidation/run-20261119"),
            await compare_replay(ledger, folder/"shared", "baseline_only",
                                 base/"sequential/run-20261119-compat"),
        ]
        return {"schemaVersion": "order-policy-benchmark/1", "httpRequestsObserved": 0,
                "referenceScope": "all-anchor-compatible-orders-fixed-stations-earliest-arrival-per-leg",
                "synthetic": synthetic, "scaling": scaling, "replay": replay}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(benchmark())
    if args.output:
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
