> 당시 AI 설계 검토 기록입니다. 인터페이스·범위는 후속 구현에서 바뀌었을 수 있습니다. 현재 계약과 실행 방법은 [프론트 README](../../../web/README.md)를 우선합니다. 특히 백엔드 수정 제안은 이번 프론트 PR에 포함되지 않습니다.

**Targets**
- `renderDemoMarkup(app: HTMLElement, html: string): void` — replaces every `app.innerHTML = html` site.
- Search dialog: staged-selection state.
- Model: `addPlaces(state, ids: string[]): State`.

**Interfaces**
`renderDemoMarkup`: first call sets innerHTML; later calls parse `html` into a detached `<template>`, swap header/planner subtrees by id, append map overlays excluding the existing SDK slot, leave map-area and slot connected.
Dialog: `staged: Set<string>`, `aria-pressed` per row, selected chips visible across filter changes, confirm button shows count, cancel discards.
`addPlaces`: one atomic immutable transition; rejects if existing + staged > 8.

**Invariants**
1. The map host is never detached or recreated after first render.
2. Markers and route survive any re-render.
3. Cancel commits nothing; confirm commits all staged exactly once.
4. Cap of 8 enforced on the batch, not per item.
5. Recommendations stay listed; pending places are excluded from the route and mark it dirty until calculate — sequential adds require no recalc.

**Acceptance**
Repeated marker selection keeps bubbles, markers, route intact. One confirm adds N places.

**Test plan**
Simulated-DOM helper asserting no ancestor of the map host is removed across successive renders; dialog tests for batch/filter/cancel/cap; model test for atomicity and cap boundary; build plus existing suite.

**Regression risks**
Id-based swap missing dynamically inserted nodes; duplicate ids; stale overlays leaking on append; `staged` not cleared on reopen; dirty-route flag not reset after calculate.

No browser QA — the earlier policy rejection stands, so acceptance rests on the DOM-simulation tests.
