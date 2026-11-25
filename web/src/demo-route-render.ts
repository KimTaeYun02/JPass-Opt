import type { DemoLeg, DemoPlace, DemoResult, RouteStation } from './demo-model';
import { formatJapanTime } from './demo-api.ts';

type RouteDay = DemoResult['days'][number];
type RouteView = { live: boolean; dayIndex: number; date: string; selected?: string; place: (id: string) => DemoPlace; legActions?: (leg: DemoLeg, index: number) => string };
const html = (value: string): string => value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);
const yen = (value: number): string => `¥${value.toLocaleString('ja-JP')}`;
const stationName = (station: string | undefined): string => station && station !== '경로 계산 후 확인' ? station : '역 정보 미제공';
const sameStation = (a: RouteStation | undefined, b: RouteStation | undefined): boolean => !!a && !!b && a.name === b.name && a.location.lat === b.location.lat && a.location.lng === b.location.lng;

function renderLeg(leg: DemoLeg, day: RouteDay, view: RouteView): string {
  const from = view.place(leg.from), to = view.place(leg.to);
  if (day.accessWalking && leg.segments) {
    return `<section class="route-journey" aria-label="${html(from.name)}에서 ${html(to.name)}까지 이동"><div class="route-transit"><div class="leg-head"><span>관광지 간 이동</span><strong>${leg.minutes}분 <span>· ${yen(leg.fare)}</span></strong></div><ol class="route-steps" aria-label="관광지 사이 이동 순서">${leg.segments.map((segment) => `<li class="route-step"><span class="route-mode${segment.mode === 'walking' ? ' walking' : ''}">${segment.mode === 'walking' ? '도보' : '노선 이동'}</span><div><strong>${segment.durationMinutes}분${segment.distanceMeters !== null ? ` · ${segment.distanceMeters.toLocaleString('ko-KR')}m` : ''}</strong><p>${html(segment.fromPoint.name)} → ${html(segment.toPoint.name)}</p>${segment.mode === 'transit' ? `<ol class="route-steps" aria-label="이용 역과 노선 순서">${(leg.route ?? []).map((step) => `<li class="route-step"><span class="route-mode${step.line === '도보' ? ' walking' : ''}">${step.line === '도보' ? '도보' : '노선 이동'}</span><div><strong>${html(step.line)}</strong><p>${html(step.fromStation.name)} → ${html(step.toStation.name)}</p></div></li>`).join('')}</ol>` : ''}</div></li>`).join('')}</ol></div></section>`;
  }
  const fromStation = stationName(day.stations?.[leg.from]?.name ?? from.station);
  const toStation = stationName(day.stations?.[leg.to]?.name ?? to.station);
  const steps = leg.route ?? [];
  const shared = !steps.length && sameStation(day.stations?.[leg.from], day.stations?.[leg.to]);
  const inconsistent = shared && (leg.minutes !== 0 || leg.fare !== 0);
  const detail = steps.length ? `<ol class="route-steps" aria-label="이용 역과 노선 순서">${steps.map((step) => `<li class="route-step"><span class="route-mode${step.line === '도보' ? ' walking' : ''}">${step.line === '도보' ? '도보' : '노선 이동'}</span><div><strong>${html(step.line)}</strong><p>${html(step.fromStation.name)} → ${html(step.toStation.name)}</p></div></li>`).join('')}</ol>`
    : `<p class="route-unavailable">${inconsistent ? '같은 역으로 조회됐지만 이동 시간·요금이 있어 경로 확인이 필요해요.' : shared ? '두 장소의 이용 역이 같아요. 장소 사이 실제 이동은 별도로 확인해 주세요.' : view.live ? '상세 이동 경로가 제공되지 않았어요.' : `${html(leg.line)} · 실제 이용 역·노선은 API 연결 후 확인할 수 있어요.`}</p>`;
  return `<section class="route-journey" aria-label="${html(from.name)}에서 ${html(to.name)}까지 이동">
    <p class="route-access"><span>${html(from.name)} → ${html(fromStation)}</span><small>장소↔역 이동 정보 미제공</small></p>
    <div class="route-transit"><div class="leg-head"><span>${inconsistent ? '역 간 이동 · 확인 필요' : shared ? '같은 이용 역' : '역 간 이동'}${view.live ? '' : ' · 예시'}</span><strong>${leg.minutes}분 <span>· ${yen(leg.fare)}</span></strong></div>${detail}</div>
    <p class="route-access"><span>${html(toStation)} → ${html(to.name)}</span><small>장소↔역 이동 정보 미제공</small></p>
  </section>`;
}

export function renderRouteDay(day: RouteDay, view: RouteView): string {
  let elapsed = 0;
  return `<div class="day-summary"><span>${view.dayIndex + 1}일차 · ${day.accessWalking ? '' : '역 간 '}교통비 ${view.live ? '조회 결과' : '예시'}</span><div class="summary-main"><strong>${yen(day.fare)}</strong><span>${day.accessWalking ? '도보 포함 이동' : '역 간 이동'} ${day.minutes}분 · ${day.order.length}곳 방문</span></div></div>
    <p class="timing-note route-basis">${day.accessWalking ? '관광지↔역 도보가 포함돼요. 접근 도보가 짧은 역과 일부 직접 도보를 비교한 결과이며, 모든 역 조합을 비교하지는 않아요. 체류 시간과 누적 출발 시각에 따른 시간표 변경은 반영하지 않아 표시 시각은 참고값이에요. 관광지↔역 버스 연결은 아직 지원하지 않아요.' : '시간·요금은 역 간 이동 기준이에요. 관광지↔역 이동과 관광지 체류 시간은 포함되지 않아요. 표시 시각은 실제 관광지 도착 시각이 아닌 참고값이에요.'}</p>
    ${day.accessWalking ? `<p class="helper">도보 경로는 베타 정보로 보도·보행로가 누락될 수 있어요. 현장 안내를 함께 확인해 주세요.</p>${day.candidateSearchTruncated ? '<p class="helper" role="status">직접 도보는 가까운 일부 구간만 비교했어요.</p>' : ''}` : ''}
    <ol class="route-list">${day.order.map((id, index) => {
      const place = view.place(id), leg = day.legs[index];
      const arrival = view.live && day.arrivals?.[id] ? formatJapanTime(day.arrivals[id], view.date) : undefined;
      const offset = elapsed;
      if (leg) elapsed += leg.minutes;
      return `<li><button type="button" class="route-stop ${view.selected === id ? 'selected' : ''}" data-action="focus" data-id="${html(id)}"><span class="route-time"><span class="sr-only">누적 ${day.accessWalking ? '전체' : '역 간'} 이동 </span>+${offset}분</span><span class="route-dot">${index + 1}</span><span class="route-info"><strong>${html(place.name)}</strong><span class="station-label">${day.accessWalking ? '참고 역' : '이용 역'} · ${html(stationName(day.stations?.[id]?.name ?? place.station))}</span>${arrival ? `<small class="route-reference-time">이동 기준 시각 ${html(arrival)}</small>` : ''}</span></button>${leg ? renderLeg(leg, day, view) + (view.legActions?.(leg, index) ?? '') : ''}</li>`;
    }).join('')}</ol>
    ${day.order.length === 1 ? '<p class="route-unavailable single-place">선택한 장소가 1곳이라 관광지 사이 이동 구간이 없어요.</p>' : ''}`;
}
