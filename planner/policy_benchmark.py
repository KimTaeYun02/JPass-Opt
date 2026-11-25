"""Offline comparison of alternate-order policies and itemized pass quotes."""

import argparse
import asyncio
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from .fixture_provider import FixtureProvider
from .ledger import Ledger
from .models import Ride
from .order_alternates import feasible, schedule_score
from .order_benchmark import (
    CASES, FIXTURE, ROOT, add_gaps, exhaustive, merged_recordings, metrics,
    prepare_case, run_policy, summarize,
)
from .replay import ReplayProvider


POLICIES = ("diverse", "reverse_first", "deadline_first")
EXTRA_CASES = (("direction_tight", 3), ("reservation_holdout", 4), ("reservation_anchored", 5))


@contextmanager
def block_network():
    with ExitStack() as stack:
        for name in ("socket.socket.connect", "socket.socket.connect_ex",
                     "socket.socket.sendto", "socket.create_connection"):
            stack.enter_context(patch(name, side_effect=AssertionError("offline only")))
        yield


def holdout(name, n):
    if name == "direction_tight":
        raw, provider = prepare_case("directional", n)
        raw["endBy"] = "2026-11-17T11:40:00+09:00"
        for edge in ("s1>s2", "s3>s1"):
            provider.data["edges"][edge] = {"headwayMinutes": 10, "rideMinutes": 55, "fare": 900}
        return raw, provider
    raw, provider = prepare_case("scaling", n)
    raw["places"][0]["anchorRole"] = "start"
    raw["places"][3]["fixedVisitAt"] = "2026-11-17T10:05:00+09:00"
    if name == "reservation_anchored":
        raw["places"][-1]["anchorRole"] = "end"
        raw["startAt"] = "2026-11-17T09:05:00+09:00"
        provider.data["edges"]["s1>s4"] = {"headwayMinutes": 1, "rideMinutes": 12, "fare": 350}
    else:
        raw["places"][3].pop("fixedVisitAt")
        raw["places"][2]["fixedVisitAt"] = "2026-11-17T10:20:00+09:00"
        provider.data["edges"]["s1>s3"] = {"headwayMinutes": 5, "rideMinutes": 15, "fare": 300}
    return raw, provider


async def compare_policies(ledger, provider, raw):
    report = {}
    for policy in POLICIES:
        payload = deepcopy(raw)
        payload["options"]["orderAlternatePolicy"] = policy
        result = await run_policy(ledger, provider, payload, policy, alternate=True, prefix=True)
        row = summarize(result)
        audit = result["candidates"].get("orderEvaluation", {})
        row["feasibilityStatus"] = audit.get("feasibilityStatus")
        row["priorityVisitIds"] = audit.get("priorityVisitIds", [])
        report[policy] = row
    return report


async def compare_case(ledger, name, n, extra):
    raw, provider = holdout(name, n) if extra else prepare_case(name, n)
    routes = await exhaustive(ledger, provider, raw)
    valid = [route for route in routes.values() if feasible(route)]
    fastest = metrics(min(valid, key=schedule_score, default=None))
    cheapest = min((r["fare"]["noPassTotal"] for r in valid), default=None)
    policies = await compare_policies(ledger, provider, raw)
    for row in policies.values():
        add_gaps(row, fastest, cheapest)
        row["missedFeasibleOrder"] = bool(valid) and not row["feasible"]
    return {"case": name, "newCase": extra, "places": n, "referenceOrders": len(routes),
            "referenceFeasibleOrders": len(valid), "referenceFastest": fastest,
            "referenceLowestFare": cheapest, "policies": policies}


