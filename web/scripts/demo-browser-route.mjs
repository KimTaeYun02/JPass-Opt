import { reportPath } from './report-files.mjs';
import assert from 'node:assert/strict';
import { readFile, writeFile } from 'node:fs/promises';
import { optimizeVisitOrder, validateQuote } from '../src/route-optimizer.ts';

const data = JSON.parse(await readFile(new URL('../fixtures/ekispert-browser-five-stops.json', import.meta.url), 'utf8'));
const stations = data.places.map(p => p.station);
const names = new Map(data.places.map(p => [p.station, p.name]));
const stays = new Map(data.places.map(p => [p.station, p.stayMinutes]));
const quote = r => ({ from: r.fromStation, to: r.toStation, source: 'snapshot', result: r.result });
const minuteOfDay = clock => {
  assert.match(clock, /^\d{2}:\d{2}$/);
  const [hour, minute] = clock.split(':').map(Number);
  assert.ok(hour < 24 && minute < 60);
  return hour * 60 + minute;
};
const jstClock = value => new Date((typeof value === 'number' ? value : Date.parse(value)) + 9 * 60 * 60_000).toISOString().slice(11, 16);
for (const r of [...data.snapshots, ...data.timed]) {
  validateQuote(quote(r));
  const readyAt = r.searchMinuteAt ?? data.snapshotSearchMinuteAt;
  const requestedAt = r.searchRequestedAt ?? data.snapshotSearchRequestedAt;
  assert.equal(Math.floor(Date.parse(requestedAt) / 60_000) * 60_000, Date.parse(readyAt));
  assert.equal((minuteOfDay(r.actualArrival) - minuteOfDay(r.actualDeparture) + 1440) % 1440, r.result.time);
  assert.equal((minuteOfDay(r.actualArrival) - minuteOfDay(jstClock(readyAt)) + 1440) % 1440, r.readyToArrivalMinutes);
}
assert.equal(data.snapshots.length, stations.length * (stations.length - 1));
assert.equal(data.searchSubmissions, data.snapshots.length + data.timed.length + data.discardedQueries.length);

