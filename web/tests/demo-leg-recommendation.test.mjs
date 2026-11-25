import assert from 'node:assert/strict';
import test from 'node:test';
import { buildNearbyRequest, createTravelApi, searchBiasForRouteLeg } from '../src/demo-api.ts';
import { writeDraftFile } from '../src/demo-draft-file.ts';
import { addPlace, emptyDraft, placeById, registerPlaces, setDayCount } from '../src/demo-model.ts';
import { harness } from './demo-harness.mjs';

const places = [
  ['a', '방문지 A', 35, 135], ['b', '방문지 B', 35.1, 135.1], ['c', '방문지 C', 36, 136],
  ['garden', '산책할 정원', 35.05, 135.04], ['cafe', '쉬어 갈 카페', 35.3, 135.3],
].map(([googlePlaceId, name, lat, lng]) => ({
  id: `google:leg-${googlePlaceId}`, googlePlaceId: `leg-${googlePlaceId}`, name, lat, lng,
  tags: ['culture'], rank: 0, area: '일본', station: '경로 계산 후 확인',
}));
registerPlaces(places);
const [a, b, c, garden, cafe] = places;

function trip(stops = [a, b, c]) {
  return stops.reduce((draft, stop) => addPlace(draft, 0, stop.id), setDayCount(emptyDraft(), 1));
}

function route(draft, walkingFirst = false) {
  const order = draft.days[0].placeIds;
  const point = ({ lat, lng }) => ({ lat, lng });
  const stationFor = (id) => ({ name: `역 ${placeById(id).name.at(-1)}`, location: point(placeById(id)) });
  const stations = Object.fromEntries(order.map((id) => [id, stationFor(id)]));
  const legs = order.slice(1).map((to, index) => {
    const from = order[index];
    const start = stations[from], end = stations[to];
    const transfer = { name: `${index + 1}번 환승역`, location: { lat: start.location.lat + .02, lng: start.location.lng + .02 } };
    const step = [{ fromStation: start, toStation: transfer, line: 'Test 1' }, { fromStation: transfer, toStation: end, line: 'Test 2' }];
    if (walkingFirst && index === 0) {
      const fromPoint = { kind: 'place', name: placeById(from).name, location: point(placeById(from)) };
      const toPoint = { kind: 'place', name: placeById(to).name, location: point(placeById(to)) };
      return { from, to, minutes: 8, fare: 0, line: '도보', route: [], segments: [{ fromPoint, toPoint, mode: 'walking', durationMinutes: 8, distanceMeters: 500, path: [fromPoint.location, toPoint.location] }] };
    }
    return { from, to, minutes: 15, fare: 200, line: '이동', route: step };
  });
  return { revision: draft.revision, dataSource: 'live', days: [{ order, legs, minutes: 30, fare: 400, stations, accessWalking: walkingFirst, startTime: '08:00' }] };
}

async function calculateDraft(draft, client) {
  const h = harness({ mode: 'api', client, storage: new Map([['jpass.travel-draft.api', writeDraftFile(draft, 'api')]]) });
  h.click('continue');
  h.click('calculate');
  await h.settle();
  assert.equal(h.state().view, 'result');
  return h;
}

test('Nearby payload contains only the selected leg stops and keeps whole-day requests available', async () => {
  const draft = trip();
  const result = route(draft);
  const requests = [];
  const api = createTravelApi('/api', async (url, options) => {
    requests.push([url, JSON.parse(options.body)]);
    return Response.json({ results: [] });
  });
  await api.nearby(draft, result, 0, 0);
  await api.nearby(draft, result, 0);
  const names = ({ stations }) => stations.map(({ name }) => name);
  assert.equal(requests[0][0], '/api/nearby/recommend');
  assert.deepEqual(names(requests[0][1]), ['역 A', '1번 환승역', '역 B']);
  assert.deepEqual(names(requests[1][1]), ['역 A', '역 B', '역 C', '1번 환승역', '2번 환승역']);
  assert.throws(() => buildNearbyRequest(draft, route(draft, true), 0, 0), /장소 검색/);
  assert.throws(() => buildNearbyRequest(draft, result, 0, 7), /역 정보/);
});

test('selecting a leg ignores a late response from another leg and adds a stop for explicit recalculation', async () => {
  const calls = [];
  const first = Promise.withResolvers();
  const client = {
    async optimize(draft) { calls.push(['optimize', draft.days[0].placeIds]); return route(draft); },
    nearby(_draft, _result, day, index) {
      calls.push(['nearby', day, index]);
      return index === 0 ? first.promise : Promise.resolve([b, cafe]);
    },
  };
  const h = await calculateDraft(trip(), client);
  h.click('toggle-leg-nearby', { leg: '0' });
  assert.match(h.markup(), /방문지 A → 방문지 B 주변 후보/);
  assert.equal(calls.length, 1);
  h.click('load-nearby');
  h.click('toggle-leg-nearby', { leg: '1' });
  h.click('load-nearby');
  await h.settle();
  first.resolve([garden]);
  await h.settle();
  assert.match(h.markup(), /방문지 B → 방문지 C 주변 후보/);
  assert.doesNotMatch(h.markup(), /산책할 정원|방문지 A → 방문지 B 주변 후보/);
  assert.match(h.markup(), /1곳을 찾았어요/);
  assert.match(h.markup(), /추가 이동·체류시간은 미확인/);
  assert.deepEqual(calls.slice(1), [['nearby', 0, 0], ['nearby', 0, 1]]);
  h.click('recommend-add', { id: cafe.id });
  assert.match(h.markup(), /추가한 장소/);
  assert.match(h.markup(), /구간 주변을 다시 찾으려면 변경한 일정으로 재계산/);
  h.click('load-nearby');
  assert.equal(calls.length, 3);
  h.click('calculate');
  await h.settle();
  assert.deepEqual(calls.at(-1), ['optimize', [a.id, b.id, c.id, cafe.id]]);
  assert.doesNotMatch(h.markup(), /주변 후보예요/);
  h.click('toggle-nearby'); h.click('load-nearby');
  await h.settle();
  assert.deepEqual(calls.at(-1), ['nearby', 0, undefined]);
});

test('a direct walking leg starts with geographically biased Text Search and no Nearby request', async () => {
  const calls = [];
  const client = {
    async optimize(draft) { return route(draft, true); },
    async search(query, bias) { calls.push([query, bias]); return { places: [garden], clarification: null }; },
    nearby() { assert.fail('direct walking leg must not use the station endpoint'); },
  };
  const h = await calculateDraft(trip(), client);
  h.click('toggle-leg-nearby', { leg: '0' });
  assert.match(h.markup(), /이 구간의 이용역 정보가 없어 장소 검색/);
  h.click('load-nearby');
  await h.settle();
  assert.equal(calls.length, 1);
  assert.match(calls[0][0], /방문지 A에서 방문지 B로 가는 길 주변/);
  assert.deepEqual(calls[0][1], searchBiasForRouteLeg(route(trip(), true).days[0], 0));
  assert.ok(calls[0][1].rectangle.high.latitude < c.lat);
  assert.match(h.markup(), /산책할 정원/);
});

test('one-stop plans keep the day recommendations without showing a leg control', async () => {
  const h = await calculateDraft(trip([a]), { async optimize(draft) { return route(draft); } });
  assert.doesNotMatch(h.markup(), /toggle-leg-nearby/);
  assert.match(h.markup(), /data-action="toggle-nearby"/);
});
