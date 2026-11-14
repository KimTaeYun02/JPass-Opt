import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, unlinkSync, rmdirSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { optimizeVisitOrder } from '../src/route-optimizer.ts';
import { buildCandidateMatrix, rankCandidates, referenceSchedule } from '../src/route-candidates.ts';
import { QuoteSession, refreshCandidate } from '../src/route-provider.ts';

const fixture = JSON.parse(await readFile(new URL('../fixtures/notion-visits.json', import.meta.url), 'utf8'));
const quotesFor = dataset => dataset.snapshots.map(({ from, to, departure, arrival, time, fee, route }) => ({
  from, to, stations: { from: departure, to: arrival }, source: 'snapshot', result: { time, fee, route },
}));
// These numbers are test costs, not Japan travel estimates.
const testEstimates = places => places.flatMap((from, i) => places.filter(to => to.id !== from.id).map(to => ({ from: from.id, to: to.id, time: 10 + i, fee: 100 })));
const makeProvider = (reuseAllowed = true) => ({ id: 'test-only', reuseAllowed, quote: async request => ({
  time: 10, fee: 100, route: [{ station: request.stations.from, line: '徒歩' }, { station: request.stations.to, line: null }],
}) });
const request = (departureAt = '2026-10-17T11:00:00+09:00', options = {}) => ({
  from: 'a', to: 'b', stations: { from: 'A', to: 'B' }, departureAt, options,
});

test('Notion datasets preserve observations and expose exactly 16 and 9 missing place pairs', () => {
  for (const [i, dataset] of fixture.datasets.entries()) {
    const matrix = buildCandidateMatrix(dataset.places, quotesFor(dataset), []);
    assert.equal(matrix.quotes.length, i === 0 ? 4 : 3);
    assert.equal(matrix.missing.length, i === 0 ? 16 : 9);
    assert.deepEqual(matrix.unresolved, matrix.missing);
    assert.throws(() => rankCandidates(dataset.places, matrix.quotes), /완전한/);
    const original = quotesFor(dataset);
    assert.equal(original.reduce((n, q) => n + q.result.time, 0), i === 0 ? 242 : 38);
    assert.equal(original.reduce((n, q) => n + q.result.fee, 0), i === 0 ? 3971 : 980);
  }
});

test('full explicitly estimated matrices use DP and preserve source grades', () => {
  for (const [i, dataset] of fixture.datasets.entries()) {
    const matrix = buildCandidateMatrix(dataset.places, quotesFor(dataset), testEstimates(dataset.places));
    const candidates = rankCandidates(dataset.places, matrix.quotes);
    assert.equal(candidates.length, 3);
    assert.equal(candidates[0].evaluatedOrders, 0);
    assert.equal(candidates[0].algorithm, 'held-karp');
    assert.equal(candidates[0].dpStates, i === 0 ? 80 : 32);
    assert.equal(candidates[0].dpTransitions, i === 0 ? 160 : 48);
    const optimum = optimizeVisitOrder(dataset.places.map(p => p.id), matrix.quotes);
    assert.deepEqual(candidates[0].order, optimum.order);
    assert.equal(candidates[0].time, optimum.time);
    assert.equal(candidates[0].grade, 'candidate');
    assert.ok(candidates[0].estimatedLegs.length > 0);
    assert.equal(matrix.missing.length, i === 0 ? 16 : 9);
    assert.deepEqual(candidates, rankCandidates([...dataset.places].reverse(), matrix.quotes));
  }
});

test('same-station attractions remain distinct and carry nonzero explicit walking estimates', () => {
  const dataset = fixture.datasets[1];
  const matrix = buildCandidateMatrix(dataset.places, quotesFor(dataset), testEstimates(dataset.places));
  const sameStation = matrix.quotes.find(q => q.from === 'skytree' && q.to === 'aquarium');
  assert.equal(sameStation.stations.from, sameStation.stations.to);
  assert.ok(sameStation.result.time > 0);
  assert.equal(sameStation.source, 'estimate');
  const candidate = rankCandidates(dataset.places, matrix.quotes)[0];
  assert.ok(candidate.order.includes('skytree') && candidate.order.includes('aquarium'));
  const schedule = referenceSchedule(dataset.places, candidate, fixture.startAt);
  assert.equal(schedule.visits.length, 4);
  assert.equal(schedule.elapsedMinutes, 480 + candidate.time);
  assert.equal(schedule.departureTimeValidated, false);
  assert.equal(schedule.openingHours, 'unknown');
  assert.equal(schedule.visits[0].arrivalAt, '2026-10-17T00:00:00.000Z');
});

