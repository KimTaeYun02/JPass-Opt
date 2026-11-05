import assert from 'node:assert/strict';
import test from 'node:test';
import { harness } from './demo-harness.mjs';
import { apiPlaces, routeResponse } from './api-fixtures.mjs';
import { parseRouteResponse } from '../src/demo-api.ts';

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function setup(overrides = {}) {
  const calls = [];
  const client = {
    initial(draft, day, query) { return client.search(query); },
    async search(query) { calls.push(['search', query]); return { places: apiPlaces, clarification: null }; },
    async optimize(draft) { calls.push(['optimize']); return parseRouteResponse(routeResponse(draft), draft); },
    async nearby() { calls.push(['nearby']); return [apiPlaces[2]]; },
    ...overrides,
  };
  return { h: harness({ mode: 'api', client }), calls };
}
async function prepare(h) {
  h.change('day-count', '1'); h.click('search'); h.filter('Kyoto');
  h.dialogClick({ action: 'submit-search' }); await h.settle();
  for (const place of apiPlaces.slice(0, 2)) h.dialogClick({ addId: place.id });
  h.dialogClick({ action: 'confirm-search' }); h.click('continue');
}

test('clicking the active day preserves fetched candidates and pending request', async () => {
  const waiting = deferred();
  const { h, calls } = setup({ async search(query) { calls.push(['search', query]); return waiting.promise; } });
  h.click('continue'); h.click('load-initial'); h.click('day', { day: '0' });
  waiting.resolve({ places: apiPlaces, clarification: null }); await h.settle();
  assert.deepEqual(h.state().initialPool, apiPlaces.map((place) => place.id));
  h.click('day', { day: '0' });
  assert.equal(h.state().initialPool.length, 3);
  assert.equal(calls.length, 1);
});

test('navigation during calculation cannot be overwritten by a late result', async () => {
  const waiting = deferred();
  const { h } = setup({ optimize: () => waiting.promise });
  await prepare(h); const snapshot = h.state().draft;
  h.click('calculate'); h.click('view', { view: 'input' });
  waiting.resolve(parseRouteResponse(routeResponse(snapshot), snapshot)); await h.settle();
  assert.equal(h.state().view, 'input');
  assert.equal(h.state().result, undefined);
  assert.doesNotMatch(h.markup(), /일정을 준비하고 있어요/);
});

test('leaving and returning to the same step still rejects the abandoned calculation', async () => {
  const waiting = deferred(); const { h } = setup({ optimize: () => waiting.promise });
  await prepare(h); const snapshot = h.state().draft;
  h.click('calculate'); h.click('view', { view: 'input' }); h.click('view', { view: 'choose' });
  waiting.resolve(parseRouteResponse(routeResponse(snapshot), snapshot)); await h.settle();
  assert.equal(h.state().view, 'choose'); assert.equal(h.state().result, undefined);
});

test('late calculation failure stays on its originating screen', async () => {
  const waiting = deferred();
  const { h } = setup({ optimize: () => waiting.promise });
  await prepare(h); h.click('calculate'); h.click('view', { view: 'input' });
  waiting.reject(new Error('old request failed')); await h.settle();
  assert.equal(h.state().view, 'input');
  assert.equal(h.state().operationError, '');
});

test('editing a modal search allows a new explicit request and ignores the previous response', async () => {
  const first = deferred(), second = deferred(); const queries = [];
  const { h } = setup({ search(query) { queries.push(query); return queries.length === 1 ? first.promise : second.promise; } });
  h.click('search'); h.filter('first'); h.searchKey('Enter');
  h.filter('second'); h.searchKey('Enter');
  assert.deepEqual(queries, ['first', 'second']);
  second.resolve({ places: [apiPlaces[1]], clarification: null }); await h.settle();
  first.resolve({ places: [apiPlaces[0]], clarification: null }); await h.settle();
  assert.match(h.searchMarkup(), /data-add-id="google:contract-b"/);
  assert.doesNotMatch(h.searchMarkup(), /data-add-id="google:contract-a"/);
  assert.equal(h.state().loading.search, false);
});

