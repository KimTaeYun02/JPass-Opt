import assert from 'node:assert/strict';
import test from 'node:test';
import { PLACES, TAGS, addPlace, addPlaces, canApplyResult, clockAt, computeExample, initialDraft, initialRecommendations, recommendations, removePlace, searchPlaces, setDayCount, validateDraft } from '../src/demo-model.ts';

test('batch additions are atomic and enforce duplicate and eight-place limits', () => {
  const draft = initialDraft();
  const before = structuredClone(draft);
  const ids = PLACES.filter((place) => !draft.days[0].placeIds.includes(place.id)).map((place) => place.id);
  const changed = addPlaces(draft, 0, ids.slice(0, 2));
  assert.deepEqual(changed.days[0].placeIds, [...draft.days[0].placeIds, ...ids.slice(0, 2)]);
  assert.deepEqual(changed.days[1], draft.days[1]);
  assert.equal(addPlaces(draft, 0, ids.slice(0, 6)).days[0].placeIds.length, 8);
  assert.throws(() => addPlaces(draft, 0, ids.slice(0, 7)));
  assert.throws(() => addPlaces(draft, 0, [ids[0], ids[0]]));
  assert.throws(() => addPlaces(draft, 0, [ids[0], 'unknown']));
  assert.deepEqual(draft, before);
});

test('day count shrink preserves all hidden days and restoration never duplicates them', () => {
  const initial = initialDraft();
  const shrunk = setDayCount(initial, 1);
  assert.equal(shrunk.days.length, 2);
  assert.deepEqual(setDayCount(shrunk, 2).days, initial.days);
  assert.equal(initial.revision, 0);
  assert.throws(() => setDayCount(initial, 8));
});
test('confirmed fixture IDs only, duplicate protection and eight-place limit', () => {
  let draft = initialDraft();
  assert.throws(() => addPlace(draft, 0, '기온'));
  assert.throws(() => addPlace(draft, 0, draft.days[0].placeIds[0]));
  assert.throws(() => addPlace(draft, -1, PLACES[0].id));
  for (const place of PLACES) {
    if (draft.days[0].placeIds.length === 8) break;
    if (!draft.days[0].placeIds.includes(place.id)) draft = addPlace(draft, 0, place.id);
  }
  const extra = PLACES.find((place) => !draft.days[0].placeIds.includes(place.id));
  assert.throws(() => addPlace(draft, 0, extra.id));
  assert.equal(removePlace(draft, 0, draft.days[0].placeIds[0]).days[0].placeIds.length, 7);
  assert.equal(initialDraft().days[0].placeIds.length, 2);
});
test('empty days, impossible dates and invalid times are not calculable', () => {
  const draft = initialDraft();
  assert.ok(validateDraft(setDayCount(draft, 3)).some((error) => error.includes('3일차')));
  assert.ok(validateDraft({ ...draft, startDate: '2026-02-30' }).length);
  assert.ok(validateDraft({ ...draft, startDate: '' }).length);
  assert.ok(validateDraft({ ...draft, days: [{ placeIds: [], startTime: '25:00' }, draft.days[1]] }).length);
});
test('result uses a request snapshot and rejects superseded or edited drafts', () => {
  const draft = initialDraft();
  const result = computeExample(draft);
  assert.equal(canApplyResult(draft, result, 1, 1), true);
  assert.equal(canApplyResult(draft, result, 1, 2), false);
  assert.equal(canApplyResult(addPlace(draft, 0, 'demo-gion'), result, 1, 1), false);
  assert.deepEqual(draft.days[0].placeIds, ['demo-kiyomizu', 'demo-nishiki']);
});
test('example totals match legs, one stop has zero legs, order preserves all places', () => {
  const draft = initialDraft();
  const result = computeExample(draft);
  result.days.forEach((day, index) => {
    assert.deepEqual([...day.order].sort(), [...draft.days[index].placeIds].sort());
    assert.equal(day.legs.length, day.order.length - 1);
    assert.equal(day.minutes, day.legs.reduce((sum, leg) => sum + leg.minutes, 0));
    assert.equal(day.fare, day.legs.reduce((sum, leg) => sum + leg.fare, 0));
  });
  const one = computeExample(removePlace(setDayCount(draft, 1), 0, 'demo-nishiki')).days[0];
  assert.equal(one.legs.length, 0);
  assert.equal(one.minutes, 0);
  assert.equal(one.fare, 0);
  assert.equal(clockAt('23:50', 30), '+1일 00:20');
});
test('recommendations require preferences, filter planned places and support explicit additions', () => {
  const draft = initialDraft();
  assert.equal(TAGS.length, 6);
  assert.deepEqual(recommendations({ ...draft, tags: [] }, 0), []);
  const candidates = recommendations(draft, 0);
  const planned = draft.days.flatMap((day) => day.placeIds);
  assert.ok(candidates.length);
  assert.ok(candidates.every((place) => !planned.includes(place.id)));
  assert.equal(new Set(candidates.map((place) => place.id)).size, candidates.length);
  const changed = addPlace(draft, 1, candidates[0].id);
  assert.ok(changed.days[1].placeIds.includes(candidates[0].id));
  assert.equal(changed.days[0].placeIds.length, draft.days[0].placeIds.length);
  assert.equal(recommendations(changed, 0).some((place) => place.id === candidates[0].id), false);
  assert.deepEqual(searchPlaces('존재하지 않는 자유 텍스트'), []);
  assert.equal(searchPlaces('기요미즈')[0].id, 'demo-kiyomizu');
});


