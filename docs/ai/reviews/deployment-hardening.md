# 배포 연결·로컬 실서비스 검증 — 2026-11-09

대상: `feat/frontend-demo`, 백엔드 통합 커밋 `08448b6` 이후 변경. Notion input/output docs의 DTO·엔드포인트·최적화 알고리즘은 변경하지 않았다.

## 변경 사항

- 프론트 `VITE_API_BASE`로 정적 배포의 전체 API 주소를 빌드 시 설정한다. 기본 `/api`, Vite 개발/preview 프록시는 유지한다. 잘못된 설정은 요청 시 화면 오류로 표시한다.
- HTML 200·403·404·405, 깨진 JSON과 네트워크 실패를 구분한다. 422·429·502·503·504는 상태별 안전한 메시지를 유지하고, 503을 API 키 누락이라고 단정하지 않는다. 응답 원문·설정 원문을 사용자 오류에 노출하지 않는다.
- CORS origin은 시작 시 검증한다. 호스트 대소문자·기본 포트·IPv6를 정규화하고 빈 포트·경로·쿼리·빈 목록을 거절한다. 잘못된 항목의 순번만 표시한다. 기본 개발 주소는 localhost·127.0.0.1의 3000·5173·4173 포트다.
- 배포/개발 지도 키, 서버 키와 빌드 환경 변수의 역할을 실행 문서에 구분했다. Vite preview가 server.proxy를 상속한다는 사실을 설치된 Vite 코드와 로컬 HTTP로 확인하고 기존 문서를 정정했다.

## 자동 검사

| 검사 | 결과 |
| --- | --- |
| `cd web; npm test` | 134/134 통과 |
| `cd backend; python -m unittest discover -s contract_checks -v` | 19/19 통과 |
| `cd web; npm run build` | TypeScript + Vite 통과 |
| 명시적 `VITE_API_BASE=http://127.0.0.1:8001/api` 빌드 | 통과; 실제 빌드 화면의 교차 origin 요청 검증 |
| Ruff check·format (변경 Python 파일) | 통과 |
| `git diff --check` | 통과 |
| 공개될 소스의 키 형태 검사 | 통과; `.env`, `web/.env.local`, `.local-run/` Git 제외 확인 |

Node 자동 검사에는 실제 Vite를 통한 공개 환경 변수 삽입과 UI→실제 HTTP 어댑터 전달을 포함한다. 해당 소규모 빌드 검사는 `configFile:false`를 사용하므로 프로젝트 Vite 설정 자체는 아래의 실제 전체 빌드 화면 검증으로 보완했다. 백엔드 검사는 실제 TS 어댑터→Vite 프록시→FastAPI 계약 검사 및 변경 없는 OpenAPI 스냅샷을 포함한다. 자동 테스트의 외부 공급자는 제어된 응답이며 외부 인증 정보를 사용하지 않는다.

## 실제 서비스·브라우저 확인

사용자 승인 후 Places API (New)를 활성화하고, Places 전용 서버 키를 현재 PC의 외부 IP로 제한했다. 실제 Text Search 및 Nearby 호출에 성공했다. Maps JavaScript API 전용 개발 키는 localhost·127.0.0.1의 5173/4173 포트 HTTP 리퍼러 4개로 제한하고 무시되는 `web/.env.local`에만 저장했다. 기존 배포용 지도 키의 제한은 유지했다. 인증 정보 값은 문서에 기록하지 않는다.

실제 Google 지도 로드 및 마커 클릭 후 유지 확인. 빌드 산출물을 4173에서 열고, 별도 8001 검증 서버를 직접 호출해 CORS까지 확인했다. **Gemini만 임시 검증 프로세스의 테스트 응답으로 대체했으며**, Google Places·HeartRails·Ekispert는 실제 호출했다. 애플리케이션 코드에 모델 실패를 대체하는 분기는 추가하지 않았다.

