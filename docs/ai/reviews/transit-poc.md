# 공식 대중교통 PoC 검토·실행 결과

2026-11-13. 독립 CLI만 추가했다. 제품 API·프론트·기존 크롤러에는 이번 작업으로 변경을 가하지 않았다.

## 메인 구현자의 최종 실행

- 백엔드 `python -m unittest discover -s contract_checks -v`: **55개 통과**. 기존 45개와 PoC 10개이며, 공급자 네트워크는 대체했다.
- 추가 스크립트·검사 파일 Ruff check/format 및 `git diff --check` 통과.
- 실제 CLI의 기본 실행과 키 없는 `--live` 실행: `MISSING_CREDENTIALS`, 종료 코드 1, Ekispert·Google 호출 **0회**.
- 로컬 Google Routes 키는 설정돼 있지만 Ekispert 공식 키는 없음. 사용자도 아직 키가 없으므로 코드부터 준비하도록 지정했다. 실버스 운행·도메인 인증·실제 응답 형상은 미검증이다.
- 새 파일에 로컬 환경의 실제 키 값이 포함되지 않음을 값 출력 없이 확인했다.
- 실호출 예산은 Ekispert 3회·Google 도보 최대 2회다. 키를 발급받은 뒤 별도로 실행해야 한다.

## 독립 검토와 반영

Opus는 제공한 설계 계약·최종 파일·53개 검사 통과 결과를 읽기 전용으로 검토해 PASS와 차단 사유 없음으로 판단했다. 아래는 그 시점의 정적 검토이며 실제 외부 요청을 실행한 결과가 아니다.

이후 메인 구현자가 비차단 권고를 반영했다. 버스 상세 유형 누락은 `bus_detail_missing`으로 분리하고, HTTP 상태 숫자를 안전한 사유 코드로 보존했다. 주변 검색 파라미터·Google 요청 상한·원본 예외 비노출을 보강하고, 시간 형식 테스트의 예외를 좁혔다. 최초 대기 시간 설명도 수정했다. 변경 후 55개 검사와 Ruff를 다시 통과했다. 이 소규모 후속 수정에는 별도 재검토를 호출하지 않았다.

남은 검증은 실제 키 발급·사용 도메인/IP 허용·오사카 첫 정류장 쌍의 시간표/버스 상세 유형 확인이다. 단 한 쌍의 실패를 전체 지역 미지원으로 일반화하지 않는다.

## 제공된 최종 파일에 대한 독립 검토 원문

## VERDICT: PASS

The PoC is self-contained, fail-closed, and keeps the stated invariants. No blockers found. Findings below are non-blocking but two are worth fixing before the key arrives, because they will cost you a diagnostic round-trip once live calls are finally possible.

## BLOCKERS
None.

Checked specifically: zero-call preflight (both keyless-live and configured-default paths provably never touch `requests.get`/`walking_route`), budget charged before dispatch (`charge` precedes the request, so an exception cannot under-count), fixed HTTPS host, `allow_redirects=False`, 15 s timeout, no raw response/exception/URL emission, reason codes are literal strings only, and the final `json.dumps` key-substitution guard. Ordering, chain length (`len(points) == len(lines)+1`), endpoint ID equality with coordinate drift tolerance only (no name/proximity merging), and the independent elapsed-vs-`timeOnBoard+timeWalk+timeOther` comparison with 1-minute tolerance all hold. Midnight rollover is handled by real `timedelta` arithmetic, verified by the 23:58 → `20261115`/`0003` case.

## RISKS

**1. Bare-string `Line.Type`/`Station.Type` produces a misleading FAIL.** `transport()` correctly accepts both shapes, but when the provider returns `Type` as a plain string the detail is `""`, so `"local" not in [""]` and the run dies as `no_local_bus_leg` (or `no_local_bus_stop` in `first_bus_stop`). That reason code reads as "no bus service on this pair" when the real cause is "mode detail absent in this response shape." Since the whole point of the PoC is to answer whether bus connection exists, a false negative here is the single most expensive failure mode. A distinct code (`bus_detail_missing`) costs one line and removes the ambiguity.

**2. `ekispert_http_error` discards the status code.** The two known pending unknowns are exactly the ones the status distinguishes: 403 (unregistered execution domain/IP) versus 429 (trial quota) versus 404 (wrong endpoint path). The numeric status leaks nothing — no body, no URL, no key — and including it turns one blind retry cycle into a direct answer.

**3. `component_minutes` requires all three fields.** A response legitimately omitting `timeOther` yields `str(None)` → `invalid_course_duration`. Fail-closed is right, but this is a plausible live-FAIL cause unrelated to route quality. Acceptable as-is for a disposable diagnostic; just know it when the first live run fails.

**4. `.env.example` content unverified.** Not included in the provided contents, and that file is also pre-existing dirty, so I cannot attest the added entry is an empty placeholder rather than a value.

## TEST_GAPS

- **`geo` request parameters are never asserted.** `FakeCalls.get` ignores `params` for the geo branch, so nothing pins `type=bus.local`, the 1000 m radius, `gcs=wgs84`, or `stationCount=2` — all four are documented claims with no test behind them. The `course` branch does capture params and is asserted. This is the one gap I would actually close: it is a two-line change to the existing fake.
- Tolerance boundary unexercised: delta of exactly 1 minute (pass) versus 1.5 (fail) is untested; only 0 and 15 appear.
- `same_stop_pair`, `walk_unavailable`, and `google_walk_error` have no coverage. Low value for a throwaway tool.
- `parse_departure` rejection cases use `assertRaises(Exception)`, which would also pass on an unrelated crash. Narrow to `argparse.ArgumentTypeError`.

## RECOMMENDATIONS

1. Separate the mode-detail failure from the no-service failure (risk 1), and include the HTTP status in `ekispert_http_error` (risk 2). These two are the only changes I would make before the key arrives.
2. Assert the geo params in the existing fake.
3. Documentation correction: `backend/docs/transit-poc.md` states 최초 정류장 대기는 별도로 총시간에 포함한다, but the initial `ready_at → first departure` wait is folded into `transitWaitingAndTransferMinutes` alongside transfer gaps and is never reported on its own. Either emit `initialWaitMinutes` or reword the doc line. Everything else in both docs — the trial-area qualification, the single-pair scope caveat, the explicit refusal to claim coverage before a real key, and the free-plan API-list limitation — is accurately hedged and matches the code.
4. `instant.second`/`microsecond` checks in `parse_departure` are dead given the regex. Harmless; delete if you touch the function.

The omission of `--origin`/`--dest`/report-file arguments is the correct call for a one-pair disposable diagnostic and is documented as out of scope — no action.