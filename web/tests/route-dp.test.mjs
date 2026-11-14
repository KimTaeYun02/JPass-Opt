import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { MAX_ROUTE_STOPS, optimizeVisitOrder, optimizeVisitOrders } from '../src/route-optimizer.ts';
import { rankCandidates } from '../src/route-candidates.ts';

const quotesFor = (ids, cost) => ids.flatMap(from => ids.filter(to => to !== from).map(to => {
  const [time, fee] = cost(from, to);
  return { from, to, source: 'snapshot', result: { time, fee,
    route: [{ station: from, line: '테스트 노선' }, { station: to, line: null }] } };
}));
const visitsFor = ids => ids.map(id => ({ id, name: id, station: id, stayMinutes: 0 }));
// Direct complete permutations, not a DP or a call to the production core.
const bruteTop = (ids, quotes, limit) => {
  const permute = remaining => remaining.length ? remaining.flatMap((id, i) =>
    permute(remaining.filter((_, j) => i !== j)).map(tail => [id, ...tail])) : [[]];
  return permute(ids).map(order => {
    const legs = order.slice(1).map((to, i) => quotes.find(q => q.from === order[i] && q.to === to));
    return { order, time: legs.reduce((s, q) => s + q.result.time, 0), fee: legs.reduce((s, q) => s + q.result.fee, 0) };
  }).sort((a, b) => {
    if (a.time !== b.time) return a.time < b.time ? -1 : 1;
    if (a.fee !== b.fee) return a.fee < b.fee ? -1 : 1;
    for (let i = 0; i < a.order.length; i++) if (a.order[i] !== b.order[i]) return a.order[i] < b.order[i] ? -1 : 1;
    return 0;
  }).slice(0, limit);
};
const score = ({ order, time, fee }) => ({ order, time, fee });

test('top 1, 2 and 3 DP paths match full permutations on 100 seeded asymmetric and tie-heavy matrices', () => {
  let seed = 0x4123f1;
  const random = max => { seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5; return (seed >>> 0) % max; };
  for (let sample = 0; sample < 100; sample++) {
    const ids = Array.from({ length: 2 + sample % 5 }, (_, i) => `place-${i}`);
    const range = sample % 3 === 0 ? 1 : sample % 3 === 1 ? 3 : 500;
    const quotes = quotesFor(ids, () => [random(range), random(range)]);
    for (const limit of [1, 2, 3]) {
      const expected = bruteTop(ids, quotes, limit);
      const plans = optimizeVisitOrders(ids, quotes, limit);
      assert.deepEqual(plans.map(score), expected, `sample=${sample} limit=${limit}`);
      assert.deepEqual(rankCandidates(visitsFor(ids), quotes, limit).map(score), expected);
      for (const plan of plans) {
        assert.equal(plan.evaluatedOrders, 0);
        assert.equal(plan.algorithm, 'held-karp');
        assert.equal(plan.dpStates, ids.length * 2 ** (ids.length - 1));
        assert.equal(plan.dpTransitions, ids.length * (ids.length - 1) * 2 ** (ids.length - 2));
      }
    }
  }
});

test('tied Unicode and prefix IDs preserve code-unit order and input-order independence', () => {
  const ids = ['가', 'a', 'a-a', 'B', '\uE000', '\u{10000}'];
  const quotes = quotesFor(ids, () => [0, 0]);
  const expected = bruteTop(ids, quotes, 3);
  assert.deepEqual(optimizeVisitOrders(ids, quotes).map(score), expected);
  assert.deepEqual(optimizeVisitOrders([...ids].reverse(), quotes).map(score), expected);
});

test('raw real data returns the same top three as complete enumeration without an initial waiting adjustment', async () => {
  const raw = await readFile(new URL('../fixtures/ekispert-browser-five-stops.json', import.meta.url), 'utf8');
  const fixture = JSON.parse(raw);
  const ids = fixture.places.map(p => p.station);
  const quotes = fixture.snapshots.map(r => ({ from: r.fromStation, to: r.toStation, source: 'snapshot', result: r.result }));
  const plans = optimizeVisitOrders(ids, quotes);
  assert.deepEqual(plans.map(score), bruteTop(ids, quotes, 3));
  assert.deepEqual(plans.map(p => [p.time, p.fee]), [[143, 2374], [143, 2561], [154, 2572]]);
  assert.deepEqual(plans[0].order, ['テレコムセンター', '東大前', '後楽園', '参宮橋', '片瀬江ノ島']);
  assert.equal(await readFile(new URL('../fixtures/ekispert-browser-five-stops.json', import.meta.url), 'utf8'), raw);
});

