// Fixture v2 is deliberately separate from the live station-only v1 contract.
export type OrderAlternatePolicy = 'diverse' | 'reverse_first' | 'deadline_first' | 'pass_hint';
export type LowCallPlace = {
  clientPlaceId: string; name: string; location: { lat: number; lng: number };
  stayMinutes: number; googlePlaceId?: string | null; anchorRole?: 'start' | 'end' | null;
  openingWindows?: { startAt: string; endAt: string }[] | null; fixedVisitAt?: string | null;
};
export type LowCallPlanRequest = {
  inputVersion?: '2'; sessionId: string; operationId: string; dataSource?: 'fixture' | 'replay'; fixtureId?: string;
  startAt: string; endBy?: string | null; places: LowCallPlace[]; mode?: 'reference' | 'strict';
  budget?: { railAttempts?: number | null }; candidateIndex?: number;
  options?: { allowCache?: boolean; allowPrefixReuse?: boolean; allowStationAlternates?: boolean; allowOrderAlternates?: boolean;
    orderAlternatePolicy?: OrderAlternatePolicy;
    allowRepair?: boolean; allowPassEvaluation?: boolean };
};
type Constraint = { kind: string; visitId: string | null; status: 'satisfied' | 'violated' | 'unknown' | 'unsupported'; reason: string | null };
export type LowCallRoute = {
  planId: string; digest: string; order: string[];
  completion: 'complete' | 'partial' | 'failed'; timing: 'timetable' | 'reference' | 'unknown';
  finishAt: string | null; elapsedMinutes?: number; constraints: Constraint[];
  visits: { clientPlaceId: string; arrivalAt: string; visitStartAt: string; leaveAt: string; stayMinutes: number }[];
  legs: Record<string, unknown>[];
  fare: { status: 'known' | 'partial' | 'unknown'; noPassTotal: number | null; fareUnits: Record<string, unknown>[] };
  [key: string]: unknown;
};
export type LowCallPlanResponse = {
  inputVersion: '2'; dataSource: 'fixture' | 'replay'; fixtureId: string; fixtureLabel: string; echoInput: LowCallPlanRequest;
  replayProvenance?: { kind: 'legacy-team-crawler' | 'captured-team-crawler' | 'captured-public-dom' | 'test-only'; sourceArtifact: string; sourceSha256: string; notice: string };
  completion: LowCallRoute['completion']; timing: LowCallRoute['timing']; accessWalking: 'routed' | 'estimated' | 'unknown';
  pricing: 'known' | 'partial' | 'unknown'; constraints: Constraint[];
  improvement: 'not_attempted' | 'completed' | 'rejected_worse' | 'incomplete' | 'skipped_budget' | 'disabled_reuse' | 'disabled_order_alternates';
  baseline: { planId: string; digest: string; preserved: boolean; reason: string; plan: LowCallRoute };
  selected: LowCallRoute;
  candidates: { orderEvaluation?: {
    plan?: LowCallRoute; feasibilityMessage?: string; policy?: OrderAlternatePolicy;
    effectivePolicy?: OrderAlternatePolicy;
    passHint?: { passId?: string; evidence?: { source: string; version: string }; reason?: string };
    alternate?: { order: string[]; passHintMatchedLegs?: number } | null;
  }; [key: string]: unknown };
  ledger: { railAttempts: number; stationAttempts: number; walkAttempts: number; ragRequests: number;
    totalRoutingOperationAttempts: number; budgetExhausted: boolean; limits: Record<string, number>; [key: string]: unknown };
  passComparison: null | { status: 'known' | 'partial' | 'unknown'; evaluations: Record<string, unknown>[];
    recommendation: null | { routeDigest: string; passId: string | null; total: number }; [key: string]: unknown };
  [key: string]: unknown;
};

