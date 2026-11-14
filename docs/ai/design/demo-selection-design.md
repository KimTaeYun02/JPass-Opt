> 당시 AI 설계 검토 기록입니다. 인터페이스·범위는 후속 구현에서 바뀌었을 수 있습니다. 현재 계약과 실행 방법은 [프론트 README](../../../web/README.md)를 우선합니다. 특히 백엔드 수정 제안은 이번 프론트 PR에 포함되지 않습니다.

**Targets** — `src/main.ts`: add `view: 'input' | 'choose' | 'result'`, three header steps, result step disabled until a result exists. Reuse `renderDemoMarkup` for all three views so the SDK map slot is never disconnected. `src/catalog.ts`: add `initialRecommendations`.

**Interfaces**
```ts
type RecCandidate = {
  name: string;
  location: { lat: number; lng: number };
  searchTags: Tag[];          // culture|nature|anime|shopping|relaxation|activity
  address: string;
  primaryType: string;
  googlePlaceId: null;        // mock catalog has no real Place ID
  demoPlaceId: string;
};
initialRecommendations(tags: Tag[], planned: Set<string>): RecCandidate[]
picked: Map<number, Set<string>>   // dayIndex (1-based) -> stage2-owned demo ids
```
Picks apply through existing `addPlace` / `removePlace` on the current immutable draft.

**Invariants**
1. Stage1/stage3 bodies unchanged; stage2 only mutates the draft.
2. Only ids in `picked[day]` are removable; base places render as fixed rows.
3. If a base edit re-adds a picked id, prune it from `picked` (it becomes base).
4. Pool excludes places planned on active days, except the current day's own picks; no tags → unplanned catalog; no match → empty.
5. Picks stay visible across tag edits.
6. Cap 8/day at selection; entering `choose` validates base input with `allowEmptyDays`; Calculate still demands 1-8/day.
7. Markers = draft ∪ candidates, deduped by id; no route line before result.
8. Existing revision guard kept; pick controls disabled while calculating.

**Acceptance** — input→choose→result plus back; multi-select/unselect; day tabs assign correctly; 9th pick blocked; skip with zero picks leaves the draft byte-identical; stale calculation discarded.

**Tests** (in-memory) — navigation, back, multiselect, unselect, day assignment, cap, empty-skip, stale-calc, marker dedup, absent polyline, provenance prune. Gate: build + full suite.

**Risks** — RouteRequest requires `googlePlaceId: string`, but catalog entries have none, so Calculate must keep sending the existing stage1 payload shape; resolving real IDs waits on the Text Search recommendation API, still "planned, not defined" (10/25). NearbyRequest stays unused — it needs stations, travelTypes, radius, placeCount and is post-route.

**Limit** — browser access is denied, so verification is build plus unit suite; no runtime click-through. No files written, per your no-tools constraint.
