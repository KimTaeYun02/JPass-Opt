import assert from 'node:assert/strict';
import test from 'node:test';
import { createDemoMapController, DemoMapLoadError } from '../src/demo-map-controller.ts';

const place = (id, order, state = 'routed') => ({ id, name: id, lat: 35 + id.charCodeAt(0) / 1000, lng: 135.75, order, state });
const view = (places, selectedId, activeDayId = '0') => ({ activeDayId, places, selectedId, showRoute: true });
const settle = () => new Promise(setImmediate);
test('live map uses server station path instead of joining attraction coordinates', async () => {
  const h = harness();
  const places = [place('a', 1), place('b', 2)];
  const path = [{ lat: 35.1, lng: 139.1 }, { lat: 35.2, lng: 139.2 }];
  h.controller.update({ ...view(places), routePath: path });
  await settle();
  assert.deepEqual(h.lines[0].path, path);
  assert.ok(h.maps[0].fits[0][0].points.some((point) => point.lng === 139.2));
  h.controller.update({ ...view(places), routePath: [] });
  assert.equal(h.lines[0].visible, false);
});
function harness(loadOverride) {
  const maps = [], markers = [], lines = [], statuses = [], selections = [];
  class FakeMap {
    constructor(host, options) { this.host = host; this.options = options; this.fits = []; this.pans = []; this.centers = []; this.zooms = []; maps.push(this); }
    fitBounds(...args) { this.fits.push(args); }
    panTo(point) { this.pans.push(point); }
    setCenter(point) { this.centers.push(point); }
    setZoom(zoom) { this.zooms.push(zoom); }
  }
  class FakeMarker {
    constructor(options) { Object.assign(this, options); this.node = options.content; this.events = new Map(); markers.push(this); }
    focus() { this.focused = true; }
    addEventListener(name, callback) { this.events.set(name, callback); }
    removeEventListener(name) { this.events.delete(name); }
  }
  class FakeLine {
    constructor(options) { Object.assign(this, options); lines.push(this); }
    setPath(path) { this.path = path; }
    setVisible(visible) { this.visible = visible; }
    setMap(map) { this.map = map; }
  }
  class FakeBounds { points = []; extend(point) { this.points.push(point); } }
  const sdk = { Map: FakeMap, AdvancedMarkerElement: FakeMarker, Polyline: FakeLine, LatLngBounds: FakeBounds };
  const host = { parentElement: null };
  let loads = 0;
  const controller = createDemoMapController({ host, load: () => { loads++; return loadOverride ? loadOverride(sdk) : Promise.resolve(sdk); }, onSelect: (id) => selections.push(id), onStatus: (...args) => statuses.push(args), createElement: () => ({ replaceChildren(...children) { this.children = children; } }) });
  return { controller, host, maps, markers, lines, statuses, selections, loads: () => loads };
}

test('async loading applies only latest snapshot and keeps one map across reattachment', async () => {
  let resolve;
  const h = harness((sdk) => new Promise((done) => { resolve = () => done(sdk); }));
  const firstParent = { append(host) { host.parentElement = this; } };
  const secondParent = { append(host) { host.parentElement = this; } };
  h.controller.attach(firstParent);
  h.controller.update(view([place('a', 1)]));
  const latest = view([place('b', null, 'pending')], undefined, '1');
  h.controller.update(latest);
  latest.places[0].name = 'mutated outside controller';
  h.controller.attach(secondParent);
  h.controller.attach(secondParent);
  resolve(); await settle();
  assert.equal(h.loads(), 1); assert.equal(h.maps.length, 1);
  assert.equal(h.host.parentElement, secondParent);
  assert.equal(h.markers.length, 1);
  assert.equal(h.controller.status(), 'ready');
  assert.match(h.markers[0].title, /^b · 추가됨/);
  assert.equal(h.lines[0].visible, false);
  assert.deepEqual(h.maps[0].zooms, [14]);
});

