# JPass-Opt 프론트

지도 중심 PC 여행 플래너입니다. 일차별 장소 입력 → 기준역 조회와 Nearby·Text Search 추천 → 관광지 선택 → 최적 경로 확인 및 추가 추천 순서로 사용합니다. 기존 시간표 프로토타입도 함께 보존했습니다.

## 바로 실행

저장소 루트에서 시작합니다. Node.js 22.20 이상이 필요합니다.

```sh
cd web
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

- [예제 모드](http://127.0.0.1:5173/demo.html?mode=example): 백엔드·키 없이 저장된 예시로 화면 흐름 확인.
- [API 모드](http://127.0.0.1:5173/demo.html): 별도 서버로 검색·추천·최적화. 빈 일정으로 시작하며 오류를 예제로 대체하지 않습니다.
- [기존 시간표](http://127.0.0.1:5173/): 드래그·시간 조정용 별도 프로토타입.

FastAPI `backend/`를 별도로 실행하고 프론트에서 연결합니다. 이 브랜치에는 계산 전 기준역 조회 API도 포함합니다. 실행 방법은 [백엔드 안내](../backend/README.md)를 참고하세요. 프론트 자체 실행에는 형제 폴더나 Python이 필요하지 않습니다.

Windows에서는 다음 도우미도 사용할 수 있습니다. 다른 폴더에서 호출해도 스크립트 위치를 기준으로 실행하며 `Ctrl+C`로 종료합니다.

```powershell
./scripts/start-demo.ps1
./scripts/start-demo.ps1 -Port 5174 -ApiTarget http://127.0.0.1:8000
./scripts/start-demo.ps1 -ValidateOnly
```

도우미는 프론트만 실행합니다. 과거의 `-BackendRoot`·`-RestartBackend` 옵션은 제거했습니다. 사용 중인 포트에서는 실패하므로 다른 포트를 지정하거나 기존 서버를 직접 종료하세요.

## 지도와 백엔드 설정

실제 지도·마커가 필요하면 `.env.example`을 `.env.local`로 복사하고 브라우저 키를 입력하세요. 키가 없거나 지도를 불러오지 못하면 상태 안내와 SVG 대체 지도를 표시합니다. 예제 모드에서도 키를 설정하면 실제 지도 SDK를 불러옵니다.

```dotenv
VITE_GOOGLE_MAPS_API_KEY=
JPASS_API_TARGET=http://127.0.0.1:8000
```

브라우저 키는 Maps JavaScript API·Places API (New)와 허용 HTTP 리퍼러로 제한하세요. `VITE_` 변수는 브라우저 번들에 포함됩니다. 서버의 `GOOGLE_PLACE_API_KEY`·`GEMINI_API_KEY`는 백엔드 환경에만 넣으세요. 과금·쿼터는 사용하는 GCP 프로젝트 설정을 확인하세요. 개인 프로젝트의 예전 제한값을 저장소 기본값으로 간주하지 않습니다.

팀원이 백엔드 서버에 Places·Gemini 키를 설정했다면 프론트 담당자가 그 키를 발급하거나 복사할 필요는 없습니다. 전달받은 서버 주소만 `JPASS_API_TARGET`에 설정하세요. 프론트 지도용 브라우저 키는 별도입니다. 기준역 조회 자체에는 API 키가 필요하지 않습니다.

개발 중 `/api`는 Vite가 `JPASS_API_TARGET`으로 전달하며 기본값은 `http://127.0.0.1:8000`입니다. `.env.local` 변경 후 Vite를 재시작하세요. `-ApiTarget`은 해당 실행에만 적용됩니다. 빌드와 `npm run preview`에는 개발 프록시가 없으므로 배포할 때는 `/api` 리버스 프록시가 필요합니다.

## API 계약

