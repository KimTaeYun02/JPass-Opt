import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { parseLowCallResponse } from '../src/low-call-api.ts';

const { passTrials } = JSON.parse(await readFile(new URL('../../../sample/low-call/policy-study/result.json', import.meta.url), 'utf8'));

test('pass comparison can recommend a validated alternate that was not chosen for time', () => {
  const parsed = parseLowCallResponse(passTrials.twoOrders, passTrials.twoOrders.echoInput);
  const alternative = parsed.candidates.orderEvaluation.plan;
  assert.notEqual(alternative.digest, parsed.selected.digest);
  assert.equal(parsed.passComparison.recommendation.routeDigest, alternative.digest);
  assert.equal(parsed.passComparison.recommendation.total, 190);
});

test('pass recommendation rejects an unrelated digest, a mismatched order, and a violated route', () => {
  for (const mutate of [
    data => { data.passComparison.recommendation.routeDigest = 'outside-the-evaluated-orders'; },
    data => { data.candidates.orderEvaluation.alternate.order.reverse(); },
    data => { data.candidates.orderEvaluation.plan.constraints[0].status = 'violated'; },
  ]) {
    const copy = structuredClone(passTrials.twoOrders);
    mutate(copy);
    assert.throws(() => parseLowCallResponse(copy), /INVALID_V2_RESPONSE/);
  }
});

test('a total-only quote preserves the no-pass fare and leaves pass costs unconfirmed', () => {
  const parsed = parseLowCallResponse(passTrials.totalOnly);
  assert.equal(parsed.passComparison.status, 'partial');
  assert.equal(parsed.passComparison.recommendation.passId, null);
  assert.equal(parsed.passComparison.recommendation.total, 440);
  assert.ok(parsed.passComparison.evaluations.every(row => row.total === null));
});

test('pass ordering exposes verified audit fields and checks the possible match count', () => {
  const data = structuredClone(passTrials.twoOrders);
  data.echoInput.options.orderAlternatePolicy = 'pass_hint';
  const audit = data.candidates.orderEvaluation;
  audit.policy = 'pass_hint';
  audit.effectivePolicy = 'pass_hint';
  audit.passHint = { passId: 'synthetic-line-pass', evidence: { source: 'station-links', version: '1' } };
  audit.alternate.passHintMatchedLegs = 2;
  assert.equal(parseLowCallResponse(data).candidates.orderEvaluation.passHint.passId, 'synthetic-line-pass');
  audit.alternate.passHintMatchedLegs = 3;
  assert.throws(() => parseLowCallResponse(data), /INVALID_V2_RESPONSE/);
  delete audit.alternate.passHintMatchedLegs;
  audit.effectivePolicy = 'diverse';
  audit.passHint = { reason: 'station_dataset_mismatch' };
  assert.equal(parseLowCallResponse(data).candidates.orderEvaluation.effectivePolicy, 'diverse');
});
