"""크롤링 후보와 검증된 이동 구간·최적화 결과의 내부 데이터 계약."""

from typing import Literal, NotRequired, TypedDict


class RoutePoint(TypedDict):
    station: str
    # 노선은 다음 역으로 이동하는 구간에 속하며, 마지막 역은 None이다.
    line: str | None


class RouteCandidate(TypedDict):
    """크롤러가 파싱한 후보. 시간·요금을 읽지 못하면 None일 수 있다."""

    time: int | None
    fee: int | None
    route: list[RoutePoint]


class RouteResult(TypedDict):
    """검증을 통과하여 최적화에 사용할 수 있는 이동 비용과 경로."""

    time: int
    fee: int
    route: list[RoutePoint]


StationPair = TypedDict("StationPair", {"from": str, "to": str})

Quote = TypedDict(
    "Quote",
    {
        "from": str,
        "to": str,
        "result": RouteResult,
        # 일반 최적화 입력은 역 이름을 ID로 쓰며 이 메타데이터를 생략할 수 있다.
        "stations": NotRequired[StationPair],
        "source": NotRequired[Literal["snapshot", "estimate"]],
        "routeIndex": NotRequired[int],
    },
)


class Plan(TypedDict):
    """방문 순서와 선택 구간. 단일 방문에서는 최적화 실행 지표를 생략한다."""

    order: list[str]
    time: int
    fee: int
    legs: list[Quote]
    algorithm: NotRequired[Literal["held-karp"]]
    dpStates: NotRequired[int]
    dpTransitions: NotRequired[int]
    evaluatedOrders: NotRequired[int]
