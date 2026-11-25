"""One provider method is one managed attempt. Retries belong to the gateway."""

from dataclasses import dataclass
from typing import Protocol

from .models import PassEvaluation, Place, RailQuery, Ride, StationCandidate


@dataclass(frozen=True)
class Capabilities:
    reuseExactQuotes: bool = False
    reusePrefixInRequest: bool = False
    ragDeliveryAllowed: bool = False


class ProviderFailure(Exception):
    def __init__(self, code="provider_failure", retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


class Provider(Protocol):
    id: str
    version: str
    dataSource: str
    capabilities: Capabilities

    async def stations(self, place: Place) -> list[StationCandidate]: ...
    async def rail(self, query: RailQuery) -> list[Ride]: ...
    async def passes(self, routes: list[dict]) -> list[PassEvaluation]: ...
