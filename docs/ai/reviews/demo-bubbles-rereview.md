> 과거 변경에 대한 AI 정적 검토 기록입니다. PASS는 해당 검토 당시 제공된 범위에만 적용됩니다. 제안·위험·테스트 부족에 대한 추정은 확정된 결함이나 현재 PR의 검증 결과와 구분해야 합니다. 브라우저·실제 외부 API 검증을 대신하지 않습니다.

**VERDICT: PASS**

**BLOCKERS:** none.
- *Blocker 1 — resolved to the agreed minimum.* The VM-injected `renderDemoMarkup` asserts exactly one `site-header`, `planner`, `map-area` and `#demo-map-slot` on every `render()` output, not a sampled one, so shape drift fails at the first bad render.
- *Blocker 2 — resolved.* The `['a','b','a']` loop pins `marker.node`, both child nodes, `markers.length === 2` and `marker.map`, so marker recreation or `replaceChildren` churn on selection now fails.

**RISKS:**
- The harness stubs the helper, so demo.ts's ancestor-preserving branch runs only in the structural test — again absent from this diff. Its existence and 77/77 are taken on report, unverified here.
- Regex counting cannot detect wrong nesting (header inside planner); only replacement order saves it today.

**TEST_GAPS:** selected-marker styling unasserted (only `/pending/`); `map.fits.length === 5` is an unexplained magic number; real DOM/layout remains browser-blocked by policy.

**RECOMMENDATIONS:** ship the structural test file in the next diff; assert the selected class; name the fit count.
