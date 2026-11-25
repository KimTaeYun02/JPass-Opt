import assert from 'node:assert/strict';
import test from 'node:test';
import { MAX_DRAFT_FILE_BYTES, readDraftFile, writeDraftFile } from '../src/demo-draft-file.ts';
import { PLACES, addPlace, emptyDraft, initialDraft, placeById, registerPlaces, setDayCount } from '../src/demo-model.ts';
import { harness } from './demo-harness.mjs';

const exampleKey = 'jpass.travel-draft.example';
const apiKey = 'jpass.travel-draft.api';
const makeFile = (text) => new File([text], 'travel-draft.json', { type: 'application/json' });
const finishImport = () => new Promise(setImmediate);

test('API draft files restore selected place records and hidden days without a search', () => {
  const place = { id: 'google:test-persisted', googlePlaceId: 'test-persisted', name: '한글 장소', area: '교토', station: '교토역', lat: 35, lng: 135, rank: 0, tags: ['culture'], address: '교토부', primaryType: 'museum' };
  registerPlaces([place]);
  const withPlace = addPlace(emptyDraft(), 1, place.id);
  const draft = { ...setDayCount(withPlace, 1), startDate: '', request: '조용한 곳', includeAccessWalking: true };
  draft.days[1].startTime = '';
  draft.days[1].searchQuery = '박물관';
  const text = writeDraftFile(draft, 'api');
  const saved = JSON.parse(text);
  assert.equal(saved.draft.revision, undefined);
  assert.equal(saved.places.length, 1);
  PLACES.splice(PLACES.findIndex((item) => item.id === place.id), 1);
  const restored = harness({ mode: 'api', storage: new Map([[apiKey, text]]), client: {} });
  assert.match(restored.markup(), /저장한 초안을 불러왔어요/);
  assert.equal(restored.state().draft.startDate, '');
  assert.equal(restored.state().draft.request, '조용한 곳');
  assert.equal(restored.state().draft.includeAccessWalking, true);
  restored.change('day-count', '2');
  restored.click('day', { day: '1' });
  assert.match(restored.markup(), /한글 장소/);
  assert.equal(restored.state().draft.days[1].startTime, '');
  assert.equal(restored.state().draft.days[1].searchQuery, '박물관');
  assert.deepEqual(placeById(place.id), place);
  assert.equal(restored.state().result, undefined);
});

test('file validation rejects unsupported versions, mixed modes, invalid dates and missing places', () => {
  const valid = JSON.parse(writeDraftFile(initialDraft(), 'example'));
  assert.throws(() => readDraftFile(JSON.stringify({ ...valid, version: 2 }), 'example'), /버전/);
  assert.throws(() => readDraftFile(JSON.stringify(valid), 'api'), /모드/);
  assert.throws(() => readDraftFile('가'.repeat(MAX_DRAFT_FILE_BYTES / 2), 'example'), /1MB/);
  const invalidDate = { ...valid, draft: { ...valid.draft, startDate: '2026-02-30' } };
  assert.throws(() => readDraftFile(JSON.stringify(invalidDate), 'example'), /형식/);
  const missing = { ...valid, mode: 'api' };
  assert.throws(() => readDraftFile(JSON.stringify(missing), 'api'), /형식/);
  const injected = { id: 'google:fake" onclick="x', googlePlaceId: 'fake', name: 'x', area: '', station: '', lat: 35, lng: 135, tags: [] };
  missing.draft.days[0].placeIds = [injected.id];
  missing.draft.days[1].placeIds = [];
  missing.places = [injected];
  assert.throws(() => readDraftFile(JSON.stringify(missing), 'api'), /형식/);
});

test('editing auto-saves and a refreshed demo restores the same mode only', () => {
  const storage = new Map();
  const demo = harness({ storage });
  assert.equal(storage.size, 0);
  demo.change('start-date', '2026-12-20');
  demo.change('start-time', '09:30');
  demo.click('tag', { tag: 'shopping' });
  demo.input('extra-request', '조용한 곳');
  demo.change('day-count', '1');
  const expected = { ...demo.state().draft, revision: 0, includeAccessWalking: false };
  assert.deepEqual(harness({ storage }).state().draft, expected);
  const apiDemo = harness({ mode: 'api', storage });
  assert.deepEqual(apiDemo.state().draft.days[0].placeIds, []);
  assert.equal(storage.has(apiKey), false);
});

