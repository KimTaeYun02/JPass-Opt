"""Geometric ranking is local, deterministic and makes no travel-time claim."""

from itertools import permutations
from math import asin, cos, radians, sin, sqrt
from datetime import timedelta

from .models import Place, PlanRequest, instant


def distance(a: Place, b: Place) -> float:
    lat1, lat2 = radians(a.location.lat), radians(b.location.lat)
    dlat = lat2 - lat1
    dlng = radians(b.location.lng - a.location.lng)
    h = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlng / 2)**2
    return 6_371_008.8 * 2 * asin(sqrt(min(1, h)))


def groups(places: list[Place], diameter_m: float) -> list[list[str]]:
    """Complete-link bound avoids joining a long chain through nearby neighbors."""
    if not 0 < diameter_m < 1_000_000:
        raise ValueError("실험용 군집 최대 직경이 올바르지 않습니다.")
    result: list[list[Place]] = []
    for place in sorted(places, key=lambda p: p.clientPlaceId):
        target = next((g for g in result if all(distance(place, other) <= diameter_m for other in g)), None)
        if target is None:
            result.append([place])
        else:
            target.append(place)
    return [[p.clientPlaceId for p in group] for group in result]


def time_only_possible(order: list[Place], request: PlanRequest) -> bool:
    """Necessary bounds with ZERO travel, never distance converted into minutes."""
    try:
        return _time_only_possible(order, request)
    except OverflowError:
        return False


def _time_only_possible(order: list[Place], request: PlanRequest) -> bool:
    current = instant(request.startAt)
    for place in order:
        if place.fixedVisitAt is not None:
            fixed = instant(place.fixedVisitAt)
            if current > fixed:
                return False
            current = fixed
        duration = timedelta(minutes=place.stayMinutes)
        if place.openingWindows is not None:
            possible = [max(current, instant(w.startAt)) for w in place.openingWindows
                        if max(current, instant(w.startAt)) + duration <= instant(w.endAt)
                        and (place.fixedVisitAt is None or instant(w.startAt) <= current)]
            if not possible:
                return False
            current = min(possible)
        current += duration
    return request.endBy is None or current <= instant(request.endBy)


def local_candidates(request: PlanRequest, diameter_m: float = 800, *, rank_limit: int | None = 3) -> dict:
    places = sorted(request.places, key=lambda p: p.clientPlaceId)
    start = next((p.clientPlaceId for p in places if p.anchorRole == "start"), None)
    end = next((p.clientPlaceId for p in places if p.anchorRole == "end"), None)
    clusters = groups(places, diameter_m)
    cluster_id = {pid: i for i, group in enumerate(clusters) for pid in group}
    distances = {(a.clientPlaceId, b.clientPlaceId): distance(a, b) for a in places for b in places}
    best = []
    for order in permutations(places):
        if (start and order[0].clientPlaceId != start) or (end and order[-1].clientPlaceId != end):
            continue
        if not time_only_possible(list(order), request):
            continue
        ids = tuple(p.clientPlaceId for p in order)
        length = sum(distances[a, b] for a, b in zip(ids, ids[1:]))
        # Soft penalty for leaving and returning to a geographic group, not a hard block.
        runs = [cluster_id[pid] for i, pid in enumerate(ids) if not i or cluster_id[pid] != cluster_id[ids[i-1]]]
        returns = len(runs) - len(set(runs))
        best.append((length + returns * diameter_m, ids))
    best.sort()
    return {"orders": [{"order": list(order), "geometricScore": score, "scoreBasis": "distance-and-soft-group-penalty-meters"}
                       for score, order in best[:rank_limit]],
            "groups": clusters, "clusterDiameterLimitMeters": diameter_m,
            "thresholdStatus": "experimental", "routeProviderAttempts": 0,
            "optimality": "geometric-ranking-only"}
