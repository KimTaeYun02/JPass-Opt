import './demo.css';
import './demo-reference.css';
import { TAGS, addPlace, addPlaces, canApplyResult, computeExample, dateAt, emptyDraft, initialDraft, initialRecommendations, placeById, recommendations, registerPlaces, removePlace, searchPlaces, setDayCount, validateDraft } from './demo-model';
import { createTravelApi, nearbyRouteStations, searchBiasForRouteLeg } from './demo-api';
import { MAX_DRAFT_FILE_BYTES, readDraftFile, writeDraftFile } from './demo-draft-file';
import { renderRouteDay } from './demo-route-render';
import { travelApiBase } from './demo-config';
import { renderDemoMarkup } from './demo-render';
import type { DemoLeg, DemoPlace, DemoResult, Draft, Tag } from './demo-model';
import type { PlaceSearch } from './demo-api';
import { createGoogleDemoMap } from './demo-map';
import { mapRoutePaths } from './demo-map-controller';
import type { DemoMapView, MapPhase } from './demo-map-controller';

const app = document.querySelector<HTMLElement>('#app')!;
const dialog = document.querySelector<HTMLDialogElement>('#place-dialog')!;
const search = document.querySelector<HTMLInputElement>('#demo-search')!;
const searchResults = document.querySelector<HTMLElement>('#search-results')!;
const searchSelection = document.querySelector<HTMLElement>('#search-selection')!;
const searchConfirm = document.querySelector<HTMLButtonElement>('#search-confirm')!;
const draftImport = document.querySelector<HTMLInputElement>('#draft-import')!;
const stagedPlaces = new Set<string>();
const toast = document.querySelector<HTMLElement>('#toast')!;
const apiMode = new URLSearchParams(window.location.search).get('mode') !== 'example';
const api = createTravelApi(travelApiBase);
const draftMode = apiMode ? 'api' : 'example';
const draftStorageKey = `jpass.travel-draft.${draftMode}`;
const freshDraft = apiMode ? emptyDraft() : initialDraft();
let draft = freshDraft;
let storedDraftText: string | null = null;
let storedDraftUnreadable = false;
let draftSaveMessage = '변경하면 이 브라우저에 자동 저장돼요.';
let latestImport = 0;
const initialQuery = (): string => draft.days[activeDay]?.searchQuery ?? '';
const initialDays = new Map<number, { ids: string[]; message: string }>();
let initialAbort: AbortController | undefined;
let initialPool: string[] = [], nearbyPool: string[] = [], searchPool: string[] = [];
const loading = { initial: false, nearby: false, search: false };
const messages = { initial: '검색 버튼을 눌러 추천 장소를 불러오세요.', nearby: '추천 받기를 누르면 계산된 역 주변에서 조회해요.', search: '검색어를 입력하고 검색 버튼을 눌러주세요.' };
const tokens = { initial: 0, nearby: 0, search: 0 };
let nearbyKind: 'nearby' | 'text' = 'nearby';
let nearbyQuery = '';
let operationError = '';
const placeCache = new Map<string, PlaceSearch>();
let activeDay = 0;
let view: 'input' | 'choose' | 'result' = 'input';
// Zero-based draft day indices; ownership is only for picks made in step 2.
const initialAddedIds = new Map<number, Set<string>>();
let nearbyOpen = false;
let nearbyLegIndex: number | undefined;
let result: DemoResult | undefined;
let resultStartDate = '';
const liveResult = (): boolean => result?.dataSource === 'live';
let selected: string | undefined;
let running = false;
let latestRequest = 0;
let navigationRevision = 0;
let mapController: ReturnType<typeof createGoogleDemoMap> | undefined;
let mapHost: HTMLElement | undefined;
let mapPhase: MapPhase = 'loading';
let mapStatusMessage = 'Google 지도를 불러오는 중입니다.';
let toastTimer: ReturnType<typeof setTimeout>;
const html = (value: string): string => value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]!);
const yen = (value: number): string => `¥${value.toLocaleString('ja-JP')}`;
const isDirty = (): boolean => !!result && result.revision !== draft.revision;
const canResetDraft = (): boolean => draft !== freshDraft || storedDraftUnreadable || running || result !== undefined;
const tagLabel = (tag: Tag): string => TAGS.find(([id]) => id === tag)![1];
// Narrow preview panels show the PC canvas at scale instead of switching to a mobile layout.
function updatePreviewScale(): void {
  document.documentElement.style.setProperty('--desktop-preview-scale', String(window.innerWidth / 1366));
}
window.addEventListener('resize', updatePreviewScale);
updatePreviewScale();
function notify(message: string): void {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add('visible');
  toastTimer = setTimeout(() => toast.classList.remove('visible'), 3200);
}
function setDraftSaveMessage(message: string): void {
  if (draftSaveMessage === message) return;
  draftSaveMessage = message;
  const status = document.getElementById('draft-save-status');
  if (status) status.textContent = message;
}
function restoreDraft(): void {
  try { storedDraftText = window.localStorage.getItem(draftStorageKey); }
  catch { setDraftSaveMessage('브라우저 저장을 사용할 수 없어요. 파일로 내보낼 수 있어요.'); return; }
  if (storedDraftText === null) return;
  try {
    const saved = readDraftFile(storedDraftText, draftMode);
    registerPlaces(saved.places);
    draft = saved.draft;
    setDraftSaveMessage('이 브라우저에서 저장한 초안을 불러왔어요.');
  } catch {
    storedDraftUnreadable = true;
    setDraftSaveMessage('저장된 초안을 읽지 못했어요. 새 초안은 파일로 내보낼 수 있어요.');
  }
}
function saveDraft(replaceUnreadable = false): void {
  if (storedDraftUnreadable && !replaceUnreadable) return;
  try {
    const storage = window.localStorage;
    if (storage.getItem(draftStorageKey) !== storedDraftText) {
      setDraftSaveMessage('다른 탭에서 초안이 바뀌었어요. 이 초안은 파일로 내보내세요.');
      return;
    }
    const text = writeDraftFile(draft, draftMode);
    if (text !== storedDraftText) storage.setItem(draftStorageKey, text);
    storedDraftText = text;
    storedDraftUnreadable = false;
    setDraftSaveMessage('이 브라우저에 저장했어요.');
  } catch {
    setDraftSaveMessage('자동 저장을 완료하지 못했어요. 파일로 내보내세요.');
  }
}
function updateDraft(next: Draft): void {
  draft = next;
  saveDraft();
}
function exportDraft(): void {
  const text = writeDraftFile(draft, draftMode);
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `jpass-${draftMode}-${draft.startDate || 'draft'}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
function replaceDraft(next: Draft, places: DemoPlace[]): void {
  if (dialog.open) dialog.close();
  navigationRevision++;
  invalidateRecommendations();
  initialAddedIds.clear();
  placeCache.clear();
  searchPool = [];
  nearbyOpen = false;
  nearbyLegIndex = undefined;
  nearbyKind = 'nearby';
  nearbyQuery = '';
  selected = undefined;
  activeDay = 0;
  result = undefined;
  resultStartDate = '';
  operationError = '';
  view = 'input';
  registerPlaces(places);
  draft = next;
  saveDraft(true);
  render();
  app.querySelector('.panel-content')?.scrollTo(0, 0);
}
function startNewDraft(): void {
  if (!canResetDraft()) return;
  if (!window.confirm('새 초안을 만들면 이 브라우저의 현재 초안이 바뀌어요. 계속할까요?')) return;
  latestImport++;
  replaceDraft(freshDraft, []);
  notify('새 여행 초안을 열었어요.');
}
async function importDraft(file: File): Promise<void> {
  const importId = ++latestImport;
  const previous = draft;
  try {
    if (file.size > MAX_DRAFT_FILE_BYTES) throw new Error('여행 초안 파일은 1MB 이하여야 해요.');
    const saved = readDraftFile(await file.text(), draftMode);
    if (importId !== latestImport) return;
    if (draft !== previous) { notify('파일을 읽는 동안 초안이 바뀌었어요. 다시 가져와 주세요.'); return; }
    if (canResetDraft() && !window.confirm('현재 초안을 이 파일의 초안으로 바꿀까요?')) return;
    replaceDraft(saved.draft, saved.places);
    notify('파일에서 여행 초안을 불러왔어요.');
  } catch (error) {
    if (importId === latestImport) notify(error instanceof Error ? error.message : '여행 초안 파일을 읽지 못했어요.');
  }
}
function dayButtons(): string {
  const start = view === 'result' && result ? resultStartDate : draft.startDate;
  return Array.from({ length: draft.dayCount }, (_, day) => `<button type="button" data-action="day" data-day="${day}" aria-pressed="${day === activeDay}" class="day-tab ${day === activeDay ? 'active' : ''}"><strong>${day + 1}일차</strong><span>${dateAt(start, day)}</span></button>`).join('');
}
function inputPanel(): string {
  const day = draft.days[activeDay]!;
  const hiddenCount = draft.days.slice(draft.dayCount).reduce((sum, item) => sum + item.placeIds.length, 0);
  return `<div class="section-label"><span>1 · 여행 기본</span><span>일본 현지 시각 기준</span></div>
    <div class="trip-fields"><label>여행 시작일<input type="date" id="start-date" value="${html(draft.startDate)}" /></label><label>여행 일수<select id="day-count" aria-label="여행 일수">${Array.from({ length: 7 }, (_, i) => `<option value="${i + 1}" ${draft.dayCount === i + 1 ? 'selected' : ''}>${i + 1}일</option>`).join('')}</select></label></div>
    ${hiddenCount ? `<p class="preserved">기간 밖 장소 ${hiddenCount}곳을 보관했어요. 여행 일수를 늘리면 복구돼요.</p>` : ''}
    <div class="section-rule"></div><div class="day-tabs" aria-label="방문 일차">${dayButtons()}</div>
    <div class="places-heading"><h2>2 · 이날 들를 곳 <span>${day.placeIds.length}/8</span></h2><label class="start-time">출발 <input id="start-time" type="time" aria-label="${activeDay + 1}일차 출발 시각" value="${day.startTime}" /></label></div>
    <ol class="place-list">${day.placeIds.map((id, index) => {
      const place = placeById(id);
      return `<li class="place-row ${selected === id ? 'selected' : ''}"><button type="button" class="place-focus" data-action="focus" data-id="${id}"><span class="place-number">${index + 1}</span><span><strong>${html(place.name)}</strong><small>${html(place.area)} · ${html(place.station)}</small></span></button><button type="button" class="remove-button" data-action="remove" data-id="${id}" aria-label="${html(place.name)} 삭제">×</button></li>`;
    }).join('')}</ol>
    ${!day.placeIds.length ? '<p class="empty-state">아직 비어 있는 하루예요.<br />가고 싶은 장소부터 하나 골라보세요.</p>' : ''}
    <button type="button" class="add-place" data-action="search" ${day.placeIds.length >= 8 ? 'disabled' : ''}><span aria-hidden="true">＋</span> 장소 추가하기</button>
    <p class="helper">방문 순서는 결과에서 확인할 수 있어요.</p>
    ${apiMode ? `<label class="walking-toggle"><input id="include-walking" type="checkbox" ${draft.includeAccessWalking ? 'checked' : ''} /> 도보까지 포함해서 계산</label><p class="helper">관광지↔역 도보와 가까운 관광지 사이 직접 도보를 비교해요.</p>` : ''}
    ${apiMode ? `<label class="request-label" for="initial-query">이날 추천받을 지역·검색어 <span>선택</span></label><input id="initial-query" value="${html(initialQuery())}" placeholder="예: 교토의 조용한 정원과 카페" /><p class="helper">선택한 장소가 있으면 그 주변을 우선해 찾아요. 장소가 없는 날은 지역이나 검색어를 입력해 주세요.</p>` : ''}
    <div class="section-rule"></div><div class="taste-heading"><h2>3 · 여행 취향</h2><span>여러 개 선택</span></div>
    <div class="tag-list">${TAGS.map(([tag, label]) => `<button type="button" class="tag ${draft.tags.includes(tag) ? 'active' : ''}" data-action="tag" data-tag="${tag}" aria-pressed="${draft.tags.includes(tag)}">${draft.tags.includes(tag) ? '<span aria-hidden="true">✓</span> ' : ''}${label}</button>`).join('')}</div>
    <label class="request-label" for="extra-request">더 바라는 점 <span>선택</span></label><textarea id="extra-request" rows="2" placeholder="예: 사람이 적은 조용한 곳이 좋아요">${html(draft.request)}</textarea><p class="helper">${apiMode ? '추천 받기를 누르면 이 요청도 함께 전달해요.' : '추가 요청 문장은 실제 추천 연결 시 반영돼요.'}</p>`;
}
function ownedPicks(): Set<string> {
  return new Set(draft.days[activeDay]!.placeIds.filter((id) => initialAddedIds.get(activeDay)?.has(id)));
}
function choiceCandidates() {
  if (apiMode) {
    const owned = ownedPicks();
    const planned = new Set(draft.days.slice(0, draft.dayCount).flatMap((day) => day.placeIds));
    return [...new Set([...owned, ...initialPool])].filter((id) => owned.has(id) || !planned.has(id)).map((id) => {
      const place = placeById(id);
      return { demoPlaceId: id, googlePlaceId: place.googlePlaceId, name: place.name, location: { lat: place.lat, lng: place.lng }, searchTags: place.tags, address: place.address, primaryType: place.primaryType };
    });
  }
  return initialRecommendations(draft, activeDay, ownedPicks());
}
function choosePanel(): string {
  const ids = draft.days[activeDay]!.placeIds;
  const owned = ownedPicks();
  const base = ids.filter((id) => !owned.has(id));
  return `<div class="choice-intro"><span class="choice-example">${apiMode ? '관광지 추천' : '예시 추천'}</span><p>취향에 맞는 곳을 더 골라보세요.<br />선택한 장소를 포함해 다음 단계에서 동선을 만들어요.</p><button type="button" data-action="view" data-view="input">입력한 조건 수정</button></div>
    ${apiMode ? `<div class="api-query"><label for="choice-query">${activeDay + 1}일차 추천 지역·검색어</label><input id="choice-query" value="${html(initialQuery())}" placeholder="예: 교토에서 조용히 산책할 곳" /><button type="button" data-action="load-initial" ${loading.initial ? 'disabled' : ''}>${loading.initial ? '추천을 찾고 있어요…' : '주변 장소와 취향으로 추천 받기'}</button><p role="status">${html(messages.initial)}</p></div>` : ''}
    <div class="condition-chips choice-tastes">${draft.tags.length ? draft.tags.map((tag) => `<span>${tagLabel(tag)}</span>`).join('') : '<span>모든 취향 둘러보기</span>'}</div>
    <div class="day-tabs" aria-label="추천 장소를 넣을 일차">${dayButtons()}</div>
    <section class="choice-summary" aria-label="현재 일차의 선택 현황"><h2>${activeDay + 1}일차 <span>총 ${ids.length}/8곳</span></h2>
      <div class="choice-base"><span>앞에서 입력한 장소 · ${base.length}곳</span><p>${base.length ? base.map((id) => html(placeById(id).name)).join(' · ') : '입력한 장소가 없어요. 아래에서 골라주세요.'}</p></div>
      <div class="choice-picked"><span role="status">추천에서 고른 장소 · ${owned.size}곳</span><div class="selected-search-chips">${[...owned].map((id) => `<button type="button" data-action="choose-toggle" data-id="${id}" ${running ? 'disabled' : ''} aria-label="${html(placeById(id).name)} 추천 선택 취소">${html(placeById(id).name)} ×</button>`).join('') || '<p>아래에서 여러 곳을 선택할 수 있어요.</p>'}</div></div>
    </section>
    <div class="choice-heading"><h2>추가로 가보고 싶은 곳</h2><span>여러 곳 선택</span></div>
    <div class="choice-candidates">${choiceCards()}</div>
    <p class="helper">${apiMode ? '선택한 장소의 기준역 주변 추천과 검색어·취향 검색 결과를 함께 보여줘요. 장소 검색은 주변을 우선하며 지역 밖 결과도 포함될 수 있어요.' : '교토 예시 후보입니다. 추가 요청 문장은 실제 추천 연결 시 반영돼요.'}<br />기존 장소만으로도 동선을 만들 수 있어요. 일차마다 최소 1곳이 필요해요.</p>`;
}
function choiceCards(): string {
  const ids = draft.days[activeDay]!.placeIds, owned = ownedPicks();
  return choiceCandidates().map((candidate) => {
      const id = candidate.demoPlaceId;
      const checked = owned.has(id);
      const place = placeById(id);
      return `<article class="recommend-card choice-card ${checked ? 'picked' : ''}"><div class="recommend-body"><button type="button" class="recommend-name" data-action="focus" data-id="${id}">${html(candidate.name)} <span aria-hidden="true">↗</span></button><p>${html(place.area)}</p><div class="small-tags">${candidate.searchTags.map((tag) => `<span>${tagLabel(tag)}</span>`).join('')}${(place.sources ?? []).map((source) => `<span>${source === 'nearby' ? '기준역 주변' : '검색어·취향 검색'}</span>`).join('')}</div><button type="button" class="choice-toggle" data-action="choose-toggle" data-id="${id}" aria-pressed="${checked}" ${running || (!checked && ids.length >= 8) ? 'disabled' : ''}><span aria-hidden="true">${checked ? '✓' : '＋'}</span> ${checked ? `${activeDay + 1}일차에 선택됨 · 취소` : `${activeDay + 1}일차에 선택`}</button></div></article>`;
    }).join('') || `<p class="empty-state">${apiMode ? '추천 받기를 눌러 관광지를 찾아보세요.' : '추가할 예시 후보가 없어요.<br />입력한 조건에서 다른 취향을 골라보세요.'}</p>`;
}
function refreshQueryCandidates(): void {
  // Keep the composing input connected; only dependent candidates/status change.
  const cards = app.querySelector<HTMLElement>('.choice-candidates');
  if (cards) cards.innerHTML = choiceCards();
  const status = app.querySelector<HTMLElement>('.api-query [role="status"]');
  if (status) status.textContent = messages.initial;
  const button = app.querySelector<HTMLButtonElement>('[data-action="load-initial"]');
  if (button) { button.disabled = false; button.textContent = '주변 장소와 취향으로 추천 받기'; }
  app.querySelectorAll('.map-marker.nearby').forEach((node) => node.remove());
  if (selected && !draft.days[activeDay]!.placeIds.includes(selected)) { selected = undefined; app.querySelector('.map-place-card')?.remove(); }
  mapController?.update(mapView());
}
function enterChoose(): boolean {
  const errors = validateDraft(draft, true);
  if (errors.length) { notify(errors[0]!); return false; }
  view = 'choose'; selected = undefined;
  return true;
}
function resultPanel(): string {
  if (!result) return '<div class="empty-state">먼저 일차별 장소를 선택하고<br />일정 미리보기를 눌러주세요.</div>';
  const day = result.days[activeDay];
  const dirty = isDirty();
  const dayNearbyOpen = nearbyOpen && nearbyLegIndex === undefined;
  return `<div class="condition-bar ${dirty ? 'dirty' : ''}"><div class="condition-chips"><span>${dateAt(resultStartDate, 0)}부터 · ${result.days.length}일</span><span>출발 ${day?.startTime ?? '미계산'}</span><span>장소 ${day?.order.length ?? 0}곳</span>${draft.tags.map((tag) => `<span>${tagLabel(tag)}</span>`).join('')}</div><button type="button" data-action="view" data-view="input">조건 수정</button></div>
    ${dirty ? '<div class="dirty-note" role="status">일정이 변경됐어요. 아래는 이전 결과이며, 다시 계산하면 새 장소가 반영돼요.</div>' : ''}
    <div class="day-tabs" aria-label="결과 일차">${dayButtons()}</div>
    ${pendingPlacesPanel()}
    ${!day ? '<p class="empty-state">새로 추가한 일차예요.<br />장소를 넣고 다시 계산해 주세요.</p>' : routePanel(day)}
    <section class="nearby-section" aria-label="취향 관광지 추천"><button type="button" class="nearby-toggle" data-action="toggle-nearby" aria-expanded="${dayNearbyOpen}" aria-controls="nearby-results"><span><strong>취향에 맞는 관광지</strong><small>일정 주변에서 더 들를 곳을 찾아보세요</small></span><span aria-hidden="true">${dayNearbyOpen ? '−' : '＋'}</span></button><div id="nearby-results" ${dayNearbyOpen ? '' : 'hidden'}>${dayNearbyOpen ? nearbyPanel() : ''}</div></section>
    <div class="trip-total"><span>여행 전체 ${result.days.some((day) => day.accessWalking) ? '' : '역 간 '}교통비 <small>${liveResult() ? '조회 결과' : '예시'}</small></span><strong>${yen(result.days.reduce((sum, item) => sum + item.fare, 0))}</strong></div>`;
}
function pendingPlaceIds(): string[] {
  if (!result) return [];
  const calculated = new Set(result.days[activeDay]?.order ?? []);
  return draft.days[activeDay]!.placeIds.filter((id) => !calculated.has(id));
}
function pendingPlacesPanel(): string {
  const ids = pendingPlaceIds();
  if (!ids.length) return '';
  return `<section class="pending-additions" aria-label="재계산 전 추가한 장소"><h2>추가한 장소 <span>${ids.length}곳 · 재계산 전</span></h2><p>지도에서 위치를 확인하세요. 방문 순서는 다시 계산하면 정해져요.</p>${ids.map((id) => {
    const place = placeById(id);
    return `<div class="pending-place"><button type="button" class="pending-place-focus" data-action="focus" data-id="${id}"><strong>${html(place.name)}</strong><small>${html(place.area)} · ${html(place.station)}</small></button><button type="button" class="pending-remove" data-action="pending-remove" data-id="${id}" aria-label="${html(place.name)} 추가 취소">추가 취소</button></div>`;
  }).join('')}</section>`;
}
function routePanel(day: DemoResult['days'][number]): string {
  return renderRouteDay(day, { live: liveResult(), dayIndex: activeDay, date: resultDate(), selected, place: placeById, legActions: legNearbyPanel });
}
function legNearbyPanel(leg: DemoLeg, index: number): string {
  const open = nearbyOpen && nearbyLegIndex === index;
  const name = `${placeById(leg.from).name} → ${placeById(leg.to).name}`;
  return `<div class="leg-recommendation"><button type="button" class="leg-recommendation-toggle" data-action="toggle-leg-nearby" data-leg="${index}" aria-label="이 사이에 들를 곳 찾기 · ${html(name)}" aria-expanded="${open}" aria-controls="nearby-leg-${index}" ${isDirty() && !open ? 'disabled' : ''}>이 사이에 들를 곳 찾기</button>
    <div id="nearby-leg-${index}" class="leg-recommendation-panel" ${open ? '' : 'hidden'}>${open ? `<p class="helper">${html(name)} 주변 후보예요. 들르는 데 필요한 추가 이동·체류시간은 미확인이에요. 추가 후 재계산하면 방문 순서가 정해져요.</p>${nearbyPanel()}` : ''}</div></div>`;
}
function nearbyPanel(): string {
  if (apiMode) return apiNearbyPanel();
  if (!draft.tags.length) return '<div class="empty-state">좋아하는 여행 취향을 골라주세요.<br />조건 수정에서 선택할 수 있어요.</div>';
  const places = nearbyCandidates();
  return `${nearbyLegIndex === undefined ? `<div class="recommend-heading"><p>선택한 취향과 장소 주변의 예시 후보예요.<br />마음에 드는 곳을 ${activeDay + 1}일차에 추가한 뒤 다시 계산하세요.</p></div>` : ''}${places.length ? places.map((place) => `<article class="recommend-card"><div class="recommend-body"><button type="button" class="recommend-name" data-action="focus" data-id="${place.id}">${html(place.name)} <span aria-hidden="true">↗</span></button><p>${html(place.station)} · ${html(place.area)}</p><div class="small-tags">${place.tags.map((tag) => `<span>${tagLabel(tag)}</span>`).join('')}</div><button type="button" class="recommend-add" data-action="recommend-add" data-id="${place.id}" ${draft.days[activeDay]!.placeIds.length >= 8 ? 'disabled' : ''}>＋ ${activeDay + 1}일차에 추가</button></div></article>`).join('') : '<p class="empty-state">이 취향의 추가 예시 후보가 없어요.<br />다른 취향도 골라보세요.</p>'}`;
}
function resultDate(): string {
  const date = new Date(resultStartDate + 'T00:00:00Z'); date.setUTCDate(date.getUTCDate() + activeDay);
  return date.toISOString().slice(0, 10);
}
function cancelInitialRequest(): void {
  initialAbort?.abort(); initialAbort = undefined;
  tokens.initial++; loading.initial = false;
}
function invalidateRecommendations(currentDayOnly = false): void {
  cancelInitialRequest();
  if (currentDayOnly) initialDays.delete(activeDay); else initialDays.clear();
  initialPool = [];
  messages.initial = '조건이 바뀌었어요. 추천 받기를 눌러 다시 조회하세요.';
  resetNearby('조건이 바뀌었어요. 추천 받기를 눌러 다시 조회하세요.');
}
function resetNearby(message = '추천 받기를 눌러 조회하세요.'): void {
  nearbyPool = [];
  tokens.nearby++;
  loading.nearby = false;
  messages.nearby = message;
}
function legNearbyUnavailable(day: DemoResult['days'][number], index: number): string | undefined {
  if (!nearbyRouteStations(day, index).length) return '이 구간의 이용역 정보가 없어 장소 검색으로 주변 후보를 찾아요.';
  if (!draft.tags.length) return '취향을 선택하면 이 구간의 역 주변 추천도 사용할 수 있어요.';
}
function setNearbyScope(index?: number): void {
  const day = result?.days[activeDay];
  nearbyLegIndex = index;
  nearbyKind = apiMode && day && index !== undefined && legNearbyUnavailable(day, index) ? 'text' : 'nearby';
  nearbyQuery = '';
  resetNearby();
}
function closeLegNearby(): void {
  if (nearbyLegIndex === undefined) return;
  setNearbyScope();
  nearbyOpen = false;
}
function toggleNearby(index?: number): void {
  if (view !== 'result') return;
  const currentlyOpen = nearbyOpen && nearbyLegIndex === index;
  if (index !== undefined && (!result?.days[activeDay]?.legs[index] || (isDirty() && !currentlyOpen))) return;
  if (nearbyLegIndex !== index) setNearbyScope(index);
  else { tokens.nearby++; loading.nearby = false; }
  nearbyOpen = !currentlyOpen;
  selected = undefined;
}
function availableNearby(): DemoPlace[] {
  const planned = new Set(draft.days.slice(0, draft.dayCount).flatMap((day) => day.placeIds));
  return nearbyPool.filter((id) => !planned.has(id)).map(placeById);
}
function nearbyCandidates(): DemoPlace[] {
  const leg = nearbyLegIndex === undefined ? undefined : result?.days[activeDay]?.legs[nearbyLegIndex];
  return apiMode ? availableNearby() : recommendations(draft, activeDay, leg);
}
function nearbyNeedsRecalculation(): boolean {
  return isDirty() && (nearbyKind === 'nearby' || nearbyLegIndex !== undefined);
}
function apiNearbyPanel(): string {
  const day = result?.days[activeDay];
  const scoped = nearbyLegIndex !== undefined;
  const unavailable = day && nearbyLegIndex !== undefined ? legNearbyUnavailable(day, nearbyLegIndex) : undefined;
  return `<div class="recommend-kinds"><button type="button" data-action="nearby-kind" data-kind="nearby" aria-pressed="${nearbyKind === 'nearby'}" ${unavailable ? 'disabled' : ''}>${scoped ? '구간의 역 주변' : '동선 주변'}</button><button type="button" data-action="nearby-kind" data-kind="text" aria-pressed="${nearbyKind === 'text'}">${scoped ? '장소 검색' : '요청으로 새 장소'}</button></div>
    ${unavailable ? `<p class="helper">${html(unavailable)}</p>` : ''}
    ${nearbyKind === 'text' ? `<label class="request-label" for="nearby-query">찾고 싶은 장소${scoped ? ' (선택)' : ''}</label><input id="nearby-query" value="${html(nearbyQuery)}" placeholder="예: 조용한 정원이 있는 카페" />${scoped ? '<p class="helper">이 구간 주변을 우선으로 검색해요.</p>' : ''}` : `<p class="helper">${scoped ? '이 구간' : '이번 일차'}의 역과 환승역을 기준으로 반경 1km에서 추천해요.</p>`}
    <button type="button" class="add-place" data-action="load-nearby" ${loading.nearby || nearbyNeedsRecalculation() ? 'disabled' : ''}>${loading.nearby ? '추천을 찾고 있어요…' : '추천 받기'}</button>
    ${scoped && nearbyNeedsRecalculation() ? '<p class="helper">구간 주변을 다시 찾으려면 변경한 일정으로 재계산해 주세요.</p>' : ''}
    <p class="helper" role="status">${html(messages.nearby)}</p>
    ${availableNearby().map((place) => `<article class="recommend-card"><div class="recommend-body"><button type="button" class="recommend-name" data-action="focus" data-id="${place.id}">${html(place.name)} <span aria-hidden="true">↗</span></button><p>${html(place.area)}</p>${place.distanceMeters !== undefined ? `<p>추천 기준역에서 직선 ${Math.round(place.distanceMeters)}m · 도보 거리 아님</p>` : ''}<button type="button" class="recommend-add" data-action="recommend-add" data-id="${place.id}" ${draft.days[activeDay]!.placeIds.length >= 8 ? 'disabled' : ''}>＋ ${activeDay + 1}일차에 추가</button></div></article>`).join('')}`;
}
function requestContext(kind: 'initial' | 'nearby' | 'search'): string {
  return JSON.stringify([draft.revision, draft.tags, draft.request, activeDay, view, result?.revision, kind === 'search' ? [dialog.open, search.value] : kind === 'initial' ? initialQuery() : [nearbyKind, nearbyQuery, nearbyOpen, nearbyLegIndex]]);
}
async function loadPlaces(kind: 'initial' | 'nearby' | 'search'): Promise<void> {
  if (!apiMode || loading[kind]) return;
  if (kind === 'nearby' && (view !== 'result' || !nearbyOpen || nearbyNeedsRecalculation())) return;
  const context = requestContext(kind), token = ++tokens[kind];
  const snapshot = structuredClone(draft), computed = result;
  const mode = nearbyKind, day = activeDay;
  const legIndex = kind === 'nearby' ? nearbyLegIndex : undefined;
  const routeDay = computed?.days[day];
  const leg = legIndex === undefined ? undefined : routeDay?.legs[legIndex];
  const bias = routeDay && legIndex !== undefined && mode === 'text' ? searchBiasForRouteLeg(routeDay, legIndex) : undefined;
  const tastes = snapshot.tags.map(tagLabel).join(', ');
  const nearbyText = leg ? [
    `${placeById(leg.from).name}에서 ${placeById(leg.to).name}로 가는 길 주변`,
    nearbyQuery.trim() || '들를 만한 장소',
    tastes ? `여행 취향: ${tastes}` : '',
    snapshot.request.trim(),
  ].filter(Boolean).join('. ') : `${nearbyQuery}. 여행 취향: ${tastes}. ${snapshot.request}`;
  const query = kind === 'search' ? search.value : kind === 'initial' ? initialQuery() : nearbyText;
  const legScope = leg && routeDay ? [day, legIndex, leg.from, leg.to, bias ?? nearbyRouteStations(routeDay, legIndex), snapshot.tags, snapshot.request] : null;
  const routeScope = kind === 'nearby' && mode === 'nearby' ? [computed, day, snapshot.tags, snapshot.request] : null;
  const initialScope = kind === 'initial' ? [day, snapshot.days[day]!.placeIds.map((id) => {
    const place = placeById(id);
    return [place.googlePlaceId, place.name, place.lat, place.lng];
  }), snapshot.tags, snapshot.request] : null;
  const key = JSON.stringify([kind, mode, query, initialScope ?? legScope ?? routeScope]);
  if (kind === 'initial') { initialAbort = new AbortController(); }
  else if (kind === 'nearby') nearbyPool = [];
  else searchPool = [];
  loading[kind] = true;
  messages[kind] = '조회 중입니다. 다른 조건으로 이동하면 응답을 반영하지 않아요.';
  if (kind === 'search') renderSearch(); else render();
  try {
    const cached = placeCache.get(key);
    const data = cached ?? (kind === 'initial' ? await api.initial(snapshot, day, query, initialAbort?.signal) : kind === 'nearby' && mode === 'nearby' ? { places: await api.nearby(snapshot, computed!, day, legIndex), clarification: null } : await api.search(query, bias));
    if (tokens[kind] !== token || requestContext(kind) !== context) return;
    if (!cached && !data.warnings?.length) {
      placeCache.set(key, data);
      if (placeCache.size > 20) placeCache.delete(placeCache.keys().next().value!);
    }
    registerPlaces(data.places);
    const ids = data.places.map((place) => place.id);
    if (kind === 'initial') initialPool = ids;
    else if (kind === 'nearby') nearbyPool = ids;
    else searchPool = ids;
    const found = kind === 'nearby' ? availableNearby().length : ids.length;
    messages[kind] = [found ? `${found}곳을 찾았어요.${kind === 'search' ? ' 여러 곳을 골라 한 번에 추가하세요.' : ' 이미 일정에 있는 장소는 제외해요.'}` : data.clarification ? '' : '조건에 맞는 장소가 없어요. 지역이나 요청사항을 바꿔보세요.', data.clarification, data.notice, ...(data.warnings ?? [])].filter(Boolean).join(' ');
    if (kind === 'initial') initialDays.set(day, { ids, message: messages.initial });
  } catch (error) {
    if (tokens[kind] === token && requestContext(kind) === context) messages[kind] = (error as Error).message;
  } finally {
    if (tokens[kind] === token) {
      loading[kind] = false;
      if (requestContext(kind) !== context) messages[kind] = '조건이 바뀌었어요. 검색 버튼을 눌러 다시 조회해 주세요.';
      if (kind === 'initial') initialAbort = undefined;
      if (kind === 'search') renderSearch(); else render();
    }
  }
}
function apiPoint(place: { lat: number; lng: number }): [number, number] {
  const current = mapView();
  const points = [...current.places, ...mapRoutePaths(current).flatMap((entry) => entry.path)];
  const lats = points.map((point) => point.lat), lngs = points.map((point) => point.lng);
  const south = Math.min(...lats, place.lat) - .005, north = Math.max(...lats, place.lat) + .005;
  const west = Math.min(...lngs, place.lng) - .005, east = Math.max(...lngs, place.lng) + .005;
  return [90 + (place.lng - west) / (east - west) * 820, 670 - (place.lat - south) / (north - south) * 560];
}
function apiMapMarkup(): string {
  const current = mapView();
  const paths = mapRoutePaths(current);
  const focus = selected ? current.places.find((place) => place.id === selected) : undefined;
  return `<div class="map-topline"><span>${activeDay + 1}일차 · ${dateAt(view === 'result' ? resultStartDate : draft.startDate, activeDay)}</span><small>${current.places.filter((place) => place.state !== 'recommended').length}곳 선택${view === 'result' && isDirty() ? ' · 이전 계산 결과' : ''}</small></div>
    <svg class="kyoto-map" ${mapPhase === 'ready' ? 'hidden' : ''} viewBox="0 0 1000 760" aria-label="장소와 역 좌표를 표시한 개략도"><rect width="1000" height="760" fill="#f1f3f4" />
    ${paths.map(({ mode, path }) => `<polyline points="${path.map((point) => apiPoint(point).join(',')).join(' ')}" fill="none" stroke="${mode === 'walking' ? '#258258' : '#3267d6'}" stroke-width="4" ${mode === 'walking' ? 'stroke-dasharray="4 10"' : ''} stroke-linecap="round"/>`).join('')}
    ${current.places.map((mark) => marker(placeById(mark.id), mark.order === null ? '+' : String(mark.order), mark.state === 'recommended', mark.state === 'pending')).join('')}</svg>
    <div id="demo-map-slot"></div>${mapPhase !== 'ready' ? `<div class="live-map-status" role="status">${html(mapStatusMessage)}</div>` : ''}
    <div class="map-legend"><span><i class="legend-visit"></i>방문 장소</span><span><i class="legend-nearby"></i>추천 후보</span>${paths.some((entry) => entry.mode === 'transit') ? '<span><i class="legend-route"></i>노선 이동</span>' : ''}${paths.some((entry) => entry.mode === 'walking') ? '<span><i class="legend-walking"></i>도보</span>' : ''}</div>
    ${focus ? `<div class="map-place-card"><div><span class="eyebrow">${focus.state === 'pending' ? '추가됨 · 재계산 전' : focus.state === 'recommended' ? '추천 후보' : '방문 장소'}</span><strong>${html(focus.name)}</strong><p>${html(placeById(focus.id).area)}</p></div><button type="button" data-action="clear-focus" class="icon-button" aria-label="장소 상세 닫기">×</button></div>` : ''}
    <div class="map-disclaimer">${mapPhase === 'ready' ? 'Google 지도' : '위치 개략도'} · ${result?.days[activeDay]?.accessWalking ? '접근·직접 도보는 조회 경로, 노선·환승 구간은 역 좌표 간 참고선입니다' : '선은 역 좌표 간 참고선이며 실제 도로·선로 모양이 아닙니다'}</div>`;
}
function point(place: DemoPlace): [number, number] {
  if (apiMode) return apiPoint(place);
  return [110 + (place.lng - 135.66) / 0.15 * 780, 650 - (place.lat - 34.96) / 0.09 * 540];
}
function stationMarkers(ids: string[]): string {
  const groups = new Map<string, DemoPlace[]>();
  ids.forEach((id) => {
    const place = placeById(id);
    groups.set(place.station, [...(groups.get(place.station) ?? []), place]);
  });
  // Schematic anchors only. The fixtures do not include actual station coordinates.
  return [...groups].map(([name, places]) => {
    const points = places.map(point);
    const x = points.reduce((sum, item) => sum + item[0], 0) / points.length;
    const y = points.reduce((sum, item) => sum + item[1], 0) / points.length;
    return `<g class="station-marker" transform="translate(${x},${y + 44})"><title>${html(name)} · 역 배치 예시</title><path d="M0 -34V-8"/><rect x="-5" y="-5" width="10" height="10" rx="1"/><text x="12" y="4">${html(name)}</text></g>`;
  }).join('');
}
function mapMarkup(): string {
  if (apiMode) return apiMapMarkup();
  const validResult = view === 'result' && result ? result.days[activeDay] : undefined;
  const ids = validResult?.order ?? (view === 'result' && result ? [] : draft.days[activeDay]!.placeIds);
  const pending = view === 'result' ? pendingPlaceIds() : [];
  const nearby = view === 'choose' ? choiceCandidates().filter((place) => !ids.includes(place.demoPlaceId)).map((place) => placeById(place.demoPlaceId)) : view === 'result' && validResult && nearbyOpen ? nearbyCandidates() : [];
  const points = ids.map((id) => point(placeById(id)).join(',')).join(' ');
  const selectedPlace = selected ? placeById(selected) : undefined;
  return `<div class="map-topline"><span>${activeDay + 1}일차 · ${dateAt(validResult ? resultStartDate : draft.startDate, activeDay)}</span><small>교토 · ${ids.length}곳 ${view !== 'result' ? '선택' : isDirty() ? '· 이전 계산 결과' : '· 동선 예시'}</small></div>
    <svg class="kyoto-map" ${mapPhase === 'ready' ? 'hidden' : ''} viewBox="0 0 1000 760" role="group" aria-label="교토 예시 도식 지도. 실제 선로가 아닌 장소의 개략 위치와 방문 순서입니다.">
      <defs><filter id="pin-shadow" x="-100%" y="-100%" width="300%" height="300%"><feDropShadow dx="0" dy="3" stdDeviation="3" flood-opacity=".1"/></filter></defs>
      <rect width="1000" height="760" fill="#f1f3f4"/>
      <path d="M0 0H385L325 77 346 114 298 153 281 216 210 252 170 340 102 358 0 418Z" fill="#e8f0e3"/>
      <path d="M900 0H1000V760H915L943 667 887 590 929 512 877 443 910 362 867 290 923 190 880 110Z" fill="#e8f0e3"/>
      <path d="M0 486Q151 408 250 487T435 620L405 675Q252 550 0 582Z" fill="#e8f0e3"/>
      <path d="M655 -20C616 102 735 167 699 274S681 395 720 476 685 672 730 780" fill="none" stroke="#cfe3f2" stroke-width="24"/>
      <path d="M8 449Q120 388 213 448T397 510" fill="none" stroke="#cfe3f2" stroke-width="18"/>
      <g fill="none" stroke="#ffffff" stroke-width="10"><path d="M365 0L465 760M510 0L570 760M810 0L760 760M0 310L1000 250M0 422L1000 360M0 541L1000 482M0 652L1000 597"/></g>
      <rect x="575" y="181" width="86" height="103" rx="28" fill="#e8f0e3" transform="rotate(-6 575 181)"/><rect x="500" y="337" width="54" height="43" rx="9" fill="#e8f0e3"/>
      <g class="map-area-label"><text x="117" y="190">嵐山</text><text x="358" y="192">北山</text><text x="565" y="237">京都御苑</text><text x="756" y="278">東山</text><text x="563" y="498">下京区</text><text x="782" y="621">伏見</text></g>
      <g class="map-small-label"><text x="630" y="362" transform="rotate(80 630 362)">KAMO RIVER</text><text x="533" y="571">京都駅 · 교토역</text></g>
      ${validResult && ids.length > 1 ? `<polyline points="${points}" fill="none" stroke="#3267d6" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" opacity=".8"/>` : ''}
      ${stationMarkers(ids)}
      ${ids.map((id, index) => marker(placeById(id), String(index + 1), false)).join('')}
      ${pending.map((id) => marker(placeById(id), '+', false, true)).join('')}
      ${nearby.map((place) => marker(place, '+', true)).join('')}
    </svg>
    <div id="demo-map-slot"></div>
    ${mapPhase !== 'ready' ? `<div class="live-map-status" role="status">${html(mapStatusMessage)}</div>` : ''}
    <div class="map-legend"><span><i class="legend-visit"></i>관광지</span>${mapPhase !== 'ready' ? '<span><i class="legend-station"></i>역 배치 예시</span>' : ''}${pending.length ? '<span><i class="legend-pending"></i>추가 장소 · 재계산 전</span>' : ''}${nearby.length ? '<span><i class="legend-nearby"></i>추천 후보</span>' : ''}${validResult ? '<span><i class="legend-route"></i>방문 순서</span>' : ''}</div>
    ${selectedPlace ? `<div class="map-place-card"><div><span class="eyebrow">${pending.includes(selectedPlace.id) ? `${activeDay + 1}일차 · 추가됨 · 재계산 전` : ids.includes(selectedPlace.id) ? `${activeDay + 1}일차 · 방문 장소` : view === 'choose' ? '취향 추천 · 예시' : '주변 추천 · 예시'}</span><strong>${html(selectedPlace.name)}</strong><p>${html(selectedPlace.area)} · ${html(selectedPlace.station)}</p></div><button type="button" data-action="clear-focus" class="icon-button" aria-label="장소 상세 닫기">×</button></div>` : ''}
    <div class="map-disclaimer">${mapPhase === 'ready' ? 'Google 지도 · 선은 방문 순서 표시이며 실제 교통 경로가 아닙니다' : '기획용 개략도 · 역 배치는 예시 · 선은 실제 선로가 아닙니다'}</div>`;
}
function mapView(): DemoMapView {
  const calculated = view === 'result' ? result?.days[activeDay] : undefined;
  const ids = calculated?.order ?? (view === 'result' && result ? [] : draft.days[activeDay]!.placeIds);
  const pending = view === 'result' ? pendingPlaceIds() : [];
  const candidates = view === 'choose' ? choiceCandidates().filter((place) => !ids.includes(place.demoPlaceId)).map((place) => placeById(place.demoPlaceId)) : view === 'result' && calculated && nearbyOpen ? nearbyCandidates() : [];
  const marks: DemoMapView['places'] = ids.map((id, index) => ({ ...placeById(id), order: index + 1, state: 'routed' }));
  pending.forEach((id) => marks.push({ ...placeById(id), order: null, state: 'pending' }));
  candidates.forEach((place) => marks.push({ ...place, order: null, state: 'recommended' }));
  const routeSegments: DemoMapView['routeSegments'] = apiMode && calculated ? calculated.legs.flatMap((leg) => {
    const transit = (leg.route ?? []).map((step) => ({ mode: step.line === '도보' ? 'walking' as const : 'transit' as const, from: step.fromStation.location, to: step.toStation.location }));
    return leg.segments ? leg.segments.flatMap((step) => step.mode === 'walking' ? [{ mode: 'walking' as const, from: step.fromPoint.location, to: step.toPoint.location, path: step.path! }] : transit) : transit;
  }) : undefined;
  return { activeDayId: String(activeDay), places: marks, selectedId: selected, showRoute: !!calculated, routeSegments };
}
function updateLiveMap(): void {
  const slot = app.querySelector<HTMLElement>('#demo-map-slot');
  if (!slot) return;
  if (!mapHost) {
    mapHost = document.createElement('div');
    mapHost.id = 'demo-live-map'; mapHost.className = 'live-map';
    mapHost.setAttribute('aria-label', '여행 장소 Google 지도');
    mapController = createGoogleDemoMap(mapHost, (id) => { selected = id; render(); }, (phase, message) => {
      if (mapPhase === phase && mapStatusMessage === message) return;
      mapPhase = phase; mapStatusMessage = message; render();
    });
  }
  mapHost.hidden = mapPhase === 'fallback';
  mapController!.attach(slot);
  mapController!.update(mapView());
}
function marker(place: DemoPlace, label: string, nearby: boolean, pending = false): string {
  const [x, y] = point(place);
  const chosen = selected === place.id;
  return `<g class="map-marker ${nearby ? 'nearby' : ''} ${pending ? 'pending' : ''} ${chosen ? 'chosen' : ''}" transform="translate(${x},${y})" role="button" tabindex="0" aria-label="${html(place.name)}${pending ? ', 추가됨, 재계산 전' : nearby ? ', 추천 후보' : `, ${label}번째 장소`}" aria-pressed="${chosen}" data-action="focus" data-id="${place.id}"><circle class="pin-halo" r="29"/><path class="pin-body" d="M0 27C-6 17-19 9-19-4a19 19 0 1 1 38 0C19 9 6 17 0 27Z" filter="url(#pin-shadow)"/><text class="pin-number" text-anchor="middle" y="2">${label}</text><rect class="pin-label-bg" x="25" y="-18" width="${Math.min(196, place.name.length * 13 + 20)}" height="29" rx="7"/><text class="pin-label" x="35" y="1">${html(place.name)}</text></g>`;
}
function render(): void {
  const focus = document.activeElement as HTMLElement | null;
  const focusData = focus?.dataset.action ? { ...focus.dataset } : undefined;
  const focusFieldId = focus?.id;
  const scroll = app.querySelector('.panel-content')?.scrollTop ?? 0;
  const steps = [['input', '정보 입력'], ['choose', '관광지 선택'], ['result', '동선 결과']] as const;
  const title = view === 'input' ? '여행 정보 입력' : view === 'choose' ? '가보고 싶은 곳 더 고르기' : '나의 여행 동선';
  const description = view === 'input' ? '날짜별로 가고 싶은 곳을 골라보세요.' : view === 'choose' ? '추천 장소를 일차별로 선택해 보세요.' : '방문 순서와 이동 정보를 확인하세요.';
  const buttonLabel = view === 'input' ? '추천 관광지 고르기' : view === 'choose' ? '선택한 장소로 동선 만들기' : '변경한 조건으로 다시 계산';
  const walkingBasis = view === 'result' ? result?.days[activeDay]?.accessWalking : draft.includeAccessWalking;
  const timingNote = walkingBasis ? '관광지↔역 도보 포함 · 체류시간 미포함' : '역 간 이동 기준 · 관광지↔역 이동·체류시간 미포함';
  const draftTools = `<div class="draft-tools" role="group" aria-label="여행 초안">
    <span id="draft-save-status" role="status">${html(draftSaveMessage)}</span>
    <button type="button" data-action="new-draft" ${canResetDraft() ? '' : 'disabled'}>새 초안</button>
    <button type="button" data-action="import-draft" aria-label="여행 초안 가져오기">가져오기</button>
    <button type="button" data-action="export-draft" aria-label="여행 초안 내보내기">내보내기</button>
  </div>`;
  renderDemoMarkup(app, `<header class="site-header"><a href="${apiMode ? './demo.html' : './demo.html?mode=example'}" class="brand" aria-label="JR Pass Opt 데모 처음으로">JR Pass Opt</a><nav class="stepper" aria-label="여행 계획 단계">${steps.map(([step, label], index) => `${index ? '<span class="step-line" aria-hidden="true"></span>' : ''}<button type="button" data-action="view" data-view="${step}" ${view === step ? 'aria-current="step"' : ''} ${step === 'result' && !result ? 'disabled' : ''}><span class="step-no">${index + 1}</span>${label}${step === 'result' && isDirty() ? '<span class="change-dot" aria-label="변경됨"></span>' : ''}</button>`).join('')}</nav><span class="demo-badge">${apiMode ? '실제 API 연결 · PC 데모' : '예시 데이터 · PC 데모'}<a href="${apiMode ? './demo.html?mode=example' : './demo.html'}">${apiMode ? '예시 체험' : 'API 모드'}</a></span></header>
    <main class="workspace"><section class="map-area" aria-label="일정 지도">${mapMarkup()}</section><aside class="planner" aria-label="여행 계획"><div class="panel-head"><div><h1>${title}</h1><p>${description}</p></div><span class="trip-length">${draft.dayCount}일 여행</span></div><div class="panel-content">${view === 'input' ? inputPanel() : view === 'choose' ? choosePanel() : resultPanel()}</div><footer class="panel-footer">${draftTools}${operationError ? `<p class="api-error" role="alert">${html(operationError)}</p>` : ''}${view === 'result' && isDirty() ? '<p class="footer-dirty">변경한 일정을 반영해 주세요.</p>' : ''}<button type="button" class="primary-button" data-action="${view === 'input' ? 'continue' : 'calculate'}" ${running ? 'disabled' : ''}>${running ? '<span class="spinner" aria-hidden="true"></span> 일정을 준비하고 있어요' : `${buttonLabel}<span aria-hidden="true">→</span>`}</button><p>${apiMode ? timingNote : '순서·시간·요금은 예시 데이터입니다'}</p></footer></aside></main>`);
  const panel = app.querySelector('.panel-content');
  if (panel) panel.scrollTop = scroll;
  updateLiveMap();
  if (!dialog.open) {
    const target = focusData ? Array.from(app.querySelectorAll<HTMLElement>('[data-action]')).find((element) => Object.entries(focusData).every(([key, value]) => element.dataset[key] === value)) : focusFieldId ? document.getElementById(focusFieldId) : undefined;
    target?.focus({ preventScroll: true });
  }
}
function renderSearch(): void {
  const places = apiMode ? searchPool.map(placeById) : searchPlaces(search.value);
  const atLimit = draft.days[activeDay]!.placeIds.length + stagedPlaces.size >= 8;
  searchResults.innerHTML = places.length ? places.map((place) => {
    const included = draft.days[activeDay]!.placeIds.includes(place.id);
    const checked = stagedPlaces.has(place.id);
    return `<button type="button" class="search-result ${checked ? 'selected' : ''}" data-add-id="${place.id}" aria-pressed="${checked}" ${included || (atLimit && !checked) ? 'disabled' : ''}><span class="search-result-mark" aria-hidden="true">${place.tags.includes('nature') ? '山' : '京'}</span><span><strong>${html(place.name)}</strong><small>${html(place.area)} · ${html(place.station)}</small></span><span class="search-add">${included ? '추가됨' : checked ? '선택됨' : '선택'}</span></button>`;
  }).join('') : `<p class="empty-state">${apiMode ? html(messages.search) : '일치하는 예시 장소가 없어요.<br />기요미즈데라 또는 기온을 검색해 보세요.'}</p>`;
  searchSelection.innerHTML = `<p role="status">${stagedPlaces.size}곳 선택 · 이날 총 ${draft.days[activeDay]!.placeIds.length + stagedPlaces.size}/8곳</p><div class="selected-search-chips">${[...stagedPlaces].map((id) => `<button type="button" data-unselect-id="${id}" aria-label="${html(placeById(id).name)} 선택 취소">${html(placeById(id).name)} ×</button>`).join('')}</div>`;
  if (apiMode && places.length) searchResults.innerHTML = `<p class="helper" role="status">${html(messages.search)}</p>` + searchResults.innerHTML;
  const submit = document.querySelector<HTMLButtonElement>('#search-submit');
  if (submit) { submit.disabled = loading.search; submit.textContent = loading.search ? '검색 중…' : '검색'; }
  searchConfirm.textContent = `${stagedPlaces.size}곳 한 번에 추가`;
  searchConfirm.disabled = stagedPlaces.size === 0;
}
async function calculate(): Promise<void> {
  if (running || view === 'input') return;
  const errors = validateDraft(draft);
  if (errors.length) { notify(errors[0]!); return; }
  const snapshot = structuredClone(draft);
  const request = ++latestRequest;
  const navigation = navigationRevision;
  running = true;
  operationError = '';
  render();
  try {
    let computed: DemoResult;
    if (apiMode) computed = await api.optimize(snapshot);
    else { await new Promise((resolve) => setTimeout(resolve, 650)); computed = computeExample(snapshot); }
    if (navigation !== navigationRevision) return;
    if (!canApplyResult(draft, computed, request, latestRequest)) {
      notify('입력이 바뀌었어요. 변경한 내용으로 다시 계산해 주세요.'); return;
    }
    result = computed;
    resultStartDate = snapshot.startDate;
    closeLegNearby();
    resetNearby('추천 받기를 누르면 계산된 역 주변에서 조회해요.');
    view = 'result'; nearbyOpen = false; selected = undefined;
  } catch (error) {
    if (navigation !== navigationRevision) return;
    operationError = (error as Error).message; notify(operationError);
  }
  finally { running = false; render(); }
  app.querySelector<HTMLElement>('.day-tabs .active')?.focus({ preventScroll: true });
  if (!operationError && view === 'result') notify(apiMode ? '동선 계산 결과가 준비됐어요.' : '일정 예시가 준비됐어요. 주변 추천도 살펴보세요.');
}
app.addEventListener('click', (event) => {
  const button = (event.target as Element).closest<HTMLElement>('[data-action]');
  if (!button || button.hasAttribute('disabled')) return;
  const action = button.dataset.action;
  try {
    if (action === 'day') {
      const day = Number(button.dataset.day);
      if (day === activeDay) return;
      closeLegNearby();
      if (apiMode) { cancelInitialRequest(); resetNearby('추천 받기를 눌러 이날의 역 주변을 조회하세요.'); }
      activeDay = day; selected = undefined;
      if (apiMode) { const saved = initialDays.get(day); initialPool = saved?.ids ?? []; messages.initial = saved?.message ?? '추천 받기를 눌러 이날의 관광지를 찾아보세요.'; }
    }
    else if (action === 'load-initial') { void loadPlaces('initial'); return; }
    else if (action === 'load-nearby') { void loadPlaces('nearby'); return; }
    else if (action === 'nearby-kind') { nearbyKind = button.dataset.kind === 'text' ? 'text' : 'nearby'; resetNearby(); selected = undefined; }
    else if (action === 'continue') { if (!enterChoose()) return; }
    else if (action === 'view') {
      const next = button.dataset.view;
      if (next === view) return;
      if (apiMode) { cancelInitialRequest(); tokens.nearby++; loading.nearby = false; }
      if (next === 'choose') { if (!enterChoose()) return; }
      else if (next === 'input' || (next === 'result' && result)) { view = next; selected = undefined; }
      else return;
      closeLegNearby();
      navigationRevision++;
      operationError = '';
    }
    else if (action === 'choose-toggle') {
      if (view !== 'choose' || running) return;
      const id = button.dataset.id!;
      const owned = ownedPicks();
      if (owned.has(id)) {
        updateDraft(removePlace(draft, activeDay, id));
        initialAddedIds.get(activeDay)?.delete(id);
        if (selected === id) selected = undefined;
      } else {
        if (!choiceCandidates().some((candidate) => candidate.demoPlaceId === id)) return;
        updateDraft(addPlace(draft, activeDay, id));
        initialAddedIds.set(activeDay, new Set([...owned, id]));
        selected = id;
      }
    }
    else if (action === 'toggle-nearby') toggleNearby();
    else if (action === 'toggle-leg-nearby') toggleNearby(Number(button.dataset.leg));
    else if (action === 'focus') selected = button.dataset.id;
    else if (action === 'clear-focus') selected = undefined;
    else if (action === 'pending-remove' || action === 'remove') { updateDraft(removePlace(draft, activeDay, button.dataset.id!)); initialAddedIds.get(activeDay)?.delete(button.dataset.id!); if (apiMode) invalidateRecommendations(true); if (selected === button.dataset.id) selected = undefined; }
    else if (action === 'tag') {
      if (apiMode) invalidateRecommendations();
      const tag = button.dataset.tag as Tag;
      updateDraft({ ...draft, tags: draft.tags.includes(tag) ? draft.tags.filter((item) => item !== tag) : [...draft.tags, tag] });
    }
    else if (action === 'search') { if (apiMode) { searchPool = []; messages.search = '검색어를 입력하고 검색 버튼을 눌러주세요.'; } stagedPlaces.clear(); search.value = ''; renderSearch(); dialog.showModal(); search.focus(); return; }
    else if (action === 'recommend-add') { updateDraft(addPlace(draft, activeDay, button.dataset.id!)); selected = button.dataset.id; notify(`${placeById(button.dataset.id!).name}을 ${activeDay + 1}일차에 추가했어요.`); }
    else if (action === 'export-draft') { exportDraft(); return; }
    else if (action === 'import-draft') { draftImport.click(); return; }
    else if (action === 'new-draft') { startNewDraft(); return; }
    else if (action === 'calculate') { void calculate(); return; }
    render();
    if (action === 'view' || action === 'continue' || action === 'day') app.querySelector('.panel-content')?.scrollTo(0, 0);
    if (action === 'recommend-add') {
      const focusTarget = nearbyLegIndex === undefined ? `.pending-place-focus[data-id="${button.dataset.id}"]` : `[data-action="toggle-leg-nearby"][data-leg="${nearbyLegIndex}"]`;
      app.querySelector<HTMLElement>(focusTarget)?.focus({ preventScroll: true });
    }
    if (action === 'pending-remove') (app.querySelector<HTMLElement>('.pending-place-focus') ?? app.querySelector<HTMLElement>('.day-tabs .active'))?.focus({ preventScroll: true });
    if (action === 'remove') app.querySelector<HTMLElement>('[data-action="search"]')?.focus({ preventScroll: true });
  } catch (error) { notify((error as Error).message); }
});
app.addEventListener('keydown', (event) => {
  if ((event.target as HTMLElement).id === 'nearby-query' && event.key === 'Enter' && !event.isComposing) {
    event.preventDefault();
    void loadPlaces('nearby');
    return;
  }
  const element = (event.target as Element).closest<SVGElement>('.map-marker');
  if (element && (event.key === 'Enter' || event.key === ' ')) {
    event.preventDefault(); selected = element.dataset.id; render();
  }
});
app.addEventListener('change', (event) => {
  const field = event.target as HTMLInputElement;
  if (apiMode && ['start-date', 'day-count', 'start-time'].includes(field.id)) invalidateRecommendations();
  if (field.id === 'start-date') updateDraft({ ...draft, startDate: field.value, revision: draft.revision + 1 });
  else if (field.id === 'day-count') { updateDraft(setDayCount(draft, Number(field.value))); closeLegNearby(); activeDay = Math.min(activeDay, draft.dayCount - 1); selected = undefined; }
  else if (field.id === 'start-time') updateDraft({ ...draft, revision: draft.revision + 1, days: draft.days.map((day, index) => index === activeDay ? { ...day, startTime: field.value } : day) });
  else if (field.id === 'include-walking') updateDraft({ ...draft, includeAccessWalking: field.checked, revision: draft.revision + 1 });
  else return;
  render();
});
app.addEventListener('input', (event) => {
  const field = event.target as HTMLTextAreaElement;
  if (field.id === 'extra-request') { if (apiMode) invalidateRecommendations(); updateDraft({ ...draft, request: field.value }); }
  else if (field.id === 'choice-query' || field.id === 'initial-query') {
    updateDraft({ ...draft, days: draft.days.map((day, index) => index === activeDay ? { ...day, searchQuery: field.value } : day) });
    cancelInitialRequest(); initialDays.delete(activeDay); initialPool = [];
    messages.initial = '검색 조건이 바뀌었어요. 추천 받기를 눌러 조회하세요.';
    refreshQueryCandidates();
  }
  else if (field.id === 'nearby-query') nearbyQuery = field.value;
});
search.addEventListener('input', () => {
  if (apiMode) {
    tokens.search++; loading.search = false; searchPool = [];
    messages.search = '검색어를 입력하고 검색 버튼을 눌러주세요.';
  }
  renderSearch();
});
search.addEventListener('keydown', (event) => { if (apiMode && event.key === 'Enter') { event.preventDefault(); void loadPlaces('search'); } });
dialog.addEventListener('click', (event) => {
  const button = (event.target as Element).closest<HTMLButtonElement>('button');
  if (!button || button.disabled) return;
  if (button.dataset.action === 'submit-search') { void loadPlaces('search'); return; }
  if (button.dataset.action === 'close-search') { dialog.close(); return; }
  if (button.dataset.action === 'confirm-search') {
    if (!stagedPlaces.size) return;
    try {
      const ids = [...stagedPlaces];
      updateDraft(addPlaces(draft, activeDay, ids));
      if (apiMode) invalidateRecommendations(true);
      selected = ids[ids.length - 1];
      stagedPlaces.clear(); dialog.close(); render();
      notify(`${ids.length}곳을 ${activeDay + 1}일차에 추가했어요.`);
    } catch (error) { notify((error as Error).message); }
    return;
  }
  if (button.dataset.unselectId) { stagedPlaces.delete(button.dataset.unselectId); renderSearch(); return; }
  if (button?.dataset.addId) {
    const id = button.dataset.addId;
    if (stagedPlaces.has(id)) stagedPlaces.delete(id);
    else if (!draft.days[activeDay]!.placeIds.includes(id) && draft.days[activeDay]!.placeIds.length + stagedPlaces.size < 8) stagedPlaces.add(id);
    renderSearch();
    dialog.querySelector<HTMLButtonElement>(`[data-add-id="${id}"]`)?.focus({ preventScroll: true });
  }
});
dialog.addEventListener('close', () => { tokens.search++; loading.search = false; stagedPlaces.clear(); app.querySelector<HTMLElement>('[data-action="search"]')?.focus({ preventScroll: true }); });
draftImport.addEventListener('change', () => {
  const file = draftImport.files?.[0];
  draftImport.value = '';
  if (file) void importDraft(file);
});
restoreDraft();
render();

const searchSubmit = document.querySelector<HTMLButtonElement>('#search-submit');
if (searchSubmit) searchSubmit.hidden = !apiMode;
const searchCaption = document.querySelector<HTMLElement>('.search-caption');
if (searchCaption && apiMode) searchCaption.textContent = '검색 버튼을 눌러 장소를 찾고, 여러 곳을 선택해 한 번에 추가하세요.';