test('initial candidates use Notion place fields without inventing Google IDs or station distances', () => {
  const draft = initialDraft();
  const before = structuredClone(draft);
  const candidates = initialRecommendations(draft, 0, new Set());
  const planned = new Set(draft.days.flatMap((day) => day.placeIds));
  assert.ok(candidates.length > 0);
  for (const candidate of candidates) {
    assert.ok(!planned.has(candidate.demoPlaceId));
    assert.equal(candidate.googlePlaceId, null);
    assert.deepEqual(Object.keys(candidate).sort(), ['demoPlaceId', 'googlePlaceId', 'name', 'location', 'searchTags', 'address', 'primaryType'].sort());
    assert.ok(Number.isFinite(candidate.location.lat) && Number.isFinite(candidate.location.lng));
    assert.ok(candidate.searchTags.some((tag) => draft.tags.includes(tag)));
  }
  assert.deepEqual(draft, before);
  const all = initialRecommendations({ ...draft, tags: [] }, 0, new Set());
  assert.equal(all.length, PLACES.length - planned.size);
  const anime = initialRecommendations({ ...draft, tags: ['anime'] }, 0, new Set());
  assert.deepEqual(anime.map((place) => place.demoPlaceId), ['demo-manga']);
});

test('owned initial picks remain cancelable after tastes change and hidden days do not exclude candidates', () => {
  let draft = addPlace(initialDraft(), 0, 'demo-manga');
  draft = { ...draft, tags: ['nature'] };
  assert.ok(initialRecommendations(draft, 0, new Set(['demo-manga'])).some((place) => place.demoPlaceId === 'demo-manga'));
  assert.ok(!initialRecommendations(draft, 1, new Set()).some((place) => place.demoPlaceId === 'demo-manga'));
  assert.ok(initialRecommendations(setDayCount(draft, 1), 0, new Set()).some((place) => place.demoPlaceId === 'demo-arashi'));
});

test('empty days only allowed while entering selection, other validations remain strict', () => {
  const draft = setDayCount(initialDraft(), 3);
  assert.match(validateDraft(draft)[0], /3일차/);
  assert.deepEqual(validateDraft(draft, true), []);
  assert.throws(() => computeExample(draft));
  assert.match(validateDraft({ ...draft, startDate: '2026-02-30' }, true)[0], /시작일/);
  const broken = structuredClone(draft);
  broken.days[0].startTime = '25:00';
  assert.match(validateDraft(broken, true)[0], /출발 시각/);
});