class PassTrialProvider(FixtureProvider):
    """Synthetic directed edges with an explicit line and base/supplement allocation."""

    covered_edges = {"s2>s1", "s1>s3"}
    covered_line = "시험 패스선"

    def __init__(self, *, total_only=False):
        super().__init__(FIXTURE)
        self.total_only = total_only
        for key in self.covered_edges:
            self.data["edges"][key] = {"headwayMinutes": 10, "rideMinutes": 25,
                                       "fare": 200, "supplement": 20}
        for candidates in self.data["stations"].values():
            del candidates[1:]
            for side in ("access", "egress"):
                candidates[0][side]["status"] = "routed"

    async def rail(self, query):
        edge = f"{query.origin.providerStationId}>{query.destination.providerStationId}"
        result = []
        for ride in await super().rail(query):
            raw = ride.model_dump(mode="json")
            if edge in self.covered_edges:
                raw["segments"][0]["line"] = self.covered_line
            if self.total_only:
                for unit in raw["fareUnits"]:
                    unit.update(quotedTotal=unit["baseFare"] + unit["supplement"], baseFare=None, supplement=None)
            result.append(Ride.model_validate(raw))
        return result

    async def passes(self, routes):
        rows = await super().passes(routes)
        by_digest = {route["digest"]: route for route in routes}
        result = []
        for row in rows:
            route = by_digest[row.routeDigest]
            lines = {segment["segmentId"]: segment["line"] for leg in route["legs"] for segment in leg["segments"]}
            amounts = []
            for unit in route["fare"]["fareUnits"]:
                covered = all(lines[sid] == self.covered_line for sid in unit["rideSegmentIds"])
                # The total-only trial deliberately supplies provider claims without itemized support.
                amounts.append({"fareUnitId": unit["fareUnitId"], "uncoveredFare": 0 if covered else 220,
                                "requiredSupplement": 20 if covered else 0})
            yield_row = row.model_dump(mode="json")
            yield_row.update(passId="synthetic-line-pass", purchasePrice=150, allocations=amounts,
                             reportedTotal=150 + sum(a["uncoveredFare"] + a["requiredSupplement"] for a in amounts))
            yield_row["evidence"]["source"] = "synthetic-line-coverage"
            yield_row["evidence"]["version"] = "trial-1"
            yield_row["documentVersion"] = "trial-1"
            result.append(yield_row)
        return result


async def pass_trial(ledger, *, total_only=False, alternate=True):
    raw, _ = prepare_case("scaling", 3)
    raw["options"]["allowPassEvaluation"] = True
    provider = PassTrialProvider(total_only=total_only)
    result = await run_policy(ledger, provider, raw, "pass-trial", alternate=alternate)
    result["echoInput"]["sessionId"] = "offline-pass-trial"
    return result


async def replay_trial(ledger, output):
    run = ROOT/"sample/low-call/divergent-revalidation/run-20261119"
    raw = json.loads((run/"scenario.json").read_text(encoding="utf-8"))["request"]
    paths = [run/"analysis"/f"{name}-recording.json" for name in ("v2", "legacy-validation")]
    merged_recordings(paths, output)
    provider = ReplayProvider(output)
    policies = await compare_policies(ledger, provider, raw)
    raw["options"]["allowPassEvaluation"] = True
    priced = await run_policy(ledger, provider, raw, "real-pass-evidence", alternate=True)
    return {"policies": policies, "passComparison": priced["passComparison"],
            "ragRequests": priced["ledger"]["ragRequests"],
            "pricingHttpRequestsObserved": priced["ledger"]["httpRequestsObserved"]}


async def benchmark():
    with tempfile.TemporaryDirectory() as workspace, block_network():
        folder = Path(workspace)
        ledger = Ledger(folder/"ledger.sqlite3", {"rail": 1000, "station": 16, "walk": 0, "rag": 12})
        cases = [await compare_case(ledger, name, n, False) for name, n in CASES]
        cases.extend([await compare_case(ledger, name, n, True) for name, n in EXTRA_CASES])
        replay = await replay_trial(ledger, folder/"recording.json")
        passes = {"oneOrder": await pass_trial(ledger, alternate=False),
                  "twoOrders": await pass_trial(ledger),
                  "totalOnly": await pass_trial(ledger, total_only=True)}
        policy_attempts = [row for case in cases for row in case["policies"].values()]
        policy_attempts.extend(replay["policies"].values())
        http_events = (sum(row["httpRequestsObserved"] for row in policy_attempts)
                       + replay["pricingHttpRequestsObserved"]
                       + sum(result["ledger"]["httpRequestsObserved"] for result in passes.values()))
        return {"schemaVersion": "alternate-policy-study/1", "httpRequestsObserved": http_events,
                "synthetic": cases, "replay": replay, "passTrials": passes}


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
