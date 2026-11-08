# 백엔드 업데이트 통합 검증 — 2026-11-08~09

`feat/frontend-demo`에 `origin/refactor/backend-config-and-errors`의 `9a2e201`까지 8개 커밋을 반영했다. 기존 Notion 우선 API 필드와 프론트 화면 흐름은 유지했다. 원격 푸시·배포는 하지 않았다.

## 통합 시 수정

- 기준역 조회에서 새 `ProviderTimeoutError`, `ProviderResponseError`, `ProviderError`를 각각 `timeout`, `invalid_response`, `unavailable`로 변환한다. 장소별 실패 격리와 성공 결과 보존을 유지한다.
- 공통 서버 시작에 필요한 세 환경 변수를 테스트 환경에만 주입한다. 실제 서버의 필수 설정 검증은 유지하며 누락·공백 값의 시작 실패를 검증한다.
- LLM 테스트 대역을 컨텍스트 매니저와 검증된 모델 반환 방식에 맞춘다. HeartRails 전송 대역도 새 공통 HTTP 클라이언트를 통과한다.
- Windows에서 `Asia/Tokyo` 시간대 데이터가 없어 import가 실패하는 문제를 확인하여 Windows 전용 `tzdata` 의존성을 추가했다.
- HTTP 확인 도구는 예상하지 않은 429/502/503/504를 가용성 오류(종료 코드 3)로 표시한다. 인증 문제로 단정하지 않는다. 연결·로컬 I/O 오류는 2, 기타 상태·스키마 불일치는 1이다. 명시적으로 기대한 HTTP 상태는 성공(0)이다.

## 실행 결과

| 검사 | 결과 |
| --- | --- |
| `cd web && npm test` | 128개 통과 |
| `cd web && npm run build` | 통과 |
| `cd backend && python -m unittest discover -s contract_checks -v` | 13개 통과 |
| `ruff check backend` 및 변경 파일 format 검사 | 통과 |
| HTTP 도구 상태 분류 검사 | 6개 경우 통과 |
| 실제 HeartRails 조회 | 센소지 → 아사쿠사역, 스카이트리 → 도쿄스카이트리역 정상 |
| 실제 Ekispert 및 최적화 서비스 | 두 장소 양방향 조회 후 역 간 3분·157엔 반환 |
| Vite 프록시 → 실제 FastAPI → HeartRails/Ekispert | 기준역 조회·최적화 모두 HTTP 200 |
| 브라우저 흐름 | 검색 → 두 장소 일괄 추가 → 초기 추천 → 최적화 → 결과 표시 → 추가 추천 정상 |

자동 통합 검사는 실제 TypeScript 어댑터 → Vite 프록시 → FastAPI → 최적화 엔진을 통과하며 외부 서비스 응답만 제어한다. 브라우저 흐름 검사도 외부 응답을 고정한 별도 테스트 앱으로 실행했다. 이를 실제 Google/Gemini 성공으로 해석하지 않는다.

실제 경로 서비스 검사는 진단 예제 ID와 실제 관광지 좌표를 사용했다. 경로 결과의 3분·157엔은 역 간 이동이며 접근 도보·체류시간은 포함하지 않는다. 실제 Google 검색 ID가 포함된 전체 일정 검증과 구분한다.

실제 HTTP 역·경로 검사에서는 테스트 전용 프로세스에 필수 설정 자리표시자를 명시적으로 주입했다. 이 두 API는 Google·Gemini를 호출하지 않으며, 키 누락 시 서버 시작을 거부하는 앱 정책은 변경하지 않았다. 이 진단 프로세스를 실제 검색·추천 서버로 사용하지 않는다.

검토 후 최대 56개 장소 검사를 HTTP 전송 경계로 보강했다. 모든 호출이 HeartRails GET·10초 제한임을 확인하고 다른 requests/httpx 통신은 차단한다. 필수 설정의 누락·빈 값·공백 값, 모델 선택 우선순위, Gemini SDK의 401/402/403 → 공개 HTTP 502 변환과 응답·로그의 원문 정보 비노출을 추가 검사했다. Docker에서 사용하는 Poetry 2.5.1의 `poetry check --lock`도 통과했다.

## 외부 설정과 남은 확인

- Gemini 실호출은 HTTP 402와 결제·크레딧 오류를 반환했다. 사용자가 실제 Gemini 검증을 보류하도록 요청했다.
- 로컬 Google 지도는 `RefererNotAllowedMapError`를 반환했다. 프론트의 인증 오류 안내와 대체 지도를 확인했다. 허용 주소 변경은 수행하지 않았다.
- 키는 로컬에서만 관리한다. `.env`, `web/.env.local`, `.local-run/`은 Git에서 제외한다. 인증 값과 원본 인증 오류는 이 기록에 넣지 않는다.

테스트 명령의 `python`은 Poetry 환경 또는 저장소 `.venv`의 Python 3.12 이상을 뜻한다. 외부 키 설정과 실제 API의 결제·권한·쿼터는 자동 검사와 별도로 확인해야 한다.

## 독립 검토

Claude Opus 설계 → 구현·검증 → 검토 → 검사 보강 → 재검토 순서로 진행했다. 최종 재검토는 `Approve`, 코드 통합을 막는 항목은 없었다. 검토 자료에는 최종 diff, 필수 설정의 배포 경로, 실제 HeartRails/Ekispert 결과와 자동 검사 결과를 제공했다. 검토 모델이 테스트를 직접 실행한 것은 아니다.

잔여 한계는 실제 Gemini 결제 상태, Places 활성화·제한 키 설정, 로컬 지도 허용 주소다. 서버의 모델명 검증은 존재 여부만 확인하므로 실제 모델 사용 가능 여부도 결제 상태 해결 후 확인해야 한다. 기준역 API의 공개 `reason` 어휘를 유지하여 공급자 요청 제한도 `unavailable`로 표현한다. 장소·주변 추천·경로 API는 `ProviderRateLimitError`를 HTTP 429로 변환한다.