export class LowCallApiError extends Error {
  readonly status: number;
  constructor(code: string, status = 0) { super(code); this.name = 'LowCallApiError'; this.status = status; }
}
function invalid(): never { throw new LowCallApiError('INVALID_V2_RESPONSE'); }
function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) invalid();
  return value as Record<string, unknown>;
}
function array(value: unknown): unknown[] { return Array.isArray(value) ? value : invalid(); }
function text(value: unknown): string { return typeof value === 'string' && value.trim() ? value : invalid(); }
function number(value: unknown): number { return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : invalid(); }
function integer(value: unknown): number { const n = number(value); return Number.isSafeInteger(n) ? n : invalid(); }
function nullableMoney(value: unknown): number | null { return value === null ? null : integer(value); }
function nullableCount(value: unknown, max: number): number | null {
  if (value === null) return null;
  const count = integer(value); return count <= max ? count : invalid();
}
function boolean(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid(); }
function member(value: unknown, values: readonly string[]): string { return values.includes(text(value)) ? value as string : invalid(); }
function timestamp(value: unknown): string {
  const s = text(value), match = /^(\d{4}-\d\d-\d\d)T(\d\d):(\d\d)(?::(\d\d)(?:\.\d{1,6})?)?(?:Z|[+-](\d\d):(\d\d))$/.exec(s);
  if (!match || !Number.isFinite(Date.parse(s)) || !Number.isFinite(Date.parse(match[1] + 'T00:00:00Z'))
      || +match[1].slice(0, 4) < 1 || +match[2] >= 24 || +match[3] >= 60 || +(match[4] || 0) >= 60 || +(match[5] || 0) >= 24 || +(match[6] || 0) >= 60
      || new Date(match[1] + 'T00:00:00Z').toISOString().slice(0, 10) !== match[1]) invalid();
  return s;
}
function micros(value: unknown): bigint {
  const s = timestamp(value), fraction = /\.(\d{1,6})/.exec(s)?.[1]?.padEnd(6, '0') || '000000';
  return BigInt(Date.parse(s)) * 1000n + BigInt(fraction.slice(3));
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value !== null && typeof value === 'object') return '{' + Object.entries(value).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, v]) => JSON.stringify(k) + ':' + canonical(v)).join(',') + '}';
  return JSON.stringify(value);
}
function constraints(value: unknown): void {
  array(value).forEach(v => {
    const c = object(v); text(c.kind); member(c.status, ['satisfied', 'violated', 'unknown', 'unsupported']);
    if (c.visitId !== null) text(c.visitId);
    if (c.reason !== null) text(c.reason);
  });
}
function walking(value: unknown): void {
  const walk = object(value), status = member(walk.status, ['routed', 'estimated', 'unknown']);
  if (status === 'unknown') { if (walk.minutes !== null) invalid(); } else integer(walk.minutes);
  const evidence = object(walk.evidence); text(evidence.source); text(evidence.version);
}
function route(value: unknown): LowCallRoute {
  const r = object(value), order = array(r.order).map(text), visits = array(r.visits).map(object), legs = array(r.legs).map(object);
  text(r.planId); text(r.digest); member(r.completion, ['complete', 'partial', 'failed']); member(r.timing, ['timetable', 'reference', 'unknown']);
  member(r.accessWalking, ['routed', 'estimated', 'unknown']); constraints(r.constraints);
  if (order.length < 1 || order.length > 8 || new Set(order).size !== order.length || visits.length > order.length || legs.length >= order.length) invalid();
  if (r.completion === 'complete' && (visits.length !== order.length || legs.length !== order.length - 1)) invalid();
  visits.forEach((v, i) => {
    if (text(v.clientPlaceId) !== order[i]) invalid();
    timestamp(v.arrivalAt); timestamp(v.visitStartAt); timestamp(v.leaveAt); integer(v.stayMinutes); number(v.visitWaitMinutes);
    boolean(v.arrivalClockExact); boolean(v.leaveClockExact);
    if (micros(v.arrivalAt) > micros(v.visitStartAt) || micros(v.leaveAt) - micros(v.visitStartAt) !== BigInt(v.stayMinutes as number) * 60_000_000n) invalid();
  });
  const segmentIds: string[] = [], unitIds: string[] = [], covered: string[] = []; let quotedTotal = 0, allFaresKnown = true;
  legs.forEach((leg, i) => {
    if (leg.from !== order[i] || leg.to !== order[i + 1]) invalid();
    const prepared = micros(leg.preparedAt), departed = micros(leg.departureAt), arrived = micros(leg.arrivalAt);
    if (prepared < micros(visits[i]!.leaveAt) || departed < prepared || arrived < departed) invalid();
    if (timestamp(leg.requestedAt) !== leg.preparedAt) invalid();
    if (Math.abs(number(leg.initialWaitMinutes) - Number(departed - prepared) / 60_000_000) > 1e-9
        || Math.abs(number(leg.rideMinutes) - Number(arrived - departed) / 60_000_000) > 1e-9) invalid();
    walking(leg.accessWalking); walking(leg.egressWalking); nullableCount(leg.transfers, 20); boolean(leg.samePhysicalStation);
    if (leg.placeArrivalAt !== null) {
      const placeArrival = timestamp(leg.placeArrivalAt);
      if (micros(placeArrival) < arrived || (visits[i+1] && visits[i+1]!.arrivalAt !== placeArrival)) invalid();
    } else if (r.completion === 'complete') invalid();
    if (leg.steps !== undefined) {
      member(leg.busStatus, ['not_checked', 'checked', 'unknown', 'no_service']);
      if (leg.transitLink !== null) {
        if (!text(leg.transitLink).startsWith('https://www.google.com/maps/dir/?')) invalid();
      } else if (leg.busStatus === 'unknown') invalid();
      array(leg.segments).map(object).forEach(segment => member(segment.mode, ['rail', 'bus']));
      const steps = array(leg.steps).map(object);
      if (!steps.length || leg.placeArrivalAt === null) invalid();
      let clock = micros(visits[i]!.leaveAt);
      for (const step of steps) {
        const mode = member(step.mode, ['walk', 'wait', 'bus', 'rail']);
        const start = micros(step.startAt), end = micros(step.endAt);
        if (start !== clock || end < start) invalid();
        if (mode === 'walk') {
          walking(step);
          if (step.status === 'unknown' || end - start !== BigInt(step.minutes as number) * 60_000_000n) invalid();
          if (r.timing === 'timetable' && step.status !== 'routed') invalid();
        }
        if (mode === 'bus') {
          text(step.line); nullableMoney(step.fareYen);
          for (const stop of [object(step.boarding), object(step.alighting)]) {
            text(stop.stopId); text(stop.operator); text(stop.name);
          }
        }
        clock = end;
      }
      if (clock !== micros(leg.placeArrivalAt)) invalid();
    }
    segmentIds.push(...array(leg.rideSegmentIds).map(text));
    const basis = member(leg.fareBasis, ['independent_journey', 'unknown']);
    if (basis === 'unknown') allFaresKnown = false;
    array(leg.fareUnits).map(object).forEach(unit => {
      unitIds.push(text(unit.fareUnitId)); covered.push(...array(unit.rideSegmentIds).map(text));
      const base = nullableMoney(unit.baseFare), supplement = nullableMoney(unit.supplement);
      if (unit.currency !== 'JPY') invalid();
      const gross = unit.quotedTotal === undefined ? null : nullableMoney(unit.quotedTotal);
      if (gross !== null && ((base !== null && base > gross) || (supplement !== null && supplement > gross)
          || (base !== null && supplement !== null && base + supplement !== gross))) invalid();
      if (gross !== null) quotedTotal += gross;
      else if (base === null || supplement === null) allFaresKnown = false;
      else quotedTotal += base + supplement;
    });
  });
  if (new Set(segmentIds).size !== segmentIds.length || new Set(unitIds).size !== unitIds.length || new Set(covered).size !== covered.length || covered.some(id => !segmentIds.includes(id))) invalid();
  if (covered.length !== segmentIds.length) allFaresKnown = false;
  const fare = object(r.fare), status = member(fare.status, ['known', 'partial', 'unknown']), total = nullableMoney(fare.noPassTotal);
  if (canonical(array(fare.fareUnits)) !== canonical(legs.flatMap(leg => array(leg.fareUnits)))) invalid();
  if (status === 'known' && (!allFaresKnown || r.completion !== 'complete' || total !== quotedTotal)) invalid();
  if (status !== 'known' && total !== null) invalid();
  if (r.completion === 'complete') { timestamp(r.finishAt); number(r.elapsedMinutes); }
  else if (r.finishAt !== null || total !== null) invalid();
  if (r.timing === 'timetable' && (visits.some(v => !v.arrivalClockExact || !v.leaveClockExact)
      || (r.constraints as Constraint[]).some(c => c.status === 'unknown')
      || legs.some(leg => object(leg.accessWalking).status !== 'routed' || object(leg.egressWalking).status !== 'routed'))) invalid();
  return r as LowCallRoute;
}

