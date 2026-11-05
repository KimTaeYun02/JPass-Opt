import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { optimizeVisitOrder, validateQuote } from '../src/route-optimizer.ts';
import { auditOrder, crossCheck, enumerateAllOrders, heldKarpBest } from '../scripts/route-verification.mjs';

const real = JSON.parse(await readFile(new URL('../fixtures/ekispert-browser-five-stops.json', import.meta.url), 'utf8'));
const synthetic = JSON.parse(await readFile(new URL('../fixtures/route-five-stops.json', import.meta.url), 'utf8'));
const costForQuotes = quotes => (from, to) => {
  const q = quotes.find(q => q.from === from && q.to === to);
  if (!q) throw new Error('Missing directed edge');
  return [q.result.time, q.result.fee];
};
const makeQuotes = (places, cost) => places.flatMap(from => places.filter(to => from !== to).map(to => {
  const [time, fee] = cost(from, to);
  return { from, to, result: { time, fee, route: [{ station: from, line: '테스트' }, { station: to, line: null }] } };
}));
const weighted = (cost, timeWeight, feeWeight) => (from, to) => {
  const [time, fee] = cost(from, to);
  return [timeWeight * time + feeWeight * fee, time, fee];
};

test('raw crawler matrix: independent algorithms reproduce the unique 143 minute and 2374 yen path', () => {
  const before = structuredClone(real);
  const places = real.places.map(p => p.station);
  const quotes = real.snapshots.map(r => ({ from: r.fromStation, to: r.toStation, result: r.result }));
  const result = crossCheck(places, costForQuotes(quotes));
  const plan = optimizeVisitOrder(places, quotes);
  assert.deepEqual(result.best, { order: ['テレコムセンター', '東大前', '後楽園', '参宮橋', '片瀬江ノ島'], keys: [143, 2374] });
  assert.deepEqual(plan.order, result.best.order);
  assert.equal(result.optimalCount, 1);
  assert.equal(result.evaluated, 120);
  assert.equal(result.dpStates, 80);
  assert.equal(result.dpTransitions, 160);
  const rawTime = plan.order.slice(1).reduce((sum, to, i) => sum + real.snapshots.find(r => r.fromStation === plan.order[i] && r.toStation === to).result.time, 0);
  assert.equal(rawTime, 143);
  assert.deepEqual(real, before);
});

test('browser costs are independently reconstructed from clocks, with endpoint and pair validation', () => {
  const minutes = clock => Number(clock.slice(0, 2)) * 60 + Number(clock.slice(3, 5));
  const readyClock = new Date(Date.parse(real.snapshotSearchMinuteAt) + 9 * 60 * 60_000).toISOString().slice(11, 16);
  const pairs = new Set();
  for (const r of real.snapshots) {
    validateQuote({ from: r.fromStation, to: r.toStation, result: r.result });
    assert.equal((minutes(r.actualArrival) - minutes(r.actualDeparture) + 1440) % 1440, r.result.time);
    assert.equal((minutes(r.actualArrival) - minutes(readyClock) + 1440) % 1440, r.readyToArrivalMinutes);
    const pair = JSON.stringify([r.fromStation, r.toStation]);
    assert.ok(!pairs.has(pair));
    pairs.add(pair);
  }
  assert.equal(pairs.size, real.places.length * (real.places.length - 1));
});

test('synthetic fixture agrees with production, independent DP, and direct permutation evaluation', () => {
  const result = crossCheck(synthetic.places, costForQuotes(synthetic.quotes));
  const plan = optimizeVisitOrder(synthetic.places, synthetic.quotes);
  assert.deepEqual(result.best, { order: plan.order, keys: [58, 760] });
});

test('all-zero costs exercise ties and use JS code-unit order rather than locale order', () => {
  const result = crossCheck(['a', 'B', '가'], () => [0, 0]);
  assert.deepEqual(result.best.order, ['B', 'a', '가']);
  assert.equal(result.optimalCount, 6);
  assert.deepEqual(crossCheck(['가', 'B', 'a'], () => [0, 0]).best, result.best);
});

test('hand-calculated weight changes and equal scores choose the expected direction', () => {
  const cost = (from, to) => from === 'A' && to === 'B' ? [10, 500] : [20, 0];
  assert.deepEqual(crossCheck(['A', 'B'], cost).best, { order: ['A', 'B'], keys: [10, 500] });
  assert.deepEqual(crossCheck(['A', 'B'], weighted(cost, 1, 0)).best, { order: ['A', 'B'], keys: [10, 10, 500] });
  assert.deepEqual(crossCheck(['A', 'B'], weighted(cost, 0, 1)).best, { order: ['B', 'A'], keys: [0, 20, 0] });
  assert.deepEqual(crossCheck(['A', 'B'], weighted(cost, 20, 1)).best, { order: ['B', 'A'], keys: [400, 20, 0] });
  assert.deepEqual(crossCheck(['A', 'B'], weighted(cost, 100, 1)).best, { order: ['A', 'B'], keys: [1500, 10, 500] });
  assert.deepEqual(crossCheck(['A', 'B'], weighted(cost, 50, 1)).best, { order: ['A', 'B'], keys: [1000, 10, 500] });
});

