import assert from 'node:assert/strict';
import test from 'node:test';
import { createTravelApi, parseRouteResponse, resolveApiBase, TravelApiError } from '../src/demo-api.ts';
import { draftWithPlaces, routeResponse } from './api-fixtures.mjs';
import { harness } from './demo-harness.mjs';

test('API bases preserve local proxy and cross-origin deployment paths', async () => {
  for (const value of [undefined, '', '  ']) assert.equal(resolveApiBase(value), '/api');
  for (const [input, expected] of [
    [' /api/ ', '/api'], ['https://example.com/api/', 'https://example.com/api'],
    ['http://127.0.0.1:45678/api', 'http://127.0.0.1:45678/api'], ['/', ''],
  ]) {
    assert.equal(resolveApiBase(input), expected);
    const calls = [];
    await createTravelApi(input, async (url) => {
      calls.push(url);
      return new Response(JSON.stringify({ textQueries: null, results: [] }));
    }).search('Tokyo');
    assert.deepEqual(calls, [`${expected}/places/search`]);
  }
});

test('invalid API configuration fails on request without fetching or blanking the UI', async () => {
  for (const input of ['localhost:8000', '//example.com/api', 'file:///tmp', 'https://u:private@example.com/api', '/api?key=private', '/api#private', '/api\\private', '/api/../other', 'https://example.com/api/%2e%2e/private']) {
    let calls = 0;
    const client = createTravelApi(input, async () => { calls++; throw Error('must not fetch'); });
    await assert.rejects(client.search('Tokyo'), (error) => error instanceof TravelApiError && /VITE_API_BASE/.test(error.message) && !error.message.includes('private'));
    assert.equal(calls, 0);
  }
  const app = harness({ mode: 'api', client: createTravelApi('file:///private') });
  app.click('search');
  app.filter('Tokyo');
  app.searchKey('Enter');
  await app.settle();
  assert.match(app.searchMarkup(), /VITE_API_BASE/);
  assert.match(app.markup(), /site-header/);
});

test('HTML routing errors and malformed JSON are distinct from network failures and preserve status', async () => {
  for (const [body, status, type] of [
    ['<html>private upstream details</html>', 200, 'text/html'],
    ['<html>private upstream details</html>', 404, 'text/html; charset=utf-8'],
    ['<html>private static hosting error</html>', 403, 'text/html'],
    ['<html>private method not allowed</html>', 405, 'text/html'],
    ['{"private":', 200, 'application/json'],
    ['private invalid body', 200, 'text/plain'],
  ]) {
    await assert.rejects(createTravelApi('/api', async () => new Response(body, { status, headers: { 'Content-Type': type } })).search('Tokyo'), (error) => {
      assert.ok(error instanceof TravelApiError);
      assert.equal(error.status, status);
      assert.match(error.message, /VITE_API_BASE/);
      assert.doesNotMatch(error.message, /private|백엔드에 연결할 수 없어요/);
      return true;
    });
  }
  await assert.rejects(createTravelApi('/api', async () => { throw new TypeError('private network cause'); }).search('Tokyo'), /백엔드에 연결할 수 없어요/);
  await assert.rejects(createTravelApi('/api', async () => new Response('<html>private gateway error</html>', { status: 502, headers: { 'Content-Type': 'text/html' } })).search('Tokyo'), (error) => error.status === 502 && /외부 검색·경로 서비스/.test(error.message));
  await assert.rejects(createTravelApi('/api', async () => new Response('<html>private crash details</html>', { status: 500, headers: { 'Content-Type': 'text/html' } })).search('Tokyo'), (error) => error.status === 500 && /백엔드 내부 오류/.test(error.message) && !error.message.includes('private'));
});

test('HTTP failures keep status and safe messages without echoing upstream bodies or retrying', async () => {
  for (const status of [404, 422, 429, 500, 502, 503, 504]) {
    let calls = 0;
    await assert.rejects(createTravelApi('/api', async () => {
      calls++;
      return new Response('{"detail":"private provider details"}', { status, headers: { 'Content-Type': 'application/json' } });
    }).search('Tokyo'), (error) => error instanceof TravelApiError && error.status === status && !error.message.includes('private'));
    assert.equal(calls, 1);
  }
});

test('all four endpoints compose the same absolute API base without duplicate slashes', async () => {
  const urls = [], draft = draftWithPlaces();
  const result = parseRouteResponse(routeResponse(draft), draft);
  const api = createTravelApi('https://backend.example.com/api/', async (url) => {
    urls.push(url);
    return new Response('{}', { status: 503 });
  });
  for (const request of [() => api.search('Tokyo'), () => api.resolveStations(draft, 0), () => api.optimize(draft), () => api.nearby(draft, result, 0)]) {
    await assert.rejects(request(), (error) => error instanceof TravelApiError && error.status === 503);
  }
  assert.deepEqual(urls, ['places/search', 'stations/resolve', 'routes/optimize', 'nearby/recommend'].map((path) => `https://backend.example.com/api/${path}`));
});
