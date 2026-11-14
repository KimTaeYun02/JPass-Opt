import assert from 'node:assert/strict';
import test from 'node:test';
import { createTravelApi, buildStationRequest, parseStationResponse } from '../src/demo-api.ts';
import { emptyDraft, mergeCandidatePlaces } from '../src/demo-model.ts';
import { apiPlaces, draftWithPlaces, routeResponse } from './api-fixtures.mjs';
import { harness } from './demo-harness.mjs';

const station = { name: 'anchor station', location: { lat: 35, lng: 139 }, line: 'test line' };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });
const stationResponse = (body) => ({ days: body.days.map((day) => ({ dayIndex: day.dayIndex, places: day.places.map((place) => ({ googlePlaceId: place.googlePlaceId, status: 'ok', station, reason: null })) })) });
const textResponse = (places) => ({ textQueries: ['query'], clarificationQuestion: null, results: [{ response: { places: places.map((p) => ({ id: p.googlePlaceId, displayName: { text: p.name }, location: { latitude: p.lat, longitude: p.lng } })) } }] });
const nearResponse = (places) => ({ results: [{ station, radius: 1000, requestedCount: 3, returnedCount: places.length, recommendedPlaces: places.map((p) => ({ googlePlaceId: p.googlePlaceId, name: p.name, location: { lat: p.lat, lng: p.lng }, searchTags: ['culture'], distanceMeters: 250 })) }] });
const deferred = () => { let resolve; const promise = new Promise((yes) => { resolve = yes; }); return { promise, resolve }; };

function httpClient({ text = apiPlaces.slice(1), nearby = apiPlaces.slice(2), fail = [], resolve = stationResponse } = {}) {
  const calls = [];
  const client = createTravelApi('/api', async (url, init) => {
    const body = JSON.parse(init.body); calls.push({ url, body, signal: init.signal });
    if (fail.some((path) => url.endsWith(path))) return json({}, 502);
    if (url.endsWith('/stations/resolve')) return json(resolve(body));
    if (url.endsWith('/nearby/recommend')) return json(nearResponse(nearby));
    if (url.endsWith('/routes/optimize')) return json(routeResponse());
    assert.ok(url.endsWith('/places/search'));
    return json(textResponse(text));
  });
  return { client, calls };
}

test('station contract uses one-based day and joins by Google ID, including shared stations', () => {
  const request = buildStationRequest(draftWithPlaces(), 0);
  assert.deepEqual(request.days[0], { dayIndex: 1, places: apiPlaces.slice(0, 2).map((p) => ({ googlePlaceId: p.googlePlaceId, location: { lat: p.lat, lng: p.lng } })) });
  const response = stationResponse(request);
  response.days[0].places.reverse();
  assert.deepEqual(parseStationResponse(response, request), { stations: [station], warnings: [] });
  for (const corrupt of [
    (r) => r.days[0].dayIndex = 2,
    (r) => r.days[0].places[0].googlePlaceId = 'unknown',
    (r) => r.days[0].places[0].googlePlaceId = r.days[0].places[1].googlePlaceId,
    (r) => r.days[0].places[0].station = null,
    (r) => r.days[0].places[0].status = 'unexpected',
    (r) => r.days[0].places.pop(),
  ]) { const copy = structuredClone(response); corrupt(copy); assert.throws(() => parseStationResponse(copy, request)); }
  response.days[0].places[0] = { googlePlaceId: response.days[0].places[0].googlePlaceId, status: 'not_found', station: null };
  assert.equal(parseStationResponse(response, request).warnings.length, 1);
  assert.match(parseStationResponse(response, request).warnings[0], /찾지 못했어요/);
  for (const reason of ['timeout', 'unavailable', 'invalid_response']) {
    response.days[0].places[0] = { googlePlaceId: response.days[0].places[0].googlePlaceId, status: 'error', station: null, reason };
    const parsed = parseStationResponse(response, request);
    assert.match(parsed.warnings[0], /조회에 실패.*다시 추천받기/);
    assert.doesNotMatch(parsed.warnings[0], /찾지 못했어요/);
    assert.deepEqual(parsed.stations, [station]);
  }
  assert.throws(() => buildStationRequest(emptyDraft(), 0));
});

