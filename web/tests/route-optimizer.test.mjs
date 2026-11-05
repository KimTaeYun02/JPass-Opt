import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { optimizeVisitOrder } from '../src/route-optimizer.ts';

const fixture = JSON.parse(await readFile(new URL('../fixtures/route-five-stops.json', import.meta.url), 'utf8'));
const quote = (from, to, time, fee = 100, line = '테스트 노선') => ({ from, to,
  result: { time, fee, route: [{ station: from, line }, { station: to, line: null }] } });

// An independently implemented subset DP checks the production score.
function optimalScoreBySubsets(places, quotes) {
  const table = new Map();
  for (let end = 0; end < places.length; end++) table.set(`${1 << end}:${end}`, [0, 0]);
  const better = (a, b) => !b || a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);
  for (let mask = 1; mask < 1 << places.length; mask++) {
    for (let end = 0; end < places.length; end++) {
      const score = table.get(`${mask}:${end}`);
      if (!score) continue;
      for (let next = 0; next < places.length; next++) {
        if (mask & (1 << next)) continue;
        const edge = quotes.find((item) => item.from === places[end] && item.to === places[next]).result;
        const candidate = [score[0] + edge.time, score[1] + edge.fee];
        const key = `${mask | (1 << next)}:${next}`;
        if (better(candidate, table.get(key))) table.set(key, candidate);
      }
    }
  }
  let best;
  for (let end = 0; end < places.length; end++) {
    const candidate = table.get(`${(1 << places.length) - 1}:${end}`);
    if (better(candidate, best)) best = candidate;
  }
  return best;
}

test('five stations visit each once using 80 DP states and match an independent optimum', () => {
  const plan = optimizeVisitOrder(fixture.places, fixture.quotes);
  assert.deepEqual([plan.time, plan.fee], optimalScoreBySubsets(fixture.places, fixture.quotes));
  assert.deepEqual(plan.order, ['池袋', '新宿', '渋谷', '銀座', '浅草']);
  assert.equal(plan.time, 58);
  assert.equal(plan.fee, 760);
  assert.equal(plan.evaluatedOrders, 0);
  assert.equal(plan.algorithm, 'held-karp');
  assert.equal(plan.dpStates, 80);
  assert.equal(plan.dpTransitions, 160);
  assert.equal(plan.legs.length, 4);
  assert.deepEqual([...plan.order].sort(), [...fixture.places].sort());
  assert.equal(plan.legs.reduce((sum, leg) => sum + leg.result.time, 0), plan.time);
  assert.deepEqual(plan, optimizeVisitOrder([...fixture.places].reverse(), fixture.quotes));
});

test('directed costs are not mirrored and time takes priority over price', () => {
  const plan = optimizeVisitOrder(['A', 'B'], [quote('A', 'B', 10, 500), quote('B', 'A', 20, 0, '徒歩')]);
  assert.deepEqual(plan.order, ['A', 'B']);
  assert.equal(plan.fee, 500);
});

test('equal times choose lower fares and valid free walking is retained', () => {
  const plan = optimizeVisitOrder(['A', 'B'], [quote('A', 'B', 10, 500), quote('B', 'A', 10, 0, '徒歩')]);
  assert.deepEqual(plan.order, ['B', 'A']);
  assert.equal(plan.fee, 0);
  assert.equal(plan.legs[0].result.route[0].line, '徒歩');
});

test('missing directions are rejected rather than treated as zero or inferred from reverse routes', () => {
  assert.throws(() => optimizeVisitOrder(fixture.places, fixture.quotes.slice(1)), /池袋 → 新宿.*누락/);
});

test('invalid crawler results and duplicate quotes are rejected', () => {
  const valid = [quote('A', 'B', 10), quote('B', 'A', 20)];
  for (const result of [
    { ...valid[0].result, time: null },
    { ...valid[0].result, fee: -1 },
    { ...valid[0].result, time: 2.5 },
    { ...valid[0].result, route: [{ station: 'A', line: '徒歩' }, { station: 'B', line: '徒歩' }] },
    { ...valid[0].result, route: [{ station: 'wrong', line: '徒歩' }, { station: 'B', line: null }] },
  ]) {
    assert.throws(() => optimizeVisitOrder(['A', 'B'], [{ ...valid[0], result }, valid[1]]));
  }
  assert.throws(() => optimizeVisitOrder(['A', 'B'], [...valid, valid[0]]), /중복/);
  assert.throws(() => optimizeVisitOrder(['A', 'A'], valid), /고유/);
});

test('optimizer preserves inputs and returns independent route objects', () => {
  const input = structuredClone(fixture);
  const before = structuredClone(input);
  const plan = optimizeVisitOrder(input.places, input.quotes);
  assert.deepEqual(input, before);
  plan.legs[0].result.route[0].line = 'changed';
  assert.deepEqual(input, before);
});