export function parseLowCallResponse(value: unknown, expectedInput?: LowCallPlanRequest): LowCallPlanResponse {
  const r = object(value);
  const keys = ['inputVersion', 'dataSource', 'fixtureId', 'fixtureLabel', 'echoInput', 'completion', 'timing', 'accessWalking', 'pricing', 'constraints',
    'improvement', 'repairLog', 'candidates', 'baseline', 'selected', 'passComparison', 'stopReasons', 'comparisonScope', 'optimality', 'ledger'];
  if (Object.keys(r).some(key => !keys.includes(key) && key !== 'replayProvenance') || keys.some(key => !(key in r)) || r.inputVersion !== '2' || !['fixture', 'replay'].includes(r.dataSource as string)) invalid();
  if (r.dataSource === 'replay') {
    const provenance = object(r.replayProvenance);
    member(provenance.kind, ['legacy-team-crawler', 'captured-team-crawler', 'captured-public-dom', 'test-only']);
    text(provenance.sourceArtifact); text(provenance.notice);
    if (!/^[a-f0-9]{64}$/.test(text(provenance.sourceSha256))) invalid();
    if (provenance.kind === 'test-only' && r.fixtureLabel !== '합성 자료 재생 (실데이터 검증 아님)') invalid();
  } else if ('replayProvenance' in r) invalid();
  text(r.fixtureId); text(r.fixtureLabel); member(r.pricing, ['known', 'partial', 'unknown']);
  member(r.improvement, ['not_attempted', 'completed', 'rejected_worse', 'incomplete', 'skipped_budget', 'disabled_reuse', 'disabled_order_alternates']);
  array(r.repairLog); array(r.stopReasons).map(text); object(r.candidates); constraints(r.constraints);
  if (r.optimality !== 'not-guaranteed' || r.comparisonScope !== 'completed-candidates-only') invalid();
  const echo = object(r.echoInput);
  if (r.dataSource !== (echo.dataSource ?? 'fixture') || r.fixtureId !== (echo.fixtureId ?? 'tokyo-demo')) invalid();
  if (expectedInput && canonical(echo) !== canonical(expectedInput)) throw new LowCallApiError('STALE_V2_INPUT');
  const selected = route(r.selected), baseline = object(r.baseline), original = route(baseline.plan);
  if (text(baseline.digest) !== original.digest || text(baseline.planId) !== original.planId || boolean(baseline.preserved) !== (original.completion === 'complete')) invalid();
  text(baseline.reason);
  if (r.completion !== selected.completion || r.timing !== selected.timing || r.accessWalking !== selected.accessWalking || canonical(r.constraints) !== canonical(selected.constraints)) invalid();
  const places = array(echo.places).map(object), ids = places.map(p => text(p.clientPlaceId));
  if (ids.length !== selected.order.length || new Set(ids).size !== ids.length || ids.some(id => !selected.order.includes(id)) || original.order.some(id => !ids.includes(id))) invalid();
  for (const p of places) {
    if (p.anchorRole === 'start' && selected.order[0] !== p.clientPlaceId) invalid();
    if (p.anchorRole === 'end' && selected.order.at(-1) !== p.clientPlaceId) invalid();
  }
  const orderEvaluation = object(r.candidates).orderEvaluation;
  const comparisonRoutes = [selected, original];
  if (orderEvaluation !== undefined) {
    const audit = object(orderEvaluation);
    const options = object(echo.options);
    if (options.allowOrderAlternates !== true) invalid();
    if (options.orderAlternatePolicy === 'pass_hint') {
      if (audit.policy !== 'pass_hint') invalid();
      const effective = member(audit.effectivePolicy, ['pass_hint', 'diverse', 'deadline_first']);
      const hint = object(audit.passHint);
      if (effective === 'pass_hint' || hint.passId !== undefined) text(hint.passId);
      if (effective === 'pass_hint' || hint.evidence !== undefined) {
        const evidence = object(hint.evidence);
        text(evidence.source); text(evidence.version);
      }
      if (effective === 'pass_hint') {
        const proposal = object(audit.alternate);
        const matches = integer(proposal.passHintMatchedLegs);
        if (matches < 1 || matches >= ids.length) invalid();
      } else text(hint.reason);
    }
    if (audit.feasibilityMessage !== undefined) text(audit.feasibilityMessage);
    if (audit.plan !== undefined) {
      const alternative = route(audit.plan), proposal = object(audit.alternate);
      if (canonical(alternative.order) !== canonical(proposal.order) || alternative.order.length !== ids.length
          || ids.some(id => !alternative.order.includes(id)) || canonical(alternative.order) === canonical(original.order)) invalid();
      for (const p of places) {
        if (p.anchorRole === 'start' && alternative.order[0] !== p.clientPlaceId) invalid();
        if (p.anchorRole === 'end' && alternative.order.at(-1) !== p.clientPlaceId) invalid();
      }
      const existing = comparisonRoutes.find(candidate => candidate.digest === alternative.digest);
      if (existing && canonical(existing) !== canonical(alternative)) invalid();
      comparisonRoutes.push(alternative);
    }
  }
  const eligible = comparisonRoutes.filter(candidate => candidate.completion === 'complete'
    && !candidate.constraints.some(check => check.status === 'violated' || check.status === 'unsupported'));
  const ledger = object(r.ledger), caps = object(ledger.limits), attempts = array(ledger.attempts).map(object);
  const multimodal = echo.options !== undefined && object(echo.options).allowMultimodal === true;
  if (multimodal && (integer(ledger.busAttempts) > 2
      || integer(ledger.busAttempts) + integer(ledger.railOnlyAttempts) !== ledger.railAttempts)) invalid();
  const kinds = ['rail', 'station', 'walk', 'rag'], fields = ['railAttempts', 'stationAttempts', 'walkAttempts', 'ragRequests'];
  attempts.forEach(a => { integer(a.id); member(a.kind, kinds); member(a.phase, ['baseline', 'repair', 'pricing']); member(a.outcome, ['ok', 'failed', 'dispatched']); if (a.error !== null) text(a.error); });
  if (new Set(attempts.map(a => a.id)).size !== attempts.length) invalid();
  kinds.forEach((kind, i) => {
    if (integer(ledger[fields[i]!]) !== attempts.filter(a => a.kind === kind).length || integer(ledger[fields[i]!]) > integer(caps[kind])) invalid();
    const usage = object(object(ledger.sessionUsage)[kind]);
    if (integer(usage.used) < integer(ledger[fields[i]!]) || integer(usage.used) > integer(usage.limit)) invalid();
  });
  if (integer(caps.rail) > ids.length-1 + Math.min(3, ids.length-1) || integer(caps.station) !== ids.length || caps.walk !== 0 || caps.rag !== 1) invalid();
  if (ledger.logicalAttempts !== ledger.railAttempts || ledger.totalRoutingOperationAttempts !== (ledger.railAttempts as number) + (ledger.stationAttempts as number) + (ledger.walkAttempts as number)) invalid();
  if (integer(ledger.baselineAttempts) !== attempts.filter(a => a.phase === 'baseline').length || integer(ledger.repairAttempts) !== attempts.filter(a => a.phase === 'repair').length
      || integer(ledger.failedAttempts) !== attempts.filter(a => a.outcome === 'failed').length) invalid();
  integer(ledger.cacheHits); integer(ledger.sharedRequests); boolean(ledger.budgetExhausted); array(ledger.stopReasons).map(text);
  if (ledger.measurementBasis !== r.dataSource + '-no-network' || ledger.providerCalls !== 0 || ledger.httpRequestsObserved !== 0 || ledger.billingUnitsObserved !== 0) invalid();
  if (r.passComparison !== null) {
    const comparison = object(r.passComparison); member(comparison.status, ['known', 'partial', 'unknown']);
    const evaluations = array(comparison.evaluations).map(object);
    evaluations.forEach(row => {
      member(row.status, ['known', 'unknown']); nullableMoney(row.total);
      if (row.status === 'known' && (row.total === null
          || !eligible.some(candidate => candidate.digest === row.routeDigest))) invalid();
      if (row.status !== 'known' && row.total !== null) invalid();
    });
    if (comparison.recommendation !== null) {
      const rec = object(comparison.recommendation), matching = eligible.find(route => route.digest === text(rec.routeDigest));
      if (!matching) invalid();
      const total = integer(rec.total);
      if (rec.passId === null) { if (total !== matching.fare.noPassTotal) invalid(); }
      else if (!evaluations.some(row => row.passId === text(rec.passId) && row.routeDigest === matching.digest && row.status === 'known' && row.total === total)) invalid();
    }
  }
  return structuredClone(r) as LowCallPlanResponse;
}

