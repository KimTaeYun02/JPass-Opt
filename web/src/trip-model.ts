export type Place = {
  id: string;
  name: string;
  address: string;
  source: 'sample' | 'google';
  placeId?: string;
  location?: { lat: number; lng: number };
  required: boolean;
  duration: number;
  fixed?: { date: string; time?: string };
};
export type Endpoint = { place: string; date: string; time: string };
export type TripInput = {
  range: { start: string; end: string };
  places: Place[];
  arrival: Endpoint;
  departure: Endpoint;
  stay: string;
  preference: 'cost' | 'balance' | 'time';
};

export const DEFAULT_VISIT_MINUTES = 120;
export const SLOT_MINUTES = 30;
export type ScheduleTarget = { kind: 'free' } | { kind: 'date'; date: string } | { kind: 'time'; date: string; minute: number };

export function snapMinute(value: number): number {
  return Math.round(value / SLOT_MINUTES) * SLOT_MINUTES;
}

export function fitMoveMinute(value: number, duration: number, lower: number, upper: number): number | undefined {
  if (![value, duration, lower, upper].every(Number.isFinite) || duration < SLOT_MINUTES || duration > upper - lower) return undefined;
  return Math.max(lower, Math.min(upper - duration, snapMinute(value)));
}

// Candidates never mutate the source; validation and commit belong to the controller.
export function movePlace(place: Place, target: ScheduleTarget): Place | undefined {
  if (target.kind === 'time' && !Number.isFinite(target.minute)) return undefined;
  return { ...place, fixed: target.kind === 'free' ? undefined : {
    date: target.date, ...(target.kind === 'time' ? { time: clock(snapMinute(target.minute)) } : {}),
  } };
}

export function resizePlace(place: Place, edge: 'start' | 'end', value: number, lower = 0, upper = 1440): Place | undefined {
  if (!place.fixed?.time || !Number.isFinite(value)) return undefined;
  const start = minutes(place.fixed.time);
  const end = start + place.duration;
  if (!Number.isFinite(start) || start < lower || end > upper || end - start < SLOT_MINUTES) return undefined;
  const point = snapMinute(value);
  const nextStart = edge === 'start' ? Math.max(lower, Math.min(end - SLOT_MINUTES, point)) : start;
  const nextEnd = edge === 'end' ? Math.min(upper, Math.max(start + SLOT_MINUTES, point)) : end;
  return { ...place, fixed: { ...place.fixed, time: clock(nextStart) }, duration: nextEnd - nextStart };
}

export function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]!);
}

export function minutes(time: string): number {
  const [hour, minute] = time.split(':').map(Number);
  return hour! * 60 + minute!;
}

export function clock(value: number): string {
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}

export function dateAt(start: string, offset: number): string {
  return new Date(Date.parse(start) + offset * 86_400_000).toISOString().slice(0, 10);
}

export function dayCount(start: string, end: string): number {
  const count = (Date.parse(end) - Date.parse(start)) / 86_400_000 + 1;
  return Number.isFinite(count) && count > 0 ? count : 0;
}

export function clampPage(page: number, days: number): number {
  const last = Math.max(0, Math.ceil(days / 5) - 1);
  return Math.max(0, Math.min(Number.isFinite(page) ? Math.floor(page) : 0, last));
}

export function placementErrors(place: Place, places: Place[], start: string, end: string): string[] {
  if (!place.fixed) return [];
  const errors: string[] = [];
  if (!place.fixed.date) errors.push('방문 날짜를 선택해 주세요.');
  else if (start && end && (place.fixed.date < start || place.fixed.date > end)) errors.push('방문 날짜가 여행 기간 밖이에요.');
  if (place.fixed.time !== undefined) {
    const time = place.fixed.time;
    if (!/^([01]\d|2[0-3]):(00|30)$/.test(time)) errors.push('방문 시간은 30분 단위로 선택해 주세요.');
    if (!Number.isFinite(place.duration) || place.duration < SLOT_MINUTES || place.duration > 1440 || place.duration % SLOT_MINUTES !== 0) errors.push('방문 시간은 30분 단위로 선택해 주세요.');
    const begin = minutes(time);
    const finish = begin + place.duration;
    if (finish > 1440) errors.push('자정을 넘는 방문은 날짜별로 나누어 입력해 주세요.');
    if (Number.isFinite(begin) && places.some((other) => other.id !== place.id && other.fixed?.date === place.fixed?.date && other.fixed?.time &&
      begin < minutes(other.fixed.time) + other.duration && finish > minutes(other.fixed.time))) {
      errors.push('이미 배치한 장소와 시간이 겹쳐요. 다른 시간을 선택해 주세요.');
    }
  }
  return errors;
}

export function validate(input: TripInput): string[] {
  const errors: string[] = [];
  const { start, end } = input.range;
  if (!start || !end) errors.push('여행 시작일과 종료일을 입력해 주세요.');
  if (start && end && end < start) errors.push('종료일은 시작일보다 빠를 수 없어요.');
  if (!input.places.length) errors.push('가고 싶은 곳을 한 곳 이상 추가해 주세요.');
  else if (!input.places.some((place) => place.required)) errors.push('꼭 방문할 곳을 한 곳 이상 선택해 주세요.');
  const ids = input.places.filter((place) => place.placeId).map((place) => place.placeId);
  if (new Set(ids).size !== ids.length) errors.push('같은 장소가 중복되어 있어요.');
  input.places.forEach((place) => errors.push(...placementErrors(place, input.places, start, end).map((message) => `${place.name}: ${message}`)));
  for (const [label, endpoint] of [['도착', input.arrival], ['출발', input.departure]] as const) {
    if (endpoint.time && !endpoint.date) errors.push(`${label} 시간을 입력했다면 날짜도 선택해 주세요.`);
    if (start && end && endpoint.date && (endpoint.date < start || endpoint.date > end)) errors.push(`${label} 날짜가 여행 기간 밖이에요.`);
  }
  if (input.arrival.date && input.departure.date &&
    `${input.arrival.date}T${input.arrival.time || '00:00'}` > `${input.departure.date}T${input.departure.time || '23:59'}`) {
    errors.push('출발 시점은 도착 시점보다 빠를 수 없어요.');
  }
  return errors;
}
