import assert from 'node:assert/strict';
import test from 'node:test';
import { harness } from './demo-harness.mjs';
import { apiPlaces, routeResponse, accessRouteResponse } from './api-fixtures.mjs';
import { parseRouteResponse, createTravelApi } from '../src/demo-api.ts';

const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function clientHarness(overrides = {}) {
  const calls = [];
  const client = {
    initial(draft, day, query) { return client.search(query); },
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

test('walking option updates revisions, renders actual paths and invalidates stale results', async () => {
  const { h, calls } = clientHarness({ async optimize(draft) {
    calls.push(['optimize', draft]);
    return parseRouteResponse(accessRouteResponse(draft), draft);
  } });
  await fill(h);
  const revision = h.state().draft.revision;
  h.click('view', { view: 'input' }); h.change('include-walking', true);
  assert.equal(h.state().draft.revision, revision + 1);
  assert.match(h.markup(), /id="include-walking"[^>]*checked/);
  h.click('continue'); h.click('calculate'); await h.settle();
  assert.equal(calls.at(-1)[1].includeAccessWalking, true);
  assert.equal(h.state().view, 'result');
  assert.deepEqual(h.state().map.routeSegments.map((s) => s.mode), ['walking', 'transit', 'walking']);
  assert.equal(h.state().map.routeSegments[0].path.length, 3);
  assert.match(h.markup(), /도보 포함 이동/);
  assert.doesNotMatch(h.markup(), /장소↔역 이동 정보 미제공/);
  assert.match(h.markup(), /관광지↔역 도보 포함 · 체류시간 미포함/);
  assert.doesNotMatch(h.markup(), /역 간 이동 기준 · 관광지↔역 이동·체류시간 미포함/);
  h.click('view', { view: 'input' }); h.change('include-walking', false);
  assert.match(h.markup(), /역 간 이동 기준 · 관광지↔역 이동·체류시간 미포함/);
  h.click('view', { view: 'result' });
  assert.match(h.markup(), /일정이 변경됐어요/);
  assert.match(h.markup(), /관광지↔역 도보 포함 · 체류시간 미포함/);
});

test('walking opt-in during an in-flight request discards the old result', async () => {
  const pending = deferred();
  const { h } = clientHarness({ optimize: () => pending.promise });
  await fill(h); h.click('calculate');
  h.change('include-walking', true);
  pending.resolve(parseRouteResponse(routeResponse({ ...h.state().draft, includeAccessWalking: false }), { ...h.state().draft, includeAccessWalking: false, revision: h.state().draft.revision - 1 }));
  await h.settle();
  assert.equal(h.state().result, undefined);
  assert.match(h.message(), /입력이 바뀌었어요/);
});

test('live map and SVG preserve step boundaries, explicit walking and truthful mode legends', async () => {
  const { h } = clientHarness({ async optimize(draft) {
    const response = routeResponse(draft);
    const station = (name, lat) => ({ name, location: { lat, lng: 139 } });
    response.days[0].legs[0].route = [
      { fromStation: station('a', 35.2), toStation: station('b', 35.3), line: 'JR line' },
      { fromStation: station('b', 35.3), toStation: station('c', 35.4), line: '도보' },
      { fromStation: station('d', 35.5), toStation: station('e', 35.6), line: '도보가 포함된 노선명' },
    ];
    return parseRouteResponse(response, draft);
  } });
  await fill(h); h.click('calculate'); await h.settle();
  const segments = h.state().map.routeSegments;
  assert.deepEqual(segments.map((entry) => entry.mode), ['transit', 'walking', 'transit']);
  assert.deepEqual(segments[1].to, { lat: 35.4, lng: 139 });
  assert.deepEqual(segments[2].from, { lat: 35.5, lng: 139 });
  assert.equal((h.markup().match(/<polyline /g) ?? []).length, 3);
  assert.match(h.markup(), /stroke="#258258" stroke-width="4" stroke-dasharray="4 10"/);
  assert.match(h.markup(), /legend-walking/);
  assert.match(h.markup(), /역 좌표 간 참고선이며 실제 도로·선로 모양이 아닙니다/);
  h.click('view', { view: 'input' });
  assert.equal(h.state().map.showRoute, false);
  assert.doesNotMatch(h.markup(), /<polyline |legend-walking/);
});

test('live day with no route steps has no attraction connector or mode legend', async () => {
  const { h } = clientHarness({ async optimize(draft) {
    const response = routeResponse(draft);
    response.days[0].legs[0].route = [];
    return parseRouteResponse(response, draft);
  } });
  await fill(h); h.click('calculate'); await h.settle();
  assert.deepEqual(h.state().map.routeSegments, []);
  assert.doesNotMatch(h.markup(), /<polyline |legend-walking|legend-route/);
});
test('walking-only map legend has no transit swatch and agrees with the result list', async () => {
  const { h } = clientHarness({ async optimize(draft) {
    const response = routeResponse(draft);
    response.days[0].legs[0].route[0].line = '도보';
    return parseRouteResponse(response, draft);
  } });
  await fill(h); h.click('calculate'); await h.settle();
  assert.match(h.markup(), /class="route-mode walking"/);
  assert.match(h.markup(), /legend-walking/);
  assert.doesNotMatch(h.markup(), /legend-route/);
  assert.deepEqual(h.state().map.routeSegments.map((entry) => entry.mode), ['walking']);
});

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
  assert.equal(h.state().map.routeSegments[0].from.lat, 35);
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
test('current backend ambiguous search response shows guidance through the HTTP adapter in both views', async () => {
  const calls = [];
  const client = createTravelApi('/api', async (url, init) => {
    const body = JSON.parse(init.body);
    calls.push({ url, body });
    assert.equal(url, '/api/places/search');
    assert.deepEqual(Object.keys(body).sort(), ['pageSize', 'userQuery']);
    return new Response(JSON.stringify({ userQuery: body.userQuery, textQueries: null, results: [] }));
  });
  const h = harness({ mode: 'api', client });
  h.click('search'); h.filter('추천해줘'); h.searchKey('Enter'); await h.settle();
  assert.match(h.searchMarkup(), /지역이나 장소.*구체적으로/);
  assert.doesNotMatch(h.searchMarkup(), /응답 형식|조건에 맞는 장소가 없어요/);
  assert.equal(h.confirm.disabled, true);
  assert.deepEqual(h.state().draft.days[0].placeIds, []);
  h.closeDialog(); h.input('initial-query', '추천해줘'); h.click('continue');
  h.click('load-initial'); await h.settle();
  assert.match(h.markup(), /지역이나 장소.*구체적으로/);
  assert.doesNotMatch(h.markup(), /응답 형식|장소 검색 실패/);
  assert.deepEqual(h.state().initialPool, []);
  assert.equal(calls.length, 2);
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
  h.click('load-initial'); await h.settle();
  assert.equal(calls.length, 1, 'unchanged request uses memory cache');
  for (let i = 0; i < 21; i++) {
    h.input('choice-query', `query ${i}`);
    h.click('load-initial'); await h.settle();
  }
  h.input('choice-query', ''); h.click('load-initial'); await h.settle();
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
