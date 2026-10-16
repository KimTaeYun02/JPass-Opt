import copy
import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from route_optimizer import (
    MAX_SAFE_INTEGER,
    optimize_matrix,
    optimize_visit_order,
    optimize_visit_orders,
    validate_quote,
)


def make_quotes(ids, cost):
    return [
        {
            "from": start,
            "to": end,
            "result": {
                "time": (values := cost(start, end))[0],
                "fee": values[1],
                "route": [
                    {"station": start, "line": "test"},
                    {"station": end, "line": None},
                ],
            },
        }
        for start in ids
        for end in ids
        if start != end
    ]


def segments_of(quotes):
    return [
        {
            "departure": q["from"],
            "arrival": q["to"],
            "routes": [copy.deepcopy(q["result"])],
        }
        for q in quotes
    ]


def score(plan):
    return plan["order"], plan["time"], plan["fee"]


def brute_top(ids, quotes, limit):
    lookup = {(quote["from"], quote["to"]): quote["result"] for quote in quotes}
    candidates = []
    for order in itertools.permutations(ids):
        legs = [lookup[start, end] for start, end in zip(order, order[1:])]
        candidates.append(
            (
                list(order),
                sum(leg["time"] for leg in legs),
                sum(leg["fee"] for leg in legs),
            )
        )
    candidates.sort(
        key=lambda plan: (
            plan[1],
            plan[2],
            tuple(name.encode("utf-16-be", errors="surrogatepass") for name in plan[0]),
        )
    )
    return candidates[:limit]


