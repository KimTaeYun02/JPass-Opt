> 과거 변경에 대한 AI 정적 검토 기록입니다. PASS는 해당 검토 당시 제공된 범위에만 적용됩니다. 제안·위험·테스트 부족에 대한 추정은 확정된 결함이나 현재 PR의 검증 결과와 구분해야 합니다. 브라우저·실제 외부 API 검증을 대신하지 않습니다.

VERDICT: PASS

BLOCKERS: none.

RISKS
1. Abandoned-request re-render: the `finally` still calls `render()` unconditionally after a discarded response. If the user navigated to `input` and is typing, a full re-render can drop caret/IME composition. Note that the form's `input` handler deliberately does *not* render, which suggests `render()` is destructive to live text fields. Previously this window was masked by the (buggy) view steal.
2. Silent staleness on choose→result during an in-flight calculate: the new result is discarded and `latestRequest` already advanced, so the user sees the older result with no hint that their recalculation was dropped. Contract-sanctioned, but user-visible.
3. Pre-existing, now more visible: the `view` branch bumps `tokens.initial/nearby` and forces `loading=false` *before* the `enterChoose()`/invalid-view `return`. A rejected transition still cancels in-flight place loads and leaves the "조회 중입니다…" message with no spinner, while `navigationRevision` is correctly not incremented.
4. `running` remains true across navigation by design; confirm no control on the `input`/`result` view is disabled on `running`, otherwise the user is locked out for the full API duration (`handleClick` bails on `[disabled]`).
5. `Number(button.dataset.day)` is still unguarded; NaN fails the new `day === activeDay` check and would assign NaN. Pre-existing, unchanged.

TEST_GAPS
- Staging path not visible in the evidence: stage items → type (clears `searchPool`) → submit "add all". If staged IDs are resolved through `searchPool` rather than the `registerPlaces` registry, items are lost. This is the highest-value untested interaction.
- Programmatic `search.value = ''` (e.g. after add) fires no `input` event, so pool/message reset is skipped; only user keystrokes were covered.
- Concurrent search: keystroke sets `loading.search=false` while request A is in flight, so B can start. Token guards look correct for both completion orders, but the two-in-flight case itself appears untested.
- No test for `render()` during typing (risk 1), and no browser/a11y pass on focus after an abandoned settle.

RECOMMENDATIONS
- Add the staged-Set-survives-query-change assertion; it is one test and covers the only plausible data-loss path.
- Consider `finally { running = false; render(); }` → keep as-is, but add a caret-preservation assertion for the input view, or skip the re-render when the view has no busy affordance.
- Leave risks 2-5 as documented follow-ups; none justify widening this bounded fix.
