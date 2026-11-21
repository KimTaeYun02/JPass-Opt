"""Select one affordable visit order and compare the resulting schedules."""

from datetime import timedelta

from .models import instant


def feasible(route):
    return route["completion"] == "complete" and all(
        check["status"] not in ("violated", "unsupported") for check in route["constraints"]
    )


def schedule_score(route):
    fare = route["fare"]["noPassTotal"]
    return instant(route["finishAt"]), fare is None, fare if fare is not None else 0


def can_select(candidate, baseline):
    if not feasible(candidate):
        return False
    timing = {"unknown": 0, "reference": 1, "timetable": 2}
    walking = {"unknown": 0, "estimated": 1, "routed": 2}
    if timing[candidate["timing"]] < timing[baseline["timing"]]:
        return False
    if walking[candidate["accessWalking"]] < walking[baseline["accessWalking"]]:
        return False
    satisfied = lambda route: {(check["kind"], check["visitId"]) for check in route["constraints"]
                               if check["status"] == "satisfied"}
    if not satisfied(baseline) <= satisfied(candidate):
        return False
    return not feasible(baseline) or schedule_score(candidate) < schedule_score(baseline)


def deadline_targets(places, baseline):
    """Order deadline visits by observed misses, then by declared latest visit start."""
    missed = {check["visitId"] for check in baseline["constraints"]
              if check["status"] == "violated" and check["kind"] in ("fixed_visit", "opening")}
    targets = []
    for place in places:
        stay = timedelta(minutes=place.stayMinutes)
        windows = [(instant(w.startAt), instant(w.endAt)) for w in place.openingWindows or []]
        closing = max((end - stay for start, end in windows if end - start >= stay), default=None)
        fixed = instant(place.fixedVisitAt) if place.fixedVisitAt else None
        bounds = [bound for bound in (fixed, closing) if bound is not None]
        if bounds:
            targets.append((place.clientPlaceId not in missed, min(bounds), place.clientPlaceId))
    return [pid for _, _, pid in sorted(targets)]


def select_alternate(ranked, primary, choices, *, remaining, reuse_prefix, policy="diverse", targets=(),
                     pass_matches=None, conservative=False):
    """Compare only alternatives affordable without retries or speculative reuse."""
    has_other_order = False
    best = None
    best_priority = None
    first_match_count = None
    best_match_count = None
    discriminating_matches = False
    reversed_primary = primary[::-1]
    for rank, row in enumerate(ranked):
        order = row["order"]
        if order == primary:
            continue
        has_other_order = True
        shared = 0
        if reuse_prefix:
            for a, b in zip(primary, order):
                if a != b:
                    break
                shared += 1
        # The last common visit is evaluated again, starting at its original arrival.
        start = max(0, shared - 1)
        required = len(order) - start - 1 if conservative else sum(
            not choices[a].station.same_physical(choices[b].station)
            for a, b in zip(order[start:], order[start + 1:]))
        if required > remaining:
            continue
        is_reverse = order == reversed_primary
        candidate = {"order": order, "geometricRank": rank + 1,
                     "kind": "reversal" if is_reverse else "different_path",
                     "reusedLegs": start, "requiredRailUpperBound": required}
        preferred = is_reverse if policy == "reverse_first" else not is_reverse
        if policy == "pass_hint" and pass_matches:
            matched = {pass_matches[a, b] for a, b in zip(order, order[1:]) if (a, b) in pass_matches}
            match_count = len(matched)
            if first_match_count is None:
                first_match_count = match_count
            discriminating_matches |= match_count != first_match_count
            priority = -match_count, not preferred, rank
        elif policy == "deadline_first" and targets:
            priority = tuple(order.index(pid) for pid in targets), not preferred, rank
        else:
            if preferred:
                return candidate, None
            if best is None:
                best = candidate
            continue
        if best_priority is None or priority < best_priority:
            best, best_priority = candidate, priority
            if policy == "pass_hint":
                best_match_count = match_count
    if best is not None:
        if discriminating_matches:
            best["passHintMatchedLegs"] = best_match_count
        return best, None
    return None, "skipped_budget" if has_other_order else "no_alternative"
