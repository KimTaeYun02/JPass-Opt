"""Exact, network-free Held-Karp routing for fixed directed station costs."""

from bisect import insort
from copy import deepcopy
from dataclasses import dataclass, field

MAX_ROUTE_STOPS = 8
MAX_SAFE_INTEGER = 2**53 - 1


def validate_places(places):
    if not isinstance(places, (list, tuple)) or not 2 <= len(places) <= MAX_ROUTE_STOPS:
        raise ValueError(f"방문 역은 2~{MAX_ROUTE_STOPS}개여야 합니다.")
    if any(not isinstance(place, str) or not place.strip() for place in places):
        raise ValueError("방문 역은 비어 있지 않은 문자열이어야 합니다.")
    if len(set(places)) != len(places):
        raise ValueError(
            "방문 역은 고유해야 합니다. 같은 역의 여러 방문지는 별도 ID가 필요합니다."
        )


def _safe_cost(value):
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def validate_quote(quote):
    if not isinstance(quote, dict):
        raise ValueError("이동 구간은 객체여야 합니다.")
    start, end = quote.get("from"), quote.get("to")
    if (
        any(not isinstance(value, str) or not value.strip() for value in (start, end))
        or start == end
    ):
        raise ValueError("이동 구간의 출발·도착 ID가 올바르지 않습니다.")
    label = f"{start} → {end}"
    result = quote.get("result")
    if not isinstance(result, dict) or not all(
        _safe_cost(result.get(key)) for key in ("time", "fee")
    ):
        raise ValueError(f"{label}: time과 fee는 0 이상의 안전한 정수여야 합니다.")
    stations = quote.get("stations", {"from": start, "to": end})
    if not isinstance(stations, dict) or any(
        not isinstance(stations.get(key), str) or not stations[key].strip()
        for key in ("from", "to")
    ):
        raise ValueError(f"{label}: 역 이름이 올바르지 않습니다.")
    if "source" in quote and quote["source"] not in ("snapshot", "estimate"):
        raise ValueError(f"{label}: 데이터 출처가 올바르지 않습니다.")
    stops = result.get("route")
    if not isinstance(stops, list) or len(stops) < 2:
        raise ValueError(f"{label}: route에는 출발역과 도착역이 필요합니다.")
    for index, stop in enumerate(stops):
        if (
            not isinstance(stop, dict)
            or not isinstance(stop.get("station"), str)
            or not stop["station"].strip()
        ):
            raise ValueError(f"{label}: route의 역 이름이 올바르지 않습니다.")
        if "line" not in stop or (
            stop["line"] is not None
            if index == len(stops) - 1
            else not isinstance(stop["line"], str) or not stop["line"].strip()
        ):
            raise ValueError(
                f"{label}: 노선은 출발역에 기록하고 최종 역만 null이어야 합니다."
            )
    if (
        stops[0]["station"] != stations["from"]
        or stops[-1]["station"] != stations["to"]
    ):
        raise ValueError(f"{label}: route의 양 끝 역이 조회 구간과 일치해야 합니다.")


@dataclass(order=True, frozen=True)
class _Label:
    time: int
    fee: int
    key: str
    end: int = field(compare=False)
    previous: "_Label | None" = field(default=None, compare=False)


@dataclass
class _State:
    labels: list = field(default_factory=list)
    max_time: int = 0
    max_fee: int = 0


def _keep(labels, candidate, limit):
    insort(labels, candidate)
    del labels[limit:]


def _held_karp(matrix, limit):
    n = len(matrix)
    size = 1 << n
    table = {}
    transitions = 0
    for mask in range(1, size):
        for end in range(n):
            if not mask & (1 << end):
                continue
            previous_mask = mask ^ (1 << end)
            state = _State()
            key_part = chr(65 + end)
            if not previous_mask:
                state.labels.append(_Label(0, 0, key_part, end))
            else:
                for previous in range(n):
                    if not previous_mask & (1 << previous):
                        continue
                    prefix = table[previous_mask, previous]
                    edge = matrix[previous][end]
                    max_time = prefix.max_time + edge["time"]
                    max_fee = prefix.max_fee + edge["fee"]
                    # Track discarded prefixes too, preserving the TypeScript guard.
                    if max_time > MAX_SAFE_INTEGER or max_fee > MAX_SAFE_INTEGER:
                        raise ValueError("합계가 안전한 정수 범위를 초과했습니다.")
                    state.max_time = max(state.max_time, max_time)
                    state.max_fee = max(state.max_fee, max_fee)
                    transitions += 1
                    for label in prefix.labels:
                        _keep(
                            state.labels,
                            _Label(
                                label.time + edge["time"],
                                label.fee + edge["fee"],
                                label.key + key_part,
                                end,
                                label,
                            ),
                            limit,
                        )
            table[mask, end] = state
    winners = []
    for end in range(n):
        for label in table[size - 1, end].labels:
            _keep(winners, label, limit)
    paths = []
    for label in winners:
        indices = []
        cursor = label
        while cursor is not None:
            indices.append(cursor.end)
            cursor = cursor.previous
        paths.append({"time": label.time, "fee": label.fee, "indices": indices[::-1]})
    return paths, {
        "algorithm": "held-karp",
        "dpStates": len(table),
        "dpTransitions": transitions,
        "evaluatedOrders": 0,
    }


def optimize_visit_orders(places, quotes, limit=3):
    """Return top 1..3 open tours; minimize time, fare, then JS UTF-16 ID order."""
    validate_places(places)
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError("후보 수는 1~3의 정수여야 합니다.")
    if not isinstance(quotes, (list, tuple)):
        raise ValueError("구간 목록은 배열이어야 합니다.")
    matrix = {}
    for quote in quotes:
        validate_quote(quote)
        key = quote["from"], quote["to"]
        if key in matrix:
            raise ValueError(f"{key[0]} → {key[1]}: 중복된 이동 결과입니다.")
        matrix[key] = quote
    for start in places:
        for end in places:
            if start != end and (start, end) not in matrix:
                raise ValueError(f"{start} → {end}: 이동 결과가 누락되었습니다.")
    # Big-endian UTF-16 bytes preserve JavaScript code-unit comparison.
    ordered = sorted(
        places, key=lambda name: name.encode("utf-16-be", errors="surrogatepass")
    )
    costs = [
        [None if start == end else matrix[start, end]["result"] for end in ordered]
        for start in ordered
    ]
    paths, metrics = _held_karp(costs, limit)
    plans = []
    for path in paths:
        order = [ordered[index] for index in path["indices"]]
        legs = [deepcopy(matrix[start, end]) for start, end in zip(order, order[1:])]
        plans.append(
            {
                "order": order,
                "time": path["time"],
                "fee": path["fee"],
                "legs": legs,
                **metrics,
            }
        )
    return plans


def optimize_visit_order(places, quotes):
    return optimize_visit_orders(places, quotes, limit=1)[0]