// Optimize the crawler's result.time unchanged; add no initial waiting or stays.
const matrix = data.snapshots.map(quote);
const best = optimizeVisitOrder(stations, matrix);
const baseline = stations.slice(1).map((to, i) => data.snapshots.find(r => r.fromStation === stations[i] && r.toStation === to));
const total = rows => ({
  movementAndWaitingMinutes: rows.reduce((s, r) => s + r.readyToArrivalMinutes, 0),
  displayedJourneyMinutes: rows.reduce((s, r) => s + r.result.time, 0),
  independentLegFee: rows.reduce((s, r) => s + r.result.fee, 0),
});
// The timed observations belong to the historical selected order, not the new raw-time winner.
const historicalOrder = [data.timed[0].fromStation, ...data.timed.map(r => r.toStation)];
assert.deepEqual([...historicalOrder].sort(), [...stations].sort());
assert.equal(data.timed.length, historicalOrder.length - 1);
let readyAt = Date.parse(data.snapshotSearchMinuteAt);
const visits = [];
for (let i = 0; i < historicalOrder.length; i++) {
  const station = historicalOrder[i];
  const arrivalAt = readyAt;
  readyAt += stays.get(station) * 60_000;
  visits.push({ name: names.get(station), station, referenceArrival: jstClock(arrivalAt), referenceLeave: jstClock(readyAt) });
  if (i < data.timed.length) {
    const r = data.timed[i];
    assert.equal(r.fromStation, station);
    assert.equal(r.toStation, historicalOrder[i + 1]);
    assert.equal(Date.parse(r.searchMinuteAt), readyAt);
    readyAt += r.readyToArrivalMinutes * 60_000;
  }
}
const sourceUrl = (r, requestedAt) => {
  const params = new URLSearchParams({
    depCode: data.stationCodes[r.fromStation], depName: r.fromStation,
    viaCode: '', viaName: '', arrCode: data.stationCodes[r.toStation], arrName: r.toStation,
    dateTime: String(Date.parse(requestedAt) / 1000), ...data.searchOptions,
  });
  return `https://roote.ekispert.net/result?${params}`;
};
const report = {
  source: data.source,
  assumptions: { optimization: 'Fixed result.time quotes; no initial waiting, attraction walking or stay added.', historicalCollection: data.assumption },
  algorithm: 'Held-Karp DP on fixed directed result.time values unchanged; minimize time, then independent fare, then JS code-unit IDs. Free first/last station; no return trip.',
  requests: { historicalBrowserSearchSubmissions: data.searchSubmissions, historicalMatrix: 20, historicalTimedCandidate: 4, correction: 1, paidApiCalls: 0, requestsOnOfflineRerun: 0, websiteInternalHttpRequests: 'not measured' },
  staticComparison: {
    baseline: { order: stations.map(s => names.get(s)), time: total(baseline).displayedJourneyMinutes,
      independentLegFee: total(baseline).independentLegFee },
    best: { order: best.order.map(s => names.get(s)), time: best.time, independentLegFee: best.fee, algorithm: best.algorithm, evaluatedOrders: best.evaluatedOrders, dpStates: best.dpStates, dpTransitions: best.dpTransitions },
    savedMinutesUnderSameSnapshotAssumption: total(baseline).displayedJourneyMinutes - best.time,
    optimality: 'Exact only for this fixed 09:00 snapshot matrix.',
  },
  historicalCandidateAtActualTimes: {
    notice: 'Previously collected timed observations for a different selected order. Excluded from the current optimization.',
    order: historicalOrder.map(s => names.get(s)),
    ...total(data.timed), visits,
    finalArrival: data.timed.at(-1).actualArrival,
    finalStayEnds: jstClock(readyAt),
    optimality: 'Selected order checked sequentially; other orders were not re-evaluated at their departure times.',
    accessWalking: 'unknown', openingHours: 'not checked',
    legs: data.timed.map(r => ({ ...r, sourceUrl: sourceUrl(r, r.searchRequestedAt) })),
  },
  snapshots: data.snapshots.map(r => ({ ...r, sourceUrl: sourceUrl(r, data.snapshotSearchRequestedAt) })),
};
const jsonPath = await reportPath('ekispert-browser-route-result.json');
const mdPath = await reportPath('ekispert-browser-route-result.md');
await writeFile(jsonPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
const table = data.timed.map(r => `| ${names.get(r.fromStation)} → ${names.get(r.toStation)} | ${jstClock(r.searchMinuteAt)} | ${r.actualDeparture} → ${r.actualArrival} | ${r.readyToArrivalMinutes}분 | ${r.result.fee}엔 | [조회 결과](${sourceUrl(r, r.searchRequestedAt)}) |`).join('\n');
const costs = stations.map(from => `| ${names.get(from)} | ${stations.map(to => from === to ? '—' : data.snapshots.find(r => r.fromStation === from && r.toStation === to).result.time).join(' | ')} |`).join('\n');
const md = `# Ekispert 자료의 Held–Karp DP 최적 순서

2026-10-13 브라우저에서 수집한 방향별 20개 역간 결과를 재사용했습니다. 예시 조회일은 2026-10-17, 일본 시간 09:00입니다. 이번 재실행의 외부 경로 조회는 0회입니다.

**${best.order.map(s => names.get(s)).join(' → ')}**

원본 크롤링 time의 합은 **${best.time}분**, 독립 구간 요금 합은 **${best.fee}엔**입니다. 원래 입력 순서는 ${total(baseline).displayedJourneyMinutes}분·${total(baseline).independentLegFee}엔이므로 같은 고정 비용표에서 ${report.staticComparison.savedMinutesUnderSameSnapshotAssumption}분 줄었습니다.

운영 계산은 Held–Karp DP입니다. ${best.dpStates}개 상태·${best.dpTransitions}개 상태 전이를 계산합니다. evaluatedOrders는 0이며, 완전한 방문 순열을 전부 평가하지 않습니다. 동률이면 요금, 그다음 ID의 JS 코드 단위 순서로 비교합니다. 첫·마지막 역은 자유이고 복귀 구간은 없습니다.

## 비용 기준

각 구간은 result.time을 그대로 사용합니다. 최초 열차 출발 전 대기, 역↔관광지 도보, 관광지 체류를 별도로 더하지 않습니다. 원본 time에 들어 있는 환승·대기는 유지합니다. 출발·체류 시각을 변경해도 같은 고정 비용표를 쓰는 한 최적 순서는 같습니다. 날짜·조건에 따른 새 자료를 원하면 별도 수집 후 다시 계산합니다.

## 원본 time 비용표

단위는 분, 행은 출발역, 열은 도착역입니다.

| 출발 / 도착 | ${stations.map(s => names.get(s)).join(' | ')} |
|---|${stations.map(() => '---|').join('')}
${costs}

## 과거 시간별 조회 기록

아래 자료는 과거 선택 순서인 **${historicalOrder.map(s => names.get(s)).join(' → ')}**에서 각 장소 120분 체류를 가정해 수집했습니다. 이번 최적화 입력이나 새 최적 순서의 시간별 검증으로 사용하지 않습니다.

| 구간 | 이동 준비 시각 | 실제 출발 → 도착 | 준비부터 도착 | 구간 요금 | 출처 |
|---|---|---|---|---|---|
${table}

과거 기록의 준비부터 도착까지 합은 ${total(data.timed).movementAndWaitingMinutes}분, 원본 time 합은 ${total(data.timed).displayedJourneyMinutes}분, 요금은 ${total(data.timed).independentLegFee}엔입니다. 역↔관광지 도보·영업시간·패스 할인은 검증하지 않았습니다.

## 조회 기록과 재현

과거 검색 제출은 방향별 자료 20회, 과거 선택 순서 4회, 시각 수정 1회로 총 25회였습니다. 유료 API 호출은 0회이며 웹사이트 내부 HTTP 요청 수는 측정하지 않았습니다. 이번 실행은 저장 자료만 읽습니다.

원본 조건·노선·시간은 web/fixtures/ekispert-browser-five-stops.json에 보존했습니다. 전체 조회 링크는 JSON 보고서에 있습니다. 재현: web 폴더에서 node scripts/demo-browser-route.mjs.
`;
await writeFile(mdPath, md, 'utf8');
console.log(JSON.stringify({ order: report.staticComparison.best.order, staticMinutes: best.time, timed: total(data.timed), browserSearchSubmissions: data.searchSubmissions, algorithm: best.algorithm, dpStates: best.dpStates, dpTransitions: best.dpTransitions, evaluatedOrders: best.evaluatedOrders, output: [jsonPath.pathname, mdPath.pathname] }, null, 2));
