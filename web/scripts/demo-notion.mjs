import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { buildCandidateMatrix, rankCandidates, referenceSchedule } from '../src/route-candidates.ts';
import { QuoteSession, refreshCandidate } from '../src/route-provider.ts';

const args = process.argv.slice(2);
const flags = new Map();
for (let i = 0; i < args.length; i += 2) {
  if (!['--estimates', '--provider', '--max-calls', '--output', '--start-at'].includes(args[i]) || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error('지원하지 않는 옵션 또는 옵션 값 누락');
  flags.set(args[i], args[i + 1]);
}
const fixture = JSON.parse(await readFile(new URL('../fixtures/notion-visits.json', import.meta.url), 'utf8'));
const estimates = flags.has('--estimates') ? JSON.parse(await readFile(flags.get('--estimates'), 'utf8')) : {};
if (!estimates || typeof estimates !== 'object' || Array.isArray(estimates)
  || Object.entries(estimates).some(([id, rows]) => !fixture.datasets.some(d => d.id === id) || !Array.isArray(rows))) {
  throw new Error('추정 파일은 올바른 dataset ID를 키로 쓰는 객체와 구간 배열이어야 합니다.');
}
const maxCalls = Number(flags.get('--max-calls') ?? 0);
if (!Number.isSafeInteger(maxCalls) || maxCalls < 0) throw new Error('호출 예산은 0 이상의 안전한 정수여야 합니다.');
const startAt = flags.get('--start-at') ?? fixture.startAt;
let session;
if (flags.has('--provider')) {
  const { default: provider } = await import(pathToFileURL(resolve(flags.get('--provider'))).href);
  session = new QuoteSession(provider, maxCalls);
}
const reports = [];
for (const dataset of fixture.datasets) {
  const snapshots = dataset.snapshots.map(({ from, to, departure, arrival, time, fee, route }) => ({
    from, to, stations: { from: departure, to: arrival }, source: 'snapshot', result: { time, fee, route },
  }));
  const matrix = buildCandidateMatrix(dataset.places, snapshots, estimates[dataset.id] ?? []);
  const baseline = {
    grade: 'unverified', order: dataset.places.map(p => p.id), time: snapshots.reduce((sum, q) => sum + q.result.time, 0),
    fee: snapshots.reduce((sum, q) => sum + q.result.fee, 0), legs: snapshots, evaluatedOrders: 0, estimatedLegs: [],
  };
  const candidates = matrix.unresolved.length ? [] : rankCandidates(dataset.places, matrix.quotes);
  const selected = candidates[0] ?? baseline;
  const refreshed = session ? await refreshCandidate(dataset.places, selected, startAt, session) : {
    status: 'unknown', reason: '실제 크롤러가 연결되지 않았습니다.', addedCalls: 0,
  };
  const decorate = plan => ({ ...plan, names: plan.order.map(id => dataset.places.find(p => p.id === id).name) });
  reports.push({ id: dataset.id, title: dataset.title, places: dataset.places,
    status: candidates.length ? 'candidate-ready' : 'needs-travel-data',
    baseline: decorate(baseline), candidates: candidates.map(decorate),
    missingObservations: matrix.missing, unresolvedEstimates: matrix.unresolved,
    referenceSchedule: referenceSchedule(dataset.places, selected, startAt), refreshed,
  });
}
const report = {
  sourceUrl: fixture.sourceUrl, sourceSection: fixture.sourceSection, notice: fixture.notice,
  assumptions: {
    startAt, endpoints: '첫·마지막 방문지 자유, 출발지 복귀 없음',
    candidateLimit: 3, externallyRefreshedCandidatesPerDataset: session ? 1 : 0,
    queryDateKnownForNotionSnapshots: false,
    fare: '구간 독립결제 가정의 합계. 패스·연결 운임은 미검증.',
    estimates: '명시적으로 제공한 추정값만 사용. 역방향·누락·동일 역 도보를 자동 추정하지 않음.',
    providerCalls: session?.calls ?? 0,
  }, reports,
};
const json = `${JSON.stringify(report, null, 2)}\n`;
if (flags.has('--output')) await writeFile(flags.get('--output'), json, 'utf8');
console.log(json);