test('all-snapshot plans remain unverified and malformed input cannot silently fill gaps', () => {
  const dataset = fixture.datasets[0];
  const matrix = buildCandidateMatrix(dataset.places, quotesFor(dataset), testEstimates(dataset.places));
  const snapshots = matrix.quotes.map(q => ({ ...q, source: 'snapshot' }));
  assert.equal(rankCandidates(dataset.places, snapshots)[0].grade, 'unverified');
  assert.throws(() => buildCandidateMatrix(dataset.places, [quotesFor(dataset)[0], quotesFor(dataset)[0]], []), /중복/);
  assert.throws(() => buildCandidateMatrix(dataset.places, [], [{ from: 'nezu', to: 'meiji', time: 0, fee: 0 }]), /양수/);
  assert.throws(() => buildCandidateMatrix(dataset.places, [{ ...quotesFor(dataset)[0], source: 'invented' }], []), /유효하지/);
  assert.throws(() => rankCandidates(dataset.places, snapshots.map(q => { const copy = { ...q }; delete copy.source; return copy; })), /출처/);
  assert.throws(() => buildCandidateMatrix(dataset.places, [{ ...quotesFor(dataset)[0], stations: { from: 'wrong', to: 'wrong' } }], []), /유효하지/);
  assert.throws(() => referenceSchedule(dataset.places, rankCandidates(dataset.places, matrix.quotes)[0], '2026-10-17T09:00:00'), /시간대/);
});

test('expired quotes are fetched again and invalid requests consume no budget', async () => {
  const realNow = Date.now;
  let now = 1000;
  Date.now = () => now;
  try {
    const session = new QuoteSession(makeProvider(), 3, 100);
    await session.quote(request());
    now += 100;
    await session.quote(request());
    assert.equal(session.calls, 2);
    await assert.rejects(session.quote(request('2026-10-17T11:00:00')), /조건/);
    await assert.rejects(session.quote(request(undefined, { invalid: NaN })), /조건/);
    assert.equal(session.calls, 2);
  } finally { Date.now = realNow; }
});

test('exact normalized keys share in-flight requests and cached quotes across place IDs', async () => {
  let release;
  const provider = makeProvider();
  provider.quote = async req => { await new Promise(resolve => { release = resolve; }); return makeProvider().quote(req); };
  const session = new QuoteSession(provider, 1);
  const first = session.quote(request(undefined, { mode: 'transit', price: false }));
  const second = session.quote({ ...request('2026-10-17T02:00:00Z', { price: false, mode: 'transit' }), from: 'other-a', to: 'other-b' });
  await Promise.resolve();
  release();
  const [a, b] = await Promise.all([first, second]);
  assert.equal(session.calls, 1);
  assert.equal(a.status, 'ok'); assert.equal(b.quote.from, 'other-a');
  a.quote.result.route[0].station = 'mutated';
  const again = await session.quote(request(undefined, { mode: 'transit', price: false }));
  assert.equal(again.quote.result.route[0].station, 'A');
  assert.equal(session.calls, 1);
  assert.equal((await session.quote(request('2026-10-17T11:01:00+09:00'))).status, 'unknown');
});

test('budget zero does not call provider; errors, changes and opt-out do not reuse old quotes', async () => {
  let invoked = 0;
  const provider = makeProvider(false);
  const original = provider.quote;
  provider.quote = async req => { invoked++; return original(req); };
  assert.equal((await new QuoteSession(provider, 0).quote(request())).status, 'unknown');
  assert.equal(invoked, 0);
  const noCache = new QuoteSession(provider, 2);
  await noCache.quote(request()); await noCache.quote(request());
  assert.equal(invoked, 2);
  const cached = new QuoteSession(makeProvider(), 4);
  await cached.quote(request());
  await cached.quote(request('2026-10-18T11:00:00+09:00'));
  await cached.quote(request(undefined, { mode: 'walk' }));
  await cached.quote({ ...request(), stations: { from: 'A', to: 'C' } });
  assert.equal(cached.calls, 4);
  const failure = new QuoteSession({ ...makeProvider(), quote: async () => { throw new Error('crawler failed'); } }, 2);
  assert.equal((await failure.quote(request())).reason, 'crawler failed');
  await failure.quote(request());
  assert.equal(failure.calls, 2);
  const malformed = new QuoteSession({ ...makeProvider(), quote: async () => ({ time: null, fee: 0, route: [] }) }, 1);
  assert.equal((await malformed.quote(request())).status, 'unknown');
});

