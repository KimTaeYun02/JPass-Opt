import assert from 'node:assert/strict';

// Verification only: frozen, directed, additive costs; free endpoints and no return leg.
function checkPlaces(places) {
  if (!Array.isArray(places) || places.length < 2 || places.length > 8
    || places.some(p => typeof p !== 'string' || !p.trim())
    || new Set(places).size !== places.length) throw new Error('2~8 unique places required');
}

function readMatrix(places, cost) {
  checkPlaces(places);
  let width;
  const matrix = places.map(from => places.map(to => {
    if (from === to) return null;
    const keys = cost(from, to);
    if (!Array.isArray(keys) || !keys.length
      || keys.some(k => !Number.isSafeInteger(k) || k < 0)) throw new Error(`Invalid or missing cost: ${from} -> ${to}`);
    width ??= keys.length;
    if (keys.length !== width) throw new Error('Inconsistent cost dimensions');
    return [...keys];
  }));
  return { matrix, width };
}

const add = (a, b) => {
  const sum = a + b;
  if (!Number.isSafeInteger(sum)) throw new Error('Cost overflow');
  return sum;
};

export function enumerateAllOrders(places, cost) {
  const { matrix, width } = readMatrix(places, cost);
  const permutations = remaining => remaining.length
    ? remaining.flatMap((item, i) => permutations(remaining.filter((_, j) => i !== j)).map(tail => [item, ...tail]))
    : [[]];
  const results = permutations(places.map((_, i) => i)).map(indices => {
    const keys = Array(width).fill(0);
    for (let i = 1; i < indices.length; i++) {
      const edge = matrix[indices[i - 1]][indices[i]];
      for (let k = 0; k < width; k++) keys[k] = add(keys[k], edge[k]);
    }
    return { order: indices.map(i => places[i]), keys };
  });
  // This comparator is separate from the DP's comparison logic.
  results.sort((a, b) => {
    for (let i = 0; i < width; i++) if (a.keys[i] !== b.keys[i]) return a.keys[i] < b.keys[i] ? -1 : 1;
    for (let i = 0; i < places.length; i++) if (a.order[i] !== b.order[i]) return a.order[i] < b.order[i] ? -1 : 1;
    return 0;
  });
  const best = results[0];
  const optimalCount = results.filter(r => r.keys.every((v, i) => v === best.keys[i])).length;
  return { best, optimalCount, evaluated: results.length };
}

export function heldKarpBest(places, cost) {
  const { matrix, width } = readMatrix(places, cost);
  const n = places.length;
  const table = new Map();
  let transitions = 0;
  const preferable = (a, b) => {
    if (!b) return true;
    for (let k = 0; k < width; k++) {
      if (a.keys[k] < b.keys[k]) return true;
      if (a.keys[k] > b.keys[k]) return false;
    }
    for (let k = 0; k < a.order.length; k++) {
      if (a.order[k] < b.order[k]) return true;
      if (a.order[k] > b.order[k]) return false;
    }
    return false;
  };
  for (let mask = 1; mask < 1 << n; mask++) {
    for (let end = 0; end < n; end++) {
      if (!(mask & (1 << end))) continue;
      const previousMask = mask ^ (1 << end);
      let best;
      if (!previousMask) best = { order: [places[end]], keys: Array(width).fill(0) };
      else for (let previous = 0; previous < n; previous++) {
        if (!(previousMask & (1 << previous))) continue;
        const prefix = table.get(previousMask * n + previous);
        const edge = matrix[previous][end];
        const candidate = { order: [...prefix.order, places[end]], keys: prefix.keys.map((v, k) => add(v, edge[k])) };
        transitions++;
        if (preferable(candidate, best)) best = candidate;
      }
      table.set(mask * n + end, best);
    }
  }
  let best;
  for (let end = 0; end < n; end++) {
    const candidate = table.get(((1 << n) - 1) * n + end);
    if (preferable(candidate, best)) best = candidate;
  }
  return { best, states: table.size, transitions };
}

export function auditOrder(places, order, cost) {
  checkPlaces(places);
  assert.ok(Array.isArray(order), 'Visits must be an array');
  assert.deepEqual([...order].sort(), [...places].sort(), 'Visits must be an exact permutation');
  const legs = order.slice(1).map((to, i) => [...cost(order[i], to)]);
  assert.ok(legs.every(row => row.length === legs[0].length && row.every(v => Number.isSafeInteger(v) && v >= 0)));
  const sums = legs[0].map((_, k) => legs.reduce((sum, row) => sum + row[k], 0));
  assert.ok(sums.every(Number.isSafeInteger));
  return sums;
}

export function crossCheck(places, cost) {
  const enumeration = enumerateAllOrders(places, cost);
  const dp = heldKarpBest(places, cost);
  assert.deepEqual(dp.best, enumeration.best, 'DP and direct permutation evaluator disagree');
  assert.deepEqual(auditOrder(places, dp.best.order, cost), dp.best.keys, 'Original edge sum mismatch');
  const factorial = places.reduce((value, _, i) => value * (i + 1), 1);
  assert.equal(enumeration.evaluated, factorial);
  assert.equal(dp.states, places.length * 2 ** (places.length - 1));
  assert.equal(dp.transitions, places.length * (places.length - 1) * 2 ** (places.length - 2));
  return { ...enumeration, dpStates: dp.states, dpTransitions: dp.transitions };
}
