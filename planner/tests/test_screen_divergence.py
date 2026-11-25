"""Offline selection uses historical data without claiming dated journeys."""

from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from planner.screen_divergence import Pool, path_difference, screen, select, write_screening
from planner.collect_sequential import analyze
from planner.sequential_benchmark import fastest_course
from planner.models import PlanRequest
from planner.ordering import local_candidates


ROOT = Path(__file__).resolve().parents[2]
POOL = ROOT/"sample/low-call/divergent/pool.json"
MATRIX = ROOT/"sample/team-crawler-route-matrix.json"


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.pool = Pool.model_validate_json(POOL.read_text(encoding="utf-8"))
        self.matrix = json.loads(MATRIX.read_text(encoding="utf-8"))

    def test_all_ten_cases_and_deterministic_non_reversal_selection(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            first = screen(self.pool, self.matrix, {})
            second = screen(self.pool, self.matrix, {})
        self.assertEqual(json.dumps(first, ensure_ascii=False), json.dumps(second, ensure_ascii=False))
        self.assertEqual(len(first["rows"]), 10)
        self.assertEqual(len({tuple(row["ids"]) for row in first["rows"]}), 10)
        self.assertTrue(all(row["historical"]["status"] == "complete" for row in first["rows"]))
        self.assertTrue(all(row["historical"]["queryBasis"] == "unknown-date" for row in first["rows"]))
        chosen = first["selected"]
        self.assertEqual(chosen["ids"], ["meiji", "nezu", "odaiba"])
        self.assertEqual(chosen["v2Order"], ["nezu", "meiji", "odaiba"])
        self.assertEqual(chosen["historicalOrder"], ["meiji", "nezu", "odaiba"])
        self.assertEqual(first["routeSearchAttempts"], 0)
        self.assertEqual(first["httpRequestsObserved"], 0)

    def test_geometric_ranking_does_not_use_historical_costs(self):
        before = screen(self.pool, self.matrix, {})
        modified = deepcopy(self.matrix)
        for row in modified:
            for course in row["routes"]:
                course["time"] = 144
                course["fee"] = 999
        after = screen(self.pool, modified, {})
        self.assertEqual([row["geometric"] for row in before["rows"]],
                         [row["geometric"] for row in after["rows"]])

    def test_direction_tie_is_explicit_and_ids_can_reverse_it(self):
        chosen = screen(self.pool,self.matrix,{})['selected']
        policy = chosen['directionSelection']
        self.assertTrue(policy['tieBreakUsed'])
        self.assertFalse(policy['transitDirectionVerified'])
        self.assertEqual(policy['scoreTiedOrders'],[chosen['v2Order'],list(reversed(chosen['v2Order']))])
        self.assertEqual(policy['tieBreak'],'clientPlaceId-sequence-python-string-order')
        request = deepcopy(chosen['scenario']['request'])
        next(place for place in request['places'] if place['clientPlaceId']=='nezu')['clientPlaceId']='z-nezu'
        order = local_candidates(PlanRequest.model_validate(request))['orders'][0]['order']
        self.assertEqual(order,['odaiba','meiji','z-nezu'])

    def test_historical_selection_matches_original_optimizer_without_dated_claims(self):
        report = screen(self.pool, self.matrix, {})
        chosen = report['selected']
        names = {binding.place.clientPlaceId:binding.candidates[0].station.name for binding in self.pool.bindings}
        inverted = {name:pid for pid,name in names.items() if pid in chosen['ids']}
        edges = []
        for row in self.matrix:
            if row['departure'] in inverted and row['arrival'] in inverted:
                _, result = fastest_course({'routes':row['routes']})
                edges.append({'from':inverted[row['departure']], 'to':inverted[row['arrival']],
                              'result':result, 'source':'snapshot',
                              'stations':{'from':row['departure'], 'to':row['arrival']}})
        sys.path.insert(0,str(ROOT/'backend'))
        optimizer = importlib.import_module('app.optimizer')
        original = optimizer.optimize_visit_orders(chosen['ids'],edges, limit=1)[0]
        self.assertEqual(original['order'],chosen['historicalOrder'])
        self.assertEqual((original['time'],original['fee']),(78,1047))

    def test_same_reversal_different_and_invalid_paths(self):
        self.assertEqual(path_difference(["a", "b", "c"], ["a", "b", "c"]), "same")
        self.assertEqual(path_difference(["a", "b", "c"], ["c", "b", "a"]), "reversal")
        self.assertEqual(path_difference(["a", "b", "c"], ["b", "a", "c"]), "different")
        self.assertEqual(path_difference(["a", "a", "c"], ["a", "b", "c"]), "unknown")
        self.assertEqual(path_difference(["a", "b", "c"], ["b", "a", "d"]), "unknown")

    def test_missing_and_duplicate_edges_are_incomplete_not_zero(self):
        matrix = [row for row in self.matrix if (row["departure"], row["arrival"]) != ("東大前", "参宮橋")]
        missing = screen(self.pool, matrix, {})
        case = next(row for row in missing["rows"] if row["ids"] == ["meiji", "nezu", "odaiba"])
        self.assertEqual(case["historical"]["status"], "incomplete")
        self.assertEqual(case["historical"]["missingEdges"], [{"from":"nezu", "to":"meiji"}])
        self.assertEqual(case["pathDifference"], "unknown")
        self.assertNotEqual(missing["selected"]["ids"], case["ids"])
        duplicate = screen(self.pool, self.matrix+[self.matrix[0]], {})
        affected = next(row for row in duplicate["rows"] if row["ids"] == ["meiji", "nezu", "odaiba"])
        self.assertEqual(affected["historical"]["status"], "incomplete")
        self.assertIn("duplicate", affected["historical"]["error"])

    def test_no_reversal_or_ineligible_fallback(self):
        rows = [{"ids":["a", "b", "c"], "eligible":True, "historical":{"status":"complete"},
                 "pathDifference":"reversal"},
                {"ids":["a", "b", "d"], "eligible":False, "historical":{"status":"complete"},
                 "pathDifference":"different"}]
        self.assertIsNone(select(rows))
        modified = self.pool.model_dump(mode="json")
        modified["eligibleIds"] = ["meiji", "nezu", "tokyo-dome"]
        report = screen(Pool.model_validate(modified), self.matrix, {})
        self.assertIsNone(report["selected"])
        self.assertEqual(sum(row["eligible"] for row in report["rows"]), 1)

    def test_duplicate_binding_and_unverified_scope_are_rejected(self):
        for mutate in (lambda value:value["bindings"].__setitem__(0, value["bindings"][1]),
                       lambda value:value["bindings"][0]["candidates"][0]["access"].update(status="routed"),
                       lambda value:value["eligibleIds"].append("missing"),
                       lambda value:value["request"]["options"].update(allowCache=True)):
            raw = self.pool.model_dump(mode="json")
            mutate(raw)
            with self.assertRaises(ValueError):
                Pool.model_validate(raw)

    def test_cli_has_no_network_or_crawler_import_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)/"screening"
            script = """import sys
from unittest.mock import patch
from planner.screen_divergence import main
with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
    result = main(sys.argv[1:])
assert 'planner.capture' not in sys.modules
assert 'app.crawling.ekispert' not in sys.modules
assert not any(name.startswith('playwright') for name in sys.modules)
raise SystemExit(result)
"""
            proc = subprocess.run([sys.executable, "-c", script, "--pool", str(POOL), "--matrix", str(MATRIX),
                                   "--output", str(output)], cwd=ROOT, capture_output=True, text=True,
                                  encoding="utf-8", timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads((output/"screening.json").read_text(encoding="utf-8"))
            self.assertEqual(report["sourceEvidence"]["pool"]["bytes"], len(POOL.read_bytes()))
            before = (output/"screening.json").read_bytes()
            with self.assertRaises(FileExistsError):
                write_screening(POOL, MATRIX, output)
            self.assertEqual((output/"screening.json").read_bytes(), before)


class SavedDivergenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_partial_run_has_no_invented_schedule_or_improvement(self):
        run = ROOT/'sample/low-call/divergent/run-20261119'
        report = json.loads((run/'acquisition.json').read_text(encoding='utf-8'))
        selected_bytes = (ROOT/'sample/low-call/divergent/screened/scenario.json').read_bytes()
        self.assertEqual(selected_bytes,(run/'scenario.json').read_bytes())
        selected = json.loads((ROOT/'sample/low-call/divergent/screened/screening.json').read_text(encoding='utf-8'))['selected']
        self.assertEqual(json.loads(selected_bytes),selected['scenario'])
        self.assertEqual(selected['v2Order'],report['v2Order'])
        self.assertEqual(report['status'],'partial')
        self.assertEqual(report['searchReservations'],2)
        self.assertEqual(report['routeSearchAttempts'],2)
        self.assertEqual(report['httpRequestsObserved'],1030)
        self.assertIsNone(report['legacyFixed'])
        self.assertEqual(report['sequences'],{})
        self.assertEqual([entry['status'] for entry in report['captures']],['captured','failed'])
        for entry in report['captures']:
            self.assertEqual(hashlib.sha256((run/entry['file']).read_bytes()).hexdigest(),entry['sha256'])
        with tempfile.TemporaryDirectory() as folder:
            with patch('socket.socket.connect',side_effect=AssertionError('offline attempted network')):
                result = await analyze(run,Path(folder)/'analysis')
            self.assertEqual(result['status'],'partial')
            self.assertEqual(result['timingOutcome'],'unknown')
            self.assertIsNone(result['v2ElapsedMinutesSaved'])
            self.assertIsNone(result['v2GrossFareSaved'])
            for name in ('legacyOrderAtActualTimes','v2AtActualTimes'):
                for field in ('elapsedMinutes','grossFare','finishAt'):
                    self.assertIsNone(result[name][field])
            self.assertEqual(result['searchesByProfile'],{'snapshot':2,'v2':0,'legacy-validation':0})
            self.assertEqual(result['replayHttpRequests'],0)
            self.assertFalse((Path(folder)/'analysis/legacy-validation-result.json').exists())


if __name__ == "__main__":
    unittest.main()
