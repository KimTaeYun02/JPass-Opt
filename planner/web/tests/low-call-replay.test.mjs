import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createLowCallSession, parseLowCallResponse, lowCallSummary, requestLowCallPlan } from '../src/low-call-api.ts';

const replay = JSON.parse(await readFile(new URL('../../../sample/low-call/replay/legacy-result.json', import.meta.url), 'utf8'));
const fixture = JSON.parse(await readFile(new URL('../../../sample/low-call/eight-node-result.json', import.meta.url), 'utf8'));
const dated = JSON.parse(await readFile(new URL('../../../sample/low-call/dated/result.json', import.meta.url), 'utf8'));
const sequential = JSON.parse(await readFile(new URL('../../../sample/low-call/sequential/run-20261119-compat/analysis/v2-result.json', import.meta.url), 'utf8'));

test('real three-place sequential replay preserves waits, stays and reference timing', () => {
  const parsed = parseLowCallResponse(sequential, sequential.echoInput);
  assert.equal(parsed.completion, 'complete');
  assert.equal(parsed.timing, 'reference');
  assert.deepEqual(parsed.selected.order, ['meiji', 'tokyo-dome', 'nezu']);
  assert.equal(parsed.replayProvenance.kind, 'captured-public-dom');
  assert.deepEqual(parsed.selected.legs.map(leg => leg.initialWaitMinutes), [11, 3]);
  assert.equal(parsed.selected.elapsedMinutes, 291);
  assert.equal(parsed.selected.fare.noPassTotal, 691);
  assert.equal(parsed.ledger.railAttempts, 2);
  assert.equal(parsed.ledger.httpRequestsObserved, 0);
});

test('actual dated capture replays with initial wait and honest estimated access', () => {
  const parsed = parseLowCallResponse(dated, dated.echoInput);
  assert.equal(parsed.dataSource, 'replay');
  assert.equal(parsed.completion, 'complete');
  assert.equal(parsed.timing, 'reference');
  assert.equal(parsed.replayProvenance.kind, 'captured-team-crawler');
  assert.equal(parsed.selected.legs[0].initialWaitMinutes, 3);
  assert.equal(parsed.selected.legs[0].rideMinutes, 2);
  assert.equal(parsed.selected.elapsedMinutes, 7);
  assert.equal(parsed.selected.fare.noPassTotal, 178);
  assert.equal(parsed.selected.accessWalking, 'estimated');
  assert.equal(parsed.ledger.httpRequestsObserved, 0);
  assert.match(lowCallSummary(parsed).source, /재생.*실시간 아님/);
});

test('v2 parses the real legacy response as replay and partial rather than live or free', () => {
  const parsed = parseLowCallResponse(replay, replay.echoInput);
  assert.equal(parsed.dataSource, 'replay');
  assert.equal(parsed.completion, 'partial');
  assert.equal(parsed.selected.stopReason, 'missing_dated_trip');
  assert.equal(parsed.selected.fare.noPassTotal, null);
  assert.equal(parsed.ledger.railAttempts, 1);
  assert.equal(parsed.ledger.httpRequestsObserved, 0);
  assert.match(lowCallSummary(parsed).source, /재생.*실시간 아님/);
  assert.equal(lowCallSummary(parsed).noPassFare, '운임 미확인');
  const wrong = structuredClone(replay); wrong.dataSource = 'fixture';
  assert.throws(() => parseLowCallResponse(wrong), /INVALID_V2_RESPONSE/);
  const wrongMetrics = structuredClone(replay); wrongMetrics.ledger.measurementBasis = 'fixture-no-network';
  assert.throws(() => parseLowCallResponse(wrongMetrics), /INVALID_V2_RESPONSE/);
});

test('v2 keeps a known gross quote while fare components and transfer counts remain unknown', () => {
  // Deliberately transformed synthetic fixture: this is a parser test, not real dated data.
  const gross = structuredClone(fixture);
  for (const route of [gross.selected, gross.baseline.plan]) {
    for (const leg of route.legs) {
      leg.transfers = null;
      for (const unit of leg.fareUnits) {
        unit.quotedTotal = unit.baseFare + unit.supplement;
        unit.baseFare = null;
        unit.supplement = null;
      }
    }
    route.transfers = null;
    route.fare.fareUnits = structuredClone(route.legs.flatMap(leg => leg.fareUnits));
  }
  const parsed = parseLowCallResponse(gross);
  assert.equal(parsed.selected.fare.noPassTotal, fixture.selected.fare.noPassTotal);
  assert.equal(parsed.selected.legs[0].transfers, null);
  const bad = structuredClone(gross);
  bad.selected.legs[0].fareUnits[0].baseFare = 999999;
  assert.throws(() => parseLowCallResponse(bad), /INVALID_V2_RESPONSE/);
  const badTransfers = structuredClone(gross);
  badTransfers.selected.legs[0].transfers = 21;
  assert.throws(() => parseLowCallResponse(badTransfers), /INVALID_V2_RESPONSE/);
});

test('test-only replay provenance is explicitly synthetic and cannot lose its label', () => {
  const synthetic = structuredClone(replay);
  synthetic.replayProvenance.kind = 'test-only';
  synthetic.replayProvenance.notice = '합성 시각 테스트';
  synthetic.fixtureLabel = '합성 자료 재생 (실데이터 검증 아님)';
  assert.match(lowCallSummary(parseLowCallResponse(synthetic)).source, /합성.*실데이터 검증 아님/);
  const mislabeled = structuredClone(synthetic);
  mislabeled.fixtureLabel = replay.fixtureLabel;
  assert.throws(() => parseLowCallResponse(mislabeled), /INVALID_V2_RESPONSE/);
  const missing = structuredClone(replay);
  delete missing.replayProvenance;
  assert.throws(() => parseLowCallResponse(missing), /INVALID_V2_RESPONSE/);
});

test('the explicit replay client accepts replay sessions and checks returned input and source', async () => {
  const fake = async (url, init) => {
    if (url.endsWith('/sessions')) return new Response(JSON.stringify({ sessionId: 'test-replay', dataSource: 'replay' }), { status: 201 });
    const response = structuredClone(replay);
    response.echoInput = JSON.parse(init.body);
    return new Response(JSON.stringify(response), { status: 200 });
  };
  assert.equal(await createLowCallSession('', fake), 'test-replay');
  assert.equal((await requestLowCallPlan(replay.echoInput, '', fake)).dataSource, 'replay');
  await assert.rejects(requestLowCallPlan({ ...replay.echoInput, dataSource: 'fixture' }, '', fake), /INVALID_V2_RESPONSE/);
});
