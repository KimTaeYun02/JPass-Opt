"""Routing core: Python standard library only, with no crawler or web imports."""

from .matrix import optimize_matrix, quotes_from_segments
from .optimizer import (
    MAX_ROUTE_STOPS,
    MAX_SAFE_INTEGER,
    optimize_visit_order,
    optimize_visit_orders,
    validate_places,
    validate_quote,
)

__all__ = [
    "MAX_ROUTE_STOPS",
    "MAX_SAFE_INTEGER",
    "optimize_matrix",
    "quotes_from_segments",
    "optimize_visit_order",
    "optimize_visit_orders",
    "validate_places",
    "validate_quote",
]
