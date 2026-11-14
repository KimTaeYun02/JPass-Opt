export const TAGS = [
  ['culture', '역사·문화'], ['nature', '자연·풍경'], ['anime', '애니메이션'],
  ['shopping', '쇼핑'], ['relaxation', '휴식·힐링'], ['activity', '액티비티'],
] as const;
export type Tag = typeof TAGS[number][0];
export type DemoPlace = {
  id: string; name: string; area: string; station: string;
  lat: number; lng: number; tags: Tag[]; rank: number;
  googlePlaceId?: string; address?: string | null; primaryType?: string | null;
  distanceMeters?: number;
  searchTags?: string[];
  sources?: ('nearby' | 'text_search')[];
};
// These local IDs are not Google Place IDs; search and optimization use local fixtures.
export const PLACES: DemoPlace[] = [
  { id: 'demo-kiyomizu', name: '기요미즈데라', area: '히가시야마', station: '기요미즈고조역', lat: 34.9949, lng: 135.785, tags: ['culture', 'nature'], rank: 5 },
  { id: 'demo-nishiki', name: '니시키 시장', area: '가와라마치', station: '가라스마역', lat: 35.005, lng: 135.7649, tags: ['shopping', 'culture'], rank: 2 },
  { id: 'demo-arashi', name: '아라시야마 대나무숲', area: '아라시야마', station: '사가아라시야마역', lat: 35.017, lng: 135.6717, tags: ['nature', 'relaxation'], rank: 0 },
  { id: 'demo-kinkaku', name: '금각사', area: '기타야마', station: '기타노하쿠바이초역', lat: 35.0394, lng: 135.7292, tags: ['culture', 'nature'], rank: 1 },
  { id: 'demo-gion', name: '기온 골목', area: '기온', station: '기온시조역', lat: 35.003, lng: 135.775, tags: ['culture', 'shopping'], rank: 4 },
  { id: 'demo-manga', name: '교토 국제 만화 뮤지엄', area: '가라스마오이케', station: '가라스마오이케역', lat: 35.0117, lng: 135.7594, tags: ['anime', 'culture'], rank: 3 },
  { id: 'demo-fushimi', name: '후시미 이나리 신사', area: '후시미', station: '이나리역', lat: 34.9671, lng: 135.7727, tags: ['culture', 'activity'], rank: 9 },
  { id: 'demo-nanzen', name: '난젠지', area: '오카자키', station: '게아게역', lat: 35.0113, lng: 135.7938, tags: ['culture', 'relaxation'], rank: 7 },
  { id: 'demo-philosopher', name: '철학의 길', area: '사쿄', station: '게아게역', lat: 35.023, lng: 135.7928, tags: ['nature', 'relaxation'], rank: 8 },
  { id: 'demo-nijo', name: '니조성', area: '니조', station: '니조조마에역', lat: 35.0142, lng: 135.7482, tags: ['culture'], rank: 6 },
  { id: 'demo-railway', name: '교토 철도박물관', area: '우메코지', station: '우메코지교토니시역', lat: 34.9872, lng: 135.742, tags: ['activity', 'culture'], rank: 10 },
  { id: 'demo-park', name: '마루야마 공원', area: '기온', station: '기온시조역', lat: 35.0034, lng: 135.7803, tags: ['nature', 'relaxation'], rank: 11 },
];
export type DemoDay = { placeIds: string[]; startTime: string; searchQuery?: string };
export type Draft = { startDate: string; dayCount: number; days: DemoDay[]; tags: Tag[]; request: string; revision: number };
export type RouteStation = { name: string; location: { lat: number; lng: number }; line?: string };
export type TransportStep = { fromStation: RouteStation; toStation: RouteStation; line: string };
export type DemoLeg = { from: string; to: string; minutes: number; fare: number; line: string; route?: TransportStep[]; departureAt?: string; arrivalAt?: string };
export type DemoResult = { revision: number; dataSource?: 'live'; days: { order: string[]; legs: DemoLeg[]; minutes: number; fare: number; startTime: string; arrivals?: Record<string, string>; stations?: Record<string, RouteStation> }[] };
export function registerPlaces(places: DemoPlace[]): void {
  for (const place of places) {
    const index = PLACES.findIndex((item) => item.id === place.id);
    if (index < 0) PLACES.push(place);
    else PLACES[index] = place;
  }
}
export function mergeCandidatePlaces(groups: DemoPlace[][]): DemoPlace[] {
  const merged = new Map<string, DemoPlace>();
  for (const place of groups.flat()) {
    const key = place.googlePlaceId ?? place.id;
    const previous = merged.get(key);
    if (!previous) { merged.set(key, { ...place }); continue; }
    merged.set(key, {
      ...previous,
      tags: [...new Set([...previous.tags, ...place.tags])],
      searchTags: [...new Set([...(previous.searchTags ?? []), ...(place.searchTags ?? [])])],
      sources: [...new Set([...(previous.sources ?? []), ...(place.sources ?? [])])],
      distanceMeters: previous.distanceMeters === undefined ? place.distanceMeters : place.distanceMeters === undefined ? previous.distanceMeters : Math.min(previous.distanceMeters, place.distanceMeters),
    });
  }
  return [...merged.values()];
}
export function emptyDraft(): Draft {
  const draft = initialDraft();
  return { ...draft, days: draft.days.map((day) => ({ ...day, placeIds: [] })) };
}
export const placeById = (id: string): DemoPlace => {
  const place = PLACES.find((item) => item.id === id);
  if (!place) throw new Error('예시 장소를 찾을 수 없어요.');
  return place;
};
export function initialDraft(): Draft {
  return { startDate: '2026-11-10', dayCount: 2, days: [
    { placeIds: ['demo-kiyomizu', 'demo-nishiki'], startTime: '08:00' },
    { placeIds: ['demo-arashi', 'demo-kinkaku'], startTime: '08:00' },
  ], tags: ['culture', 'nature'], request: '', revision: 0 };
}
export function setDayCount(draft: Draft, count: number): Draft {
  if (!Number.isInteger(count) || count < 1 || count > 7) throw new Error('데모에서는 1~7일을 선택할 수 있어요.');
  const days = draft.days.map((day) => ({ ...day, placeIds: [...day.placeIds] }));
  while (days.length < count) days.push({ placeIds: [], startTime: '08:00' });
  // Hidden days stay in memory and return when the user extends the trip again.
  return { ...draft, dayCount: count, days, revision: draft.revision + 1 };
}
export function addPlace(draft: Draft, day: number, id: string): Draft {
  placeById(id);
  if (!Number.isInteger(day) || day < 0 || day >= draft.dayCount) throw new Error('방문 일차를 확인해 주세요.');
  if (draft.days[day]!.placeIds.includes(id)) throw new Error('이미 이날 일정에 있는 장소예요.');
  if (draft.days[day]!.placeIds.length >= 8) throw new Error('하루에 최대 8곳까지 추가할 수 있어요.');
  return { ...draft, revision: draft.revision + 1, days: draft.days.map((item, index) => index === day ? { ...item, placeIds: [...item.placeIds, id] } : item) };
}
export function removePlace(draft: Draft, day: number, id: string): Draft {
  return { ...draft, revision: draft.revision + 1, days: draft.days.map((item, index) => index === day ? { ...item, placeIds: item.placeIds.filter((itemId) => itemId !== id) } : item) };
}
export function addPlaces(draft: Draft, day: number, ids: string[]): Draft {
  // addPlace returns copies, so a rejected batch cannot partially alter the draft.
  return ids.reduce((next, id) => addPlace(next, day, id), draft);
}
export function validateDraft(draft: Draft, allowEmptyDays = false): string[] {
  const errors: string[] = [];
  const date = new Date(`${draft.startDate}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(draft.startDate) || !Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== draft.startDate) errors.push('여행 시작일을 확인해 주세요.');
  if (!Number.isInteger(draft.dayCount) || draft.dayCount < 1 || draft.dayCount > 7 || draft.days.length < draft.dayCount) errors.push('여행 일수를 확인해 주세요.');
  draft.days.slice(0, draft.dayCount).forEach((day, index) => {
    if ((!allowEmptyDays && day.placeIds.length < 1) || day.placeIds.length > 8) errors.push(`${index + 1}일차에는 장소를 1~8곳 넣어 주세요.`);
    if (new Set(day.placeIds).size !== day.placeIds.length || day.placeIds.some((id) => !PLACES.some((item) => item.id === id))) errors.push(`${index + 1}일차 장소를 확인해 주세요.`);
    if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(day.startTime)) errors.push(`${index + 1}일차 출발 시각을 확인해 주세요.`);
  });
  return errors;
}
export const searchPlaces = (query: string): DemoPlace[] => PLACES.filter((place) => `${place.name} ${place.area}`.includes(query.trim()));
// The initial recommendation endpoint is not specified yet. These are local examples,
// with Notion-compatible place fields; demoPlaceId must never be sent as googlePlaceId.
export type InitialCandidate = {
  demoPlaceId: string; googlePlaceId: null; name: string;
  location: { lat: number; lng: number }; searchTags: Tag[];
  address: null; primaryType: null;
};
export function initialRecommendations(draft: Draft, day: number, ownedIds: ReadonlySet<string>): InitialCandidate[] {
  const current = new Set(draft.days[day]?.placeIds ?? []);
  const otherDays = new Set(draft.days.slice(0, draft.dayCount).flatMap((item, index) => index === day ? [] : item.placeIds));
  return PLACES.filter((place) =>
    (current.has(place.id) && ownedIds.has(place.id)) ||
    (!current.has(place.id) && !otherDays.has(place.id) && (!draft.tags.length || place.tags.some((tag) => draft.tags.includes(tag))))
  ).sort((a, b) => {
    const matches = (place: DemoPlace): number => place.tags.filter((tag) => draft.tags.includes(tag)).length;
    return matches(b) - matches(a) || a.rank - b.rank;
  }).map((place) => ({
    demoPlaceId: place.id, googlePlaceId: null, name: place.name,
    location: { lat: place.lat, lng: place.lng }, searchTags: [...place.tags],
    address: null, primaryType: null,
  }));
}
export function recommendations(draft: Draft, day: number): DemoPlace[] {
  if (!draft.tags.length || !draft.days[day]?.placeIds.length) return [];
  const visited = new Set(draft.days.slice(0, draft.dayCount).flatMap((item) => item.placeIds));
  const anchors = draft.days[day]!.placeIds.map(placeById);
  // A local proximity fixture for interaction only, not a production nearby-search algorithm.
  return PLACES.filter((place) => !visited.has(place.id) && place.tags.some((tag) => draft.tags.includes(tag)) && anchors.some((anchor) => Math.hypot(anchor.lat - place.lat, anchor.lng - place.lng) < 0.035)).slice(0, 3);
}
export function computeExample(draft: Draft): DemoResult {
  const errors = validateDraft(draft);
  if (errors.length) throw new Error(errors[0]);
  return { revision: draft.revision, days: draft.days.slice(0, draft.dayCount).map((day) => {
    // A fixed fixture rank demonstrates a changed order; this is not backend optimization.
    const order = [...day.placeIds].sort((a, b) => placeById(a).rank - placeById(b).rank);
    const legs = order.slice(1).map((to, index) => {
      const from = order[index]!;
      const difference = Math.abs(placeById(from).rank - placeById(to).rank);
      return { from, to, minutes: 12 + difference * 4, fare: 230 + difference * 20, line: '교통편 예시' };
    });
    return { order, legs, minutes: legs.reduce((sum, leg) => sum + leg.minutes, 0), fare: legs.reduce((sum, leg) => sum + leg.fare, 0), startTime: day.startTime };
  }) };
}
export function canApplyResult(draft: Draft, result: DemoResult, requestId: number, latestRequestId: number): boolean {
  return draft.revision === result.revision && requestId === latestRequestId;
}
export function dateAt(start: string, offset: number): string {
  const date = new Date(`${start}T00:00:00Z`);
  if (!Number.isFinite(date.getTime())) return '날짜 미정';
  date.setUTCDate(date.getUTCDate() + offset);
  return new Intl.DateTimeFormat('ko-KR', { month: 'numeric', day: 'numeric', weekday: 'short', timeZone: 'Asia/Tokyo' }).format(date);
}
export function clockAt(start: string, elapsed: number): string {
  const [hour, minute] = start.split(':').map(Number);
  const total = hour! * 60 + minute! + elapsed;
  return `${total >= 1440 ? `+${Math.floor(total / 1440)}일 ` : ''}${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}
