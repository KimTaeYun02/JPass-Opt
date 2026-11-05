import { validateQuote } from './route-optimizer.ts';
import type { RouteQuote, RouteResult } from './route-optimizer.ts';
import { referenceSchedule } from './route-candidates.ts';
import type { Candidate, VisitPlace } from './route-candidates.ts';

export type QuoteRequest = {
  from: string; to: string; stations: { from: string; to: string };
  departureAt: string; options: Record<string, string | number | boolean>;
};
export type Unknown = { status: 'unknown'; reason: string };
export type QuoteProvider = {
  id: string;
  // Explicit opt-in for temporary in-memory reuse; not a statement about supplier terms.
  reuseAllowed: boolean;
  quote(request: QuoteRequest): Promise<RouteResult | Unknown>;
};
export type QuoteOutcome = { status: 'ok'; quote: RouteQuote } | Unknown;

export class QuoteSession {
  readonly provider: QuoteProvider;
  readonly maxCalls: number;
  readonly ttlMs: number;
  #calls = 0;
  #cache = new Map<string, { result: RouteResult; expiresAt: number }>();
  #pending = new Map<string, Promise<RouteResult | Unknown>>();

  constructor(provider: QuoteProvider, maxCalls = 0, ttlMs = 300_000) {
    if (!provider.id?.trim() || typeof provider.quote !== 'function' || typeof provider.reuseAllowed !== 'boolean'
      || !Number.isSafeInteger(maxCalls) || maxCalls < 0 || !Number.isSafeInteger(ttlMs) || ttlMs <= 0) throw new Error('공급자·호출 예산·캐시 유효시간이 올바르지 않습니다.');
    this.provider = provider; this.maxCalls = maxCalls; this.ttlMs = ttlMs;
  }

  get calls(): number { return this.#calls; }

  async quote(request: QuoteRequest): Promise<QuoteOutcome> {
    const date = Date.parse(request.departureAt);
    if (!request.from?.trim() || !request.to?.trim() || request.from === request.to
      || !request.stations?.from?.trim() || !request.stations?.to?.trim()
      || !/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:\.\d{1,3})?(?:Z|[+-]\d\d:\d\d)$/.test(request.departureAt)
      || !Number.isFinite(date) || !request.options || Object.values(request.options).some(v => !['string', 'number', 'boolean'].includes(typeof v) || (typeof v === 'number' && !Number.isFinite(v)))) throw new Error('조회 조건이 올바르지 않습니다.');
    const normalized: QuoteRequest = structuredClone({ ...request, departureAt: new Date(date).toISOString() });
    // Station quotes can be reused across distinct places using the same station.
    const key = JSON.stringify([this.provider.id, normalized.stations.from, normalized.stations.to,
      normalized.departureAt, Object.entries(normalized.options).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)]);
    const cached = this.provider.reuseAllowed ? this.#cache.get(key) : undefined;
    if (cached && cached.expiresAt <= Date.now()) this.#cache.delete(key);
    let result: RouteResult | Unknown;
    if (cached && cached.expiresAt > Date.now()) result = cached.result;
    else {
      let pending = this.#pending.get(key);
      if (!pending) {
        if (this.#calls >= this.maxCalls) return { status: 'unknown', reason: '호출 예산 소진' };
        this.#calls++;
        pending = Promise.resolve().then(() => this.provider.quote(normalized)).then(value => {
          if ('status' in value && value.status === 'unknown') return value;
          const raw = value as RouteResult;
          validateQuote({ from: normalized.from, to: normalized.to, stations: normalized.stations, result: raw });
          const copy = structuredClone(raw);
          if (this.provider.reuseAllowed) this.#cache.set(key, { result: copy, expiresAt: Date.now() + this.ttlMs });
          return copy;
        }).catch(error => ({ status: 'unknown' as const, reason: error instanceof Error ? error.message : '공급자 조회 실패' }));
        this.#pending.set(key, pending);
        void pending.then(() => this.#pending.delete(key));
      }
      result = await pending;
    }
    if ('status' in result) return structuredClone(result);
    return { status: 'ok', quote: {
      from: normalized.from, to: normalized.to, stations: normalized.stations, source: 'snapshot', result: structuredClone(result),
    } };
  }
}

// Only a station-to-station reference schedule: access walks and opening hours remain unknown.
export async function refreshCandidate(places: VisitPlace[], candidate: Candidate, startAt: string, session: QuoteSession,
  options: Record<string, string | number | boolean> = {}) {
  referenceSchedule(places, candidate, startAt);
  const before = session.calls; let current = Date.parse(startAt);
  const refreshed: RouteQuote[] = [];
  const visits: { id: string; arrivalAt: string; leaveAt: string }[] = [];
  for (const [i, id] of candidate.order.entries()) {
    const from = places.find(p => p.id === id)!;
    const arrivalAt = new Date(current).toISOString();
    current += from.stayMinutes * 60_000;
    visits.push({ id, arrivalAt, leaveAt: new Date(current).toISOString() });
    const next = candidate.order[i + 1];
    if (!next) continue;
    const to = places.find(p => p.id === next)!;
    const outcome = await session.quote({ from: id, to: next, stations: { from: from.station, to: to.station }, departureAt: new Date(current).toISOString(), options });
    if (outcome.status === 'unknown') return { status: 'unknown', reason: outcome.reason, failedLeg: { from: id, to: next }, visits, legs: refreshed, addedCalls: session.calls - before };
    refreshed.push(outcome.quote);
    current += outcome.quote.result.time * 60_000;
  }
  return { status: 'unverified', scope: 'station-only-reference', openingHours: 'unknown', accessWalking: 'unknown', visits,
    legs: refreshed, finishAt: new Date(current).toISOString(), addedCalls: session.calls - before,
    optimality: 'not-re-evaluated', movementMinutes: refreshed.reduce((sum, q) => sum + q.result.time, 0),
    independentLegFee: refreshed.reduce((sum, q) => sum + q.result.fee, 0) };
}
