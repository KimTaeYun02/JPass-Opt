import { reportPath } from './report-files.mjs';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { optimizeVisitOrder } from '../src/route-optimizer.ts';
import { rankCandidates } from '../src/route-candidates.ts';
import { auditOrder, crossCheck } from './route-verification.mjs';


async function main() {
  const real = JSON.parse(await readFile(new URL('../fixtures/ekispert-browser-five-stops.json', import.meta.url), 'utf8'));
  const synthetic = JSON.parse(await readFile(new URL('../fixtures/route-five-stops.json', import.meta.url), 'utf8'));
  const before = structuredClone({ real, synthetic });
  const realQuotes = real.snapshots.map(r => ({ from: r.fromStation, to: r.toStation, source: 'snapshot',
    result: r.result }));
  const results = [];
  for (const dataset of [
    { name: '실제 Ekispert 09:00 자료', places: real.places.map(p => p.station), quotes: realQuotes, names: new Map(real.places.map(p => [p.station, p.name])) },
    { name: '기존 가상 자료', places: synthetic.places, quotes: synthetic.quotes.map(q => ({ ...q, source: 'snapshot' })), names: new Map(synthetic.places.map(p => [p, p])) },
  ]) {
    const cost = (from, to) => {
      const quote = dataset.quotes.find(q => q.from === from && q.to === to);
      if (!quote) throw new Error(`Missing edge: ${from} -> ${to}`);
      return [quote.result.time, quote.result.fee];
    };
    const checked = crossCheck(dataset.places, cost);
    const production = optimizeVisitOrder(dataset.places, dataset.quotes);
    assert.deepEqual(production.order, checked.best.order);
    assert.deepEqual([production.time, production.fee], checked.best.keys);
    assert.equal(production.dpStates, checked.dpStates);
    assert.equal(production.dpTransitions, checked.dpTransitions);
    assert.equal(production.evaluatedOrders, 0);
    assert.deepEqual(auditOrder(dataset.places, production.order, cost), [production.time, production.fee]);
    const ranked = rankCandidates(dataset.places.map(station => ({ id: station, name: station, station, stayMinutes: 0 })), dataset.quotes, 1)[0];
    assert.deepEqual([ranked.time, ranked.fee], checked.best.keys);
    assert.deepEqual(ranked.order, checked.best.order);
    results.push({ dataset: dataset.name, status: 'PASS', order: checked.best.order.map(s => dataset.names.get(s)),
      stations: checked.best.order, minutes: production.time, fee: production.fee, optimalCount: checked.optimalCount,
      evaluatedOrders: production.evaluatedOrders, enumeratedOrders: checked.evaluated,
      algorithm: production.algorithm, dpStates: production.dpStates, dpTransitions: production.dpTransitions,
      methods: ['production Held-Karp DP', 'independent subset DP', 'direct permutation evaluator', 'top-k candidate DP'],
    });
  }
  assert.deepEqual({ real, synthetic }, before, 'Original fixtures mutated');
  assert.deepEqual([results[0].minutes, results[0].fee, results[0].optimalCount], [143, 2374, 1]);
  assert.deepEqual([results[1].minutes, results[1].fee], [58, 760]);
  const displayedJourneyMinutes = results[0].stations.slice(1).reduce((sum, to, i) => sum + real.snapshots.find(r => r.fromStation === results[0].stations[i] && r.toStation === to).result.time, 0);
  assert.equal(displayedJourneyMinutes, 143);
  const referenceClockJst = new Date(Date.parse(real.snapshotSearchMinuteAt) + 9 * 60 * 60_000).toISOString().slice(0, 16).replace('T', ' ');
  const weightedExpectations = [[1, 0], [0, 1], [20, 1], [23, 1], [24, 1], [100, 1]].map(([t, f]) => [t, f, 143, 2374]);
  const weighted = weightedExpectations.map(([timeWeight, feeWeight, expectedTime, expectedFee]) => {
    const cost = (from, to) => {
      const row = real.snapshots.find(r => r.fromStation === from && r.toStation === to);
      return [timeWeight * row.result.time + feeWeight * row.result.fee, row.result.time, row.result.fee];
    };
    const checked = crossCheck(real.places.map(p => p.station), cost);
    assert.deepEqual(checked.best.keys, [timeWeight * expectedTime + feeWeight * expectedFee, expectedTime, expectedFee]);
    return { timeWeight, feeWeight, score: checked.best.keys[0], minutes: checked.best.keys[1], fee: checked.best.keys[2],
      optimalCountIncludingTimeAndFareTiebreaks: checked.optimalCount,
      order: checked.best.order.map(s => real.places.find(p => p.station === s).name), status: 'PASS' };
  });
  const report = { status: 'PASS', objective: 'Time, then fee, then JS code-unit order; free first/last stop, no return trip.',
    scope: 'Frozen directed station costs, result.time unchanged. No initial wait or attraction stay/access walk added. Weighted checks use raw fares; pass discounts and live timetable feasibility are not validated.',
    externalRouteRequests: 0, networkPolicy: 'Only local files are read; no network client or route provider is invoked.', referenceClockJst, displayedJourneyMinutes, results, weighted,
  };
  await writeFile(await reportPath('route-verification-result.json'), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  const rows = results.map(r => `| ${r.dataset} | ${r.minutes}분 | ${r.fee}엔 | ${r.enumeratedOrders}개 순서 | ${r.dpStates}개 상태 | ${r.status} |`).join('\n');
  const md = `# Held–Karp DP 결과 검증

운영 Held–Karp DP, 독립 검증용 DP, 전체 순열 직접 평가, 상위 후보 DP를 비교했습니다. 최소 시간·요금과 순서가 일치합니다. 원본 구간을 다시 합산하고 방문 중복·누락도 검사했습니다. 외부 경로 조회는 0회입니다.

| 자료 | 시간 | 요금 | 완전탐색 검증 | 운영 DP | 결과 |
|---|---|---|---|---|---|
${rows}

실제 자료의 순서: **${results[0].order.join(' → ')}**. 시간·요금 기준 최적해는 유일합니다. 비용은 크롤러의 원본 result.time을 그대로 합산한 값입니다. 별도의 최초 열차 대기, 관광지 접근 도보, 체류시간은 더하지 않습니다. 원본 time에 포함된 환승·대기는 유지합니다.

자료의 조회 조건은 ${referenceClockJst} JST이며, 최적화에서는 이 구간 비용을 고정값으로 사용합니다. 장소별 체류 후 출발 시각으로 재조회하지 않습니다. 패스 할인은 적용하지 않았습니다.

운영 evaluatedOrders는 0입니다. 순열을 전부 평가하지 않으므로 dpStates와 dpTransitions를 보고합니다. 표의 완전탐색 횟수는 검증 전용 직접 순열 평가의 횟수입니다.

가중치 검증은 일반 운임과 원본 time을 사용합니다. 점수 동률이면 시간·요금·ID 순으로 비교합니다.

| 시간 가중치 | 요금 가중치 | 최소 점수 | 시간 | 요금 | 결과 |
|---|---|---|---|---|---|
${weighted.map(r => `| ${r.timeWeight} | ${r.feeWeight} | ${r.score} | ${r.minutes}분 | ${r.fee}엔 | ${r.status} |`).join('\n')}

실행: web 폴더에서 npm run verify:route. 자동 검증: npm test.
`;
  await writeFile(await reportPath('route-verification-result.md'), md, 'utf8');
  console.log(JSON.stringify(report, null, 2));
}

try {
  await main();
} catch (error) {
  const failure = { status: 'FAIL', error: error.message };
  await Promise.allSettled([
    writeFile(await reportPath('route-verification-result.json'), JSON.stringify(failure, null, 2) + '\n', 'utf8'),
    writeFile(await reportPath('route-verification-result.md'), '# 경로 검증 실패\n\n이번 실행은 검증을 통과하지 못했습니다. 터미널 오류를 확인하세요.\n', 'utf8'),
  ]);
  throw error;
}
