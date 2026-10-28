import { initPlaceSearch } from './place-search';
import { renderTimetable } from './timetable';
import { initTimetableGestures } from './timetable-gestures';
import { DEFAULT_VISIT_MINUTES, dayCount, dateAt, escapeHtml, minutes, placementErrors, validate } from './trip-model';
import type { Endpoint, Place, TripInput } from './trip-model';

export { validate } from './trip-model';

export function initTripInput(root: HTMLElement): void {
  const get = <T extends HTMLElement>(selector: string): T => root.querySelector<T>(selector)!;
  const form = get<HTMLFormElement>('#trip-form');
  const start = get<HTMLInputElement>('#trip-start');
  const end = get<HTMLInputElement>('#trip-end');
  const board = get<HTMLElement>('#timetable');
  const tray = get<HTMLElement>('#place-tray');
  const error = get<HTMLElement>('#form-error');
  const summary = get<HTMLElement>('#input-summary');
  const dialog = get<HTMLDialogElement>('#place-editor');
  const editorForm = get<HTMLFormElement>('#place-editor-form');
  const choice = get<HTMLSelectElement>('#place-choice');
  const mode = get<HTMLSelectElement>('#placement-mode');
  const editorDate = get<HTMLInputElement>('#editor-date');
  const editorTime = get<HTMLInputElement>('#editor-time');
  const duration = get<HTMLSelectElement>('#editor-duration');
  const priority = get<HTMLSelectElement>('#editor-priority');
  const editorError = get<HTMLElement>('#editor-error');
  const chosen = get<HTMLElement>('#chosen-place');
  const view = get<HTMLSelectElement>('#view-hours');
  const gestureStatus = get<HTMLElement>('#gesture-status');
  duration.innerHTML = Array.from({ length: 48 }, (_, index) => {
    const value = (index + 1) * 30;
    return `<option value="${value}">${Math.floor(value / 60) ? `${Math.floor(value / 60)}시간` : ''}${value % 60 ? `${value >= 60 ? ' ' : ''}30분` : ''}</option>`;
  }).join('');
  let page = 0;
  let selected: Place | undefined;
  let opener: HTMLElement | undefined;
  let places: Place[] = [
    { id: 'sample-osaka', name: '오사카성', address: '오사카시 주오구 · 화면 구성용 예시', source: 'sample', required: true, duration: 120, fixed: { date: '2026-11-04', time: '10:00' } },
    { id: 'sample-kyoto', name: '교토', address: '교토시 · 화면 구성용 예시', source: 'sample', required: true, duration: DEFAULT_VISIT_MINUTES, fixed: { date: '2026-11-05' } },
    { id: 'sample-nara', name: '나라 공원', address: '나라시 · 화면 구성용 예시', source: 'sample', required: false, duration: DEFAULT_VISIT_MINUTES },
    { id: 'sample-dotonbori', name: '도톤보리', address: '오사카시 주오구 · 화면 구성용 예시', source: 'sample', required: true, duration: 120, fixed: { date: '2026-11-03', time: '17:00' } },
  ];
  const clone = (place: Place): Place => ({ ...place, fixed: place.fixed ? { ...place.fixed } : undefined });
  const gestures = initTimetableGestures(root, gestureStatus, () => ({ places, start: start.value, end: end.value }), (candidate) => {
    places = places.map((place) => place.id === candidate.id ? candidate : place);
    if (candidate.fixed?.time && (minutes(candidate.fixed.time) < 480 || minutes(candidate.fixed.time) + candidate.duration > 1320)) view.value = 'full';
    render();
  });

  function invalidate(): void {
    summary.hidden = true;
    error.hidden = true;
    error.textContent = '';
  }
  function render(): void {
    gestures.cancel();
    gestureStatus.textContent = '';
    gestureStatus.classList.remove('gesture-invalid');
    invalidate();
    end.min = start.value;
    const days = dayCount(start.value, end.value);
    get('#duration').textContent = days ? `${days - 1}박 ${days}일` : '여행 기간을 확인해 주세요.';
    get('#place-count').textContent = `${places.length}곳`;
    page = renderTimetable(board, tray, places, start.value, end.value, page, view.value === 'full');
    get<HTMLButtonElement>('#prev-days').disabled = !page || !days;
    get<HTMLButtonElement>('#next-days').disabled = (page + 1) * 5 >= days;
    get('#visible-range').textContent = days ? `${dateAt(start.value, page * 5).slice(5).replace('-', '/')} — ${dateAt(start.value, Math.min(days - 1, page * 5 + 4)).slice(5).replace('-', '/')}` : '기간 미정';
    form.querySelectorAll<HTMLInputElement>('input[type="date"]:not(#trip-start):not(#trip-end)').forEach((field) => {
      field.min = start.value; field.max = end.value;
    });
  }
  function renderChosen(): void {
    chosen.hidden = !selected;
    get<HTMLButtonElement>('#delete-place').hidden = !selected || !places.some((place) => place.id === selected!.id);
    chosen.innerHTML = selected ? `<strong>${escapeHtml(selected.name)}</strong><p>${escapeHtml(selected.address)}</p><span>${selected.source === 'google' ? 'Google Maps 제공 · 검색 결과에서 선택한 장소' : '예시 데이터 · 실제 검색 결과가 아닙니다'}</span>` : '';
  }
  function updateMode(): void {
    get('#editor-date-field').hidden = mode.value === 'free';
    get('#editor-time-fields').hidden = mode.value !== 'time';
    editorError.hidden = true;
  }
  const search = initPlaceSearch(get('#place-query'), get('#place-results'), get('#search-status'), (place) => {
    const existing = places.find((item) => item.placeId === place.placeId);
    selected = existing ? clone(existing) : place;
    choice.value = existing?.id || '';
    if (existing) {
      duration.value = String(existing.duration);
      priority.value = existing.required ? 'required' : 'optional';
      if (mode.value === 'free' && existing.fixed) {
        mode.value = existing.fixed.time ? 'time' : 'date';
        editorDate.value = existing.fixed.date;
        editorTime.value = existing.fixed.time || '10:00';
        updateMode();
      }
    }
    renderChosen();
    if (place.location) window.dispatchEvent(new CustomEvent('trip-place-selected', { detail: place }));
  }, () => { selected = undefined; choice.value = ''; renderChosen(); });

  function openEditor(place?: Place, date = '', time = '', trigger?: HTMLElement): void {
    opener = trigger;
    search.reset();
    choice.innerHTML = '<option value="">새 장소를 검색하거나 기존 장소를 선택하세요</option>' + places.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}${item.source === 'sample' ? ' (예시)' : ''}</option>`).join('');
    selected = place ? clone(place) : undefined;
    choice.value = place?.id || '';
    mode.value = place?.fixed ? (place.fixed.time ? 'time' : 'date') : date ? (time ? 'time' : 'date') : 'free';
    editorDate.value = place?.fixed?.date || date || start.value;
    editorDate.min = start.value;
    editorDate.max = end.value;
    editorTime.value = place?.fixed?.time || time || '10:00';
    duration.value = String(place?.duration || DEFAULT_VISIT_MINUTES);
    priority.value = place?.required === false ? 'optional' : 'required';
    renderChosen();
    updateMode();
    dialog.showModal();
    (place ? mode : date ? choice : get<HTMLInputElement>('#place-query')).focus();
  }
  choice.addEventListener('change', () => {
    search.reset();
    const place = places.find((item) => item.id === choice.value);
    selected = place ? clone(place) : undefined;
    if (place) { duration.value = String(place.duration); priority.value = place.required ? 'required' : 'optional'; }
    if (place?.fixed && mode.value === 'free') {
      mode.value = place.fixed.time ? 'time' : 'date';
      editorDate.value = place.fixed.date;
      editorTime.value = place.fixed.time || '10:00';
      updateMode();
    }
    renderChosen();
  });
  mode.addEventListener('change', updateMode);
  get('#add-place').addEventListener('click', (event) => openEditor(undefined, '', '', event.currentTarget as HTMLElement));
  for (const container of [board, tray]) container.addEventListener('click', (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('button');
    if (!button) return;
    if (button.dataset.edit) openEditor(places.find((place) => place.id === button.dataset.edit), '', '', button);
    else if (button.dataset.date) openEditor(undefined, button.dataset.date, button.dataset.time || '', button);
  });
  for (const selector of ['#close-editor', '#cancel-editor']) get(selector).addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {
    search.reset();
    const replacement = opener?.dataset.edit ? root.querySelector<HTMLElement>(`[data-edit="${CSS.escape(opener.dataset.edit)}"]`) : null;
    if (opener?.isConnected) opener.focus(); else if (replacement) replacement.focus(); else get('#add-place').focus();
  });
  get('#delete-place').addEventListener('click', () => {
    if (!selected) return;
    places = places.filter((place) => place.id !== selected!.id);
    dialog.close(); render();
  });
  editorForm.addEventListener('submit', (event) => {
    event.preventDefault();
    const fail = (message: string): void => { editorError.textContent = message; editorError.hidden = false; };
    if (!selected) { fail('검색 결과에서 장소를 선택하거나 기존 장소를 골라 주세요.'); return; }
    const candidate: Place = { ...selected, required: priority.value === 'required', duration: Number(duration.value),
      fixed: mode.value === 'free' ? undefined : { date: editorDate.value, ...(mode.value === 'time' ? { time: editorTime.value } : {}) },
    };
    const errors = placementErrors(candidate, places, start.value, end.value);
    if (mode.value !== 'free' && !dayCount(start.value, end.value)) errors.unshift('먼저 올바른 여행 기간을 입력해 주세요.');
    if (errors.length) { fail(errors.join(' ')); return; }
    const index = places.findIndex((place) => place.id === candidate.id);
    if (index >= 0) places[index] = candidate; else places.push(candidate);
    if (candidate.fixed) {
      page = Math.floor((Date.parse(candidate.fixed.date) - Date.parse(start.value)) / 86_400_000 / 5);
      if (candidate.fixed.time && (minutes(candidate.fixed.time) < 480 || minutes(candidate.fixed.time) + candidate.duration > 1320)) view.value = 'full';
    }
    dialog.close(); render();
  });
  get('#prev-days').addEventListener('click', () => { page -= 1; render(); });
  get('#next-days').addEventListener('click', () => { page += 1; render(); });
  view.addEventListener('change', render);
  form.addEventListener('input', (event) => {
    if (event.target === start || event.target === end) render(); else invalidate();
  });
  form.addEventListener('change', invalidate);
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const value = (key: string): string => String(data.get(key) || '').trim();
    const endpoint = (prefix: string): Endpoint => ({ place: value(`${prefix}Place`), date: value(`${prefix}Date`), time: value(`${prefix}Time`) });
    const input: TripInput = {
      range: { start: start.value, end: end.value }, places,
      arrival: endpoint('arrival'), departure: endpoint('departure'), stay: value('stay'),
      preference: value('preference') === 'cost' ? 'cost' : value('preference') === 'time' ? 'time' : 'balance',
    };
    const errors = validate(input);
    error.textContent = errors.join(' '); error.hidden = !errors.length;
    if (errors.length) { error.scrollIntoView({ block: 'nearest' }); return; }
    const endpointSummary = (label: string, entry: Endpoint): string => {
      const details = [entry.place, entry.date, entry.time].filter(Boolean).join(' · ');
      return details ? `<p>${label}: ${escapeHtml(details)}</p>` : '';
    };
    summary.innerHTML = `<h2>프로토타입 — 입력 요약</h2><p>${escapeHtml(start.value)} ~ ${escapeHtml(end.value)} · ${{ cost: '비용 절약', balance: '균형 있게', time: '이동 시간 단축' }[input.preference]}</p>
      <ul>${places.map((place) => `<li>${escapeHtml(place.name)} · ${place.required ? '꼭 방문' : '선택 방문'} · ${place.fixed ? `${escapeHtml(place.fixed.date)} ${place.fixed.time ? `${escapeHtml(place.fixed.time)} · 방문 ${place.duration}분` : '날짜만 고정'}` : '날짜 자유'}${place.source === 'sample' ? ' · 예시' : ''}</li>`).join('')}</ul>
      ${endpointSummary('도착', input.arrival)}${endpointSummary('출발', input.departure)}${input.stay ? `<p>숙소: ${escapeHtml(input.stay)}</p>` : ''}
      <p class="field-help">고정 일정만 입력한 상태예요. 경로·이동 시간·요금·패스 추천은 아직 계산하지 않았습니다.</p>`;
    summary.hidden = false; summary.focus(); summary.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });
  render();
}
