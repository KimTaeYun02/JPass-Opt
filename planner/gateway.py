"""Deadline-aware, serialized offline dispatch with committed attempt budgets."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import time
import weakref

from .ledger import Ledger, Stopped
from .models import RailQuery, Ride, digest, instant, iso
from .provider import Provider, ProviderFailure


@dataclass
class Context:
    ledger: Ledger
    session: str
    operation: str
    deadline: float
    monotonic: object = time.monotonic
    phase: str = "baseline"
    cache_allowed: bool = False
    cache: dict = field(default_factory=dict)
    cache_hits: int = 0
    stop_reasons: list[str] = field(default_factory=list)
    bus_attempts: int = 0

    def time_left(self):
        left = self.deadline - self.monotonic()
        if left <= 0:
            raise Stopped("deadline_exceeded")
        return left

    def remaining(self, kind="rail"):
        return self.ledger.remaining(self.session, self.operation, kind)


class Gateway:
    _provider_locks = weakref.WeakKeyDictionary()

    def __init__(self, provider: Provider, retries=1, monotonic=time.monotonic):
        if provider.dataSource not in ("fixture", "replay"):
            raise ValueError("외부 통신이 없는 fixture 또는 replay 제공자만 연결할 수 있습니다.")
        if type(retries) is not int or not 0 <= retries <= 1:
            raise ValueError("명시적 재시도는 최대 1회입니다.")
        self.provider, self.retries, self.monotonic = provider, retries, monotonic

    @property
    def semaphore(self):
        # One server event loop; all Planner instances share this provider's gate.
        loop = asyncio.get_running_loop()
        locks = self._provider_locks.setdefault(loop, {})
        return locks.setdefault(self.provider.id, asyncio.Semaphore(1))

    async def call(self, ctx: Context, kind: str, fn, *, retry_allowed=True):
        retries = self.retries if retry_allowed else 0
        for retry in range(retries + 1):
            acquired = False
            attempt = None
            try:
                await asyncio.wait_for(self.semaphore.acquire(), timeout=ctx.time_left())
                acquired = True
                ctx.time_left()
                attempt = ctx.ledger.reserve(ctx.session, ctx.operation, kind, ctx.phase)
                result = await asyncio.wait_for(fn(), timeout=ctx.time_left())
                ctx.time_left()
                ctx.ledger.record(attempt, "ok")
                return result
            except asyncio.CancelledError:
                if attempt is not None:
                    ctx.ledger.record(attempt, "failed", "cancelled")
                raise
            except (asyncio.TimeoutError, Stopped) as error:
                reason = error.reason if isinstance(error, Stopped) else "deadline_exceeded"
                if attempt is not None:
                    ctx.ledger.record(attempt, "failed", reason)
                ctx.stop_reasons.append(reason)
                raise Stopped(reason) from error
            except Exception as error:
                code = error.code if isinstance(error, ProviderFailure) else "provider_invalid_response"
                if attempt is not None:
                    ctx.ledger.record(attempt, "failed", code)
                if isinstance(error, ProviderFailure) and error.retryable and retry < retries and ctx.remaining(kind) > 0:
                    continue
                ctx.stop_reasons.append(code)
                raise Stopped(code) from error
            finally:
                if acquired:
                    self.semaphore.release()
        raise AssertionError("unreachable")

    async def rail(self, ctx: Context, query: RailQuery, *, retry_allowed=True) -> list[Ride]:
        key = digest([self.provider.id, self.provider.version, query.origin.identity(), query.destination.identity(),
                      iso(instant(query.preparedAt)), query.options])
        if ctx.cache_allowed and self.provider.capabilities.reuseExactQuotes and key in ctx.cache:
            ctx.time_left()
            ctx.cache_hits += 1
            return deepcopy(ctx.cache[key])

        async def fetch():
            return [Ride.model_validate(r) for r in await self.provider.rail(query)]

        rides = await self.call(ctx, "rail", fetch, retry_allowed=retry_allowed)
        if ctx.cache_allowed and self.provider.capabilities.reuseExactQuotes:
            ctx.cache[key] = deepcopy(rides)
        return deepcopy(rides)
