> 당시 AI 설계 검토 기록입니다. 인터페이스·범위는 후속 구현에서 바뀌었을 수 있습니다. 현재 계약과 실행 방법은 [프론트 README](../../../web/README.md)를 우선합니다. 특히 백엔드 수정 제안은 이번 프론트 PR에 포함되지 않습니다.

**Targets**
- `web/src/demo-api.ts` (new; the only module that knows HTTP): DTOs, parsers, request builders, fetch client, base `import.meta.env.VITE_API_BASE ?? '/api'`.
- `web/vite.config.ts`: proxy `/api` → `127.0.0.1:8000`.
- demo state module: `mode` from `?mode=example`, empty live draft, dynamic `DemoPlace { googlePlaceId?, address?, primaryType? }` registry.
- nearby/text-search modal + post-route separate buttons.
- backend crawler: env-gated headless opt-in, existing `False` default unchanged.

**Interfaces**
- `optimizeRoute(req): RouteResult` — exact Notion shape; `dayIndex` 1-based, 1..8 places, non-empty `googlePlaceId`. Validate and reject locally before fetch.
- `matchVisits(snapshot, visits)` — pair by `googlePlaceId` + `order`, never by `visitId`; duplicates and same-station entries survive.
- `normalizeNearby(json)` — explicit adapter accepting documented inner object *or* actual `results[]` wrapper; returns `results[]`. Send only confirmed fields; omit `maxAnchorCount`.
- `searchPlaces(req)` — provisional, confined to one marked backend-only region: `{ userQuery, languageCode:'ko', regionCode:'JP', pageSize:8, locationBias? }` → `{textQueries} | {clarificationQuestion} | places[]` mapped to DemoPlace.

**Invariants**
1. Mode explicit; API mode never substitutes fixtures — errors surface.
2. Fixture-origin IDs never posted; unresolved place blocks submit.
3. Zero requests on initial render; search only on explicit click; no keystroke/autoload/retry loops.
4. Day draft immutable; apply gated on revision plus request-token fingerprint (tags, request, dayIndex, resultRevision); stale results dropped on modal close, day change, mode change.
5. Server times/fares/transport steps rendered verbatim; no client recomputation in live mode.
6. Map host and marker reuse preserved; station polylines carry straight-line disclaimer.
7. No keys in client. Request-scoped cache, dedupe by Google ID, filter already-planned.
8. `computeExample` reachable only in example mode.

**Acceptance**
Example mode behaviorally identical; live mode renders server timings/fares; missing ID blocks submit with a message; nearby parses both response shapes; failed optimize keeps prior result and clears loading in `finally`; live candidates visible and cancelable across tag changes.

**Tests** (keyless)
- Parser/builder round-trips: both nearby shapes, clarification, empty results, HTTP error.
- Visit matching: duplicate names, same station, reordered response.
- Token/revision guard: late response after day switch, mode switch, modal close.
- Validation: fixture ID, 0 and 9 places, malformed date/time.
- One single-stop optimize smoke against controlled fixture — no live-provider success claimed.
- Existing 80 unit tests unchanged.

**Regression Risks / Pitfalls**
`visitId`-based matching collapsing same-station attractions; silent fixture fallback masking backend failure; absolute URLs bypassing the proxy (CORS, key leakage); `?mode=example` lost on reload or in-app navigation; stale async overwriting a newer draft; double-fetch from effect re-runs; hardening the text-search contract as if Notion-confirmed; headless default flipped on Windows; duplicate markers from registry churn; timezone drift from re-parsing server ISO into local `Date`.

**Reminder:** API keys are still outstanding — all live-provider paths remain unverified until supplied.
