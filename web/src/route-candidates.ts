import { MAX_ROUTE_STOPS, optimizeVisitOrders, validateQuote } from './route-optimizer.ts';
import type { RoutePlan, RouteQuote } from './route-optimizer.ts';

export type VisitPlace = { id: string; name: string; station: string; stayMinutes: number };
export type Pair = { from: string; to: string };
export type Estimate = Pair & { time: number; fee: number };
export type Candidate = RoutePlan & {
  grade: 'candidate' | 'unverified'; estimatedLegs: Pair[];
};

export function checkPlaces(places: readonly VisitPlace[]): void {
  if (places.length < 2 || places.length > MAX_ROUTE_STOPS
    || new Set(places.map(p => p.id)).size !== places.length
    || places.some(p => !p.id?.trim() || !p.name?.trim() || !p.station?.trim()
      || !Number.isSafeInteger(p.stayMinutes) || p.stayMinutes < 0)) {
    throw new Error('2~8개의 고유한 장소 ID와 유효한 역·체류시간이 필요합니다.');
  }
}

// Missing real observations stay missing even when an explicit estimate fills the search matrix.
export function buildCandidateMatrix(places: readonly VisitPlace[], snapshots: readonly RouteQuote[], estimates: readonly Estimate[]) {
  checkPlaces(places);
  const byId = new Map(places.map(p => [p.id, p]));
  const real = new Map<string, Map<string, RouteQuote>>();
  for (const quote of snapshots) {
    const from = byId.get(quote.from); const to = byId.get(quote.to);
    if (!from || !to || quote.from === quote.to || quote.source !== 'snapshot'
      || (quote.stations && (quote.stations.from !== from.station || quote.stations.to !== to.station))) throw new Error('유효하지 않은 실측 구간입니다.');
    const normalized: RouteQuote = { ...structuredClone(quote), source: 'snapshot', stations: { from: from.station, to: to.station } };
    validateQuote(normalized);
    if (!real.has(from.id)) real.set(from.id, new Map());
    if (real.get(from.id)!.has(to.id)) throw new Error('중복된 실측 구간입니다.');
    real.get(from.id)!.set(to.id, normalized);
  }
  const estimated = new Map<string, Map<string, Estimate>>();
  for (const estimate of estimates) {
    if (!byId.has(estimate.from) || !byId.has(estimate.to) || estimate.from === estimate.to
      || !Number.isSafeInteger(estimate.time) || estimate.time <= 0
      || !Number.isSafeInteger(estimate.fee) || estimate.fee < 0) throw new Error('추정 구간에는 양수 시간과 0 이상 운임이 필요합니다.');
    if (!estimated.has(estimate.from)) estimated.set(estimate.from, new Map());
    if (estimated.get(estimate.from)!.has(estimate.to)) throw new Error('중복된 추정 구간입니다.');
    estimated.get(estimate.from)!.set(estimate.to, estimate);
  }
  const quotes: RouteQuote[] = []; const missing: Pair[] = []; const unresolved: Pair[] = [];
  for (const from of places) for (const to of places) {
    if (from.id === to.id) continue;
    const pair = { from: from.id, to: to.id };
    const observation = real.get(from.id)?.get(to.id);
    if (observation) { quotes.push(observation); continue; }
    missing.push(pair);
    const estimate = estimated.get(from.id)?.get(to.id);
    if (!estimate) { unresolved.push(pair); continue; }
    quotes.push({ ...pair, source: 'estimate', stations: { from: from.station, to: to.station }, result: {
      time: estimate.time, fee: estimate.fee,
      route: [{ station: from.station, line: '추정 구간 — 노선 미확인' }, { station: to.station, line: null }],
    } });
  }
  return { quotes, missing, unresolved };
}

export function rankCandidates(places: readonly VisitPlace[], quotes: readonly RouteQuote[], limit = 3): Candidate[] {
  checkPlaces(places);
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 3) throw new Error('후보 수는 1~3개여야 합니다.');
  quotes.forEach(validateQuote);
  if (quotes.some(q => q.source === undefined)) throw new Error('후보 구간에는 snapshot 또는 estimate 출처가 필요합니다.');
  const built = buildCandidateMatrix(places, quotes.filter(q => q.source !== 'estimate'), quotes.filter(q => q.source === 'estimate').map(q => ({ from: q.from, to: q.to, time: q.result.time, fee: q.result.fee })));
  if (built.unresolved.length) throw new Error('완전한 후보 행렬이 필요합니다.');
  return optimizeVisitOrders(places.map(p => p.id), built.quotes, limit).map(plan => {
    const estimatedLegs = plan.legs.filter(q => q.source === 'estimate').map(q => ({ from: q.from, to: q.to }));
    return { ...plan, grade: estimatedLegs.length ? 'candidate' : 'unverified', estimatedLegs };
  });
}

export function referenceSchedule(places: readonly VisitPlace[], plan: Candidate, startAt: string) {
  checkPlaces(places);
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?(?:Z|[+-]\d\d:\d\d)$/.test(startAt) || !Number.isFinite(Date.parse(startAt))) throw new Error('시작 시각에는 날짜와 시간대가 필요합니다.');
  if (plan.order.length !== places.length || new Set(plan.order).size !== places.length
    || plan.order.some(id => !places.some(p => p.id === id)) || plan.legs.length !== places.length - 1) throw new Error('후보 방문 순서가 올바르지 않습니다.');
  let current = Date.parse(startAt);
  const visits = plan.order.map((id, i) => {
    const place = places.find(p => p.id === id)!;
    const arrivalAt = new Date(current).toISOString();
    current += place.stayMinutes * 60_000;
    const leaveAt = new Date(current).toISOString();
    const leg = plan.legs[i];
    if (leg) {
      if (leg.from !== id || leg.to !== plan.order[i + 1]) throw new Error('후보 구간 순서가 올바르지 않습니다.');
      validateQuote(leg);
      current += leg.result.time * 60_000;
    }
    return { id, name: place.name, arrivalAt, leaveAt, stayMinutes: place.stayMinutes };
  });
  return { status: 'reference-only', openingHours: 'unknown', accessWalking: 'unknown', departureTimeValidated: false, visits,
    finishAt: new Date(current).toISOString(), elapsedMinutes: (current - Date.parse(startAt)) / 60_000 };
}