test('clearing search input removes old selectable results while keeping staged selections', async () => {
  const { h, calls } = setup();
  h.click('search'); h.filter('Kyoto'); h.searchKey('Enter'); await h.settle();
  h.dialogClick({ addId: apiPlaces[0].id }); h.filter('');
  assert.doesNotMatch(h.searchMarkup(), /data-add-id=/);
  assert.match(h.selectionMarkup(), /google:contract-a/);
  assert.equal(calls.length, 1);
  h.dialogClick({ unselectId: apiPlaces[0].id });
  assert.equal(h.confirm.disabled, true);
});

test('an old search failure cannot stop or replace a newer pending search', async () => {
  const first = deferred(), second = deferred(); let calls = 0;
  const { h } = setup({ search() { return ++calls === 1 ? first.promise : second.promise; } });
  h.click('search'); h.filter('first'); h.searchKey('Enter');
  h.filter('second'); h.searchKey('Enter');
  first.reject(new Error('obsolete failure')); await h.settle();
  assert.equal(h.state().loading.search, true);
  assert.doesNotMatch(h.searchMarkup(), /obsolete failure/);
  second.resolve({ places: [apiPlaces[1]], clarification: null }); await h.settle();
  assert.equal(h.state().loading.search, false);
  assert.match(h.searchMarkup(), /google:contract-b/);
});

test('confirm commits staged selections across different search result lists', async () => {
  let calls = 0;
  const { h } = setup({ async search() { return { places: [apiPlaces[calls++]], clarification: null }; } });
  h.click('search'); h.filter('first'); h.searchKey('Enter'); await h.settle();
  h.dialogClick({ addId: apiPlaces[0].id });
  h.filter('second'); h.searchKey('Enter'); await h.settle();
  assert.doesNotMatch(h.searchMarkup(), /google:contract-a/);
  assert.match(h.selectionMarkup(), /google:contract-a/);
  h.dialogClick({ addId: apiPlaces[1].id }); h.dialogClick({ action: 'confirm-search' });
  assert.deepEqual(h.state().draft.days[0].placeIds, apiPlaces.slice(0, 2).map((place) => place.id));
  assert.equal(h.dialogOpen(), false); assert.equal(calls, 2);
});

test('repeated Enter on an unchanged pending search sends only one request', async () => {
  const waiting = deferred(); let calls = 0;
  const { h } = setup({ search() { calls++; return waiting.promise; } });
  h.click('search'); h.filter('Kyoto'); h.searchKey('Enter'); h.searchKey('Enter');
  assert.equal(calls, 1);
  waiting.resolve({ places: apiPlaces, clarification: null }); await h.settle();
  assert.equal(h.state().loading.search, false);
});

test('empty days and cleared date/time block calculation without an API call', async () => {
  const { h, calls } = setup();
  h.click('continue'); h.click('calculate');
  assert.match(h.message(), /1일차/);
  assert.equal(calls.length, 0);
  h.click('view', { view: 'input' }); h.change('start-date', ''); h.click('continue');
  assert.equal(h.state().view, 'input'); assert.match(h.message(), /시작일/);
  h.change('start-date', '2026-11-10'); h.change('start-time', ''); h.click('continue');
  assert.equal(h.state().view, 'input'); assert.match(h.message(), /출발 시각/);
});

test('API failure can be retried and successful calculation clears its error', async () => {
  let fail = true; let attempts = 0;
  const { h } = setup({ async optimize(draft) { attempts++; if (fail) throw new Error('retry me'); return parseRouteResponse(routeResponse(draft), draft); } });
  await prepare(h); h.click('calculate'); h.click('calculate'); await h.settle();
  assert.equal(attempts, 1); assert.match(h.markup(), /retry me/);
  fail = false; h.click('calculate'); await h.settle();
  assert.equal(h.state().view, 'result'); assert.equal(h.state().operationError, '');
  assert.equal(attempts, 2);
});