test('invalid and canceled imports preserve the current draft and its saved copy', async () => {
  const storage = new Map();
  let confirmations = 0;
  const demo = harness({ storage, confirmImport: () => { confirmations++; return false; } });
  demo.change('start-date', '2026-12-21');
  const before = storage.get(exampleKey);
  demo.importFile(makeFile('broken JSON'));
  await finishImport();
  assert.match(demo.message(), /형식/);
  assert.equal(confirmations, 0);
  demo.importFile(makeFile(writeDraftFile(initialDraft(), 'example')));
  await finishImport();
  assert.equal(confirmations, 1);
  assert.equal(demo.state().draft.startDate, '2026-12-21');
  assert.equal(storage.get(exampleKey), before);
});

test('a successful import clears the last calculation and ignores a calculation already in progress', async () => {
  const storage = new Map();
  const demo = harness({ storage });
  demo.click('continue');
  demo.click('calculate');
  await demo.settle();
  assert.ok(demo.state().result);
  demo.click('calculate');
  assert.equal(demo.pending(), 1);
  const replacement = { ...initialDraft(), startDate: '2026-12-22' };
  demo.importFile(makeFile(writeDraftFile(replacement, 'example')));
  await finishImport();
  assert.equal(demo.state().view, 'input');
  assert.equal(demo.state().result, undefined);
  assert.equal(demo.state().draft.startDate, '2026-12-22');
  await demo.settle();
  assert.equal(demo.state().result, undefined);
  assert.equal(readDraftFile(storage.get(exampleKey), 'example').draft.startDate, '2026-12-22');
});

test('starting a new draft replaces only the active mode after confirmation', () => {
  const storage = new Map([[apiKey, writeDraftFile({ ...emptyDraft(), startDate: '2026-12-19' }, 'api')]]);
  let confirmed = false;
  const demo = harness({ storage, confirmImport: () => confirmed });
  demo.change('start-date', '2026-12-21');
  demo.click('new-draft');
  assert.equal(demo.state().draft.startDate, '2026-12-21');
  confirmed = true;
  demo.click('new-draft');
  assert.equal(harness({ storage }).state().draft.startDate, initialDraft().startDate);
  assert.equal(harness({ mode: 'api', storage }).state().draft.startDate, '2026-12-19');
});

test('a file that finishes reading after a draft edit does not replace that edit', async () => {
  const demo = harness();
  const { promise, resolve } = Promise.withResolvers();
  demo.importFile({ size: 0, text: () => promise });
  demo.change('start-date', '2026-12-27');
  resolve(writeDraftFile(initialDraft(), 'example'));
  await finishImport();
  assert.match(demo.message(), /읽는 동안 초안이 바뀌었어요/);
  assert.equal(demo.state().draft.startDate, '2026-12-27');
});

test('storage failures and concurrent edits leave the current working draft usable', () => {
  const storage = new Map();
  const earlierTab = harness({ storage });
  const laterTab = harness({ storage });
  laterTab.change('start-date', '2026-12-23');
  earlierTab.change('start-date', '2026-12-24');
  assert.match(earlierTab.markup(), /다른 탭에서 초안이 바뀌었어요/);
  assert.equal(earlierTab.state().draft.startDate, '2026-12-24');
  assert.equal(readDraftFile(storage.get(exampleKey), 'example').draft.startDate, '2026-12-23');

  const blocked = harness({ storage: { get() { throw new Error('blocked'); } } });
  blocked.change('start-date', '2026-12-25');
  assert.match(blocked.markup(), /자동 저장을 완료하지 못했어요/);
  assert.equal(blocked.state().draft.startDate, '2026-12-25');

  const unreadable = new Map([[exampleKey, '{']]);
  const recovered = harness({ storage: unreadable });
  recovered.change('start-date', '2026-12-26');
  assert.match(recovered.markup(), /저장된 초안을 읽지 못했어요/);
  assert.equal(unreadable.get(exampleKey), '{');
});