1. 장소 검색: Google 결과 16개 표시, 센소지·도쿄 스카이트리 두 곳 선택·일괄 추가.
2. 계산 전 추천: 기준역 Nearby와 Text Search 결과 합계 19개 표시.
3. 실제 두 장소 최적화: 아사쿠사→도쿄 스카이트리 역, 08:00→08:03, 3분·157엔 표시. 접근 도보·체류시간은 포함하지 않는 기존 `stationTravelOnly` 계약이다.
4. 계산 후 Nearby: 실제 결과 3개 표시. 추가한 장소가 재계산 전 목록·마커·상세에 보이고 추가 취소 시 제거됨을 확인했다. 기존 경로 결과를 유지하며 일정 편집 후 재계산을 요구하는 현재 정책도 유지했다.
5. 콘솔 오류 0개. 검사 뷰포트에서 문서 가로 크기 1280px = 가시 영역 1280px, 가로 넘침 없음.

증거 이미지는 비공개 `.local-run/`에 보관: `local-google-map.jpg`, `production-recommendations.jpg`, `production-pending-place.jpg`, `development-map-referrers.jpg`. 검증 후 임시 서버·preview는 종료하고 기본 `/api` 설정으로 다시 빌드했다. 실제 설정을 사용하는 백엔드와 개발 프론트만 실행해 두었다.

## 확인 범위와 남은 배포 조건

- Gemini 실호출은 앞서 확인한 HTTP 402 결제·크레딧 문제 때문에 사용자 요청으로 보류했다. 전체 LLM 포함 실서비스 성공으로 보고하지 않는다.
- Docker Desktop 실행을 시도했으나 Linux 엔진 파이프가 존재하지 않아 실제 이미지 빌드·컨테이너 기동은 검증하지 못했다. Windows/Python 로컬 실행 성공이 Linux 컨테이너 실행 보증은 아니다.
- 공개 백엔드 주소는 아직 제공되지 않았다. 실제 배포에는 HTTPS API 주소·프론트 origin의 CORS·배포 서버의 고정 외부 IP로 제한한 Places 키가 필요하다. 현재 PC IP 제한 키는 배포 서버에 그대로 사용할 수 없다.
- 요청당 공급자 지연·쿼터·장애와 역 접근 이동시간 등 기존 알고리즘의 제약은 이 수정으로 제거되지 않는다. 실제 배포 환경의 프록시 시간 제한도 최적화 대기 600초에 맞춰 확인해야 한다.

## 독립 검토

1차 Opus 검토 PASS 이후 권고한 CORS 정규화·빈 포트·오류 순번·HTML 403/405·타입 참조·테스트 보강을 반영했다. 최종 검토 결과를 아래에 수록한다.

### 최종 Opus 검토

## VERDICT: PASS

All four interface items are implemented, the invariants hold, and the reported evidence (134/134 web, 19/19 contract incl. unchanged OpenAPI, TS+Vite builds with explicit and default base, Ruff, and a real production-built UI exercising all four endpoints cross-origin against live non-LLM providers) covers every acceptance criterion. The five substantive recommendations from the previous pass were all adopted. Nothing demonstrated rises to a blocker.

## BLOCKERS

None.

## RISKS

1. **`500` with an HTML body is now diagnosed as an API-base error.** `knownFailure = [422, 429, 502, 503, 504]` carves out the gateway statuses, but not `500`. A genuine backend crash behind nginx/gunicorn typically answers `500 text/html`, which now yields "API 응답을 읽을 수 없어요. VITE_API_BASE와 /api 프록시 설정을 확인해 주세요." — pointing the operator at a correctly configured variable. This is the mirror image of the original bug, introduced by (correctly) widening the guard. Adding `500` to `knownFailure`, or a mapped `500` message, closes it without reopening the `403`/`405` hole.
2. **`VITE_API_BASE=/` resolves to the empty string.** `'/'` → `''`, so requests go to `/places/search` at the site root. Accepted silently; it will fail into the HTML guard and produce the config message, so it is self-diagnosing rather than silent — but a plausible operator typo is validated instead of rejected.
3. **Non-JSON empty bodies still read as config errors.** A future `204`/empty body would surface the base-configuration message. No current endpoint does this; unchanged from the last pass, listed only so the next reader doesn't rediscover it.
4. **Build-time inlining remains the operational sharp edge.** Correctly documented in both READMEs and declared out of scope; the residual risk is purely operator-procedural (change the variable → rebuild, not restart).
5. **Unverified-by-construction items, as declared:** no live Gemini path (402), no Docker engine, no public backend origin. None is claimed as verified, so none affects the verdict.

