"""Validate supplied fare/RAG decisions; do not infer pass eligibility with an LLM."""

from .models import PassEvaluation, instant


def unit_total(unit: dict) -> int | None:
    if unit.get("quotedTotal") is not None:
        return unit["quotedTotal"]
    if unit["baseFare"] is None or unit["supplement"] is None:
        return None
    return unit["baseFare"] + unit["supplement"]


def fare_summary(legs: list[dict]) -> dict:
    units = [unit for leg in legs for unit in leg["fareUnits"]]
    ids = [u["fareUnitId"] for u in units]
    covered = [sid for u in units for sid in u["rideSegmentIds"]]
    all_segments = [sid for leg in legs for sid in leg["rideSegmentIds"]]
    bases_known = all(leg["fareBasis"] == "independent_journey" for leg in legs)
    known = bases_known and len(set(ids)) == len(ids) and len(set(covered)) == len(covered) and set(covered) == set(all_segments)
    amounts = [unit_total(u) for u in units]
    known = known and all(v is not None for v in amounts)
    return {"status": "known" if known else "partial" if units else "unknown",
            "noPassTotal": sum(amounts) if known else None,
            "currency": "JPY", "fareUnits": units,
            "scope": "independent-quoted-journeys"}


def compare_passes(routes: list[dict], raw_evaluations: list) -> dict:
    routes = [r for r in routes if r["completion"] == "complete" and not any(c["status"] in ("violated", "unsupported") for c in r["constraints"])]
    by_digest = {r["digest"]: r for r in routes}
    rows = []
    seen = set()
    for raw in raw_evaluations:
        try:
            item = PassEvaluation.model_validate(raw)
        except Exception:
            rows.append({"passId": None, "routeDigest": None, "status": "unknown", "total": None, "reason": "invalid_pass_schema"})
            continue
        route = by_digest.get(item.routeDigest)
        key = (item.routeDigest, item.passId)
        row = {"passId": item.passId, "routeDigest": item.routeDigest, "status": "unknown", "total": None,
               "savings": None, "reason": None, "decision": item.model_dump(mode="json")}
        rows.append(row)
        if route is None or key in seen:
            row["reason"] = "invalid_or_duplicate_route_pass"
            continue
        seen.add(key)
        units = route["fare"]["fareUnits"]
        unit_ids = [u["fareUnitId"] for u in units]
        allocated_ids = [a.fareUnitId for a in item.allocations]
        if len(set(allocated_ids)) != len(allocated_ids) or set(unit_ids) != set(allocated_ids):
            row["reason"] = "missing_or_duplicate_fare_allocation"
            continue
        if item.eligibility != "eligible" or item.coverage != "known" or item.validity != "valid":
            row["reason"] = "eligibility_coverage_or_validity_unconfirmed"
            continue
        unconfirmed = [unit for unit in units if unit["baseFare"] is None or unit["supplement"] is None]
        bus_segments = {segment["segmentId"] for leg in route["legs"] for segment in leg["segments"]
                        if segment.get("mode") == "bus"}
        allocations = {allocation.fareUnitId: allocation for allocation in item.allocations}
        # A gross-only bus fare can be compared when it is explicitly paid in full.
        # Without a fare breakdown it never establishes any bus discount.
        if any(unit.get("quotedTotal") is None or not set(unit["rideSegmentIds"]) <= bus_segments
               or allocations[unit["fareUnitId"]].uncoveredFare != unit["quotedTotal"]
               or allocations[unit["fareUnitId"]].requiredSupplement != 0 for unit in unconfirmed):
            row["reason"] = "fare_components_unconfirmed"
            row.pop("decision", None)
            continue
        if not item.validFrom or not item.validUntil:
            row["reason"] = "missing_validity_interval"
            continue
        # Validate the explicit interval returned by RAG, not the pass's legal rules.
        if any(not (instant(item.validFrom) <= instant(leg["departureAt"]) <= instant(leg["arrivalAt"]) < instant(item.validUntil))
               for leg in route["legs"] if leg["rideSegmentIds"]):
            row["reason"] = "journey_outside_reported_validity"
            continue
        amounts = [item.purchasePrice, item.reportedTotal] + [v for a in item.allocations for v in (a.uncoveredFare, a.requiredSupplement)]
        if any(v is None for v in amounts) or route["fare"]["noPassTotal"] is None:
            row["reason"] = "unknown_amount"
            continue
        total = item.purchasePrice + sum(a.uncoveredFare + a.requiredSupplement for a in item.allocations)
        if total != item.reportedTotal:
            row["reason"] = "total_mismatch"
            continue
        row.update(status="known", total=total, savings=route["fare"]["noPassTotal"] - total)

    # Duplicate decisions invalidate ALL rows of the ambiguous key, not only the second.
    counts = {}
    for row in rows:
        key = (row["routeDigest"], row["passId"])
        counts[key] = counts.get(key, 0) + 1
    for row in rows:
        if counts[(row["routeDigest"], row["passId"])] > 1:
            row.update(status="unknown", total=None, savings=None, reason="duplicate_route_pass")
    choices = []
    for route in routes:
        value = route["fare"]["noPassTotal"]
        if value is not None:
            choices.append({"routeDigest": route["digest"], "passId": None, "total": value})
    choices.extend({"routeDigest": r["routeDigest"], "passId": r["passId"], "total": r["total"]} for r in rows if r["status"] == "known")
    choices.sort(key=lambda r: (r["total"], by_digest[r["routeDigest"]].get("elapsedMinutes", float("inf")),
                                by_digest[r["routeDigest"]]["transfers"] if by_digest[r["routeDigest"]]["transfers"] is not None else float("inf"), r["passId"] is not None, r["passId"] or "", r["routeDigest"]))
    return {"status": "known" if choices and all(r["status"] == "known" for r in rows) else "partial" if choices else "unknown",
            "evaluations": rows, "recommendation": choices[0] if choices else None,
            "scope": "completed-candidates-only", "purchasePriceCount": 1}
