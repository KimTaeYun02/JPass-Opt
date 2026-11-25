"""Small exhaustive offline reference, scoped to fixed stations and this evaluator."""

import argparse
import asyncio
from copy import deepcopy
from itertools import permutations
import json
from pathlib import Path
import tempfile
import time

from .evaluator import evaluate
from .fixture_provider import FixtureProvider
from .gateway import Context
from .ledger import Ledger
from .models import PlanRequest, StationCandidate, rail_budget
from .service import Planner

ROOT = Path(__file__).resolve().parent.parent


async def benchmark():
    provider = FixtureProvider(ROOT / "planner/fixtures/tokyo-demo.json")
    provider.data["edges"]["s1>s2"] = {"headwayMinutes": 70, "rideMinutes": 12, "fare": 220, "transfers": 1}
    raw = json.loads((ROOT / "sample/low-call/eight-node-input.json").read_text(encoding="utf-8"))
    raw["places"] = raw["places"][:4]
    raw["options"] = {"allowRepair": False, "allowPassEvaluation": False}
    with tempfile.TemporaryDirectory(prefix="jpass-benchmark-") as folder:
        ledger = Ledger(Path(folder) / "ledger.sqlite3", {"rail": 100, "station": 16, "walk": 0, "rag": 0})
        raw["sessionId"] = ledger.create_session()["sessionId"]
        raw["operationId"] = "geometric-baseline"
        request = PlanRequest.model_validate(raw)
        planner = Planner(ledger, provider)
        started = time.perf_counter()
        proposed = await planner.plan(request, raw)
        candidate_ms = (time.perf_counter() - started) * 1000
        choices = {p.clientPlaceId: StationCandidate.model_validate(provider.data["stations"][p.clientPlaceId][0]) for p in request.places}
        reference = []
        for i, tail in enumerate(permutations(["p2", "p3", "p4"])):
            operation = f"reference-{i}"
            ledger.begin(raw["sessionId"], operation, operation, {"rail": rail_budget(4), "station": 0, "walk": 0, "rag": 0})
            context = Context(ledger, raw["sessionId"], operation, time.monotonic() + 300)
            route = await evaluate(request, ["p1", *tail], choices, planner.gateway, context)
            ledger.finish(raw["sessionId"], operation)
            reference.append({"order": route["order"], "elapsedMinutes": route["elapsedMinutes"],
                              "noPassTotal": route["fare"]["noPassTotal"], "railAttempts": ledger.snapshot(raw["sessionId"], operation)["railAttempts"]})
        reference.sort(key=lambda r: (r["elapsedMinutes"], r["noPassTotal"], r["order"]))
        selected = proposed["selected"]
        return {"dataSource": "fixture", "fixtureModification": "s1>s2 headway=70min", "nodeCount": 4,
                "referenceScope": "all-6-orders-with-fixed-start-and-primary-stations-using-earliest-arrival-evaluator",
                "globalTransitOrPassOptimum": False, "httpRequestsObserved": 0,
                "candidate": {"order": selected["order"], "elapsedMinutes": selected["elapsedMinutes"], "noPassTotal": selected["fare"]["noPassTotal"],
                              "railAttempts": proposed["ledger"]["railAttempts"], "stationAttempts": proposed["ledger"]["stationAttempts"], "evaluationMs": candidate_ms},
                "referenceBest": reference[0], "referenceOrders": reference,
                "gapMinutes": selected["elapsedMinutes"] - reference[0]["elapsedMinutes"],
                "note": "전수 참조 조회는 오프라인 비교용이며 실제 한 요청의 호출 예산 안에서 전수 최적화했다는 의미가 아님"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default="sample/low-call/benchmark-result.json")
    args = parser.parse_args()
    result = asyncio.run(benchmark())
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": args.report, "gapMinutes": result["gapMinutes"], "referenceOrderCount": len(result["referenceOrders"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
