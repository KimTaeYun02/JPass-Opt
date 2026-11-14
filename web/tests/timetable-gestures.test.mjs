import assert from 'node:assert/strict';
import test from 'node:test';
import { initTimetableGestures } from '../src/timetable-gestures.ts';

// A small unit-only DOM adapter exercises controller lifecycle without a browser or APIs.
function fixture(t, overlap = false) {
  const listeners = new Map();
  const classes = () => {
    const values = new Set();
    return { add: (value) => values.add(value), remove: (value) => values.delete(value), contains: (value) => values.has(value), toggle: (value, on) => on ? values.add(value) : values.delete(value) };
  };
  const on = (prefix) => (type, fn) => {
    const key = `${prefix}:${type}`;
    listeners.set(key, [...(listeners.get(key) || []), fn]);
  };
  const box = { top: 100, bottom: 716, left: 0, right: 200, width: 200, height: 616 };
  const track = { dataset: { dropDate: '2026-11-03', begin: '480', finish: '1320' }, classList: classes(), getBoundingClientRect: () => box, hasAttribute: () => false };
  track.classList.add('day-track');
  track.closest = () => track;
  let captured = false;
  const source = {
    dataset: { edit: 'a' }, classList: classes(), closest: (selector) => selector === '.day-track' ? track : selector === '[data-edit]' ? source : null,
    getBoundingClientRect: () => ({ top: 188, bottom: 276 }), setPointerCapture: () => { captured = true; },
    hasPointerCapture: () => captured, releasePointerCapture: () => { captured = false; }, focus: () => {},
  };
  source.classList.add('timed-event');
  const root = { classList: classes(), addEventListener: on('root'), contains: () => true, closest: () => root,
    getBoundingClientRect: () => ({ top: 0, bottom: 900, left: 0, right: 900 }), querySelector: (selector) => selector === '.timetable-scroll' ? null : source };
  const status = { textContent: '', classList: classes() };
  const place = Object.freeze({ id: 'a', name: 'A', address: 'address', source: 'sample', required: true, duration: 120, fixed: Object.freeze({ date: '2026-11-03', time: '10:00' }) });
  const places = overlap ? [place, { ...place, id: 'b', fixed: { date: '2026-11-03', time: '12:00' } }] : [place];
  const commits = [];
  const ghosts = new Set();
  let drop = track;
  const overrides = {
    window: { addEventListener: on('window'), innerHeight: 900 },
    document: { elementFromPoint: () => drop, scrollingElement: null,
      createElement: () => { const node = { style: {}, classList: classes(), setAttribute: () => {}, remove: () => ghosts.delete(node) }; return node; },
      body: { append: (node) => ghosts.add(node) } },
    getComputedStyle: () => ({ overflowY: 'auto' }), requestAnimationFrame: () => 1, cancelAnimationFrame: () => {}, CSS: { escape: (value) => value },
  };
  const originals = new Map(Object.keys(overrides).map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries(overrides)) Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  t.after(() => { for (const [key, descriptor] of originals) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } });
  const controller = initTimetableGestures(root, status, () => ({ places, start: '2026-11-03', end: '2026-11-07' }), (candidate) => commits.push(candidate));
  const emit = (surface, type, extra = {}) => {
    const event = { button: 0, isPrimary: true, pointerId: 1, clientX: 100, clientY: 220, target: source, detail: 1,
      prevented: false, stopped: false, preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; }, ...extra };
    for (const listener of listeners.get(`${surface}:${type}`) || []) listener(event);
    return event;
  };
  const handle = (edge) => ({ closest: (selector) => selector === '[data-edit]' ? source : selector === '[data-resize]' ? { dataset: { resize: edge } } : null });
  const destination = (kind) => {
    const target = { classList: classes(), dataset: { dropDate: '2026-11-05' }, hasAttribute: (name) => name === 'data-drop-free' && kind === 'free' };
    target.closest = () => target;
    return target;
  };
  return { emit, controller, source, status, commits, ghosts, place, handle, destination, setDrop: (value) => { drop = value; } };
}

