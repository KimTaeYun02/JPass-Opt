import { clock, fitMoveMinute, minutes, movePlace, placementErrors, resizePlace } from './trip-model.ts';
import type { Place, ScheduleTarget } from './trip-model.ts';

type ScheduleState = { places: Place[]; start: string; end: string };
type Gesture = {
  pointer: number; source: HTMLElement; place: Place; edge?: 'start' | 'end';
  x: number; y: number; lastX: number; lastY: number; offset: number; active: boolean;
  candidate?: Place; target?: HTMLElement; errors: string[]; ghost?: HTMLElement;
};

export function initTimetableGestures(
  root: HTMLElement, status: HTMLElement, getState: () => ScheduleState, commit: (place: Place) => void,
): { cancel: () => void } {
  let gesture: Gesture | undefined;
  let suppressClick = false;
  let frame = 0;
  const announce = (message: string, invalid = false): void => {
    if (status.textContent !== message) status.textContent = message;
    status.classList.toggle('gesture-invalid', invalid);
  };
  const describe = (place: Place): string => `${place.name} · ${place.fixed ? `${place.fixed.date} ${place.fixed.time ? `${place.fixed.time}–${clock(minutes(place.fixed.time) + place.duration)}` : '날짜만 고정'}` : '날짜 자유'}`;

  function cleanup(): Gesture | undefined {
    const previous = gesture;
    gesture = undefined;
    cancelAnimationFrame(frame);
    previous?.ghost?.remove();
    previous?.target?.classList.remove('drop-highlight');
    previous?.source.classList.remove('gesture-source');
    root.classList.remove('is-gesturing');
    if (previous?.source.hasPointerCapture(previous.pointer)) previous.source.releasePointerCapture(previous.pointer);
    if (previous?.active) suppressClick = true;
    return previous;
  }
  function cancel(): void {
    if (cleanup()?.active) announce('변경을 취소했어요. 원래 일정은 그대로 유지됩니다.');
  }

  function update(): void {
    const current = gesture;
    if (!current?.active) return;
    const hit = document.elementFromPoint(current.lastX, current.lastY);
    const drop = current.edge ? current.source.closest<HTMLElement>('.day-track') :
      hit?.closest<HTMLElement>('[data-drop-free], [data-drop-date]');
    current.target?.classList.remove('drop-highlight');
    current.target = drop && root.contains(drop) ? drop : undefined;
    current.target?.classList.add('drop-highlight');
    current.candidate = undefined;
    if (current.target) {
      const target = current.target;
      if (current.edge) {
        const boundary = Number(target.dataset.begin) + (current.lastY - target.getBoundingClientRect().top - current.offset) / 22 * 30;
        current.candidate = resizePlace(current.place, current.edge, boundary, Number(target.dataset.begin), Number(target.dataset.finish));
      } else {
        const minute = target.classList.contains('day-track') ? fitMoveMinute(
          Number(target.dataset.begin) + (current.lastY - target.getBoundingClientRect().top - current.offset) / 22 * 30,
          current.place.duration, Number(target.dataset.begin), Number(target.dataset.finish),
        ) : undefined;
        const destination: ScheduleTarget | undefined = target.hasAttribute('data-drop-free') ? { kind: 'free' } :
          target.classList.contains('day-track') ? (minute === undefined ? undefined : { kind: 'time', date: target.dataset.dropDate!, minute }) :
          { kind: 'date', date: target.dataset.dropDate! };
        current.candidate = destination ? movePlace(current.place, destination) : undefined;
      }
    }
    const state = getState();
    current.errors = current.candidate ? placementErrors(current.candidate, state.places, state.start, state.end) : ['시간표의 날짜·시간 칸 또는 날짜 자유 영역에 놓아 주세요.'];
    if (!current.candidate && current.target?.classList.contains('day-track')) current.errors = ['이 방문 시간은 표시 범위보다 길어요. 24시간 보기로 변경해 주세요.'];
    if (current.candidate?.fixed && (!state.start || !state.end || state.end < state.start)) current.errors.unshift('먼저 여행 기간을 확인해 주세요.');
    const ghost = current.ghost!;
    ghost.classList.toggle('invalid-preview', !!current.errors.length);
    ghost.textContent = current.candidate ? describe(current.candidate) : current.place.name;
    if (current.candidate?.fixed?.time && current.target?.classList.contains('day-track')) {
      const box = current.target.getBoundingClientRect();
      ghost.style.left = `${box.left + 3}px`;
      ghost.style.top = `${box.top + (minutes(current.candidate.fixed.time) - Number(current.target.dataset.begin)) / 30 * 22}px`;
      ghost.style.width = `${box.width - 6}px`;
      ghost.style.height = `${current.candidate.duration / 30 * 22}px`;
    } else {
      ghost.style.left = `${current.lastX + 12}px`;
      ghost.style.top = `${current.lastY + 12}px`;
      ghost.style.width = '170px';
      ghost.style.height = 'auto';
    }
    announce(current.errors.length ? current.errors[0]! : `${describe(current.candidate!)} · 놓으면 저장 / Esc로 취소`, !!current.errors.length);
  }

  function scrollEdges(): void {
    if (!gesture?.active) return;
    const { lastX: x, lastY: y } = gesture;
    const scroll = root.querySelector<HTMLElement>('.timetable-scroll');
    const box = scroll?.getBoundingClientRect();
    if (scroll && box && y >= box.top && y <= box.bottom && x >= box.left && x <= box.right) {
      if (x < box.left + 24) scroll.scrollLeft -= 7;
      else if (x > box.right - 24) scroll.scrollLeft += 7;
    }
    const panel = root.closest<HTMLElement>('.panel') || root;
    const panelBox = panel.getBoundingClientRect();
    const scroller = getComputedStyle(panel).overflowY === 'auto' ? panel : document.scrollingElement;
    if (scroller && x >= panelBox.left && x <= panelBox.right) {
      if (y < Math.max(0, panelBox.top) + 28) scroller.scrollTop -= 7;
      else if (y > Math.min(window.innerHeight, panelBox.bottom) - 28) scroller.scrollTop += 7;
    }
    update();
    frame = requestAnimationFrame(scrollEdges);
  }

  root.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || !event.isPrimary || gesture) return;
    suppressClick = false;
    const hit = event.target as HTMLElement;
    const source = hit.closest<HTMLElement>('[data-edit]');
    const place = getState().places.find((item) => item.id === source?.dataset.edit);
    if (!source || !place) return;
    const edge = hit.closest<HTMLElement>('[data-resize]')?.dataset.resize as 'start' | 'end' | undefined;
    gesture = {
      pointer: event.pointerId, source, place: { ...place, fixed: place.fixed ? { ...place.fixed } : undefined }, edge,
      x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY,
      offset: edge ? event.clientY - (edge === 'start' ? source.getBoundingClientRect().top : source.getBoundingClientRect().bottom) :
        source.classList.contains('timed-event') ? event.clientY - source.getBoundingClientRect().top : 0,
      active: false, errors: [],
    };
    source.setPointerCapture(event.pointerId);
  });
  window.addEventListener('pointermove', (event) => {
    const current = gesture;
    if (!current || event.pointerId !== current.pointer) return;
    current.lastX = event.clientX;
    current.lastY = event.clientY;
    if (!current.active && Math.hypot(event.clientX - current.x, event.clientY - current.y) < 6) return;
    if (!current.active) {
      current.active = true;
      current.source.classList.add('gesture-source');
      root.classList.add('is-gesturing');
      const ghost = document.createElement('div');
      ghost.className = 'gesture-preview';
      ghost.setAttribute('aria-hidden', 'true');
      document.body.append(ghost);
      current.ghost = ghost;
      frame = requestAnimationFrame(scrollEdges);
    }
    event.preventDefault();
    update();
  }, { passive: false });
  window.addEventListener('pointerup', (event) => {
    if (!gesture || event.pointerId !== gesture.pointer) return;
    gesture.lastX = event.clientX;
    gesture.lastY = event.clientY;
    update();
    const finished = cleanup();
    if (!finished?.active) return;
    if (!finished.candidate || finished.errors.length) { announce(`${finished.errors[0] || '이곳에는 배치할 수 없어요.'} 원래 일정은 유지됩니다.`, true); return; }
    commit(finished.candidate);
    announce(`${describe(finished.candidate)} · 저장했어요.`);
    root.querySelector<HTMLElement>(`[data-edit="${CSS.escape(finished.candidate.id)}"]`)?.focus({ preventScroll: true });
  });
  window.addEventListener('pointercancel', (event) => { if (event.pointerId === gesture?.pointer) cancel(); });
  root.addEventListener('lostpointercapture', (event) => { if (event.pointerId === gesture?.pointer) cancel(); });
  window.addEventListener('blur', cancel);
  window.addEventListener('resize', cancel);
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && gesture) { event.preventDefault(); cancel(); }
  });
  root.addEventListener('click', (event) => {
    if (suppressClick && event.detail > 0) { event.preventDefault(); event.stopImmediatePropagation(); suppressClick = false; }
  }, true);
  return { cancel };
}
