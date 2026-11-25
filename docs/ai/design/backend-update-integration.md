# 설계 계약: refactor/backend-config-and-errors → feat/frontend-demo 병합

## Context
`origin/refactor/backend-config-and-errors`(9a2e201, 8 커밋)는 (1) 기동 시 `GOOGLE_PLACE_API_KEY`·`GEMINI_API_KEY`·`GEMINI_MODEL` 필수화(`lifespan`), (2) 서비스별 예외를 `app/errors.py`의 `Provider*` 계층으로 통합, (3) `run_flow`/`run_search`/`rewrite_query`/`build_search_plan`/`select_recommendations` 제거 및 `GEMINI_NEARBY_MODEL` 추가를 가져온다. 공개 스키마는 불변이지만 `503`(설정 누락)이 사라지고 클라이언트가 `requests` 예외 대신 `ProviderError`를 던진다. 현 브랜치의 기준역 API(`/api/stations/resolve`)와 로컬 프론트 연동은 이 두 축에 모두 의존하므로 병합과 함께 좁은 적응이 필요하다. `git merge-tree` 결과 텍스트 충돌은 없다(의미적 회귀만 존재).

## 대상 (변경 범위)
- `backend/app/services/station_service.py` — 유일한 실제 애플리케이션 수정.
- `backend/contract_checks/http_fixture.py`, `test_frontend_contract.py`, `test_station_contract.py` — 테스트 대역을 새 seam에 맞춤.
- `backend/scripts/test_api_http.py` — 실제 통합 실행의 원인 분리.
- `backend/docs/stations.md` 1줄(키 불필요 서술), 필요 시 `backend/docs/openapi.json`.
- 유지(ours): `api/stations.py`, `schemas/station.py`, `examples/http/stations.json`, `web/**`.

## 인터페이스
- `station_service._lookup`: `ProviderTimeoutError→"timeout"`, `ProviderResponseError→"invalid_response"`, `ProviderError→"unavailable"`, 기타 `Exception→"unavailable"`. `requests` 임포트 제거.
- 테스트 환경 seam: `http_fixture.contract_env()` 컨텍스트매니저가 세 필수 변수에 자리표시자 값을 넣는다. 모든 `TestClient(create_app())` 진입이 이를 감싼다.
- LLM 대역 seam: `GeminiWrapper` 자리에 들어가는 대역은 컨텍스트매니저(`__enter__`/`__exit__`)이며 `request()`는 `response_validator`(없으면 `response_schema`)로 검증한 Pydantic 모델을 반환한다.
- HTTP 전송 대역 seam: `app.clients.heartrails.requests.get` → `app.clients.http_client.requests.request`.

## 불변식
1. 상류의 필수 설정 정책을 완화하지 않는다. 기본값 주입·검증 우회·`getenv` 되돌리기 금지.
2. `/api/stations/resolve`는 Google·Gemini를 호출하지 않는다(장소당 실패 격리 유지, 부분 성공 보존).
3. 프론트 가시 계약 불변: 역 응답의 `status`/`reason` 어휘, `parseStationResponse`·`demo-api.ts` 상태 메시지 맵(`503`은 기동 실패·프록시 미가동 안내로 그대로 유지).
4. 외부 제공자 원문 메시지·키 값은 응답·로그에 노출하지 않는다.

## 수용 기준
- 병합 후 `openapi()`가 스냅샷과 일치(`stations` 경로 + `routes` `429` + `places/nearby` `503` 제거).
- 세 변수 중 하나라도 공백이면 `lifespan`이 `RuntimeError`로 기동 실패하고 누락 이름을 나열한다.
- 역 조회 타임아웃은 `reason="timeout"`을 유지하고 `unavailable`로 강등되지 않는다.
- `contract_checks` 전체와 `web` 테스트가 외부 호출 없이 통과(`audit["blocked"] == []`).

## 테스트
- `test_frontend_contract`: 키 누락 케이스를 "기동 실패" 단정으로 교체하고, `422`는 유효 환경에서 검증한다.
- `test_station_contract`: 키 없는 최대 요청 테스트는 "Google/Gemini 호출 0회"(`assert_not_called`)로 재정의, 실패 격리 테스트는 `Provider*`를 던지는 대역, HeartRails 매핑 테스트는 숫자 좌표 수용 + 문자열 좌표 거부(`invalid_response`)로 분리, Text Search 테스트는 `rewrite_query` 패치 대신 LLM 대역 사용.
- `http_fixture`: `GEMINI_MODEL` 추가, `ControlledLLM`에 컨텍스트매니저·모델 반환 적용(교차 스택 브리지 유지).
- `scripts/test_api_http.py`: 연결 거부·기동 실패·`429/502/503/504`는 외부 자격증명/권한 문제로 분류해 전용 종료 코드와 설명을 쓰고, `422`·형식 위반은 애플리케이션 결함으로 남긴다. 환경 변수 값은 출력하지 않는다.

## 회귀 리스크

### 구현 중 확인한 Windows 실행 조건

Windows Python 3.12에서 `ZoneInfo("Asia/Tokyo")` import가 `ZoneInfoNotFoundError`로 실패했다. 로컬 연동 검증과 Windows 실행을 위해 `backend/pyproject.toml`·`poetry.lock`에 Windows 전용 `tzdata`를 추가하는 범위를 확장한다. Linux 의존성은 변경하지 않는다. 런타임 환경과 일치하는 Poetry 2.5.1로 lock 유효성을 확인한다.
- 상류가 문자열 좌표를 거부해 HeartRails 응답 형태 변화가 `ok`→`error`로 바뀔 수 있다(프론트는 경고 문구로 흡수).
- `GEMINI_NEARBY_MODEL`은 nearby에만 적용된다(장소 검색은 `GEMINI_MODEL` 고정).
- 키 누락이 이제 크래시루프이므로 로컬·k8s 기동 절차가 깨질 수 있다. `.env`/Secret은 읽지 않고 변수 이름만 문서로 안내한다.
- 푸시·배포는 하지 않는다.

## 검증 절차
1. `git merge --no-ff origin/refactor/backend-config-and-errors`(충돌 없음 예상) 후 `ruff check`.
2. `cd backend && poetry run python -m unittest discover -s contract_checks -t .`
3. `cd web && npm test`(브리지 포함).
4. 실제 서버 1회: 자리표시자 아닌 실키로 기동 후 `python backend/scripts/test_api_http.py --api stations`, 자격증명 실패와 결함이 다른 종료 코드로 갈리는지 확인.
