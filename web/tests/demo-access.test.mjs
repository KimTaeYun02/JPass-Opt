import assert from 'node:assert/strict';
import test from 'node:test';
import { accessRouteResponse, draftWithPlaces } from './api-fixtures.mjs';
import { buildRouteRequest, parseRouteResponse, buildNearbyRequest, createTravelApi } from '../src/demo-api.ts';
import { mapRoutePaths } from '../src/demo-map-controller.ts';
import { placeById } from '../src/demo-model.ts';
import { renderRouteDay } from '../src/demo-route-render.ts';

const draft = () => ({ ...draftWithPlaces(), includeAccessWalking: true });
test('shared-station walking-only legs keep both positive access segments without transit', () => {
  const raw = accessRouteResponse(draft()), day = raw.days[0], leg = day.legs[0];
  const [outward, , inward] = leg.segments;
  inward.fromPoint = structuredClone(outward.toPoint);
  inward.path[0] = inward.fromPoint.location;
  leg.segments = [outward, inward]; leg.route = [];
  raw.totalTravelMinutes = day.totalTravelMinutes = leg.durationMinutes = 6;
  raw.totalTransportFare = day.totalTransportFare = leg.fare = 0;
  day.endAt = leg.arrivalAt = day.visits[1].estimatedArrivalAt = new Date(Date.parse(day.startAt) + 6 * 60000).toISOString();
  const parsed = parseRouteResponse(raw, draft());
  assert.equal(parsed.days[0].minutes, 6);
  assert.deepEqual(parsed.days[0].legs[0].segments.map(s => s.mode), ['walking', 'walking']);
  assert.equal(parsed.days[0].legs[0].route.length, 0);
});
test('walking is explicit opt-in and incompatible or incomplete responses are rejected', () => {
  assert.equal(buildRouteRequest(draft()).includeAccessWalking, true);
  assert.ok(!('includeAccessWalking' in buildRouteRequest(draftWithPlaces())));
  const parsed = parseRouteResponse(accessRouteResponse(draft()), draft());
  assert.equal(parsed.days[0].accessWalking, true);
  assert.deepEqual(parsed.days[0].legs[0].segments.map((s) => s.mode), ['walking', 'transit', 'walking']);
  for (const corrupt of [
    (r) => r.timingBasis = 'stationTravelOnly',
    (r) => delete r.days[0].legs[0].segments,
    (r) => r.days[0].legs[0].segments[0].durationMinutes++,
    (r) => r.days[0].legs[0].segments[0].path = [],
    (r) => r.days[0].legs[0].segments[0].distanceMeters = null,
    (r) => r.days[0].legs[0].segments[0].path[0].lat = Infinity,
    (r) => r.days[0].legs[0].segments[0].fromPoint.location = { lat: 40, lng: 140 },
    (r) => r.days[0].legs[0].segments[1].fromPoint.location = { lat: 40, lng: 140 },
    (r) => delete r.days[0].candidateSearchTruncated,
  ]) { const raw = accessRouteResponse(draft()); corrupt(raw); assert.throws(() => parseRouteResponse(raw, draft())); }
  assert.throws(() => parseRouteResponse(accessRouteResponse(draft()), draftWithPlaces()));
  const equivalent = accessRouteResponse(draft());
  const day = equivalent.days[0];
  day.startAt = new Date(day.startAt).toISOString();
  day.endAt = new Date(day.endAt).toISOString();
  day.legs[0].departureAt = new Date(day.legs[0].departureAt).toISOString();
  day.legs[0].arrivalAt = new Date(day.legs[0].arrivalAt).toISOString();
  assert.equal(parseRouteResponse(equivalent, draft()).days[0].minutes, 17);
});

test('actual walking paths keep bends while transit retains station-step reference lines', () => {
  const leg = parseRouteResponse(accessRouteResponse(draft()), draft()).days[0].legs[0];
  const step = leg.segments[0];
  const paths = mapRoutePaths({ showRoute: true, routeSegments: [{ mode: 'walking', from: step.fromPoint.location, to: step.toPoint.location, path: step.path }], places: [] });
  assert.equal(paths[0].path.length, 3);
  assert.equal(paths[0].path[1].lng, 139.001);
});

test('walking results explain totals, limitations, beta paths and keep tourist places out of station recommendations', () => {
  const result = parseRouteResponse(accessRouteResponse(draft()), draft());
  const day = result.days[0];
  const output = renderRouteDay(day, { live: true, dayIndex: 0, date: draft().startDate, place: placeById });
  assert.match(output, /도보 포함 이동 17분/);
  assert.match(output, /관광지 간 이동/);
  assert.match(output, /3분 · 200m/);
  assert.match(output, /test|server line/);
  assert.match(output, /베타 정보/);
  const truncated = renderRouteDay({ ...day, candidateSearchTruncated: true }, { live: true, dayIndex: 0, date: draft().startDate, place: placeById });
  assert.match(truncated, /role="status">직접 도보는 가까운 일부 구간만 비교/);
  assert.doesNotMatch(output, /장소↔역 이동 정보 미제공/);
  const request = buildNearbyRequest(draft(), result, 0);
  assert.deepEqual(request.stations.map((s) => s.name), ['same station', 'transfer']);
  const walk = day.legs[0].segments[0], end = day.legs[0].segments[2].toPoint;
  day.legs[0].segments = [{ ...walk, toPoint: end }];
  day.legs[0].route = [];
  assert.throws(() => buildNearbyRequest(draft(), result, 0), /추천에 사용할 역/);
});

test('missing optional server configuration gives actionable safe walking error', async () => {
  const api = createTravelApi('/api', async () => new Response('private-key', { status: 503 }));
  await assert.rejects(api.optimize(draft()), (error) => error.status === 503 && /옵션을 끄고/.test(error.message) && !error.message.includes('private-key'));
});