**[Notion API 스키마 확정 — input/output docs](https://app.notion.com/p/3fd6b39941c2803f97bbe4d893075738)가 최우선 기준입니다.** 마지막 명세 대조일은 2026-10-27입니다. [동선 최적화 API](https://app.notion.com/p/3fd6b39941c2807aa445d3690556bd37)와 [26.10.25 회의](https://app.notion.com/p/4046b39941c280b3b609db1d0b15d53e)는 보조 근거입니다. 과거 AI 기획이나 백엔드 임의 확장을 합의된 계약으로 취급하지 않습니다.

| API | 프론트 처리 |
| --- | --- |
| `POST /api/routes/optimize` | Notion 입력 필드, 1부터 시작하는 `dayIndex`, 일차별 1–8개 장소. 실제 Google Place ID·좌표를 보내며 예제 ID는 차단. 응답은 요청 스냅샷의 Google Place ID로 매칭. |
| `POST /api/nearby/recommend` | `travelTypes`, `userRequest`, `stations`, `placeCount`, `radius`만 전송. `maxAnchorCount`는 제외. 명세의 단일 역 결과와 예제의 `results[]` 래퍼를 어댑터로 수용. |
| `POST /api/places/search` | 최신 백엔드 스키마에 맞춰 `userQuery`, `pageSize: 8`, 선택적 `locationBias`만 전송. 서버 고정값인 `languageCode`·`regionCode`는 보내지 않음. `textQueries: null, results: []`는 구체적인 검색어 입력 안내로 표시. |
| `POST /api/stations/resolve` | 일차별 `googlePlaceId`·`location`으로 계산 전에 기준역 조회. 1부터 시작하는 `dayIndex`, 하루 1–8곳. 장소별 `status`로 실패를 구분하고 성공한 역은 유지. [상세 계약](../backend/docs/stations.md). |

장소 검색은 2026-11-05 확인한 `main`(`478e697`) 및 `refactor/backend-config-and-errors`(`9a2e201`)의 요청·응답 형식과 대조했습니다. `clarificationQuestion`을 반환하는 이전 로컬 백엔드도 수용합니다. 기준역 API는 이 브랜치의 추가 계약이며 기존 Notion 확정 API와 구분합니다.

계산 전 추천은 Text Search와 기준역→Nearby 조회를 함께 실행하고 Google Place ID로 합칩니다. 이미 선택한 장소의 좌표 범위를 Text Search의 우선 검색 영역으로 사용하며 엄격한 지역 제한은 아닙니다. 장소가 없으면 지역·검색어·요청 문장으로 Text Search만 실행하고, 취향 태그가 없으면 Nearby를 생략합니다. 한 공급원이 실패하면 다른 결과와 경고를 표시하고, 모두 실패하면 이전 후보를 유지합니다. 각 일차의 검색어는 따로 보관합니다.

기준역을 찾지 못한 경우와 조회가 실패한 경우의 안내를 구분하며 조회 실패에는 재시도를 안내합니다. 기준역의 노선 정보가 있으면 Nearby 앵커 선택에도 전달합니다.

서버의 방문 시각·시간·요금·교통편을 사용합니다. 일본 시간 표시에서 날짜 변경을 유지합니다. 현재 백엔드 시간은 역 사이 이동 기준이며 접근 도보·체류·고정 일정은 포함하지 않습니다. 지도 선은 역 좌표를 잇는 참고선이며 실제 철도 궤적이 아닙니다. 예제 모드 선은 예시 방문 순서입니다. 주변 추천 거리는 직선거리입니다.

## 화면 동작

일정은 1–7일, 하루 장소는 1–8개입니다. 검색은 버튼이나 Enter로 실행하며 타이핑 중 호출·자동 재시도·자동 페이지 넘김은 없습니다. 여러 결과를 선택해 한 번에 추가할 수 있습니다. 추천을 추가하면 재계산 전에도 대기 목록과 마커로 표시되며 취소할 수 있습니다. 경로 계산과 추가 추천은 사용자가 명시적으로 실행합니다.

날짜나 단계를 바꾼 뒤 늦게 도착한 응답은 현재 선택을 덮어쓰지 않습니다. 계산 중 단계를 이동하면 응답 적용은 포기하지만 서버 요청은 완료될 수 있고, 다음 계산은 그 요청이 끝난 뒤 가능합니다. 오류 시 이전 결과를 유지합니다. 일수를 줄여 숨긴 날짜는 복원할 수 있습니다.

일정·검색 캐시(최대 20개)는 메모리에만 보관하며 새로고침·모드 전환 시 초기화됩니다. PC 기준은 1366×768·1920×1080이며 좁은 미리보기에서는 PC 화면을 축소합니다. 고급 마커용 `DEMO_MAP_ID`를 사용하며 제품 배포 시 자체 Map ID를 검토하세요.

일차·검색어·취향·장소가 바뀌면 진행 중인 초기 추천 요청을 취소하고 늦게 도착한 응답은 적용하지 않습니다. 실패가 섞인 추천은 캐시하지 않아 명시적으로 다시 시도할 수 있습니다. 한글 조합 입력 중에는 검색 입력 요소를 교체하지 않습니다.

## 코드와 문서

| 경로 | 용도 |
| --- | --- |
| `src/demo.ts`, `demo-model.ts`, `demo-render.ts` | PC 흐름·모델·렌더링 |
| `src/demo-api.ts` | 명세 DTO, 검증, HTTP 어댑터 |
| `src/demo-map*.ts` | 지속적으로 유지하는 SDK 지도·마커 |
| `src/main.ts`, `trip-*.ts`, `timetable*.ts` | 기존 시간표 프로토타입 |
| `src/route-*.ts`, `scripts/`, `fixtures/` | 별도 오프라인 경로 실험 |
| `tests/` | 모델·API·가상 DOM/SDK 회귀 검사 |
| [오프라인 경로 실험](docs/routing-prototypes.md) | CLI 사용법과 한계 |
| [AI 문서 모음](../docs/ai/README.md) | 과거 기획·설계·검토·출처 |

## 검증

```sh
npm test
npm run build
npm run demo:route
npm run demo:notion
npm run verify:route
```

테스트는 키 없이 제어된 데이터·가상 DOM/SDK로 실행하며 실제 외부 모델·지도 호출은 하지 않습니다. 실행 경로 검사에서는 다른 폴더명·작업 디렉터리에서 도우미의 실행 인자·환경 전달을 확인합니다. 자동 CLI 보고서는 `.local-run/reports/`에 저장되어 Git에서 제외됩니다. 빌드는 `dist/index.html`·`dist/demo.html`을 생성합니다.

실제 브라우저 화면·포커스·지도 클릭과 Google/Gemini·크롤러의 전체 연동은 별도 수동 검증이 필요합니다. 현재 기록은 [패키징 검토](../docs/ai/reviews/frontend-packaging.md)를 확인하세요.

백엔드와 함께 확인하려면 `backend/`에서 `poetry install` 후 `poetry run python -m unittest discover -s contract_checks -v`를 실행하세요. `web/npm ci`와 Node.js 22.20 이상이 있으면 실제 프론트 어댑터→Vite 프록시→FastAPI→최적화 엔진까지 검증하며 외부 공급자 호출만 대체합니다. Node 또는 Vite가 없으면 이 통합 검사만 건너뜁니다. 최신 검증 범위와 배포 후 확인 항목은 [기준역 API 통합 검토](../docs/ai/reviews/station-integration.md)에 기록합니다.
