"""Select a historical different-path case offline; never start a live search."""

import argparse
from copy import deepcopy
import hashlib
from itertools import combinations
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import DTO, PlanRequest, Text
from .ordering import local_candidates
from .replay import Binding
from .sequential_benchmark import Scenario, fixed_time_order


def utf16(value: str) -> bytes:
    return value.encode("utf-16-be")


class Pool(DTO):
    schemaVersion: Literal["divergence-pool/1"]
    request: PlanRequest
    bindings: list[Binding] = Field(min_length=5, max_length=5)
    eligibleIds: list[Text] = Field(min_length=3, max_length=5)
    assumptions: list[Text] = Field(min_length=1, max_length=14)
    sources: list[Text] = Field(max_length=14)

    @model_validator(mode="after")
    def comparable(self):
        ids = {place.clientPlaceId for place in self.request.places}
        bound = [binding.place.clientPlaceId for binding in self.bindings]
        if len(ids) != 5 or len(set(bound)) != 5 or set(bound) != ids:
            raise ValueError("five_unique_place_bindings_required")
        if len(set(self.eligibleIds)) != len(self.eligibleIds) or not set(self.eligibleIds) <= ids:
            raise ValueError("eligible_ids_must_be_a_unique_subset")
        # Reuse the same scope and identity checks as the actual acquisition.
        for subset in combinations(sorted(ids, key=utf16), 3):
            scenario_for(self, subset)
        return self


def scenario_for(pool: Pool, ids) -> Scenario:
    request = pool.request.model_dump(mode="json")
    request["places"] = [place for place in request["places"] if place["clientPlaceId"] in ids]
    return Scenario.model_validate({
        "schemaVersion":"sequential-scenario/1", "request":request,
        "bindings":[binding.model_dump(mode="json") for binding in pool.bindings
                    if binding.place.clientPlaceId in ids],
        "assumptions":list(pool.assumptions), "sources":list(pool.sources),
    })


def provenance(path: Path) -> dict:
    raw = path.read_bytes()
    return {"path":path.as_posix(), "sha256":hashlib.sha256(raw).hexdigest(), "bytes":len(raw)}


def path_difference(a: list[str], b: list[str]) -> str:
    if len(a) < 2 or len(set(a)) != len(a) or len(b) != len(a) or set(a) != set(b):
        return "unknown"
    if a == b:
        return "same"
    edges = lambda order:{frozenset((x, y)) for x, y in zip(order, order[1:])}
    if edges(a) != edges(b):
        return "different"
    return "reversal" if a == list(reversed(b)) else "unknown"


def historical(matrix: list[dict], scenario: Scenario) -> dict:
    names = {binding.candidates[0].station.name:binding.place.clientPlaceId for binding in scenario.bindings}
    ids = sorted(names.values(), key=utf16)
    edges = [{"from":names[row["departure"]], "to":names[row["arrival"]],
              "payload":{"routes":row["routes"]}}
             for row in matrix if row["departure"] in names and row["arrival"] in names]
    present = {(edge["from"], edge["to"]) for edge in edges}
    missing = [{"from":a, "to":b} for a in ids for b in ids if a != b and (a, b) not in present]
    try:
        result = fixed_time_order(ids, edges)
    except ValueError as error:
        return {"status":"incomplete", "missingEdges":missing, "error":str(error)[:256]}
    return {**result, "status":"complete", "missingEdges":[], "queryBasis":"unknown-date",
            "evidence":"historical-static-not-fixed-0900"}


def select(rows: list[dict]) -> dict | None:
    eligible = [row for row in rows if row["eligible"] and row["historical"]["status"] == "complete"
                and row["pathDifference"] == "different"]
    return min(eligible, key=lambda row:tuple(utf16(pid) for pid in row["ids"]), default=None)


def screen(pool: Pool, matrix: list[dict], evidence: dict) -> dict:
    rows = []
    for ids in combinations(sorted((place.clientPlaceId for place in pool.request.places), key=utf16), 3):
        scenario = scenario_for(pool, ids)
        # This call has no rail matrix input. The matrix only selects a test case.
        geometric = local_candidates(scenario.request)
        tied = [candidate["order"] for candidate in geometric["orders"]
                if candidate["geometricScore"] == geometric["orders"][0]["geometricScore"]]
        direction = {"scoreTiedOrders":tied, "tieBreak":"clientPlaceId-sequence-python-string-order",
                     "tieBreakUsed":len(tied) > 1, "transitDirectionVerified":False}
        old = historical(matrix, scenario)
        difference = path_difference(geometric["orders"][0]["order"], old["order"]) if old["status"] == "complete" else "unknown"
        rows.append({"ids":list(ids), "eligible":set(ids) <= set(pool.eligibleIds),
                     "geometric":geometric, "directionSelection":direction,
                     "historical":old, "pathDifference":difference})
    chosen = select(rows)
    selected = None
    if chosen is not None:
        scenario = scenario_for(pool, chosen["ids"]).model_dump(mode="json")
        scenario["sources"] += [f"{label} sha256:{entry['sha256']}" for label, entry in evidence.items()]
        selected = {"ids":chosen["ids"], "v2Order":chosen["geometric"]["orders"][0]["order"],
                    "directionSelection":chosen["directionSelection"],
                    "historicalOrder":chosen["historical"]["order"],
                    "scenario":Scenario.model_validate(scenario).model_dump(mode="json")}
    return {"schemaVersion":"divergence-screening/1", "sourceEvidence":deepcopy(evidence),
            "selectionRule":"first-UTF16-ids-eligible-complete-different-undirected-path",
            "eligibleIds":sorted(pool.eligibleIds, key=utf16), "rows":rows, "selected":selected,
            "routeSearchAttempts":0, "httpRequestsObserved":0,
            "notice":"과거 정적 응답으로 목적 선택한 시험 사례. 현행 시각표·평균 개선율·최적성의 근거가 아님."}


def write_screening(pool_path: Path, matrix_path: Path, output: Path) -> dict:
    pool = Pool.model_validate_json(pool_path.read_text(encoding="utf-8"))
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    report = screen(pool, matrix, {"pool":provenance(pool_path), "matrix":provenance(matrix_path)})
    output.mkdir(parents=True, exist_ok=False)
    files = {"screening.json":report}
    if report["selected"] is not None:
        files["scenario.json"] = report["selected"]["scenario"]
    for name, value in files.items():
        with (output/name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2)+"\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = write_screening(args.pool, args.matrix, args.output)
    print(json.dumps({"mode":"offline-screening", "cases":len(report["rows"]),
                      "selectedIds":None if report["selected"] is None else report["selected"]["ids"],
                      "routeSearchAttempts":0}, ensure_ascii=False))
    return 0 if report["selected"] is not None else 2


if __name__ == "__main__":
    raise SystemExit(main())