test('overflow in a discarded expensive path remains an error for time and fare separately', () => {
  const ids = ['A', 'B', 'C', 'D'];
  for (const dimension of [0, 1]) {
    const quotes = quotesFor(ids, (from, to) => {
      const values = [0, 0];
      if (from === 'A' && to === 'B') values[dimension] = Number.MAX_SAFE_INTEGER;
      if (from === 'B' && to === 'C') values[dimension] = 1;
      return values;
    });
    assert.throws(() => optimizeVisitOrder(ids, quotes), /안전한 정수/);
    assert.throws(() => rankCandidates(visitsFor(ids), quotes), /안전한 정수/);
  }
});

test('exact safe boundary is accepted and a large isolated edge does not cause a conservative false rejection', () => {
  const ids = ['A', 'B', 'C'];
  for (const dimension of [0, 1]) {
    const quotes = quotesFor(ids, (from, to) => {
      const values = [0, 0];
      if (from === 'A' && to === 'B') values[dimension] = Number.MAX_SAFE_INTEGER;
      return values;
    });
    assert.deepEqual(score(optimizeVisitOrder(ids, quotes)), bruteTop(ids, quotes, 1)[0]);
  }
  const boundary = quotesFor(['A', 'B'], () => [Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER]);
  assert.equal(optimizeVisitOrder(['A', 'B'], boundary).time, Number.MAX_SAFE_INTEGER);
});

test('two places yield two paths at most; limits and existing place bounds are enforced', () => {
  const ids = ['A', 'B']; const quotes = quotesFor(ids, () => [1, 1]);
  assert.equal(optimizeVisitOrders(ids, quotes).length, 2);
  assert.equal(rankCandidates(visitsFor(ids), quotes).length, 2);
  for (const limit of [0, 4, 1.5, NaN]) assert.throws(() => optimizeVisitOrders(ids, quotes, limit), /후보 수/);
  assert.equal(MAX_ROUTE_STOPS, 8);
  assert.throws(() => optimizeVisitOrder(['A'], []), /2~8/);
  assert.throws(() => optimizeVisitOrder(Array.from({ length: 9 }, (_, i) => String(i)), []), /2~8/);
  const eight = Array.from({ length: 8 }, (_, i) => String.fromCharCode(65 + i));
  const matrix = quotesFor(eight, (from, to) => [to.charCodeAt(0) === from.charCodeAt(0) + 1 ? 0 : 9, 0]);
  const result = optimizeVisitOrder(eight, matrix);
  assert.deepEqual(result.order, eight);
  assert.equal(result.dpStates, 1024);
  assert.equal(result.dpTransitions, 3584);
});

test('frozen quotes and shared stations retain metadata and independently cloned legs', () => {
  const ids = ['a', 'b', 'c'];
  const quotes = quotesFor(ids, () => [1, 0]).map(q => ({ ...q,
    stations: { from: '공용역', to: '공용역' },
    result: { ...q.result, route: [{ station: '공용역', line: '徒歩' }, { station: '공용역', line: null }] },
  }));
  const freeze = value => { if (value && typeof value === 'object') { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };
  freeze(ids); freeze(quotes);
  const plans = optimizeVisitOrders(ids, quotes);
  for (const plan of plans) for (const leg of plan.legs) {
    const original = quotes.find(q => q.from === leg.from && q.to === leg.to);
    assert.deepEqual(leg, original);
    assert.notEqual(leg.result.route, original.result.route);
  }
  plans[0].legs[0].result.route[0].line = '변경';
  assert.equal(plans[1].legs[0].result.route[0].line, '徒歩');
  assert.equal(optimizeVisitOrder(ids, quotes).legs[0].result.route[0].line, '徒歩');
});

test('changing attraction stays changes no candidate order or cost and the oracle imports no production solver', async () => {
  const ids = ['A', 'B', 'C']; const quotes = quotesFor(ids, () => [3, 20]);
  const visits = visitsFor(ids);
  assert.deepEqual(rankCandidates(visits, quotes), rankCandidates(visits.map((p, i) => ({ ...p, stayMinutes: i * 1000 })), quotes));
  const oracle = await readFile(new URL('../scripts/route-verification.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(oracle, /import.*(?:route-dp|route-optimizer)/);
});

test('offline browser demo and verification surface raw-time DP and keep old timed observations historical', () => {
  const cwd = fileURLToPath(new URL('..', import.meta.url));
  const demo = JSON.parse(execFileSync(process.execPath, ['scripts/demo-browser-route.mjs'], { cwd, encoding: 'utf8' }));
  assert.equal(demo.staticMinutes, 143);
  assert.equal(demo.algorithm, 'held-karp');
  assert.equal(demo.evaluatedOrders, 0);
  const verification = JSON.parse(execFileSync(process.execPath, ['scripts/verify-route.mjs'], { cwd, encoding: 'utf8' }));
  assert.equal(verification.externalRouteRequests, 0);
  assert.deepEqual([verification.results[0].minutes, verification.results[0].fee], [143, 2374]);
  assert.equal(verification.results[0].evaluatedOrders, 0);
  assert.equal(verification.results[0].enumeratedOrders, 120);
  for (const row of verification.weighted) assert.deepEqual([row.minutes, row.fee], [143, 2374]);
});
