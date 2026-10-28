import assert from 'node:assert/strict';
import test from 'node:test';
import { draftWithPlaces, routeResponse } from './api-fixtures.mjs';
import { initialDraft, emptyDraft } from '../src/demo-model.ts';
import { buildRouteRequest, parseRouteResponse, normalizeNearby, parseTextSearch, buildNearbyRequest, createTravelApi, formatJapanTime, TravelApiError } from '../src/demo-api.ts';

test('Notion route builder is exact and rejects unresolved example places, empty days and invalid dates', () => {
  const draft = draftWithPlaces();
  const request = buildRouteRequest(draft);
  assert.deepEqual(Object.keys(request).sort(), ['startDate', 'dayCount', 'days'].sort());
  assert.equal(request.days[0].dayIndex, 1);
  assert.equal(request.days[0].dailyStartTime, '08:00');
  assert.deepEqual(request.days[0].places[0], { name: 'same name', googlePlaceId: 'contract-a', location: { lat: 35, lng: 139 } });
  assert.throws(() => buildRouteRequest(initialDraft()), /실제 장소 검색/);
  assert.throws(() => buildRouteRequest(emptyDraft()), /1일차/);
  assert.throws(() => buildRouteRequest({ ...draft, startDate: '2026-02-30' }));
});
test('server order/arrival/fare/steps survive duplicate names and same-station attractions', () => {
  const draft = draftWithPlaces(), raw = routeResponse(draft);
  const result = parseRouteResponse(raw, draft);
  assert.deepEqual(result.days[0].order, draft.days[0].placeIds.toReversed());
  assert.equal(result.days[0].arrivals['google:contract-a'], '2026-11-10T08:17:00+09:00');
  assert.equal(result.days[0].fare, 321);
  assert.equal(result.days[0].legs[0].route[0].line, 'server line');
  assert.equal(result.dataSource, 'live');
  for (const corrupt of [
    (r) => r.days[0].visits[1].googlePlaceId = 'unknown',
    (r) => r.days[0].visits[1].visitId = r.days[0].visits[0].visitId,
    (r) => r.days[0].visits[1].order = 1,
    (r) => r.days[0].legs[0].fromVisitId = 'unknown',
    (r) => r.days[0].legs[0].fare = 999,
    (r) => r.days[0].visits[0].estimatedArrivalAt = '08:00',
    (r) => r.currency = 'USD',
    (r) => r.days[0].dayIndex = 2,
  ]) { const copy = structuredClone(raw); corrupt(copy); assert.throws(() => parseRouteResponse(copy, draft)); }
  assert.equal(formatJapanTime('2026-11-10T15:12:00Z', '2026-11-10'), '+1일 00:12');
  assert.equal(formatJapanTime('2026-11-09T14:12:00Z', '2026-11-10'), '-1일 23:12');
});
const station = { name: 'same station', location: { lat: 35, lng: 139 } };
test('nearby compatibility adapter accepts both documented inner object and results wrapper, dedupes IDs', () => {
  const group = { station, radius: 1000, requestedCount: 3, returnedCount: 1, recommendedPlaces: [{ googlePlaceId: 'ChIJnearby', name: 'nearby', location: { lat: 35, lng: 139 }, searchTags: ['culture'], distanceMeters: 222 }] };
  assert.deepEqual(normalizeNearby(group), normalizeNearby({ results: [group] }));
  assert.equal(normalizeNearby({ results: [group, group] }).length, 1);
  const openTags = structuredClone(group);
  openTags.recommendedPlaces[0].searchTags = ['future-document-tag'];
  assert.deepEqual(normalizeNearby(openTags)[0].searchTags, ['future-document-tag']);
  assert.deepEqual(normalizeNearby(openTags)[0].tags, []);
  const result = parseRouteResponse(routeResponse(), draftWithPlaces());
  const request = buildNearbyRequest(draftWithPlaces(), result, 0);
  assert.equal(request.radius, 1000);
  assert.equal(request.placeCount, 3);
  assert.ok(request.stations.some((station) => station.name === 'transfer'));
  assert.equal(request.stations.filter((station) => station.name === 'same station').length, 1);
  assert.ok(request.stations.every((station) => !('stationId' in station)));
  assert.equal(request.stations.find((station) => station.name === 'same station').line, 'server line');
  assert.ok(!('maxAnchorCount' in request));
  assert.throws(() => buildNearbyRequest({ ...draftWithPlaces(), revision: 99 }, result, 0), /다시 계산/);
  assert.throws(() => normalizeNearby({ ...group, returnedCount: 2 }));
});
test('provisional text-search parses raw Google fields, dedupes, handles clarification and empty results', () => {
  const place = { id: 'ChIJtext', displayName: { text: 'text place' }, location: { latitude: 35, longitude: 139 }, formattedAddress: 'Japan', primaryType: 'park' };
  const response = parseTextSearch({ textQueries: ['query'], clarificationQuestion: null, results: [{ response: { places: [place, place, { id: 'missing coordinate' }, { ...place, displayName: {} }, { ...place, location: {} }] } }] });
  assert.equal(response.places.length, 1);
  assert.equal(response.places[0].googlePlaceId, 'ChIJtext');
  assert.deepEqual(parseTextSearch({ textQueries: null, clarificationQuestion: '어느 지역인가요?', results: [] }), { places: [], clarification: '어느 지역인가요?' });
  assert.deepEqual(parseTextSearch({ textQueries: null, clarificationQuestion: '어느 지역인가요?' }), { places: [], clarification: '어느 지역인가요?' });
  assert.deepEqual(parseTextSearch({ textQueries: ['query'], clarificationQuestion: null, results: [{ response: {} }] }).places, []);
});
test('HTTP uses POST with exact payload and no retry, handles provider/configuration/network failures', async () => {
  const requests = [];
  const api = createTravelApi('/api', async (url, init) => {
    requests.push({ url, init });
    return new Response(JSON.stringify({ textQueries: ['q'], clarificationQuestion: null, results: [] }));
  });
  await api.search(' Kyoto ');
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/places/search');
  assert.deepEqual(JSON.parse(requests[0].init.body), { userQuery: 'Kyoto', languageCode: 'ko', regionCode: 'JP', pageSize: 8 });
  for (const status of [422, 429, 502, 503, 504]) {
    let calls = 0;
    const failing = createTravelApi('/api', async () => { calls++; return new Response('provider private details', { status }); });
    await assert.rejects(failing.search('q'), (error) => error instanceof TravelApiError && error.status === status && !error.message.includes('private'));
    assert.equal(calls, 1);
  }
  const offline = createTravelApi('/api', async () => { throw new TypeError('private network detail'); });
  await assert.rejects(offline.search('q'), /백엔드에 연결/);
});
