import { PLACES, TAGS, googlePlaceKey, isTravelDate, isTravelTime, placeById } from './demo-model.ts';
import type { DemoDay, DemoPlace, Draft, Tag } from './demo-model.ts';

export type DraftMode = 'api' | 'example';
export const MAX_DRAFT_FILE_BYTES = 1024 * 1024;

const invalid = (): never => { throw new Error('여행 초안의 파일 형식이나 입력값을 확인해 주세요.'); };
const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid();
const array = (value: unknown): unknown[] => Array.isArray(value) ? value : invalid();
const string = (value: unknown): string => typeof value === 'string' ? value : invalid();
const nonblank = (value: unknown): string => {
  const text = string(value);
  return text.trim() ? text : invalid();
};

function readTags(value: unknown): Tag[] {
  return array(value).map((tag) => TAGS.find(([id]) => id === tag)?.[0] ?? invalid());
}

function readDay(value: unknown): DemoDay {
  const day = record(value);
  const placeIds = array(day.placeIds).map(nonblank);
  const startTime = string(day.startTime);
  if (placeIds.length > 8 || new Set(placeIds).size !== placeIds.length || (startTime && !isTravelTime(startTime))) invalid();
  return { placeIds, startTime, searchQuery: day.searchQuery === undefined ? undefined : string(day.searchQuery) };
}

function readPlace(value: unknown): DemoPlace {
  const place = record(value);
  const id = nonblank(place.id), googlePlaceId = nonblank(place.googlePlaceId);
  const { lat, lng } = place;
  if (typeof lat !== 'number' || !Number.isFinite(lat) || Math.abs(lat) > 90) return invalid();
  if (typeof lng !== 'number' || !Number.isFinite(lng) || Math.abs(lng) > 180) return invalid();
  try {
    if (id !== googlePlaceKey(googlePlaceId)) return invalid();
  } catch { return invalid(); }
  return {
    id, googlePlaceId, lat, lng, name: nonblank(place.name), area: string(place.area), station: string(place.station),
    tags: readTags(place.tags), rank: 0,
    address: place.address == null ? null : string(place.address),
    primaryType: place.primaryType == null ? null : string(place.primaryType),
  };
}

export function readDraftFile(text: string, mode: DraftMode): { draft: Draft; places: DemoPlace[] } {
  if (new TextEncoder().encode(text).byteLength > MAX_DRAFT_FILE_BYTES) throw new Error('여행 초안 파일은 1MB 이하여야 해요.');
  let data: unknown;
  try { data = JSON.parse(text); } catch { return invalid(); }
  const file = record(data);
  if (file.version !== 1) throw new Error('지원하지 않는 여행 초안 파일 버전이에요.');
  if (file.mode !== mode) throw new Error('이 파일을 저장한 모드에서 가져와 주세요.');

  const row = record(file.draft);
  const startDate = string(row.startDate);
  const { dayCount, includeAccessWalking } = row;
  const days = array(row.days).map(readDay);
  if (startDate && !isTravelDate(startDate)) return invalid();
  if (typeof dayCount !== 'number' || !Number.isInteger(dayCount) || dayCount < 1 || dayCount > 7 || days.length < dayCount || days.length > 7) return invalid();
  if (typeof includeAccessWalking !== 'boolean' || (mode === 'example' && includeAccessWalking)) return invalid();

  const ids = new Set(days.flatMap((day) => day.placeIds));
  let places: DemoPlace[] = [];
  if (mode === 'example') {
    const exampleIds = new Set(PLACES.filter((place) => place.id.startsWith('demo-') && !place.googlePlaceId).map((place) => place.id));
    if (array(file.places).length || [...ids].some((id) => !exampleIds.has(id))) return invalid();
  } else {
    places = array(file.places).map(readPlace);
    if (places.length !== ids.size || new Set(places.map((place) => place.id)).size !== ids.size || places.some((place) => !ids.has(place.id))) return invalid();
  }
  const tags = readTags(row.tags);
  if (new Set(tags).size !== tags.length) return invalid();
  return { draft: { startDate, dayCount, days, tags, request: string(row.request), includeAccessWalking, revision: 0 }, places };
}

export function writeDraftFile(draft: Draft, mode: DraftMode): string {
  const { startDate, dayCount, tags, request, includeAccessWalking } = draft;
  if (startDate && !isTravelDate(startDate)) throw new Error('여행 시작일을 확인해 주세요.');
  const days = draft.days.map(({ placeIds, startTime, searchQuery }) => ({ placeIds, startTime, searchQuery }));
  const places = mode === 'example' ? [] : [...new Set(days.flatMap((day) => day.placeIds))].map((id) => {
    const { googlePlaceId, name, area, station, lat, lng, tags, address, primaryType } = placeById(id);
    return { id, googlePlaceId, name, area, station, lat, lng, tags, address, primaryType };
  });
  const text = JSON.stringify({ version: 1, mode, draft: { startDate, dayCount, days, tags, request, includeAccessWalking: includeAccessWalking === true }, places });
  if (new TextEncoder().encode(text).byteLength > MAX_DRAFT_FILE_BYTES) throw new Error('여행 초안이 1MB를 넘어요. 입력 내용을 줄인 뒤 다시 시도해 주세요.');
  return text;
}
