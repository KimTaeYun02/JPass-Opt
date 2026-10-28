import { kBestHeldKarp } from './route-dp.ts';

export type RouteStop = { station: string; line: string | null };
export type RouteResult = { time: number; fee: number; route: RouteStop[] };
export type RouteQuote = {
  from: string; to: string; result: RouteResult;
  stations?: { from: string; to: string };
  source?: 'snapshot' | 'estimate';
};
export type RouteLeg = RouteQuote;
export type RoutePlan = {
  order: string[];
  time: number;
  fee: number;
  legs: RouteLeg[];
  evaluatedOrders: number;
  algorithm: 'held-karp';
  dpStates: number;
  dpTransitions: number;
};

export const MAX_ROUTE_STOPS = 8;

export function validateQuote(quote: RouteQuote): void {
  const { from, to, result } = quote;
  if (!from?.trim() || !to?.trim() || from === to) {
    throw new Error('이동 구간의 출발역과 도착역이 올바르지 않습니다.');
  }
  if (!result || !Number.isSafeInteger(result.time) || result.time < 0
    || !Number.isSafeInteger(result.fee) || result.fee < 0) {
    throw new Error(`${from} → ${to}: time과 fee는 0 이상의 안전한 정수여야 합니다.`);
  }
  const stations = quote.stations ?? { from, to };
  if (!stations.from?.trim() || !stations.to?.trim()
    || (quote.source !== undefined && !['snapshot', 'estimate'].includes(quote.source))) {
    throw new Error(`${from} → ${to}: 역 또는 데이터 출처가 올바르지 않습니다.`);
  }
  const stops = result.route;
  if (!Array.isArray(stops) || stops.length < 2
    || stops[0]?.station !== stations.from || stops.at(-1)?.station !== stations.to) {
    throw new Error(`${from} → ${to}: route의 양 끝 역이 조회 구간과 일치해야 합니다.`);
  }
  for (const [index, stop] of stops.entries()) {
    if (!stop || typeof stop.station !== 'string' || !stop.station.trim()
      || (index === stops.length - 1
        ? stop.line !== null
        : typeof stop.line !== 'string' || !stop.line.trim())) {
      throw new Error(`${from} → ${to}: 노선은 출발 역에 기록하고 최종 역만 null이어야 합니다.`);
    }
  }
}

// All costs are fixed snapshots supplied by the caller; this module makes no requests.
export function optimizeVisitOrder(places: readonly string[], quotes: readonly RouteQuote[]): RoutePlan {
  return optimizeVisitOrders(places, quotes, 1)[0]!;
}

// Shared top-k search for the single winner and the visit candidate list.
export function optimizeVisitOrders(places: readonly string[], quotes: readonly RouteQuote[], limit = 3): RoutePlan[] {
  if (places.length < 2 || places.length > MAX_ROUTE_STOPS) {
    throw new Error(`경로 최적화는 2~${MAX_ROUTE_STOPS}개 역을 지원합니다.`);
  }
  if (places.some((place) => typeof place !== 'string' || !place.trim())
    || new Set(places).size !== places.length) {
    throw new Error('방문 역은 비어 있지 않은 고유한 이름이어야 합니다.');
  }
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 3) throw new Error('후보 수는 1~3개여야 합니다.');

  const matrix = new Map<string, Map<string, RouteQuote>>();
  for (const quote of quotes) {
    validateQuote(quote);
    let destinations = matrix.get(quote.from);
    if (!destinations) matrix.set(quote.from, destinations = new Map());
    if (destinations.has(quote.to)) {
      throw new Error(`${quote.from} → ${quote.to}: 중복된 이동 결과입니다.`);
    }
    destinations.set(quote.to, quote);
  }
  for (const from of places) {
    for (const to of places) {
      if (from !== to && !matrix.get(from)?.has(to)) {
        throw new Error(`${from} → ${to}: 이동 결과가 누락되어 최적 동선을 확정할 수 없습니다.`);
      }
    }
  }

  // Sorted indices preserve JS code-unit order, independent of input order.
  const candidates = [...places].sort();
  const costs = candidates.map(from => candidates.map(to => from === to ? null : matrix.get(from)!.get(to)!.result));
  const { paths, ...metrics } = kBestHeldKarp(costs, limit);
  return paths.map(path => {
    const order = path.indices.map(index => candidates[index]!);
    const legs = order.slice(0, -1).map((from, index) => {
      const to = order[index + 1]!;
      const quote = matrix.get(from)!.get(to)!;
      const result = quote.result;
      return { from, to,
        ...(quote.stations ? { stations: { ...quote.stations } } : {}),
        ...(quote.source ? { source: quote.source } : {}),
        result: {
          time: result.time, fee: result.fee, route: result.route.map((stop) => ({ ...stop })),
        } };
    });
    return { order, time: path.time, fee: path.fee, legs, ...metrics };
  });
}
