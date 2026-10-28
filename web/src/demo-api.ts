import { TAGS, placeById, validateDraft } from './demo-model.ts';
import type { DemoPlace, DemoResult, Draft, RouteStation, Tag, TransportStep } from './demo-model.ts';

// Contract: Notion API 스키마 확정 / input-output docs, read 2026-10-27.
export type Location = { lat: number; lng: number };
export type RouteRequest = { startDate: string; dayCount: number; days: { dayIndex: number; dailyStartTime: string; places: { name: string; googlePlaceId: string; location: Location }[] }[] };
export type NearbyRequest = { travelTypes: Tag[]; userRequest: string; stations: { name?: string; stationId?: string; line?: string; location: Location }[]; placeCount: number; radius: number };
export type PlaceSearch = { places: DemoPlace[]; clarification: string | null };
export class TravelApiError extends Error {
  status: number;
  constructor(message: string, status = 0) { super(message); this.status = status; }
}
const invalid = (): never => { throw new TravelApiError('서버 응답 형식이 명세와 달라요. 응답 데이터를 확인해 주세요.'); };
const obj = (value: unknown): Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : invalid();
const list = (value: unknown): unknown[] => Array.isArray(value) ? value : invalid();
const str = (value: unknown): string => typeof value === 'string' && value.trim() ? value : invalid();
const num = (value: unknown): number => typeof value === 'number' && Number.isFinite(value) ? value : invalid();
const count = (value: unknown): number => { const n = num(value); return Number.isInteger(n) && n >= 0 ? n : invalid(); };
function location(value: unknown): Location {
  const point = obj(value), lat = num(point.lat), lng = num(point.lng);
  return Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? { lat, lng } : invalid();
}
function timestamp(value: unknown): string {
  const text = str(value);
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(text) && Number.isFinite(Date.parse(text)) ? text : invalid();
}
function station(value: unknown): RouteStation {
  const row = obj(value);
  return { name: str(row.name), location: location(row.location) };
}
export function formatJapanTime(iso: string, date: string): string {
  const instant = new Date(timestamp(iso));
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(instant);
  const part = (name: string) => parts.find((item) => item.type === name)!.value;
  const day = `${part('year')}-${part('month')}-${part('day')}`;
  const offset = Math.round((Date.parse(day + 'T00:00:00Z') - Date.parse(date + 'T00:00:00Z')) / 86400000);
  return `${offset !== 0 ? `${offset > 0 ? '+' : ''}${offset}일 ` : ''}${part('hour')}:${part('minute')}`;
}
export function buildRouteRequest(draft: Draft): RouteRequest {
  const errors = validateDraft(draft);
  if (errors.length) throw new TravelApiError(errors[0]);
  return { startDate: draft.startDate, dayCount: draft.dayCount, days: draft.days.slice(0, draft.dayCount).map((day, index) => ({
    dayIndex: index + 1, dailyStartTime: day.startTime, places: day.placeIds.map((id) => {
      const place = placeById(id);
      if (!place.googlePlaceId?.trim() || id.startsWith('demo-')) throw new TravelApiError(`${place.name}: 실제 장소 검색 결과를 선택해 주세요.`);
      return { name: place.name, googlePlaceId: place.googlePlaceId, location: location({ lat: place.lat, lng: place.lng }) };
    }),
  })) };
}
export function parseRouteResponse(value: unknown, snapshot: Draft): DemoResult {
  const root = obj(value), request = buildRouteRequest(snapshot);
  if (root.timezone !== 'Asia/Tokyo' || root.currency !== 'JPY' || root.dataSource !== 'live' || root.timingBasis !== 'stationTravelOnly' || root.startDate !== snapshot.startDate || root.dayCount !== snapshot.dayCount) invalid();
  const rawDays = list(root.days);
  if (rawDays.length !== snapshot.dayCount) invalid();
  const days = request.days.map((input, index) => {
    const matches = rawDays.filter((item) => obj(item).dayIndex === input.dayIndex);
    if (matches.length !== 1) invalid();
    const day = obj(matches[0]);
    const date = new Date(snapshot.startDate + 'T00:00:00Z'); date.setUTCDate(date.getUTCDate() + index);
    if (day.date !== date.toISOString().slice(0, 10)) invalid();
    timestamp(day.startAt); timestamp(day.endAt);
    const visits = list(day.visits).map(obj).sort((a, b) => count(a.order) - count(b.order));
    if (visits.length !== input.places.length) invalid();
    const order: string[] = [], arrivals: Record<string, string> = {}, stations: Record<string, RouteStation> = {};
    const visitIds = new Map<string, string>();
    visits.forEach((visit, i) => {
      if (visit.order !== i + 1) invalid();
      const googleId = str(visit.googlePlaceId);
      const localIds = snapshot.days[index]!.placeIds.filter((id) => placeById(id).googlePlaceId === googleId);
      if (localIds.length !== 1) invalid();
      const id = localIds[0]!, visitId = str(visit.visitId);
      if (order.includes(id) || visitIds.has(visitId)) invalid();
      location(visit.location); str(visit.name);
      order.push(id); visitIds.set(visitId, id);
      arrivals[id] = timestamp(visit.estimatedArrivalAt); stations[id] = station(visit.station);
    });
    const legs = list(day.legs).map((item, i) => {
      const leg = obj(item);
      const from = visitIds.get(str(leg.fromVisitId)), to = visitIds.get(str(leg.toVisitId));
      if (from !== order[i] || to !== order[i + 1]) invalid();
      const route: TransportStep[] = list(leg.route).map((value) => {
        const step = obj(value); return { fromStation: station(step.fromStation), toStation: station(step.toStation), line: str(step.line) };
      });
      return { from: from!, to: to!, minutes: count(leg.durationMinutes), fare: count(leg.fare), line: route.map((step) => step.line).join(' → ') || '동일 역', route, departureAt: timestamp(leg.departureAt), arrivalAt: timestamp(leg.arrivalAt) };
    });
    const minutes = count(day.totalTravelMinutes), fare = count(day.totalTransportFare);
    if (legs.length !== order.length - 1 || minutes !== legs.reduce((sum, leg) => sum + leg.minutes, 0) || fare !== legs.reduce((sum, leg) => sum + leg.fare, 0)) invalid();
    return { order, arrivals, stations, legs, minutes, fare, startTime: formatJapanTime(str(day.startAt), str(day.date)) };
  });
  if (count(root.totalTravelMinutes) !== days.reduce((sum, day) => sum + day.minutes, 0) || count(root.totalTransportFare) !== days.reduce((sum, day) => sum + day.fare, 0)) invalid();
  return { revision: snapshot.revision, dataSource: 'live', days };
}
function apiPlace(googlePlaceId: string, name: string, point: Location, address: unknown, primaryType: unknown, tags: Tag[] = []): DemoPlace {
  return { id: `google:${encodeURIComponent(googlePlaceId)}`, googlePlaceId, name, lat: point.lat, lng: point.lng, area: typeof address === 'string' ? address : '일본', station: '경로 계산 후 확인', address: typeof address === 'string' ? address : null, primaryType: typeof primaryType === 'string' ? primaryType : null, tags, rank: 0 };
}
export function normalizeNearby(value: unknown): DemoPlace[] {
  const root = obj(value);
  // Notion's schema describes the inner object; its JSON example wraps it in results[].
  const groups = root.results === undefined ? [root] : list(root.results).map(obj);
  const places = new Map<string, DemoPlace>();
  groups.forEach((group) => {
    location(obj(group.station).location);
    num(group.radius); count(group.requestedCount);
    const items = list(group.recommendedPlaces);
    if (count(group.returnedCount) !== items.length) invalid();
    items.forEach((item) => {
      const row = obj(item), tags = list(row.searchTags).map(str);
      if (!tags.length) invalid();
      const knownTags = tags.filter((tag): tag is Tag => TAGS.some(([id]) => id === tag));
      const place = apiPlace(str(row.googlePlaceId), str(row.name), location(row.location), row.address, row.primaryType, knownTags);
      // Notion constrains input travelTypes, but output searchTags is an open string array.
      place.searchTags = [...tags];
      place.distanceMeters = num(row.distanceMeters);
      if (place.distanceMeters < 0) invalid();
      const existing = places.get(place.id);
      if (existing) { existing.tags = [...new Set([...existing.tags, ...place.tags])]; existing.searchTags = [...new Set([...existing.searchTags!, ...tags])]; existing.distanceMeters = Math.min(existing.distanceMeters!, place.distanceMeters); }
      else places.set(place.id, place);
    });
  });
  return [...places.values()];
}
export function buildNearbyRequest(draft: Draft, result: DemoResult, dayIndex: number): NearbyRequest {
  if (!draft.tags.length) throw new TravelApiError('여행 취향을 한 개 이상 선택해 주세요.');
  if (draft.revision !== result.revision) throw new TravelApiError('변경한 일정으로 먼저 다시 계산해 주세요.');
  const day = result.days[dayIndex];
  if (!day?.stations) throw new TravelApiError('추천에 사용할 계산된 역 정보가 없어요.');
  const stations = new Map<string, NearbyRequest['stations'][number]>();
  const add = (value: RouteStation, line?: string) => {
    const key = JSON.stringify([value.name, value.location.lat, value.location.lng]);
    const previous = stations.get(key);
    // Use this identity only for local deduplication. Optional stationId is omitted.
    // Preserve the first raw line string instead of synthesizing a combined line name.
    stations.set(key, { ...value, line: previous?.line ?? line });
  };
  Object.values(day.stations).forEach((value) => add(value));
  day.legs.forEach((leg) => leg.route?.forEach((step) => { add(step.fromStation, step.line); add(step.toStation, step.line); }));
  if (!stations.size) throw new TravelApiError('추천에 사용할 역이 없어요.');
  return { travelTypes: [...draft.tags], userRequest: draft.request, stations: [...stations.values()], placeCount: 3, radius: 1000 };
}

