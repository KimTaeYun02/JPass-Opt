import { readFile, writeFile } from 'node:fs/promises';
import { optimizeVisitOrder } from '../src/route-optimizer.ts';

const fixture = JSON.parse(await readFile(new URL('../fixtures/route-five-stops.json', import.meta.url), 'utf8'));
const plan = optimizeVisitOrder(fixture.places, fixture.quotes);
const originalLegs = fixture.places.slice(0, -1).map((from, index) =>
  fixture.quotes.find((quote) => quote.from === from && quote.to === fixture.places[index + 1]).result);
const originalTime = originalLegs.reduce((sum, leg) => sum + leg.time, 0);
const originalFee = originalLegs.reduce((sum, leg) => sum + leg.fee, 0);
const report = {
  source: fixture.source,
  notice: fixture.description,
  assumptions: {
    objective: '총 이동시간 최소, 동률이면 운임 최소',
    endpoints: '첫 역과 마지막 역 모두 자유, 출발지 복귀 없음',
    travelTimes: '방향별 고정 소요시간',
    fare: '각 구간을 독립 결제하는 패스 미적용 가상 운임',
    externalRequests: 0,
  },
  original: { order: fixture.places, time: originalTime, fee: originalFee },
  optimized: plan,
  savedMinutes: originalTime - plan.time,
  savedYen: originalFee - plan.fee,
};
const json = `${JSON.stringify(report, null, 2)}\n`;
if (process.argv[2] === '--output') {
  if (!process.argv[3]) throw new Error('--output에는 결과 파일 경로가 필요합니다.');
  await writeFile(process.argv[3], json, 'utf8');
}
console.log(json);
