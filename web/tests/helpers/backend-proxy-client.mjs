// Real adapter -> Vite proxy -> FastAPI. Only outbound providers are controlled in Python.
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { createTravelApi } from '../../src/demo-api.ts';
import { addPlace, emptyDraft, registerPlaces } from '../../src/demo-model.ts';

process.env.JPASS_API_TARGET = process.argv[2];
const webRoot = fileURLToPath(new URL('../../', import.meta.url));
const vite = await createServer({ root: webRoot, server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
try {
  await vite.listen();
  const origin = `http://127.0.0.1:${vite.httpServer.address().port}`;
  const calls = [];
  const api = createTravelApi(`${origin}/api`, async (url, init) => {
    calls.push(new URL(url).pathname);
    return fetch(url, init);
  });
  const searched = await api.search('Tokyo gardens');
  assert.equal(searched.places.length, 2);
  registerPlaces(searched.places);
  let draft = emptyDraft();
  draft.dayCount = 1;
  draft.tags = ['culture'];
  draft = addPlace(draft, 0, searched.places[0].id);
  const resolved = await api.resolveStations(draft, 0);
  assert.deepEqual(resolved, { stations: [{ name: 'A', location: { lat: 35.68, lng: 139.76 }, line: 'test line' }], warnings: [] });
  const initial = await api.initial(draft, 0, 'Tokyo gardens');
  assert.deepEqual(initial.warnings, []);
  const candidate = initial.places.find(place => place.googlePlaceId === 'contract-b');
  assert.deepEqual(candidate.sources, ['nearby', 'text_search']);
  assert.ok(!calls.includes('/api/routes/optimize'), 'station lookup must not optimize');
  registerPlaces(initial.places);
  draft = addPlace(draft, 0, candidate.id);
  const result = await api.optimize(draft);
  assert.equal(result.days[0].minutes, 9);
  assert.equal(result.days[0].fare, 180);
  assert.deepEqual(result.days[0].order, ['google:contract-b', 'google:contract-a']);
  assert.equal(result.days[0].legs[0].route[0].line, 'test line');
  assert.equal((await api.nearby(draft, result, 0)).length, 1);
  const ambiguous = await api.search('ambiguous');
  assert.equal(ambiguous.places.length, 0);
  assert.match(ambiguous.clarification, /구체적으로/);
  const invalid = await fetch(`${origin}/api/places/search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ userQuery: 'Tokyo', languageCode: 'ko', regionCode: 'JP' }),
  });
  assert.equal(invalid.status, 422);
  console.log(JSON.stringify({ result: 'PASS', transport: 'real Vite proxy and FastAPI', calls }));
} finally {
  await vite.close();
}
