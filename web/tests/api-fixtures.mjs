import { registerPlaces, emptyDraft, addPlace } from '../src/demo-model.ts';
export const apiPlaces = ['a', 'b', 'c'].map((id, index) => ({ id: `google:contract-${id}`, googlePlaceId: `contract-${id}`, name: index < 2 ? 'same name' : '<img src=x onerror=alert(1)>', lat: 35 + index / 100, lng: 139, area: 'Japan', station: 'not yet known', tags: ['culture'], rank: 0 }));
registerPlaces(apiPlaces);
export function draftWithPlaces() {
  const draft = emptyDraft();
  draft.dayCount = 1;
  return apiPlaces.slice(0, 2).reduce((state, place) => addPlace(state, 0, place.id), draft);
}
const station = { name: 'same station', location: { lat: 35, lng: 139 } };
export function routeResponse(draft = draftWithPlaces()) {
  const ids = [...draft.days[0].placeIds].reverse();
  const visits = ids.map((id, i) => {
    const place = apiPlaces.find((place) => place.id === id);
    return { visitId: `server-${i}`, order: i + 1, name: place.name, googlePlaceId: place.googlePlaceId, location: { lat: place.lat, lng: place.lng }, station, estimatedArrivalAt: `2026-11-10T08:${i ? '17' : '00'}:00+09:00` };
  });
  return { startDate: draft.startDate, dayCount: 1, timezone: 'Asia/Tokyo', currency: 'JPY', dataSource: 'live', timingBasis: 'stationTravelOnly', totalTravelMinutes: 17, totalTransportFare: 321, days: [{ dayIndex: 1, date: draft.startDate, startAt: '2026-11-10T08:00:00+09:00', endAt: '2026-11-10T08:17:00+09:00', visits, legs: [{ fromVisitId: 'server-0', toVisitId: 'server-1', departureAt: '2026-11-10T08:00:00+09:00', arrivalAt: '2026-11-10T08:17:00+09:00', durationMinutes: 17, fare: 321, route: [{ fromStation: station, toStation: { name: 'transfer', location: { lat: 35.005, lng: 139.002 } }, line: 'server line' }] }], totalTravelMinutes: 17, totalTransportFare: 321 }] };
}
