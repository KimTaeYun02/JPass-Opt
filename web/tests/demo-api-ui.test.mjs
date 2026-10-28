import assert from 'node:assert/strict';
import test from 'node:test';
import { harness } from './demo-harness.mjs';
import { apiPlaces, routeResponse } from './api-fixtures.mjs';
import { parseRouteResponse, createTravelApi } from '../src/demo-api.ts';

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function clientHarness(overrides = {}) {
  const calls = [];
  const client = {
    async search(query) { calls.push(['search', query]); return { places: apiPlaces, clarification: null }; },
    async optimize(draft) { calls.push(['optimize', draft]); return parseRouteResponse(routeResponse(draft), draft); },
    async nearby(draft, result, day) { calls.push(['nearby', draft, result, day]); return [apiPlaces[2]]; },
    ...overrides,
  };
  return { h: harness({ mode: 'api', client }), calls };
}
async function fill(h) {
  h.change('day-count', '1');
  h.click('search');
  h.filter('Japan');
  h.dialogClick({ action: 'submit-search' });
  await h.settle();
  h.dialogClick({ addId: apiPlaces[0].id });
  h.dialogClick({ addId: apiPlaces[1].id });
  h.dialogClick({ action: 'confirm-search' });
  h.click('continue');
}
test('live UI has no startup/keystroke requests and renders server order, time, fare, steps and station path', async () => {
  const { h, calls } = clientHarness();
  assert.match(h.markup(), /실제 API 연결/);
  assert.deepEqual(h.state().draft.days[0].placeIds, []);
  assert.equal(calls.length, 0);
  h.click('search'); h.filter('Japan');
  assert.equal(calls.length, 0);
  h.closeDialog();
  await fill(h);
  assert.equal(calls.filter(([kind]) => kind === 'search').length, 1);
  assert.doesNotMatch(h.searchMarkup(), /<img src=x/);
  h.click('calculate');
  await h.settle();
  assert.equal(h.state().view, 'result');
  assert.deepEqual(h.state().result.days[0].order, [apiPlaces[1].id, apiPlaces[0].id]);
  assert.match(h.markup(), /08:17/);
  assert.match(h.markup(), /¥321/);
  assert.match(h.markup(), /server line/);
  assert.doesNotMatch(h.markup(), /교통편 예시|교통비 예시/);
  assert.equal(h.state().map.routePath[0].lat, 35);
  h.click('toggle-nearby');
  assert.equal(calls.filter(([kind]) => kind === 'nearby').length, 0);
  h.click('load-nearby'); await h.settle();
  assert.equal(calls.filter(([kind]) => kind === 'nearby').length, 1);
  assert.doesNotMatch(h.markup(), /<img src=x/);
  assert.match(h.markup(), /&lt;img/);
  h.click('recommend-add', { id: apiPlaces[2].id });
  assert.match(h.markup(), /추가한 장소/);
  assert.ok(h.state().draft.days[0].placeIds.includes(apiPlaces[2].id));
});
test('live route failures never become example results, recover loading and retain prior successful result', async () => {
  let fail = false;
  const { h } = clientHarness({ async optimize(draft) { if (fail) throw new Error('server unavailable'); return parseRouteResponse(routeResponse(draft), draft); } });
  await fill(h); h.click('calculate'); await h.settle();
  const previous = h.state().result;
  fail = true; h.click('calculate'); await h.settle();
  assert.deepEqual(h.state().result, previous);
  assert.match(h.markup(), /role="alert">server unavailable/);
  assert.doesNotMatch(h.markup(), /일정을 준비하고 있어요/);
});
test('live calculation rejects edited snapshot and initial suggestions reject responses after day navigation', async () => {
  const waiting = deferred();
  const { h } = clientHarness({ optimize: () => waiting.promise });
  await fill(h); h.click('calculate');
  const snapshot = h.state().draft;
  h.change('start-time', '09:00');
  waiting.resolve(parseRouteResponse(routeResponse(snapshot), snapshot));
  await h.settle();
  assert.equal(h.state().result, undefined);
  assert.match(h.message(), /입력이 바뀌었어요/);
  const late = deferred();
  const other = clientHarness({ search: () => late.promise }).h;
  other.click('continue');
  other.click('load-initial');
  other.click('day', { day: '1' }); other.click('day', { day: '0' });
  late.resolve({ places: [apiPlaces[2]], clarification: null }); await other.settle();
  assert.deepEqual(other.state().initialPool, []);
});
test('live modal ignores late responses after close and shows clarification or service errors without fallback', async () => {
  const waiting = deferred();
  const { h } = clientHarness({ search: () => waiting.promise });
  h.click('search'); h.filter('late'); h.dialogClick({ action: 'submit-search' }); h.closeDialog();
  waiting.resolve({ places: apiPlaces, clarification: null }); await h.settle();
  assert.deepEqual(h.state().draft.days[0].placeIds, []);
  const question = clientHarness({ async search() { return { places: [], clarification: '어느 지역인가요?' }; } }).h;
  question.click('search'); question.filter('q'); question.dialogClick({ action: 'submit-search' }); await question.settle();
  assert.match(question.searchMarkup(), /어느 지역인가요/);
  const failed = clientHarness({ async search() { throw new Error('키 설정이 필요해요.'); } }).h;
  failed.click('continue'); failed.click('load-initial'); await failed.settle();
  assert.match(failed.markup(), /키 설정이 필요해요/);
  assert.deepEqual(failed.state().initialPool, []);
});
test('initial API suggestions support multi-select and preserve owned picks after taste edits', async () => {
  const { h, calls } = clientHarness();
  h.change('day-count', '1'); h.click('continue');
  h.click('load-initial'); await h.settle();
  assert.equal(calls.length, 1);
  h.click('choose-toggle', { id: apiPlaces[0].id }); h.click('choose-toggle', { id: apiPlaces[1].id });
  assert.match(h.markup(), /추천에서 고른 장소 · 2곳/);
  h.click('view', { view: 'input' }); h.click('tag', { tag: 'anime' }); h.click('continue');
  assert.match(h.markup(), /추천에서 고른 장소 · 2곳/);
  h.click('choose-toggle', { id: apiPlaces[0].id });
  assert.deepEqual(h.state().draft.days[0].placeIds, [apiPlaces[1].id]);
});
test('query blur does not clear loaded candidates and explicit identical searches reuse cache', async () => {
  const { h, calls } = clientHarness();
  h.click('continue'); h.click('load-initial'); await h.settle();
  const previous = h.state().initialPool;
  h.change('choice-query', 'query after blur');
  assert.deepEqual(h.state().initialPool, previous);
  h.click('choose-toggle', { id: apiPlaces[0].id });
  assert.ok(h.state().draft.days[0].placeIds.includes(apiPlaces[0].id));
  h.click('load-initial'); await h.settle();
  assert.equal(calls.length, 1, 'unchanged request uses memory cache');
  for (let i = 0; i < 21; i++) {
    h.input('choice-query', `query ${i}`);
    h.click('load-initial'); await h.settle();
  }
  h.input('choice-query', '교토 관광지'); h.click('load-initial'); await h.settle();
  assert.equal(calls.length, 23, 'evicted query is fetched again');
});
test('live UI runs through real HTTP adapter, Enter search, request builder and response parser', async () => {
  const calls = [];
  const client = createTravelApi('/api', async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, method: init.method, headers: init.headers, body });
    if (url.endsWith('/places/search')) return new Response(JSON.stringify({ textQueries: ['q'], clarificationQuestion: null, results: [{ response: { places: apiPlaces.map((place) => ({ id: place.googlePlaceId, displayName: { text: place.name }, location: { latitude: place.lat, longitude: place.lng } })) } }] }));
    return new Response(JSON.stringify(routeResponse()));
  });
  const h = harness({ mode: 'api', client });
  h.change('day-count', '1'); h.click('search'); h.filter('query'); h.searchKey('Enter'); await h.settle();
  h.dialogClick({ addId: apiPlaces[0].id }); h.dialogClick({ addId: apiPlaces[1].id }); h.dialogClick({ action: 'confirm-search' });
  h.click('continue'); h.click('calculate'); await h.settle();
  assert.equal(h.state().result.dataSource, 'live');
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, '/api/places/search');
  assert.equal(calls[1].url, '/api/routes/optimize');
  assert.equal(calls[1].method, 'POST');
  assert.equal(calls[1].headers['Content-Type'], 'application/json');
  assert.deepEqual(calls[1].body.days[0].places.map((place) => place.googlePlaceId), ['contract-a', 'contract-b']);
  const example = harness();
  assert.match(example.markup(), /href="\.\/demo.html\?mode=example" class="brand"/);
});