test('sequential refresh includes stays, reuses unchanged prefix and propagates an edited departure', async () => {
  const dataset = fixture.datasets[0];
  const matrix = buildCandidateMatrix(dataset.places, quotesFor(dataset), testEstimates(dataset.places));
  const plan = rankCandidates(dataset.places, matrix.quotes)[0];
  const requests = [];
  const provider = makeProvider(); const original = provider.quote;
  provider.quote = async req => { requests.push(req); return original(req); };
  const session = new QuoteSession(provider, 20);
  const first = await refreshCandidate(dataset.places, plan, fixture.startAt, session);
  assert.equal(first.status, 'unverified'); assert.equal(first.addedCalls, 4);
  assert.equal(first.movementMinutes, 40); assert.equal(first.optimality, 'not-re-evaluated');
  assert.equal(requests[0].departureAt, '2026-10-17T02:00:00.000Z');
  assert.equal(requests[1].departureAt, '2026-10-17T04:10:00.000Z');
  assert.equal(first.finishAt, '2026-10-17T10:40:00.000Z');
  const unchanged = await refreshCandidate(dataset.places, plan, fixture.startAt, session);
  assert.equal(unchanged.addedCalls, 0);
  const edited = structuredClone(dataset.places);
  edited.find(p => p.id === plan.order[1]).stayMinutes += 30;
  const changed = await refreshCandidate(edited, plan, fixture.startAt, session);
  assert.equal(changed.addedCalls, 3);
  assert.equal(changed.finishAt, '2026-10-17T11:10:00.000Z');
});

test('partial refresh stops at unknown instead of propagating fabricated zero travel', async () => {
  const dataset = fixture.datasets[0];
  const matrix = buildCandidateMatrix(dataset.places, quotesFor(dataset), testEstimates(dataset.places));
  const plan = rankCandidates(dataset.places, matrix.quotes)[0];
  const result = await refreshCandidate(dataset.places, plan, fixture.startAt, new QuoteSession(makeProvider(), 1));
  assert.equal(result.status, 'unknown');
  assert.equal(result.legs.length, 1);
  assert.equal(result.addedCalls, 1);
  assert.ok(!('finishAt' in result));
});

const webRoot = fileURLToPath(new URL('..', import.meta.url));
const runDemo = args => JSON.parse(execFileSync(process.execPath, ['scripts/demo-notion.mjs', ...args], { cwd: webRoot, encoding: 'utf8', stdio: 'pipe' }));

test('default CLI reports actual baseline schedules without provider calls or fabricated candidates', () => {
  const report = runDemo([]);
  assert.equal(report.assumptions.providerCalls, 0);
  assert.deepEqual(report.reports.map(r => r.status), ['needs-travel-data', 'needs-travel-data']);
  assert.deepEqual(report.reports.map(r => r.candidates.length), [0, 0]);
  assert.deepEqual(report.reports.map(r => r.referenceSchedule.elapsedMinutes), [842, 518]);
  assert.deepEqual(report.reports.map(r => r.refreshed.addedCalls), [0, 0]);
});

test('CLI adapter import shares a bounded budget across both datasets and rejects malformed estimates', () => {
  const dir = mkdtempSync(join(tmpdir(), 'jr-pass-opt-cli-'));
  const estimateFile = join(dir, 'estimates.json'); const providerFile = join(dir, 'provider.mjs');
  try {
    writeFileSync(estimateFile, JSON.stringify(Object.fromEntries(fixture.datasets.map(d => [d.id, testEstimates(d.places)]))));
    writeFileSync(providerFile, "export default {id:'test-only',reuseAllowed:true,async quote(r){return {time:10,fee:100,route:[{station:r.stations.from,line:'TEST WALK'},{station:r.stations.to,line:null}]}}};");
    const report = runDemo(['--estimates', estimateFile, '--provider', providerFile, '--max-calls', '2']);
    assert.deepEqual(report.reports.map(r => r.candidates[0].evaluatedOrders), [0, 0]);
    assert.deepEqual(report.reports.map(r => r.candidates[0].dpStates), [80, 32]);
    assert.equal(report.assumptions.providerCalls, 2);
    assert.deepEqual(report.reports.map(r => r.refreshed.status), ['unknown', 'unknown']);
    assert.deepEqual(report.reports.map(r => r.refreshed.addedCalls), [2, 0]);
    writeFileSync(estimateFile, '[]');
    assert.throws(() => runDemo(['--estimates', estimateFile]), /dataset ID/);
    assert.throws(() => runDemo(['--max-calls', '-1']), /호출 예산/);
  } finally {
    unlinkSync(estimateFile); unlinkSync(providerFile); rmdirSync(dir);
  }
});
