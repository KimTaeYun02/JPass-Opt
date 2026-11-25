# DESIGN CONTRACT — deployment hardening (frontend→backend hop)

## Context
`feat/frontend-demo` @ `08448b6` uses relative `/api` by default; development and preview provide a Vite proxy. Evidence:

- `web/src/demo-api.ts:216` — `createTravelApi(base = '/api', fetcher = fetch)`; `web/src/demo.ts:20` — `createTravelApi()` with no args. The browser bundle emits **relative** `/api/*`.
- `web/vite.config.ts:7` — `server.proxy['/api']` from `JPASS_API_TARGET` (config-time, non-`VITE_`). `preview.proxy` defaults to `server.proxy` (confirmed by local HTTP and Vite official docs); static build output has no proxy server.
- No nginx, no frontend Docker stage, `.dockerignore` allow-lists `backend/` only; no CI. The README previously incorrectly excluded preview; correct it alongside the static deployment guidance.
- No env name is reserved for an API base. `docs/ai/design/api-integration-design.md:4` mentions `VITE_API_BASE` but is superseded and unimplemented.

So a server deploy today either 404s or (on an SPA-fallback static host) returns `200 text/html`, which `demo-api.ts:229` feeds to `response.json()`; the `SyntaxError` is swallowed by the outer `catch` (`:230-232`) and reported as **"백엔드에 연결할 수 없어요. 서버 실행 상태를 확인해 주세요."** — a wrong diagnosis for a misconfigured base.

Backend, `backend/app/main.py:43-55`: `CORS_ORIGINS` defaults to `http://localhost:3000,http://localhost:5173` — missing the `127.0.0.1:5173` that the launcher/README actually use — and an empty or malformed value silently yields an empty allow-list, so every cross-origin call fails as an opaque network error. Starlette compares origin strings exactly, so a trailing slash or a path in an entry never matches.

Goal: make a non-proxied, cross-origin deployment configurable and make its failures legible. No public backend URL exists yet, so no address is invented and nothing is deployed.

## Scope
In: `web/src/demo-api.ts`, new `web/src/vite-env.d.ts` and `web/src/demo-config.ts`, `web/.env.example`, `web/README.md`, `backend/app/main.py`, two new test files.
Out: endpoints, DTOs, response schemas, optimizer/algorithm, `vite.config.ts`, Dockerfile/compose/k8s manifests, nginx, CI, Google key access/billing, any deploy or push.

## Interface
1. **`VITE_API_BASE`** (new, build-time inlined). Value is the *full* base including the `/api` path segment: `/api` (default) or `https://host[:port]/api`. Declared in `web/src/vite-env.d.ts` (`ImportMetaEnv`) alongside the existing `VITE_GOOGLE_MAPS_API_KEY` — no `.d.ts` exists today and `tsconfig.include` is `["src"]`, so the file must live under `web/src/`.
2. **`resolveApiBase(raw?: string): string`** — exported pure function in `demo-api.ts`. Empty/whitespace/undefined → `'/api'`. Trims one trailing slash. Accepts a root-relative path or an absolute `http(s)` URL with optional path. Rejects (throws `TravelApiError`) missing scheme (`localhost:8000`), non-http schemes, userinfo, query or hash — mirroring the validation already in `web/scripts/start-demo.ps1`. Called lazily inside `post`; `demo-config.ts` reads the build-time public env and `demo.ts` passes it to the adapter. Invalid configuration appears through existing request error UI, without throwing at startup.
3. **Non-JSON / wrong-base detection** in `post` (`demo-api.ts:224-233`): read `content-type`; if the response declares HTML or JSON parsing fails (valid JSON with a missing/nonstandard content type remains accepted), raise `TravelApiError` with a distinct message naming API-base/path misconfiguration, carrying the real `status`. Applies to HTML 404 and successful HTML SPA fallback. Known error statuses such as 502/503/504 retain their status-based messages even when a proxy responds with HTML. Existing status handling, abort and timeout behavior remain; 503 now indicates unavailability rather than asserting that keys are missing.
4. **Backend CORS validation** in `create_app()`: each `CORS_ORIGINS` entry must be `scheme://host[:port]` with no path/trailing slash/query; empty list or any malformed entry raises at startup with a Korean message in the style of the existing `lifespan` gate (`main.py:26-29`). Default list includes localhost and 127.0.0.1 on 3000, 5173 and preview port 4173. `allow_methods=["POST"]` and `allow_headers=["Content-Type"]` stay as-is (Starlette answers preflight itself; no credentials are sent, so no `allow_credentials`).

