import { reportPath } from './report-files.mjs';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';

const data = JSON.parse(await readFile(new URL('../fixtures/ekispert-browser-five-stops.json', import.meta.url), 'utf8'));
const ids = data.places.map((_, i) => String.fromCharCode(65 + i));
const stationIds = new Map(data.places.map((p, i) => [p.station, ids[i]]));
const truth = new Map(data.snapshots.map(r => [stationIds.get(r.fromStation) + stationIds.get(r.toStation), { time: r.readyToArrivalMinutes, fee: r.result.fee }]));
const permutations = xs => xs.length ? xs.flatMap((x, i) => permutations(xs.filter((_, j) => i !== j)).map(p => [x, ...p])) : [[]];
const orders = permutations(ids).map(order => ({ order, edges: order.slice(1).map((to, i) => order[i] + to) }));
const compare = (a, b) => a.time - b.time || a.fee - b.fee || a.order.join('').localeCompare(b.order.join(''));
const lowerCost = (path, known) => ({ ...path, time: path.edges.reduce((s, e) => s + (known.get(e)?.time ?? 0), 0), fee: path.edges.reduce((s, e) => s + (known.get(e)?.fee ?? 0), 0), missing: path.edges.filter(e => !known.has(e)) });
const canImprove = (p, best) => !best || p.time < best.time || (p.time === best.time && p.fee <= best.fee);

function replay(strategy, seed) {
  const known = new Map();
  const history = [];
  const checkpoints = [];
  let best = null;
  const lookup = edge => {
    assert.ok(!known.has(edge));
    const value = truth.get(edge);
    assert.ok(value);
    known.set(edge, value);
    history.push({ edge, ...value });
  };
  for (const edge of seed) lookup(edge);
  while (true) {
    // Query selection sees only known values; unknown edges remain unknown in the data.
    const ranked = orders.map(path => lowerCost(path, known)).sort(compare);
    for (const p of ranked.filter(p => p.missing.length === 0)) if (!best || compare(p, best) < 0) best = p;
    const live = ranked.filter(p => p.missing.length && canImprove(p, best));
    if ([4, 8, 12, 16, 20].includes(known.size) || !live.length) checkpoints.push({ lookups: known.size, bestOrder: best?.order ?? null, bestTime: best?.time ?? null, bestFee: best?.fee ?? null, globalLowerBoundMinutes: Math.min(best?.time ?? Infinity, ...live.map(p => p.time)), unresolvedOrders: live.length, certified: live.length === 0 });
    if (!live.length) break;
    let edge;
    if (strategy === 'optimistic-route-first') edge = live[0].missing[0];
    else {
      const frequency = new Map();
      for (const p of live) for (const e of p.missing) frequency.set(e, (frequency.get(e) ?? 0) + 1);
      edge = [...frequency].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0][0];
    }
    lookup(edge);
    assert.ok(known.size <= truth.size);
  }
  return { strategy, seed, lookups: known.size, bestOrder: best.order, bestTime: best.time, bestFee: best.fee, history, checkpoints };
}
const complete = orders.map(p => lowerCost(p, truth)).sort(compare)[0];
// Retrospective certificate analysis uses the full matrix only as the reference.
// If withholding just one edge allows a better route, that edge is mandatory
// for any exact certificate relying solely on nonnegative unknown costs.
const omissionChecks = [...truth.keys()].map(edge => {
  const known = new Map(truth);
  known.delete(edge);
  const optimistic = orders.map(p => lowerCost(p, known)).sort(compare)[0];
  const containing = orders.filter(p => p.edges.includes(edge)).map(p => lowerCost(p, known)).sort(compare)[0];
  const certified = optimistic.time > complete.time || (optimistic.time === complete.time && optimistic.fee >= complete.fee);
  return { withheld: edge, optimisticOrder: optimistic.order, optimisticTime: optimistic.time, optimisticFee: optimistic.fee, containingEdgeLowerBoundMinutes: containing.time, certified };
});
const mandatoryEdges = omissionChecks.filter(r => !r.certified).map(r => r.withheld);
const mandatoryOnly = new Map(mandatoryEdges.map(e => [e, truth.get(e)]));
const mandatoryBound = orders.map(p => lowerCost(p, mandatoryOnly)).sort(compare)[0];
assert.ok(complete.edges.every(e => mandatoryOnly.has(e)));
assert.equal(mandatoryBound.time, complete.time);
assert.equal(mandatoryBound.fee, complete.fee);
const baselineEdges = orders.find(p => p.order.join('') === ids.join('')).edges;
const runs = ['optimistic-route-first', 'most-frequent-live-edge'].flatMap(strategy => [replay(strategy, []), replay(strategy, baselineEdges)]);
for (const r of runs) {
  assert.equal(r.bestTime, complete.time);
  assert.equal(r.bestFee, complete.fee);
  assert.deepEqual(r.bestOrder, complete.order);
}
const report = {
  notice: 'Offline replay of the fixed 09:00 browser data, not new external calls. No adjacency, coordinates, symmetry or answer-specific rules. Unknown time/fare use 0 only as a mathematical lower bound, never as a travel estimate.',
  ids: Object.fromEntries(data.places.map((p, i) => [ids[i], p.name])),
  objective: 'Minimize time, then fee. Deterministic lexicographic order on complete equal-cost routes.',
  reference: { order: complete.order, time: complete.time, fee: complete.fee },
  retrospectiveCertificate: { mandatoryEdges, minimumLookupsForThisMatrix: mandatoryEdges.length, omissionChecks, notice: 'This is a hindsight lower-bound certificate, not a query policy that knows which edge to skip in advance.' },
  runs,
};
await writeFile(await reportPath('lazy-routing-replay.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
console.log(JSON.stringify({ ids: report.ids, reference: report.reference, minimumLookupsForThisMatrix: mandatoryEdges.length, skippableInHindsight: omissionChecks.filter(r => r.certified), runs: runs.map(({ history, checkpoints, ...r }) => r) }, null, 2));
