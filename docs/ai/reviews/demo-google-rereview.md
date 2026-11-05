> 과거 변경에 대한 AI 정적 검토 기록입니다. PASS는 해당 검토 당시 제공된 범위에만 적용됩니다. 제안·위험·테스트 부족에 대한 추정은 확정된 결함이나 현재 PR의 검증 결과와 구분해야 합니다. 브라우저·실제 외부 API 검증을 대신하지 않습니다.

VERDICT: APPROVE — the four prior blockers are addressed (phase/`status()`, typed `DemoMapLoadError` with masked generic SDK errors + separate auth message, overlay offsets with map slot `z-index:0`, `content: node` marker construction with `core` imported). Denied browser QA accepted as non-code.

BLOCKERS: none.

RISKS
- `fitBounds` bottom padding 100px vs. overlay stack (legend at `bottom:94px` plus its own height ≈ 120px+): the lowest marker can still sit under the legend. The single-place branch (`setCenter` + `setZoom(14)`) ignores padding entirely.
- `mapId: 'DEMO_MAP_ID'` is a literal, not a registered Map ID. The real SDK logs an error and AdvancedMarkerElement may not render; the README note is documentation, not a guard, so it fails silently anywhere without a real ID.
- `gm_authFailure` is restored only via `import.meta.hot.dispose`; after `destroy()` in production the global stays bound (no-op, but leaks).
- `gestureHandling: 'greedy'` traps one-finger pan on mobile.

TEST_GAPS
- No assertion that the marker content node reaches the host DOM; mocks accept any options, so the Map-ID failure mode is unobservable in tests.
- Real loader path untested: 20s timeout, `clearTimeout` in `finally`, missing-key branch — only mocked rejections are covered.
- CSS layering/padding and the attribution claim rest entirely on the denied browser QA.

RECOMMENDATIONS: derive the bottom padding from the legend offset constant and apply padding in the single-place branch; read `mapId` from env with a documented fallback; restore `gm_authFailure` in `destroy()`.