## Invariants
- Notion DTOs (`demo-api.ts:4-10`), the route envelope assertion (`:54`), nearby `results[]` acceptance (`:99-101`) and station `status` handling (`:155-180`) unchanged.
- `createTravelApi(base?, fetcher?)` signature and all four URLs (`/places/search`, `/stations/resolve`, `/routes/optimize`, `/nearby/recommend`) unchanged; absolute bases must not produce double slashes.
- With no env set, behaviour is byte-for-byte today's: base `/api`, dev proxy, 600 s optimize timeout.
- Current UI, copy and layout unchanged except for the new failure message.

## Acceptance
- `VITE_API_BASE=https://host/api npm run build` produces a bundle calling that absolute origin; unset → `/api`.
- Wrong base (HTML 404 or HTML 200) yields a message pointing at API-base configuration, never "서버 실행 상태를 확인해 주세요" and never an uncaught `SyntaxError`.
- Invalid `VITE_API_BASE` shows an actionable in-app error, not a white screen.
- A listed origin receives CORS headers; an unlisted one does not; empty/malformed `CORS_ORIGINS` refuses to start.
- All 13 existing contract checks and all 16 `web/tests/*.test.mjs` still pass.

## Tests
`web/tests/api-base-config.test.mjs` (node:test + node:assert/strict, injected `fetcher` — the repo's DI pattern, cf. `web/tests/station-recommendations.test.mjs:9-30`):
- default `/api` for undefined/`''`/`'   '`; trailing slash stripped; `https://h/api` accepted and URLs composed without `//`.
- invalid values (`localhost:8000`, `file:///tmp`, `http://u:p@h/api`, base with `?`/`#`) → `TravelApiError`, no fetch performed.
- `Response('<!doctype html>…', {status:404, headers:{'content-type':'text/html'}})` → `TravelApiError`, `status === 404`, config-oriented message.
- same HTML body at `status:200` (static-host fallback) → config-oriented message, not the connect-failure string.
- `status:200` with `content-type: application/json` but truncated body → format/config message.
- `status:502` with a JSON `{"detail":…}` body → existing 502 string preserved (no message-schema expansion).

`backend/contract_checks/test_cors_config.py` (unittest, `http_fixture.contract_env`, method names as full sentences, `unittest.main()` footer):
- `test_configured_origin_receives_cors_headers_on_preflight_and_post`
- `test_unlisted_origin_receives_no_allow_origin_header`
- `test_empty_or_malformed_cors_origins_prevent_server_startup`

## Regression risks
- `backend/contract_checks/test_frontend_bridge.py` drives `createTravelApi(`${origin}/api`)` with an ephemeral port — the validator **must** accept `http://127.0.0.1:<port>/api`.
- `http_fixture.contract_env()` sets `CORS_ORIGINS="http://localhost:5173"` and every one of the 13 checks builds the app: an over-strict validator breaks all of them at once.
- `web/tests/packaging.test.mjs:86-107` pins `vite.config.ts`'s proxy target and both build inputs — do not touch that file.
- `test_station_contract.py:29` snapshots OpenAPI; middleware changes must not alter routes.
- `VITE_API_BASE` is inlined at build time: changing it needs a rebuild, not a restart. A runtime-config indirection is a known limitation, deliberately out of scope.
- The optimize call allows 600 s (`demo-api.ts:244`); any reverse proxy in front of the backend needs a read timeout ≥600 s or users get the existing 504 message. Document, don't code around.
- `https` page + `http` base is blocked as mixed content — documentation-only, since the target origin is unknown at build time.

## Verification
```
cd web && npm run build && npm test
cd backend && poetry run python -m unittest discover -s contract_checks -v
```
No `.env` is read, no secret is logged, nothing is deployed or pushed.

## Integration notes

- Changes authorized by the user; Claude supplies read-only design/review, main agent implements.
- Correction to initial Opus premise: preview inherits server.proxy; see https://vite.dev/config/preview-options#preview-proxy. Static hosting still needs an explicit API base or an external reverse proxy.
- No endpoint, Notion DTO, optimizer, provider retry policy, deployment or billing change.

## Review refinements

- Unknown HTML error statuses (including 403/405) identify the API routing setting; mapped 422/429/502/503/504 retain safe status-based messages. JSON 404 has a deliberate path-setting message.
- CORS accepts valid mixed-case hosts and default ports, canonicalizes to browser origin format, rejects an empty port, supports compressed IPv6, and reports the 1-based invalid entry position without echoing the value. Explicit empty configuration remains invalid, including same-origin topology; documented unset/explicit-origin alternatives.
- Explicit vite/client type reference. Node tests intentionally import the Vite env module through a Vite build; it is a browser build module.
- Absolute base composition is tested for all four endpoints. Actual production-built UI additionally verifies the project Vite configuration and cross-origin CORS using real non-LLM providers.

- Final PASS follow-up: HTML500 retains a dedicated internal-server-error message and status, with tests; this tiny follow-up was checked by the main agent. Root `/` is an explicit root-mounted gateway base, not the direct backend default. 404/503 copy changes and the new500 message are intentional diagnostics changes, without expanding response schemas.
