import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createLowCallSession, requestLowCallPlan, parseLowCallResponse, lowCallSummary } from '../src/low-call-api.ts';

const example = JSON.parse(await readFile(new URL('../../../sample/low-call/eight-node-result.json', import.meta.url), 'utf8'));

test('v2 parses the Python CLI fixture with separate axes and explicit source', () => {
  const parsed = parseLowCallResponse(example, example.echoInput);
  assert.equal(parsed.dataSource, 'fixture');
  assert.equal(parsed.completion, 'complete');
  assert.equal(parsed.timing, 'reference');
  assert.equal(parsed.ledger.railAttempts, 8);
  assert.equal(parsed.ledger.stationAttempts, 8);
  assert.equal(parsed.baseline.preserved, true);
  assert.equal(parsed.selected.fare.noPassTotal, 1480);
  const summary = lowCallSummary(parsed);
  assert.match(summary.source, /가상/);
  assert.match(summary.timing, /참고/);
  assert.equal(summary.railAttempts, '8/10');
  parsed.selected.visits[0].stayMinutes = 1;
  assert.equal(example.selected.visits[0].stayMinutes, 30);
});

test('v2 rejects stale input, v1 responses, wrong provenance and impossible counters', () => {
  const stale = structuredClone(example.echoInput);
  stale.places[0].stayMinutes++;
  assert.throws(() => parseLowCallResponse(example, stale), /STALE_V2_INPUT/);
  const mutations = [
    r => { r.dataSource = 'live'; },
    r => { r.inputVersion = '1'; },
    r => { r.ledger.railAttempts = 11; },
    r => { r.ledger.logicalAttempts = 0; },
    r => { r.ledger.providerCalls = r.ledger.totalRoutingOperationAttempts; },
    r => { r.ledger.attempts.push(r.ledger.attempts[0]); },
    r => { r.baseline.digest = 'other'; },
    r => { r.selected.order.pop(); },
    r => { r.selected.fare.noPassTotal = 0; },
    r => { r.selected.legs[0].initialWaitMinutes = 0; },
    r => { r.selected.visits[0].arrivalAt = '2026-02-30T09:00:00+09:00'; },
    r => { r.selected.visits[0].arrivalAt = '2026-11-17T24:00:00Z'; },
    r => { r.selected.legs[0].arrivalAt = '2026-11-16T00:00:00Z'; },
    r => { r.selected.legs[0].accessWalking.minutes = null; },
    r => { r.selected.timing = r.timing = 'timetable'; },
    r => { r.unknownNewVersionField = true; },
  ];
  for (const mutate of mutations) {
    const value = structuredClone(example); mutate(value);
    assert.throws(() => parseLowCallResponse(value), /INVALID_V2_RESPONSE/);
  }
  assert.throws(() => parseLowCallResponse({ dataSource: 'live', timingBasis: 'stationTravelOnly' }), /INVALID_V2_RESPONSE/);
});

test('v2 client sends only explicit POST actions and snapshots input while awaiting HTTP', async () => {
  const calls = [];
  const fake = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith('/sessions')) return new Response(JSON.stringify({ sessionId: 'new-session', dataSource: 'fixture' }), { status: 201 });
    const response = structuredClone(example);
    response.echoInput = JSON.parse(init.body);
    return new Response(JSON.stringify(response), { status: 200 });
  };
  assert.equal(await createLowCallSession('http://localhost:8010/', fake), 'new-session');
  const request = structuredClone(example.echoInput);
  const result = await requestLowCallPlan(request, 'http://localhost:8010', fake);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].url, 'http://localhost:8010/api/v2/routes/plan');
  assert.equal(calls[1].init.method, 'POST');
  assert.deepEqual(JSON.parse(calls[1].init.body), request);
  assert.deepEqual(result.echoInput, request);
});

test('v2 errors never become fixture successes or replace a completed baseline', async () => {
  const bad = async () => new Response(JSON.stringify({ error: 'LIVE_UNSUPPORTED' }), { status: 501 });
  await assert.rejects(requestLowCallPlan(example.echoInput, '', bad), error => error.message === 'LIVE_UNSUPPORTED' && error.status === 501);
  const malformed = async () => new Response('<html>not json</html>', { status: 502 });
  await assert.rejects(requestLowCallPlan(example.echoInput, '', malformed), /INVALID_V2_JSON/);
  const originalDigest = example.baseline.digest;
  const controller = new AbortController(); controller.abort();
  const cancelled = async (_url, options) => { options.signal.throwIfAborted(); };
  await assert.rejects(requestLowCallPlan(example.echoInput, '', cancelled, controller.signal), error => error.name === 'AbortError');
  assert.equal(example.baseline.digest, originalDigest);
});
