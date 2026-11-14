"""Translate crawler alternatives into the fixed directed costs used by the DP."""

from copy import deepcopy
from typing import Literal, TypedDict, cast

from app.optimizer.optimizer import (
    optimize_visit_orders,
    validate_places,
    validate_quote,
)
from app.optimizer.types import Plan, Quote


class SkippedRoute(TypedDict):
    departure: str
    arrival: str
    routeIndex: int
    reason: str


class MatrixResult(TypedDict):
    plans: list[Plan]
    selection: Literal["first", "fastest"]
    skippedRoutes: list[SkippedRoute]


def quotes_from_segments(
    segments: object, *, selection: str = "first"
) -> tuple[list[Quote], list[SkippedRoute]]:
    if selection not in ("first", "fastest"):
        raise ValueError("selection은 first 또는 fastest여야 합니다.")
    if not isinstance(segments, list):
        raise ValueError("matrix는 구간 객체의 배열이어야 합니다.")
    quotes: list[Quote] = []
    skipped: list[SkippedRoute] = []
    seen: set[tuple[str, str]] = set()
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("matrix의 각 구간은 객체여야 합니다.")
        start, end = segment.get("departure"), segment.get("arrival")
        if (
            any(
                not isinstance(value, str) or not value.strip()
                for value in (start, end)
            )
            or start == end
        ):
            raise ValueError(
                "구간의 departure와 arrival은 서로 다른 역 이름이어야 합니다."
            )
        start, end = cast(str, start), cast(str, end)
        if (start, end) in seen:
            raise ValueError(f"{start} → {end}: 중복된 이동 결과입니다.")
        seen.add((start, end))
        routes = segment.get("routes")
        if not isinstance(routes, list) or not routes:
            raise ValueError(
                f"{start} → {end}: routes는 비어 있지 않은 배열이어야 합니다."
            )
        candidates: list[tuple[int, int, int, Quote]] = []
        for index, result in enumerate(
            routes if selection == "fastest" else routes[:1]
        ):
            if (
                selection == "fastest"
                and isinstance(result, dict)
                and (result.get("time") is None or result.get("fee") is None)
            ):
                skipped.append(
                    {
                        "departure": start,
                        "arrival": end,
                        "routeIndex": index,
                        "reason": "time 또는 fee가 null이거나 누락되었습니다.",
                    }
                )
                continue
            raw_quote = {
                "from": start,
                "to": end,
                "source": "snapshot",
                "result": result,
            }
            validate_quote(raw_quote)
            quote = cast(Quote, raw_quote)
            candidates.append(
                (quote["result"]["time"], quote["result"]["fee"], index, quote)
            )
        if not candidates:
            raise ValueError(f"{start} → {end}: time과 fee가 유효한 경로가 없습니다.")
        chosen = min(candidates, key=lambda candidate: candidate[:3])
        quote = deepcopy(chosen[3])
        quote["routeIndex"] = chosen[2]
        quotes.append(quote)
    return quotes, skipped


def optimize_matrix(
    stations: list[str] | tuple[str, ...],
    segments: object,
    *,
    selection: str = "first",
    limit: int = 1,
) -> MatrixResult:
    """Optimize a complete crawler matrix without crawling or altering its costs."""
    validate_places(stations)
    quotes, skipped = quotes_from_segments(segments, selection=selection)
    station_set = set(stations)
    if any(
        quote["from"] not in station_set or quote["to"] not in station_set
        for quote in quotes
    ):
        raise ValueError(
            "matrix에는 stations에 지정한 역 사이의 구간만 입력해야 합니다."
        )
    plans = optimize_visit_orders(stations, quotes, limit=limit)
    return {
        "plans": plans,
        "selection": cast(Literal["first", "fastest"], selection),
        "skippedRoutes": skipped,
    }
