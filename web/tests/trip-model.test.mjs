import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_VISIT_MINUTES, fitMoveMinute, snapMinute, movePlace, resizePlace, validate, placementErrors, dayCount, dateAt, clock, clampPage, escapeHtml } from '../src/trip-model.ts';

const spot = { id: 'a', name: 'A', address: '', source: 'sample', required: true, duration: 60, fixed: { date: '2026-11-03', time: '10:00' } };
const input = { range: { start: '2026-11-03', end: '2026-11-07' }, places: [spot], arrival: { place: '', date: '', time: '' }, departure: { place: '', date: '', time: '' }, stay: '', preference: 'balance' };
const check = (changed, others = []) => placementErrors({ ...spot, ...changed }, [spot, ...others], input.range.start, input.range.end);

test('travel dates are inclusive, invalid ranges are empty, dates cross a year boundary', () => {
  assert.deepEqual(validate(input), []);
  assert.equal(dayCount('2026-11-03', '2026-11-07'), 5);
  assert.equal(dayCount('', '2026-11-07'), 0);
  assert.equal(dayCount('2026-11-03', '2026-11-02'), 0);
  assert.equal(dateAt('2026-12-30', 3), '2027-01-02');
  assert.equal(clock(1440), '24:00');
  assert.ok(validate({ ...input, range: { ...input.range, end: '2026-11-02' } }).some((error) => error.includes('종료일')));
});

test('free and date-only anchors are allowed, missing and out-of-range anchor dates are blocked', () => {
  assert.deepEqual(check({ fixed: undefined }), []);
  assert.deepEqual(check({ fixed: { date: '2026-11-07' } }), []);
  assert.ok(check({ fixed: { date: '2026-11-08' } }).some((error) => error.includes('기간 밖')));
  assert.ok(check({ fixed: { date: '' } }).length);
  assert.ok(validate({ ...input, range: { start: '2026-11-04', end: '2026-11-07' } }).some((error) => error.includes('기간 밖')));
  assert.equal(spot.fixed.date, '2026-11-03');
});

test('visits use half-hour increments and cannot span midnight', () => {
  assert.ok(check({ fixed: { date: '2026-11-03', time: '10:15' } }).some((error) => error.includes('30분')));
  assert.ok(check({ fixed: { date: '2026-11-03', time: '23:30' } }).some((error) => error.includes('자정')));
  assert.deepEqual(check({ duration: 30, fixed: { date: '2026-11-03', time: '23:30' } }), []);
  assert.ok(check({ duration: 15 }).length);
  assert.ok(check({ fixed: { date: '2026-11-03', time: '' } }).length);
});

test('overlap is blocked, adjacency and visits on different dates are allowed', () => {
  assert.ok(check({}, [{ ...spot, id: 'b', fixed: { date: '2026-11-03', time: '10:30' } }]).some((error) => error.includes('겹쳐')));
  assert.deepEqual(check({}, [{ ...spot, id: 'b', fixed: { date: '2026-11-03', time: '11:00' } }]), []);
  assert.deepEqual(check({}, [{ ...spot, id: 'b', fixed: { date: '2026-11-04', time: '10:30' } }]), []);
  assert.deepEqual(check({}, [{ ...spot, id: 'b', fixed: { date: '2026-11-03' } }]), []);
});

test('selection requires a place and a mandatory visit, deduplicates IDs not names', () => {
  assert.ok(validate({ ...input, places: [] }).length);
  assert.ok(validate({ ...input, places: [{ ...spot, required: false }] }).length);
  assert.ok(validate({ ...input, places: [{ ...spot, placeId: 'one' }, { ...spot, id: 'b', placeId: 'one', fixed: undefined }] }).some((error) => error.includes('중복')));
  assert.deepEqual(validate({ ...input, places: [{ ...spot, placeId: 'one', fixed: undefined }, { ...spot, id: 'b', placeId: 'two', fixed: undefined }] }), []);
});

test('endpoint dates are required for times and departure must follow arrival', () => {
  assert.ok(validate({ ...input, arrival: { place: '', date: '', time: '10:00' } }).length);
  assert.ok(validate({ ...input, departure: { place: '', date: '2026-11-08', time: '' } }).length);
  assert.ok(validate({ ...input, arrival: { place: '', date: '2026-11-04', time: '' }, departure: { place: '', date: '2026-11-03', time: '' } }).some((error) => error.includes('출발 시점')));
  assert.deepEqual(validate({ ...input, arrival: { place: '', date: '2026-11-03', time: '' }, departure: { place: '', date: '2026-11-03', time: '' } }), []);
  assert.ok(validate({ ...input, arrival: { place: '', date: '2026-11-03', time: '15:00' }, departure: { place: '', date: '2026-11-03', time: '10:00' } }).some((error) => error.includes('출발 시점')));
});

test('pagination is clamped after a trip shrinks and never becomes negative', () => {
  assert.equal(clampPage(5, 0), 0);
  assert.equal(clampPage(-1, 12), 0);
  assert.equal(clampPage(2, 12), 2);
  assert.equal(clampPage(2, 6), 1);
  assert.equal(clampPage(1, 5), 0);
  assert.equal(clampPage(Infinity, 10), 0);
});

test('free-form and API names cannot turn into HTML markup', () => {
  assert.equal(escapeHtml('<script>"&'), '&lt;script&gt;&quot;&amp;');
  assert.equal(escapeHtml("<img src=x onerror='alert(1)'>"), '&lt;img src=x onerror=&#39;alert(1)&#39;&gt;');
});