// PROVISIONAL: /api/places/search exists in backend, but has no Notion input/output docs yet.
// Keep this adapter isolated so an agreed Notion contract can replace it.
export function parseTextSearch(value: unknown): PlaceSearch {
  const root = obj(value), places = new Map<string, DemoPlace>();
  const clarification = root.clarificationQuestion == null ? null : str(root.clarificationQuestion);
  if (clarification) return { places: [], clarification };
  const results = list(root.results);
  list(root.textQueries).forEach(str);
  results.forEach((group) => {
    const response = obj(obj(group).response);
    for (const value of response.places === undefined ? [] : list(response.places)) {
      const row = obj(value);
      // Google omits unavailable fields. Never turn incomplete records into selectable places.
      if (!row.id || !row.displayName || !row.location) continue;
      if (!obj(row.displayName).text || obj(row.location).latitude === undefined || obj(row.location).longitude === undefined) continue;
      const rawPoint = obj(row.location);
      const place = apiPlace(str(row.id), str(obj(row.displayName).text), location({ lat: rawPoint.latitude, lng: rawPoint.longitude }), row.formattedAddress, row.primaryType);
      if (!places.has(place.id)) places.set(place.id, place);
    }
  });
  return { places: [...places.values()], clarification: null };
}
export function createTravelApi(base = '/api', fetcher: typeof fetch = fetch) {
  async function post(path: string, body: unknown, timeout: number): Promise<unknown> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetcher(`${base.replace(/\/$/, '')}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: controller.signal });
      if (!response.ok) {
        const messages: Record<number, string> = { 422: '입력값 또는 조회 가능한 역·경로를 확인해 주세요.', 429: '검색 서비스의 호출 한도를 초과했어요. 잠시 후 다시 시도해 주세요.', 502: '외부 검색·경로 서비스에서 정상 응답을 받지 못했어요.', 503: '백엔드의 Google Places·Gemini API 키 설정이 필요해요.', 504: '외부 조회 시간이 초과됐어요. 잠시 후 직접 다시 시도해 주세요.' };
        throw new TravelApiError(messages[response.status] ?? '백엔드 요청에 실패했어요. 서버 실행 상태를 확인해 주세요.', response.status);
      }
      return await response.json();
    } catch (error) {
      if (error instanceof TravelApiError) throw error;
      throw new TravelApiError(controller.signal.aborted ? '응답 대기 시간이 초과됐어요. 다시 요청하기 전에 서버 상태를 확인해 주세요.' : '백엔드에 연결할 수 없어요. 서버 실행 상태를 확인해 주세요.');
    } finally { clearTimeout(timer); }
  }
  return {
    async optimize(draft: Draft) { return parseRouteResponse(await post('/routes/optimize', buildRouteRequest(draft), 600000), draft); },
    async nearby(draft: Draft, result: DemoResult, day: number) { return normalizeNearby(await post('/nearby/recommend', buildNearbyRequest(draft, result, day), 120000)); },
    async search(query: string) {
      if (!query.trim()) throw new TravelApiError('찾고 싶은 장소나 요청사항을 입력해 주세요.');
      return parseTextSearch(await post('/places/search', { userQuery: query.trim(), languageCode: 'ko', regionCode: 'JP', pageSize: 8 }, 120000));
    },
  };
}
