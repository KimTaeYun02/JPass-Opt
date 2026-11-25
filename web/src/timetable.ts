import { clampPage, clock, dateAt, dayCount, escapeHtml } from './trip-model';
import type { Place } from './trip-model';

const dateLabel = (date: string): string => new Intl.DateTimeFormat('ko', { month: 'numeric', day: 'numeric', weekday: 'short', timeZone: 'UTC' }).format(new Date(`${date}T00:00:00Z`));

export function renderTimetable(board: HTMLElement, tray: HTMLElement, places: Place[], start: string, end: string, page: number, fullDay: boolean): number {
  const days = dayCount(start, end);
  const offset = clampPage(page, days) * 5;
  const dates = Array.from({ length: Math.min(5, days - offset) }, (_, index) => dateAt(start, offset + index));
  const begin = fullDay ? 0 : 8 * 60;
  const finish = fullDay ? 24 * 60 : 22 * 60;
  const slots = (finish - begin) / 30;
  const colors = ['peach', 'lavender', 'mint', 'blue', 'sand'];
  const color = (place: Place): string => colors[Math.max(0, places.findIndex((item) => item.id === place.id)) % colors.length]!;
  const validDate = (place: Place): boolean => days > 0 && !!place.fixed?.date && place.fixed.date >= start && place.fixed.date <= end;
  const event = (place: Place, timed = false): string => {
    const time = place.fixed?.time;
    const styles = timed && time ? `style="top:${(Number(time.slice(0, 2)) * 60 + Number(time.slice(3)) - begin) / 30 * 22}px;height:${place.duration / 30 * 22}px"` : '';
    return `<button type="button" class="schedule-event ${color(place)}${timed ? ' timed-event' : ''}${place.duration === 30 ? ' short-event' : ''}" data-edit="${escapeHtml(place.id)}" ${styles} aria-label="${escapeHtml(place.name)} 일정 수정">
      <strong>${escapeHtml(place.name)}</strong><span>${timed && time ? `${time}–${clock(Number(time.slice(0, 2)) * 60 + Number(time.slice(3)) + place.duration)}` : '날짜만 고정'}</span>
      <small>${place.required ? '꼭 방문' : '선택 방문'}${place.source === 'sample' ? ' · 예시' : ''}</small>${timed ? '<span class="resize-handle resize-start" data-resize="start" aria-hidden="true" title="시작 시간 조절"></span><span class="resize-handle resize-end" data-resize="end" aria-hidden="true" title="종료 시간 조절"></span>' : ''}</button>`;
  };
  const unscheduled = places.filter((place) => !place.fixed);
  const invalid = places.filter((place) => place.fixed && !validDate(place));
  const outsideView = places.filter((place) => validDate(place) && place.fixed?.time &&
    (Number(place.fixed.time.slice(0, 2)) * 60 + Number(place.fixed.time.slice(3)) < begin || Number(place.fixed.time.slice(0, 2)) * 60 + Number(place.fixed.time.slice(3)) + place.duration > finish));
  const trayCard = (place: Place, warning = false): string => `<button type="button" class="tray-place ${warning ? 'warning-place' : color(place)}" data-edit="${escapeHtml(place.id)}" aria-label="${escapeHtml(place.name)} 일정 수정"><strong>${escapeHtml(place.name)}</strong><span>${warning ? `${escapeHtml(place.fixed?.date || '')} ${escapeHtml(place.fixed?.time || '')} · ${invalid.includes(place) ? '여행 기간 밖' : '표시 시간 밖'}` : place.required ? '꼭 방문 · 날짜 자유' : '선택 방문 · 날짜 자유'}${place.source === 'sample' ? ' · 예시' : ''}</span></button>`;
  tray.innerHTML = `<div class="free-drop-zone" data-drop-free><div class="tray-title"><h3>날짜가 자유로운 장소</h3><span>끌어서 시간표에 놓아보세요</span></div><div class="tray-places">${unscheduled.map((place) => trayCard(place)).join('') || '<p class="empty-tray">이곳에 놓으면 날짜가 자유로워져요.</p>'}</div></div>
    ${invalid.length || outsideView.length ? `<div class="placement-warning"><p>화면 밖 일정도 유지하고 있어요. 눌러서 수정하거나 표시 시간을 바꿔 주세요.</p><div class="tray-places">${[...invalid, ...outsideView].map((place) => trayCard(place, true)).join('')}</div></div>` : ''}`;
  if (!dates.length) { board.innerHTML = '<p class="board-empty">시간표를 보려면 올바른 여행 기간을 입력해 주세요.</p>'; return 0; }
  const allDayHeight = Math.max(68, ...dates.map((date) => places.filter((place) => place.fixed?.date === date && !place.fixed.time).length * 66 + 28));
  board.style.setProperty('--day-count', String(dates.length));
  board.style.setProperty('--track-height', `${slots * 22}px`);
  board.innerHTML = `<div class="timetable-head"><div class="axis-heading">JST<span>일본 시간</span></div>${dates.map((date, index) => `<div class="day-heading" data-drop-date="${date}"><span>DAY ${offset + index + 1}</span><strong>${dateLabel(date)}</strong></div>`).join('')}</div>
    <div class="all-day-grid" style="min-height:${allDayHeight}px"><div class="all-day-label">날짜만<br />고정</div>${dates.map((date) => `<div class="all-day-column" data-drop-date="${date}">${places.filter((place) => place.fixed?.date === date && !place.fixed.time).map((place) => event(place)).join('')}<button type="button" class="all-day-add" data-date="${date}" data-time="" aria-label="${dateLabel(date)} 날짜만 고정해서 장소 배치">+ 날짜만 지정</button></div>`).join('')}</div>
    <div class="timetable-body"><div class="time-axis">${Array.from({ length: (finish - begin) / 60 + 1 }, (_, index) => `<span style="top:${index * 44}px">${clock(begin + index * 60)}</span>`).join('')}</div>
      ${dates.map((date) => `<div class="day-track" data-drop-date="${date}" data-begin="${begin}" data-finish="${finish}">${Array.from({ length: slots }, (_, index) => `<button type="button" class="time-slot" data-date="${date}" data-time="${clock(begin + index * 30)}" aria-label="${dateLabel(date)} ${clock(begin + index * 30)}에 장소 배치"><span aria-hidden="true">+</span></button>`).join('')}${places.filter((place) => place.fixed?.date === date && place.fixed.time && !outsideView.includes(place)).map((place) => event(place, true)).join('')}</div>`).join('')}</div>`;
  return offset / 5;
}