test('new visits default to two hours, snapping rounds to the nearest half-hour', () => {
  assert.equal(DEFAULT_VISIT_MINUTES, 120);
  assert.equal(snapMinute(600), 600);
  assert.equal(snapMinute(614), 600);
  assert.equal(snapMinute(615), 630);
  assert.equal(snapMinute(629), 630);
  assert.equal(snapMinute(0), 0);
  assert.equal(snapMinute(1440), 1440);
});

test('drop start fits the visible track while preserving duration and never produces negative clock values', () => {
  assert.equal(fitMoveMinute(-90, 120, 0, 1440), 0);
  assert.equal(fitMoveMinute(390, 120, 480, 1320), 480);
  assert.equal(fitMoveMinute(1310, 120, 480, 1320), 1200);
  assert.equal(fitMoveMinute(615, 120, 480, 1320), 630);
  assert.equal(fitMoveMinute(30, 1440, 0, 1440), 0);
  assert.equal(fitMoveMinute(480, 1440, 480, 1320), undefined);
  assert.equal(fitMoveMinute(NaN, 120, 0, 1440), undefined);
});

test('moving preserves user duration and metadata across free, date-only and timed modes', () => {
  const original = { ...spot, duration: 180, source: 'google', placeId: 'google-id', location: { lat: 35, lng: 135 } };
  const snapshot = structuredClone(original);
  const timed = movePlace(original, { kind: 'time', date: '2026-11-05', minute: 614 });
  assert.deepEqual(timed.fixed, { date: '2026-11-05', time: '10:00' });
  assert.equal(timed.duration, 180);
  assert.equal(timed.placeId, original.placeId);
  assert.deepEqual(timed.location, original.location);
  assert.equal(timed.required, original.required);
  const dateOnly = movePlace(timed, { kind: 'date', date: '2026-11-04' });
  assert.deepEqual(dateOnly.fixed, { date: '2026-11-04' });
  assert.equal(dateOnly.duration, 180);
  const free = movePlace(dateOnly, { kind: 'free' });
  assert.equal(free.fixed, undefined);
  assert.equal(free.duration, 180);
  assert.equal(movePlace(free, { kind: 'time', date: '2026-11-04', minute: 600 }).duration, 180);
  assert.deepEqual(original, snapshot);
  assert.notEqual(timed, original);
  assert.equal(movePlace(original, { kind: 'time', date: '2026-11-04', minute: NaN }), undefined);
});

test('resizing start holds end fixed, resizing end holds start fixed and supports every half-hour duration', () => {
  const original = { ...spot, duration: 120 };
  const earlier = resizePlace(original, 'start', 569);
  assert.equal(earlier.fixed.time, '09:30');
  assert.equal(earlier.duration, 150);
  assert.deepEqual(check(earlier), []);
  const later = resizePlace(original, 'end', 751);
  assert.equal(later.fixed.time, '10:00');
  assert.equal(later.duration, 150);
  assert.deepEqual(check(later), []);
  assert.equal(resizePlace(original, 'end', 721).duration, 120);
  assert.equal(resizePlace(original, 'start', 601).duration, 120);
  assert.equal(original.duration, 120);
  assert.equal(original.fixed.time, '10:00');
});

test('resize clamps at minimum length, view boundaries and midnight', () => {
  const original = { ...spot, duration: 120 };
  assert.equal(resizePlace(original, 'start', 800).duration, 30);
  assert.equal(resizePlace(original, 'end', 400).duration, 30);
  assert.equal(resizePlace(original, 'start', -100).fixed.time, '00:00');
  assert.equal(resizePlace(original, 'start', -100, 480, 1320).fixed.time, '08:00');
  assert.equal(resizePlace(original, 'end', 2000).duration, 840);
  assert.equal(resizePlace(original, 'end', 2000, 480, 1320).duration, 720);
  assert.equal(resizePlace({ ...original, fixed: undefined }, 'end', 700), undefined);
  assert.equal(resizePlace(original, 'end', NaN), undefined);
  assert.equal(resizePlace(original, 'end', Infinity), undefined);
  assert.equal(resizePlace(original, 'end', 700, 720, 1320), undefined);
  assert.deepEqual(check({ duration: 1440, fixed: { date: '2026-11-03', time: '00:00' } }), []);
  for (const duration of [0, -30, 45, 1470, NaN, Infinity]) assert.ok(check({ duration }).length);
});

test('invalid move/resize candidates are rejected without changing originals or other places', () => {
  const other = { ...spot, id: 'b', fixed: { date: '2026-11-03', time: '11:00' } };
  const original = structuredClone([spot, other]);
  const resized = resizePlace(spot, 'end', 690);
  assert.ok(check(resized, [other]).some((error) => error.includes('겹쳐')));
  const moved = movePlace(spot, { kind: 'time', date: '2026-11-03', minute: 660 });
  assert.ok(check(moved, [other]).some((error) => error.includes('겹쳐')));
  assert.deepEqual(check(movePlace(spot, { kind: 'time', date: '2026-11-03', minute: 600 })), []);
  assert.ok(check(movePlace(spot, { kind: 'time', date: '2026-11-08', minute: 600 })).some((error) => error.includes('기간 밖')));
  assert.ok(check(movePlace(spot, { kind: 'time', date: '2026-11-03', minute: 1440 })).length);
  assert.ok(check(movePlace(spot, { kind: 'time', date: '2026-11-03', minute: -30 })).length);
  assert.deepEqual([spot, other], original);
});
