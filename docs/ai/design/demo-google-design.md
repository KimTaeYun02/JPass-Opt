> 당시 AI 설계 검토 기록입니다. 인터페이스·범위는 후속 구현에서 바뀌었을 수 있습니다. 현재 계약과 실행 방법은 [프론트 README](../../../web/README.md)를 우선합니다. 특히 백엔드 수정 제안은 이번 프론트 PR에 포함되지 않습니다.

## Design Contract — Real Google Map in PC Demo

### Interfaces

```ts
// demo-map.ts (new, isolated; index.ts / main.ts untouched)
type PlaceMark = {
  id: string; name: string; lat: number; lng: number;
  order: number | null;              // null = not routed
  state: 'routed' | 'pending' | 'recommended';
};
type DemoMapView = {
  activeDayId: string | null;
  places: PlaceMark[];               // visible set only
  selectedId: string | null;
};
createDemoMapController(host: HTMLElement): {
  attach(): void;                    // idempotent: re-parents the one persistent node
  update(v: DemoMapView): void;      // safe before SDK resolves
  status(): 'loading' | 'ready' | 'fallback';
};
```

Derived state, owned by the model: `pendingIds = draftPlaceIds \ calculatedDayOrderIds`.

### Invariants

1. Exactly one map DOM node per session. `render()` wiping `app.innerHTML` is followed by `attach()`, which re-parents that node — never recreates the map.
2. `update()` before SDK resolution stores a single latest snapshot; the resolver renders that snapshot, never an earlier one.
3. Markers are `AdvancedMarkerElement` with `mapId: DEMO_MAP_ID` and styled DOM content (name + order badge). Pending/recommended = amber/blue; routed = default.
4. `fitBounds` fires only when `activeDayId` or the visible place-ID set changes. Selection never refits; an explicit `selectedId` change pans only.
5. `gmp-click` on a marker selects the corresponding panel item through the existing selection callback — the map writes no other state.
6. No `stationCoords` or synthetic anchors on the real map. No Directions/Routes/Distance Matrix calls. The polyline connects routed places in visit order and is labelled illustrative.
7. Pending places are visible whenever a draft exists, including when the recommendation section is closed, are cancellable, and are never routed or time/fare-costed until explicit recalculation.
8. Missing `VITE_GOOGLE_MAPS_API_KEY` or loader rejection ⇒ `status() === 'fallback'`, existing SVG renders, and a visible status line states the reason. No silent blank panel.

### Acceptance

Add a recommendation → amber marker and pending list entry appear immediately, no route/time/fare change; cancel removes both; recalculate turns it routed with an order badge and extends the polyline. Switching day refits; clicking a marker selects without zoom change. No key ⇒ SVG plus status message.

### Risks

`DEMO_MAP_ID` is unverified — advanced markers silently degrade on a bad map ID; surface the loader error rather than swallowing it. Re-parenting is the single point of failure for panel rerenders. The polyline invites misreading as a real route; the label is load-bearing.

### Tests

Mocked-SDK controller tests: attach idempotence across simulated rerenders, stale-snapshot discard, fitBounds call count under selection vs. day change, pending-set derivation, fallback path. Plus `tsc` + production build.

Real browser verification is unavailable here (tool URL policy previously rejected); it is not circumvented and remains an open manual step.