test('pending marker remains outside route until recalculate, selection preserves zoom, cancel cleans up', async () => {
  const h = harness();
  const a = place('a', 1), b = place('b', 2), pending = place('c', null, 'pending');
  h.controller.update(view([a, b])); await settle();
  const map = h.maps[0];
  assert.equal(map.fits.length, 1);
  h.controller.update(view([a, b], 'b'));
  h.controller.update(view([a, b], 'b'));
  assert.equal(map.fits.length, 1); assert.equal(map.pans.length, 1);
  assert.deepEqual(map.zooms, []);
  const firstMarker = h.markers[0], originalContent = firstMarker.node;
  const originalBadge = originalContent.children[0], originalName = originalContent.children[1];
  for (const id of ['a', 'b', 'a']) {
    h.controller.update(view([a, b], id));
    firstMarker.events.get('gmp-click')();
    assert.equal(firstMarker.node, originalContent);
    assert.equal(originalContent.children[0], originalBadge);
    assert.equal(originalContent.children[1], originalName);
    assert.equal(firstMarker.map, map);
  }
  assert.equal(h.markers.length, 2);
  h.controller.update(view([a, b, pending], 'c'));
  assert.equal(map.fits.length, 2);
  assert.equal(h.lines[0].path.length, 2);
  const marker = h.markers.find((item) => item.title.startsWith('c ·'));
  assert.match(marker.node.className, /pending/);
  marker.events.get('gmp-click')(); assert.deepEqual(h.selections, ['a', 'a', 'a', 'c']);
  assert.equal(marker.focused, true);
  h.controller.update(view([a, b]));
  assert.equal(marker.map, null); assert.equal(marker.events.size, 0);
  h.controller.update(view([a, b, place('c', 3)]));
  assert.equal(h.lines[0].path.length, 3);
  assert.equal(h.lines[0].visible, true);
  h.controller.update(view([a, b, place('c', 3)], undefined, '1'));
  assert.equal(map.fits.length, 5);
  h.controller.update({ ...view([a, b]), showRoute: false });
  assert.equal(h.lines[0].visible, false);
});

test('loader failures and authentication errors expose fallback without retry loops', async () => {
  const failed = harness(() => Promise.reject(new Error('offline')));
  failed.controller.update(view([place('a', 1)])); await settle();
  failed.controller.update(view([place('b', 1)]));
  assert.equal(failed.loads(), 1);
  assert.equal(failed.statuses.at(-1)[0], 'fallback');
  assert.equal(failed.controller.status(), 'fallback');
  assert.equal(failed.maps.length, 0);
  const ready = harness();
  ready.controller.update(view([place('a', 1)])); await settle();
  ready.controller.fail('authentication failed');
  assert.equal(ready.statuses.at(-1)[0], 'fallback');
  assert.equal(ready.markers[0].map, null); assert.equal(ready.lines[0].map, null);
});

test('safe loader reasons distinguish missing configuration and timeout without exposing SDK errors', async () => {
  for (const reason of ['지도 키가 설정되지 않았어요', '로딩 시간이 초과됐어요']) {
    const h = harness(() => Promise.reject(new DemoMapLoadError(reason)));
    h.controller.update(view([place('a', 1)])); await settle();
    assert.equal(h.statuses.at(-1)[1], reason);
  }
  const h = harness(() => Promise.reject(new Error('https://sdk.example?key=secret')));
  h.controller.update(view([place('a', 1)])); await settle();
  assert.doesNotMatch(h.statuses.at(-1)[1], /secret/);
});

test('destroy before asynchronous load finishes cannot initialize a detached map', async () => {
  let resolve;
  const h = harness((sdk) => new Promise((done) => { resolve = () => done(sdk); }));
  h.controller.update(view([place('a', 1)])); h.controller.destroy();
  resolve(); await settle();
  assert.equal(h.maps.length, 0);
  assert.equal(h.statuses.some(([phase]) => phase === 'ready'), false);
});