Previously raised items 2, 3, 5, 7 (CORS host case / default port / empty port / entry position / `vite/client` reference) are resolved in this diff and I confirm the fixes read correctly: `urlsplit().hostname` is already lowercased, `netloc.endswith(':')` rejects the empty port, `IPv6Address(...).compressed` with bracket re-wrapping matches the browser serialization, the `port != {80, 443}[scheme]` suffix matches origin format, dedup happens post-canonicalization, and the `RuntimeError` carries a 1-based index with no value. `ValueError`/`UnicodeError` from `.port` and `.encode("idna")` are both caught.

## TEST_GAPS

- No case pins the `500`-with-HTML behaviour either way, so risk 1 is undocumented in the suite — the one gap I would actually close.
- `resolveApiBase('/')` → `''` is asserted as expected output, but no test shows what the resulting request does; the semantics of that case are pinned without being justified.
- No backend case for IDNA/Unicode hosts (`http://例え.jp`), a trailing-dot host (`http://example.com.`), or an over-long label — all routed through the `UnicodeError` branch, which is reachable but untested.
- No backend case asserts post-canonicalization deduplication (`http://localhost:80, http://localhost`), despite the `if origin not in origins` guard.
- Preflight is still exercised only on `/api/stations/resolve`; acceptable given shared middleware plus the OpenAPI snapshot.
- `deployment-build.test.mjs` still builds with `configFile: false`, so the project's own `vite.config.ts` inlining rests on the manual production build rather than the automated suite. Correctly declared in the refinements; `packaging.test.mjs` must not be touched, so this is the right split.

## RECOMMENDATIONS

1. Add `500` to the `knownFailure` list (or map it alongside `404`) so an HTML error page from a real server fault is not reported as a base misconfiguration. Single-token change, highest value of anything remaining.
2. Reject a bare `'/'` in `resolveApiBase` — a base that strips the `/api` segment is never a working configuration, and the validator exists to catch exactly that class.
3. Optional: one backend case covering the `UnicodeError` path and one covering dedup, both cheap additions to `test_valid_origins_use_browser_case_port_and_ipv6_serialization`'s neighbourhood.
4. Record the `404`/`503` message rewordings as intentional deviations from "only the new failure message" in the design doc's refinements list — the CORS and HTML items are already recorded there, these two copy changes are not.

Notion DTOs, the route envelope, the four endpoint paths, `createTravelApi(base?, fetcher?)`, abort/timeout/cancellation semantics, and the single-attempt no-retry policy are intact; the status-loop test asserting `calls === 1` per failure is the right place to pin the last of those.

### 통합자 후속 확인

PASS 이후 제시된 HTML 500 오진 권고를 반영해 500 전용 내부 오류 안내와 안전한 메시지·단일 호출 테스트를 추가했다. 이 소규모 후속 수정은 메인 에이전트가 테스트·빌드로 검증했다. `/`는 루트에 API를 마운트하는 별도 게이트웨이를 위한 명시적인 고급 설정으로 유지했다. 현재 백엔드에 직접 연결할 때는 `/api`가 필요하며, 주소 검증은 실제 엔드포인트의 존재를 보증하지 않는다. 해당 테스트는 `/places/search`로의 조합까지 이미 확인한다. 국제 도메인·장애/쿼터 등 모든 환경을 검증한 것으로 해석하지 않는다.