test('changing dates and trip length rejects a pending recommendation response', async () => {
  for (const [field, value] of [['start-date', '2026-12-01'], ['day-count', '1']]) {
    const waiting = deferred(); const { h } = setup({ search: () => waiting.promise });
    h.click('continue'); h.click('load-initial'); h.click('view', { view: 'input' });
    h.change(field, value); h.click('continue');
    waiting.resolve({ places: apiPlaces, clarification: null }); await h.settle();
    assert.deepEqual(h.state().initialPool, []); assert.equal(h.state().loading.initial, false);
  }
});

test('nearby tabs and collapse ignore old responses and recover for a retry', async () => {
  const waiting = deferred(); const { h } = setup({ nearby: () => waiting.promise });
  await prepare(h); h.click('calculate'); await h.settle();
  h.click('toggle-nearby'); h.click('load-nearby');
  h.click('nearby-kind', { kind: 'text' });
  waiting.resolve([apiPlaces[2]]); await h.settle();
  assert.deepEqual(h.state().nearbyPool, []); assert.equal(h.state().loading.nearby, false);
  h.input('nearby-query', 'gardens'); h.click('load-nearby'); await h.settle();
  assert.ok(h.state().nearbyPool.length);
  h.click('toggle-nearby'); h.click('toggle-nearby');
  assert.equal(h.state().loading.nearby, false);
});

test('canceling multi-select never changes the draft and reopening starts empty', async () => {
  const { h } = setup(); const before = h.state().draft;
  h.click('search'); h.filter('Kyoto'); h.searchKey('Enter'); await h.settle();
  h.dialogClick({ addId: apiPlaces[0].id }); h.dialogClick({ addId: apiPlaces[1].id });
  h.dialogClick({ action: 'close-search' });
  assert.deepEqual(h.state().draft, before);
  h.click('search'); assert.equal(h.confirm.disabled, true);
  assert.doesNotMatch(h.selectionMarkup(), /data-unselect-id=/);
});

test('shrinking and extending a trip restores selected places and clamps the active day', async () => {
  const { h } = setup(); h.click('day', { day: '1' });
  h.click('search'); h.filter('Kyoto'); h.searchKey('Enter'); await h.settle();
  h.dialogClick({ addId: apiPlaces[0].id }); h.dialogClick({ action: 'confirm-search' });
  h.change('day-count', '1');
  assert.equal(h.state().map.activeDayId, '0');
  assert.deepEqual(h.state().draft.days[1].placeIds, [apiPlaces[0].id]);
  assert.match(h.markup(), /기간 밖 장소 1곳을 보관/);
  h.change('day-count', '2'); h.click('day', { day: '1' });
  assert.deepEqual(h.state().draft.days[1].placeIds, [apiPlaces[0].id]);
});

test('removing a newly recommended place and recalculating keeps the original stops', async () => {
  const { h } = setup(); await prepare(h); h.click('calculate'); await h.settle();
  const original = h.state().draft.days[0].placeIds;
  h.click('toggle-nearby'); h.click('load-nearby'); await h.settle();
  h.click('recommend-add', { id: apiPlaces[2].id });
  assert.ok(h.state().map.places.some((place) => place.id === apiPlaces[2].id && place.state === 'pending'));
  h.click('pending-remove', { id: apiPlaces[2].id }); h.click('calculate'); await h.settle();
  assert.deepEqual(h.state().draft.days[0].placeIds, original);
  assert.equal(h.state().result.revision, h.state().draft.revision);
  assert.doesNotMatch(h.markup(), /class="pending-additions"/);
});

test('recommendation-only completion and an example one-stop itinerary both work', async () => {
  const { h } = setup(); h.change('day-count', '1'); h.click('continue');
  h.click('load-initial'); await h.settle();
  h.click('choose-toggle', { id: apiPlaces[0].id }); h.click('choose-toggle', { id: apiPlaces[1].id });
  h.click('calculate'); await h.settle(); assert.equal(h.state().view, 'result');
  const example = harness(); example.change('day-count', '1'); example.click('remove', { id: 'demo-nishiki' });
  example.change('start-time', '23:50'); example.click('continue'); example.click('calculate'); await example.settle();
  assert.equal(example.state().view, 'result'); assert.equal(example.state().result.days[0].legs.length, 0);
  assert.match(example.markup(), /¥0/); assert.match(example.markup(), /23:50/);
});