test('pre-optimization fan-out sends coordinates, query and tastes, merges IDs without optimizing', async () => {
  const { client, calls } = httpClient();
  const draft = draftWithPlaces(); draft.request = '조용한 곳';
  const result = await client.initial(draft, 0, 'Tokyo gardens');
  assert.deepEqual(calls.map((c) => c.url).sort(), ['/api/nearby/recommend', '/api/places/search', '/api/stations/resolve']);
  const search = calls.find((c) => c.url.endsWith('/places/search')).body;
  assert.match(search.userQuery, /Tokyo gardens.*역사·문화.*조용한 곳/);
  assert.ok(search.locationBias.rectangle.low.latitude < apiPlaces[0].lat);
  const nearby = calls.find((c) => c.url.endsWith('/nearby/recommend')).body;
  assert.deepEqual(nearby.stations, [station]);
  assert.deepEqual(nearby.travelTypes, draft.tags);
  assert.match(nearby.userRequest, /Tokyo gardens.*조용한 곳/);
  assert.equal(result.places.length, 2);
  assert.deepEqual(result.places.find((p) => p.googlePlaceId === apiPlaces[2].googlePlaceId).sources, ['nearby', 'text_search']);
  assert.equal(result.places[0].distanceMeters, 250);
});

test('no anchors or no tastes skip nearby; an empty day requires a location or query', async () => {
  for (const withAnchors of [false, true]) {
    const { client, calls } = httpClient();
    const draft = withAnchors ? draftWithPlaces() : emptyDraft();
    draft.tags = [];
    await client.initial(draft, 0, 'Kyoto gardens');
    assert.deepEqual(calls.map((c) => c.url), ['/api/places/search']);
    assert.equal('locationBias' in calls[0].body, withAnchors);
  }
  const { client, calls } = httpClient();
  await assert.rejects(client.initial(emptyDraft(), 0, ''), /지역/);
  assert.equal(calls.length, 0);
});

test('partial search/provider failures keep successful candidates and both failures reject', async () => {
  for (const failed of ['/nearby/recommend', '/stations/resolve', '/places/search']) {
    const { client } = httpClient({ fail: [failed] });
    const result = await client.initial(draftWithPlaces(), 0, 'gardens');
    assert.ok(result.places.length > 0);
    assert.ok(result.warnings.length > 0);
  }
  const { client } = httpClient({ fail: ['/places/search', '/nearby/recommend'] });
  await assert.rejects(client.initial(draftWithPlaces(), 0, 'gardens'), /실패/);
  const missing = httpClient({ resolve: (body) => ({ days: body.days.map((day) => ({ ...day, places: day.places.map((p) => ({ googlePlaceId: p.googlePlaceId, status: 'not_found', station: null })) })) }) });
  const result = await missing.client.initial(draftWithPlaces(), 0, 'gardens');
  assert.ok(result.places.length); assert.ok(result.warnings.length);
  assert.ok(!missing.calls.some((c) => c.url.endsWith('/nearby/recommend')));
});

test('canceling while stations are pending prevents the downstream Nearby call', async () => {
  const waiting = deferred(), calls = [];
  const client = createTravelApi('/api', async (url, init) => {
    calls.push(url);
    if (url.endsWith('/stations/resolve')) return waiting.promise;
    return json(textResponse(apiPlaces));
  });
  const controller = new AbortController();
  const pending = client.initial(draftWithPlaces(), 0, 'gardens', controller.signal);
  controller.abort();
  waiting.resolve(json(stationResponse(buildStationRequest(draftWithPlaces(), 0))));
  await pending;
  assert.ok(!calls.some((url) => url.endsWith('/nearby/recommend')));
});

test('candidate merge uses Google ID even when local IDs differ', () => {
  const merged = mergeCandidatePlaces([[{ ...apiPlaces[0], sources: ['nearby'], tags: ['nature'] }], [{ ...apiPlaces[0], id: 'another-local-id', sources: ['text_search'], tags: ['culture'] }]]);
  assert.equal(merged.length, 1);
  assert.deepEqual(merged[0].tags, ['nature', 'culture']);
  assert.deepEqual(merged[0].sources, ['nearby', 'text_search']);
});