test('controller separates click from drag and commits only once on release', (t) => {
  const f = fixture(t);
  f.emit('root', 'pointerdown');
  f.emit('window', 'pointermove', { clientY: 223 });
  f.emit('window', 'pointerup', { clientY: 223 });
  assert.equal(f.commits.length, 0);
  assert.equal(f.emit('root', 'click').stopped, false);
  f.emit('root', 'pointerdown');
  assert.equal(f.emit('window', 'pointermove', { clientY: 264 }).prevented, true);
  assert.equal(f.commits.length, 0);
  assert.equal(f.ghosts.size, 1);
  f.emit('window', 'pointerup', { clientY: 264 });
  f.emit('window', 'pointerup', { clientY: 264 });
  assert.equal(f.commits.length, 1);
  assert.equal(f.commits[0].fixed.time, '11:00');
  assert.equal(f.commits[0].duration, 120);
  assert.equal(f.ghosts.size, 0);
  assert.equal(f.emit('root', 'click').stopped, true);
  assert.equal(f.emit('root', 'click', { detail: 0 }).stopped, false);
  f.emit('root', 'pointerdown'); f.emit('window', 'pointerup');
  assert.equal(f.emit('root', 'click').stopped, false);
});

test('controller resizes both edges without jumping from the handle grab offset', (t) => {
  const f = fixture(t);
  f.emit('root', 'pointerdown', { target: f.handle('start'), clientY: 193 });
  f.emit('window', 'pointermove', { clientY: 171 }); f.emit('window', 'pointerup', { clientY: 171 });
  assert.equal(f.commits[0].fixed.time, '09:30');
  assert.equal(f.commits[0].duration, 150);
  f.emit('root', 'pointerdown', { target: f.handle('end'), clientY: 271 });
  f.emit('window', 'pointermove', { clientY: 315 }); f.emit('window', 'pointerup', { clientY: 315 });
  assert.equal(f.commits[1].fixed.time, '10:00');
  assert.equal(f.commits[1].duration, 180);
  assert.equal(f.place.duration, 120);
});

test('controller maps date and free targets and preserves duration', (t) => {
  const f = fixture(t);
  f.setDrop(f.destination('date'));
  f.emit('root', 'pointerdown'); f.emit('window', 'pointermove', { clientY: 264 }); f.emit('window', 'pointerup', { clientY: 264 });
  assert.deepEqual(f.commits[0].fixed, { date: '2026-11-05' });
  assert.equal(f.commits[0].duration, 120);
  f.setDrop(f.destination('free'));
  f.emit('root', 'pointerdown'); f.emit('window', 'pointermove', { clientY: 264 }); f.emit('window', 'pointerup', { clientY: 264 });
  assert.equal(f.commits[1].fixed, undefined);
  assert.equal(f.commits[1].duration, 120);
});

test('controller rejects overlapping and outside-board drops without mutating source', (t) => {
  const f = fixture(t, true);
  f.emit('root', 'pointerdown'); f.emit('window', 'pointermove', { clientY: 264 }); f.emit('window', 'pointerup', { clientY: 264 });
  assert.equal(f.commits.length, 0);
  assert.match(f.status.textContent, /겹쳐/);
  assert.equal(f.place.fixed.time, '10:00');
  f.setDrop(null);
  f.emit('root', 'pointerdown'); f.emit('window', 'pointermove', { clientY: 264 }); f.emit('window', 'pointerup', { clientY: 264 });
  assert.equal(f.commits.length, 0);
  assert.equal(f.ghosts.size, 0);
  assert.match(f.status.textContent, /원래 일정/);
});

for (const [surface, type, extra] of [
  ['window', 'keydown', { key: 'Escape' }], ['window', 'pointercancel', {}],
  ['root', 'lostpointercapture', {}], ['window', 'blur', {}], ['window', 'resize', {}],
]) test(`${type} cancels a gesture and late pointerup cannot commit`, (t) => {
  const f = fixture(t);
  f.emit('root', 'pointerdown'); f.emit('window', 'pointermove', { clientY: 264 });
  f.emit(surface, type, extra); f.emit('window', 'pointerup', { clientY: 264 });
  assert.equal(f.commits.length, 0);
  assert.equal(f.ghosts.size, 0);
  assert.equal(f.source.classList.contains('gesture-source'), false);
  assert.equal(f.source.hasPointerCapture(), false);
  assert.equal(f.place.duration, 120);
  assert.match(f.status.textContent, /취소/);
});

test('render-triggered cancellation preserves source and ignores secondary pointers', (t) => {
  const f = fixture(t);
  f.emit('root', 'pointerdown', { isPrimary: false });
  f.emit('window', 'pointermove', { clientY: 264 });
  assert.equal(f.ghosts.size, 0);
  f.emit('root', 'pointerdown'); f.emit('window', 'pointermove', { clientY: 264 });
  f.emit('window', 'pointercancel', { pointerId: 2 });
  assert.equal(f.ghosts.size, 1);
  f.controller.cancel(); f.emit('window', 'pointerup', { clientY: 264 });
  assert.equal(f.commits.length, 0);
  assert.equal(f.ghosts.size, 0);
});