test('200 seeded asymmetric matrices compare DP, production, and direct enumeration; weighted objectives also agree', () => {
  let seed = 0x72ab01;
  const random = max => {
    seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
    return (seed >>> 0) % max;
  };
  for (let sample = 0; sample < 200; sample++) {
    const places = Array.from({ length: 2 + sample % 5 }, (_, i) => `P${i}`);
    const range = sample % 2 ? 3 : 1000;
    const quotes = makeQuotes(places, () => [random(range), random(range)]);
    const cost = costForQuotes(quotes);
    const result = crossCheck(places, cost);
    const production = optimizeVisitOrder(places, quotes);
    assert.deepEqual([production.time, production.fee], result.best.keys, `sample=${sample}, seed=${seed >>> 0}`);
    assert.deepEqual(production.order, result.best.order, `sample=${sample}, seed=${seed >>> 0}`);
    for (const weights of [[1, 0], [0, 1], [20, 1], [100, 1]]) crossCheck(places, weighted(cost, ...weights));
  }
});

test('eight places exercise upper-bound masks and have a hand-known unique zero-cost chain', () => {
  const places = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];
  const result = crossCheck(places, (from, to) => to.charCodeAt(0) === from.charCodeAt(0) + 1 ? [0, 0] : [9, 9]);
  assert.deepEqual(result.best, { order: places, keys: [0, 0] });
  assert.equal(result.evaluated, 40320);
  assert.equal(result.dpStates, 1024);
});

test('reversing directed edges preserves optimum values, scaling and fixed edge charges preserve order', () => {
  const cost = costForQuotes(synthetic.quotes);
  const original = crossCheck(synthetic.places, cost);
  const reversed = crossCheck(synthetic.places, (from, to) => cost(to, from));
  assert.deepEqual(reversed.best.keys, original.best.keys);
  const scaled = crossCheck(synthetic.places, (from, to) => cost(from, to).map(v => v * 3));
  assert.deepEqual(scaled.best.order, original.best.order);
  assert.deepEqual(scaled.best.keys, original.best.keys.map(v => v * 3));
  const shifted = crossCheck(synthetic.places, (from, to) => cost(from, to).map(v => v + 7));
  assert.deepEqual(shifted.best.order, original.best.order);
  assert.deepEqual(shifted.best.keys, original.best.keys.map(v => v + 28));
});

test('missing, inconsistent, invalid, unsafe or oversized inputs are rejected by both independent methods', () => {
  for (const solve of [heldKarpBest, enumerateAllOrders]) {
    assert.throws(() => solve(['A', 'B'], () => undefined), /missing cost/);
    assert.throws(() => solve(['A', 'B'], from => from === 'A' ? [1] : [1, 2]), /dimensions/);
    for (const value of [-1, 0.5, Infinity, Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => solve(['A', 'B'], () => [value]), /Invalid/);
    assert.throws(() => solve(['A', 'A'], () => [1]), /unique/);
    assert.throws(() => solve(Array.from({ length: 9 }, (_, i) => String(i)), () => [1]), /2~8/);
    assert.throws(() => solve(['A', 'B', 'C'], () => [Number.MAX_SAFE_INTEGER]), /overflow/);
  }
});

test('independent audit detects corrupt totals and duplicate or missing visits', () => {
  assert.deepEqual(auditOrder(['A', 'B', 'C'], ['A', 'B', 'C'], () => [3, 10]), [6, 20]);
  assert.throws(() => auditOrder(['A', 'B', 'C'], ['A', 'B', 'B'], () => [3, 10]), /permutation/);
  assert.throws(() => auditOrder(['A', 'B', 'C'], ['A', 'B'], () => [3, 10]), /permutation/);
  assert.notDeepEqual(auditOrder(['A', 'B', 'C'], ['A', 'B', 'C'], () => [3, 10]), [7, 20]);
  assert.throws(() => auditOrder(['A'], ['A'], () => [0]), /2~8/);
  assert.throws(() => auditOrder(['A', 'B', 'C'], ['A', 'B', 'C'], () => [Number.MAX_SAFE_INTEGER]));
});

test('verification guards detect an unstable cost source that changes the optimum or the audited sum', () => {
  let reads = 0;
  assert.throws(() => crossCheck(['A', 'B'], from => {
    reads++;
    return reads <= 2 ? [from === 'A' ? 1 : 2] : [from === 'A' ? 9 : 0];
  }), /DP and direct permutation evaluator disagree/);
  reads = 0;
  assert.throws(() => crossCheck(['A', 'B'], from => {
    reads++;
    return [reads <= 4 ? (from === 'A' ? 1 : 2) : 9];
  }), /Original edge sum mismatch/);
});

test('historical wait-inclusive objective retains the old weighted crossover independently of current raw-time optimization', () => {
  const places = real.places.map(p => p.station);
  const cost = (from, to) => {
    const row = real.snapshots.find(r => r.fromStation === from && r.toStation === to);
    return [row.readyToArrivalMinutes, row.result.fee];
  };
  assert.deepEqual(crossCheck(places, weighted(cost, 23, 1)).best.keys, [6238, 168, 2374]);
  assert.deepEqual(crossCheck(places, weighted(cost, 24, 1)).best.keys, [6401, 160, 2561]);
  const scoreOnly = (from, to) => { const [time, fee] = cost(from, to); return [187 * time + 8 * fee]; };
  const tied = crossCheck(places, scoreOnly);
  assert.equal(tied.best.keys[0], 50408);
  assert.equal(tied.optimalCount, 2);
});