class CoreTests(unittest.TestCase):
    def test_seeded_top_three_matches_independent_permutations(self):
        rng = random.Random(41231)
        for sample in range(100):
            ids = [f"place-{i}" for i in range(2 + sample % 5)]
            scale = (1, 3, 500)[sample % 3]
            quotes = make_quotes(
                ids, lambda _a, _b: (rng.randrange(scale), rng.randrange(scale))
            )
            for limit in (1, 2, 3):
                with self.subTest(sample=sample, limit=limit):
                    plans = optimize_visit_orders(ids, quotes, limit)
                    self.assertEqual(
                        [score(plan) for plan in plans], brute_top(ids, quotes, limit)
                    )
                    for plan in plans:
                        n = len(ids)
                        self.assertEqual(plan["algorithm"], "held-karp")
                        self.assertEqual(plan["evaluatedOrders"], 0)
                        self.assertEqual(plan["dpStates"], n * 2 ** (n - 1))
                        self.assertEqual(
                            plan["dpTransitions"], n * (n - 1) * 2 ** (n - 2)
                        )

    def test_unicode_and_prefix_ties_use_javascript_order(self):
        ids = ["가", "a", "a-a", "B", "\ue000", "\U00010000"]
        quotes = make_quotes(ids, lambda _a, _b: (0, 0))
        expected = brute_top(ids, quotes, 3)
        self.assertEqual(
            [score(plan) for plan in optimize_visit_orders(ids, quotes)], expected
        )
        self.assertEqual(
            [score(plan) for plan in optimize_visit_orders(ids[::-1], quotes)], expected
        )
        self.assertLess(
            expected[0][0].index("\U00010000"), expected[0][0].index("\ue000")
        )

    def test_bounds_two_nodes_and_eight_nodes(self):
        for ids in (["A"], [str(i) for i in range(9)], ["A", "A"], ["A", ""]):
            with self.assertRaises(ValueError):
                optimize_visit_order(ids, [])
        quotes = make_quotes(["A", "B"], lambda _a, _b: (1, 2))
        self.assertEqual(len(optimize_visit_orders(["A", "B"], quotes)), 2)
        for limit in (0, 4, 1.5, True, float("nan")):
            with self.assertRaises(ValueError):
                optimize_visit_orders(["A", "B"], quotes, limit)
        ids = list("ABCDEFGH")
        eight = make_quotes(ids, lambda a, b: (0 if ord(b) == ord(a) + 1 else 9, 0))
        plan = optimize_visit_order(ids, eight)
        self.assertEqual(plan["order"], ids)
        self.assertEqual([plan["dpStates"], plan["dpTransitions"]], [1024, 3584])

    def test_invalid_costs_routes_and_metadata(self):
        valid = make_quotes(["A", "B"], lambda _a, _b: (1, 2))[0]
        for key in ("time", "fee"):
            for value in (None, -1, 1.5, True, "2", MAX_SAFE_INTEGER + 1):
                quote = copy.deepcopy(valid)
                quote["result"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    validate_quote(quote)
        invalid = []
        for mutation in (
            lambda q: q["result"]["route"][0].update(station="wrong"),
            lambda q: q["result"]["route"][0].update(line=None),
            lambda q: q["result"]["route"][-1].update(line="test"),
            lambda q: q.update(source="unknown"),
            lambda q: q.update(stations=None),
            lambda q: q["result"].update(route=[None, None]),
        ):
            quote = copy.deepcopy(valid)
            mutation(quote)
            invalid.append(quote)
        for quote in invalid:
            with self.assertRaises(ValueError):
                validate_quote(quote)

    def test_missing_reverse_and_duplicates_are_errors(self):
        quotes = make_quotes(["A", "B"], lambda _a, _b: (1, 2))
        with self.assertRaisesRegex(ValueError, "B → A"):
            optimize_visit_order(["A", "B"], quotes[:1])
        with self.assertRaisesRegex(ValueError, "중복"):
            optimize_visit_order(["A", "B"], quotes + quotes[:1])

    def test_overflow_in_discarded_prefix_and_exact_boundary(self):
        ids = list("ABCD")
        for dimension in (0, 1):

            def cost(start, end):
                values = [0, 0]
                if (start, end) == ("A", "B"):
                    values[dimension] = MAX_SAFE_INTEGER
                if (start, end) == ("B", "C"):
                    values[dimension] = 1
                return values

            with self.assertRaisesRegex(ValueError, "안전한 정수"):
                optimize_visit_order(ids, make_quotes(ids, cost))
        boundary = make_quotes(
            ["A", "B"], lambda _a, _b: (MAX_SAFE_INTEGER, MAX_SAFE_INTEGER)
        )
        self.assertEqual(
            optimize_visit_order(["A", "B"], boundary)["time"], MAX_SAFE_INTEGER
        )
        isolated = make_quotes(
            list("ABC"),
            lambda a, b: (MAX_SAFE_INTEGER if (a, b) == ("A", "B") else 0, 0),
        )
        self.assertEqual(
            score(optimize_visit_order(list("ABC"), isolated)),
            brute_top(list("ABC"), isolated, 1)[0],
        )

    def test_no_mutation_and_shared_station_place_ids(self):
        ids = list("ABC")
        quotes = make_quotes(ids, lambda _a, _b: (1, 2))
        for quote in quotes:
            quote["stations"] = {"from": "shared", "to": "shared"}
            quote["source"] = "snapshot"
            quote["result"]["route"] = [
                {"station": "shared", "line": "徒歩"},
                {"station": "shared", "line": None},
            ]
        before = copy.deepcopy(quotes)
        plans = optimize_visit_orders(ids, quotes)
        plans[0]["legs"][0]["result"]["route"][0]["line"] = "changed"
        self.assertEqual(quotes, before)
        self.assertTrue(
            all(leg["result"]["route"][0]["line"] == "徒歩" for leg in plans[1]["legs"])
        )


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.stations = list("ABC")
        self.matrix = segments_of(make_quotes(self.stations, lambda _a, _b: (10, 20)))

    def test_selection_reports_index_and_preserves_crawler_values(self):
        alternative = copy.deepcopy(self.matrix[0]["routes"][0])
        alternative.update(time=1, fee=30)
        self.matrix[0]["routes"].append(alternative)
        before = copy.deepcopy(self.matrix)
        first = optimize_matrix(self.stations, self.matrix)
        fastest = optimize_matrix(self.stations, self.matrix, selection="fastest")
        self.assertEqual(first["plans"][0]["time"], 20)
        self.assertEqual(fastest["plans"][0]["time"], 11)
        self.assertEqual(fastest["plans"][0]["legs"][0]["routeIndex"], 1)
        self.assertEqual(self.matrix, before)

    def test_null_first_rejected_and_fastest_skips_with_report(self):
        alternative = copy.deepcopy(self.matrix[0]["routes"][0])
        self.matrix[0]["routes"][0]["fee"] = None
        self.matrix[0]["routes"].append(alternative)
        with self.assertRaises(ValueError):
            optimize_matrix(self.stations, self.matrix)
        report = optimize_matrix(self.stations, self.matrix, selection="fastest")
        self.assertEqual(len(report["skippedRoutes"]), 1)
        self.assertEqual(report["skippedRoutes"][0]["routeIndex"], 0)
        self.matrix[0]["routes"].pop()
        with self.assertRaises(ValueError):
            optimize_matrix(self.stations, self.matrix, selection="fastest")

    def test_malformed_empty_unknown_and_duplicate_inputs(self):
        for matrix in (
            None,
            [None],
            [{}],
            [{"departure": "A", "arrival": "B", "routes": []}],
            self.matrix[:-1],
            self.matrix + self.matrix[:1],
        ):
            with self.assertRaises(ValueError):
                optimize_matrix(self.stations, matrix)
        with self.assertRaises(ValueError):
            optimize_matrix(["A", "B"], self.matrix)
        with self.assertRaises(ValueError):
            optimize_matrix(self.stations, self.matrix, selection="cheap")

    def test_cli_success_stdin_and_failure(self):
        command = [
            sys.executable,
            "-m",
            "route_optimizer",
            "-",
            "--stations",
            *self.stations,
        ]
        result = subprocess.run(
            command,
            input=json.dumps(self.matrix),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["plans"][0]["time"], 20)
        invalid = subprocess.run(
            command, input="[]", capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(invalid.returncode, 2)
        self.assertEqual(invalid.stdout, "")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "matrix.json")
            destination = Path(directory, "nested", "plan.json")
            source.write_text(json.dumps(self.matrix), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "route_optimizer",
                    str(source),
                    "--stations",
                    *self.stations,
                    "--output",
                    str(destination),
                ],
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(destination.read_text(encoding="utf-8"))["selection"],
                "first",
            )

    @unittest.skipUnless(
        os.getenv("JPASS_ROUTE_MATRIX"), "JPASS_ROUTE_MATRIX is not set"
    )
    def test_real_matrix_matches_previous_result_and_oracle(self):
        matrix = json.loads(
            Path(os.environ["JPASS_ROUTE_MATRIX"]).read_text(encoding="utf-8")
        )
        stations = ["東大前", "テレコムセンター", "後楽園", "片瀬江ノ島", "参宮橋"]
        plans = optimize_matrix(stations, matrix, limit=3)["plans"]
        quotes = [
            {"from": row["departure"], "to": row["arrival"], "result": row["routes"][0]}
            for row in matrix
        ]
        self.assertEqual(
            [score(plan) for plan in plans], brute_top(stations, quotes, 3)
        )
        self.assertEqual(
            score(plans[0]),
            (
                ["テレコムセンター", "東大前", "後楽園", "参宮橋", "片瀬江ノ島"],
                143,
                2374,
            ),
        )


if __name__ == "__main__":
    unittest.main()
