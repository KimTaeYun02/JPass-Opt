// Actual frontend adapter -> Vite proxy -> FastAPI -> directed-cost optimizer.
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';
import { createTravelApi } from '../../src/demo-api.ts';
import { addPlace, emptyDraft, registerPlaces } from '../../src/demo-model.ts';

process.env.JPASS_API_TARGET = process.argv[2];
const vite = await createServer({ root: fileURLToPath(new URL('../../', import.meta.url)), server: { host: '127.0.0.1', port: 0 }, logLevel: 'error' });
try {
  await vite.listen();
  const api = createTravelApi(`http://127.0.0.1:${vite.httpServer.address().port}/api`);
  const places = [0, 1].map(i => ({ id: `access-${i}`, googlePlaceId: `access-${i}`, name: `Place ${i}`, lat: 35 + i * 0.02, lng: 139, tags: [] }));
  registerPlaces(places);
  let draft = { ...emptyDraft(), dayCount: 1, startDate: '2026-11-10', includeAccessWalking: true };
  for (const place of places) draft = addPlace(draft, 0, place.id);
  const result = await api.optimize(draft);
  const day = result.days[0];
  assert.equal(day.accessWalking, true);
  assert.equal(day.minutes, 11);
  assert.equal(day.fare, 100);
  assert.deepEqual(day.legs[0].segments.map(s => s.mode), ['walking', 'transit', 'walking']);
  assert.equal(day.legs[0].segments[0].path.length, 3);
  assert.equal(day.legs[0].route[0].line, 'test rail');
  assert.equal(day.legs[0].segments.reduce((sum, s) => sum + s.durationMinutes, 0), day.minutes);
  console.log(JSON.stringify({ result: 'PASS', transport: 'walking adapter + Vite proxy + FastAPI + optimizer' }));
} finally {
  await vite.close();
}
