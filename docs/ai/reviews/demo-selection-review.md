> 과거 변경에 대한 AI 정적 검토 기록입니다. PASS는 해당 검토 당시 제공된 범위에만 적용됩니다. 제안·위험·테스트 부족에 대한 추정은 확정된 결함이나 현재 PR의 검증 결과와 구분해야 합니다. 브라우저·실제 외부 API 검증을 대신하지 않습니다.

**VERDICT: PASS** (review of supplied diff only; no tools, no execution, so build/test results are taken as reported.)

**BLOCKERS** — none. Ordering in `choose-toggle` is correct in both branches: `addPlace` throws before `initialAddedIds.set`, so a cap rejection cannot create phantom ownership; `removePlace` only runs for ids already in `owned`, which is derived from the draft, so it cannot throw. Base protection holds because `choiceCandidates()` excludes current-day ids unless owned, and `ownedPicks()` intersects the stored set with live `placeIds`, which also prunes stale keys on the next write to that day. Provenance pruning is wired into `remove`, `pending-remove`, and the toggle itself. `showRoute`/polyline stay off in `choose` because `calculated` is undefined; markers dedupe since candidates are filtered against `ids`. Invariants 1–8 all hold as written.

**RISKS**
- Hidden-day leak: `initialRecommendations` scopes `otherDays` to `slice(0, dayCount)` (intentional, test-asserted), so shrink→pick→restore can place the same id on two days. `validateDraft` permits it and the existing search dialog already allows it, so not a regression — but it will surface if the real `/api/routes/optimize` rejects repeats.
- `googlePlaceId: null` is inert today (Calculate stays on local `computeExample`, no payload). When the POST is wired, a null must throw at the mapping boundary, not be coerced; prose-level "don't send demoPlaceId" is not a guard.
- `<div class="choice-picked" role="status">` wraps interactive cancel buttons; a live region containing focusable controls announces noisily and can disturb focus.
- Contract drift: the contract says `src/main.ts`/`src/catalog.ts`, 1-based `picked`, `address: string`. The implementation is `demo.ts`/`demo-model.ts`, zero-based, nullable — correct per your scope note, but the stale contract will re-trigger this flag on the next review.

**TEST_GAPS**
- The rendered `disabled` attribute on the step-3 button is never asserted; the harness's `hasAttribute: () => false` bypasses it, so only the handler guard is covered.
- No focus assertion for `choose-toggle`. Cancelling from a chip destroys the focused node; the generic `focusData` restore will likely land on the candidate card instead, which is untested either way (`remove`/`recommend-add`/`pending-remove` each got explicit focus handling; the toggle did not).
- `#demo-map-slot` persistence is asserted for prior flows, not across `input→choose→result` specifically.
- `location`, `address`, `primaryType` are asserted in shape but consumed by nothing — the card renders `placeById(id).area`, so the candidate type is documentation, not a contract under test.

**RECOMMENDATIONS** — update the design contract to the shipped names/indices/nullability; add one assertion for the disabled step-3 attribute and one for focus after chip cancel; move the "never send `demoPlaceId` as `googlePlaceId`" rule from README prose to a throwing mapper when the route POST lands. Browser QA remains outstanding and is correctly scoped out.