async function post(baseUrl: string, path: string, body: unknown, fetchImpl: typeof fetch, signal?: AbortSignal): Promise<unknown> {
  const response = await fetchImpl(baseUrl.replace(/\/$/, '') + path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  let raw: unknown;
  try { raw = await response.json(); } catch { throw new LowCallApiError('INVALID_V2_JSON', response.status); }
  if (!response.ok) {
    const error = object(raw); throw new LowCallApiError(typeof error.error === 'string' ? error.error : 'V2_REQUEST_FAILED', response.status);
  }
  return raw;
}

export async function createLowCallSession(baseUrl = '', fetchImpl: typeof fetch = fetch): Promise<string> {
  const row = object(await post(baseUrl, '/api/v2/sessions', {}, fetchImpl));
  if (!['fixture', 'replay'].includes(row.dataSource as string)) invalid();
  return text(row.sessionId);
}

export async function requestLowCallPlan(body: LowCallPlanRequest, baseUrl = '', fetchImpl: typeof fetch = fetch, signal?: AbortSignal): Promise<LowCallPlanResponse> {
  const snapshot = structuredClone(body);
  return parseLowCallResponse(await post(baseUrl, '/api/v2/routes/plan', snapshot, fetchImpl, signal), snapshot);
}

export function lowCallSummary(value: LowCallPlanResponse) {
  return {
    source: value.dataSource === 'replay' ? value.replayProvenance?.kind === 'test-only' ? '합성 자료 재생 · 실데이터 검증 아님' : '저장된 응답 재생 · 실시간 아님' : '가상 자료 시험',
    timing: value.timing === 'timetable' ? '시간표 기준' : value.timing === 'reference' ? '도보·영업시간 가정이 포함된 참고 일정' : '이동 시각 미확인',
    completion: value.completion === 'complete' ? '모든 구간 평가 완료' : '일부 구간 미평가',
    feasibility: value.candidates.orderEvaluation?.feasibilityMessage ?? null,
    railAttempts: `${value.ledger.railAttempts}/${value.ledger.limits.rail}`,
    noPassFare: value.selected.fare.noPassTotal === null ? '운임 미확인' : `${value.selected.fare.noPassTotal.toLocaleString('ko-KR')}엔`,
    baselinePreserved: value.baseline.preserved,
  };
}