test('UI keeps day queries, rejects stale fan-out responses and retains successful day candidates', async () => {
  const first = deferred(), second = deferred(), requests = [];
  const client = {
    initial(draft, day, query, signal) { requests.push({ day, query, signal }); return requests.length === 1 ? first.promise : second.promise; },
  };
  const h = harness({ mode: 'api', client });
  h.input('initial-query', 'Tokyo'); h.click('continue'); h.click('load-initial');
  h.input('choice-query', 'Kyoto'); h.click('load-initial');
  assert.equal(requests[0].signal.aborted, true);
  second.resolve({ places: [apiPlaces[1]], clarification: null }); await h.settle();
  first.resolve({ places: [apiPlaces[0]], clarification: null }); await h.settle();
  assert.deepEqual(h.state().initialPool, [apiPlaces[1].id]);
  h.click('day', { day: '1' }); h.input('choice-query', 'Osaka');
  h.click('day', { day: '0' });
  assert.match(h.markup(), /value="Kyoto"/);
  assert.deepEqual(h.state().initialPool, [apiPlaces[1].id]);
  h.click('day', { day: '1' }); assert.match(h.markup(), /value="Osaka"/);
});

test('UI refresh failures preserve candidates and selections, and typing never requests', async () => {
  let attempts = 0;
  const h = harness({ mode: 'api', client: { async initial() { attempts++; if (attempts > 1) throw new Error('both failed'); return { places: apiPlaces, clarification: null, warnings: ['Nearby unavailable'] }; } } });
  h.input('initial-query', 'Kyoto'); assert.equal(attempts, 0);
  h.click('continue'); h.click('load-initial'); await h.settle();
  h.click('choose-toggle', { id: apiPlaces[0].id });
  h.click('load-initial'); await h.settle();
  assert.deepEqual(h.state().initialPool, apiPlaces.map((p) => p.id));
  assert.deepEqual(h.state().draft.days[0].placeIds, [apiPlaces[0].id]);
  assert.match(h.markup(), /both failed/);
});

test('a selected candidate missing from refreshed results stays visible and cancelable', async () => {
  let attempts = 0;
  const h = harness({ mode: 'api', client: { async initial() { return { places: ++attempts === 1 ? apiPlaces : [apiPlaces[1]], clarification: null, warnings: ['retryable source failure'] }; } } });
  h.click('continue'); h.click('load-initial'); await h.settle();
  h.click('choose-toggle', { id: apiPlaces[0].id }); h.click('load-initial'); await h.settle();
  assert.deepEqual(h.state().initialPool, [apiPlaces[1].id]);
  assert.match(h.markup(), /data-action="choose-toggle" data-id="google:contract-a" aria-pressed="true"/);
  h.click('choose-toggle', { id: apiPlaces[0].id });
  assert.deepEqual(h.state().draft.days[0].placeIds, []);
});

test('real HTTP adapter completes station lookup, merged selection, optimization and result display', async () => {
  const { client, calls } = httpClient({ text: apiPlaces.slice(0, 2), nearby: apiPlaces.slice(0, 2) });
  const h = harness({ mode: 'api', client });
  h.change('day-count', '1'); h.click('search'); h.filter('Tokyo'); h.searchKey('Enter'); await h.settle();
  h.dialogClick({ addId: apiPlaces[0].id }); h.dialogClick({ action: 'confirm-search' });
  h.input('initial-query', 'gardens'); h.click('continue'); h.click('load-initial'); await h.settle();
  assert.ok(calls.some((c) => c.url.endsWith('/stations/resolve')));
  assert.ok(!calls.some((c) => c.url.endsWith('/routes/optimize')));
  const beforeRepeat = calls.length;
  h.click('load-initial'); await h.settle();
  assert.equal(calls.length, beforeRepeat, 'source/tag metadata updated by initial results must not defeat request reuse');
  h.click('choose-toggle', { id: apiPlaces[1].id });
  const beforeAnchorChange = calls.length;
  h.click('load-initial'); await h.settle();
  assert.equal(calls.length, beforeAnchorChange + 3, 'adding a selected anchor changes the request and must bypass the old response cache');
  h.click('calculate'); await h.settle();
  assert.equal(h.state().view, 'result');
  assert.deepEqual(h.state().result.days[0].order, [apiPlaces[1].id, apiPlaces[0].id]);
  assert.match(h.markup(), /¥321/);
  assert.deepEqual(calls.find((c) => c.url.endsWith('/routes/optimize')).body.days[0].places.map((p) => p.googlePlaceId), apiPlaces.slice(0, 2).map((p) => p.googlePlaceId));
});
