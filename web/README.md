# JPass-Opt 프론트

지도 중심 PC 여행 플래너입니다. 일차별 장소 입력 → 취향 관광지 추가 선택 → 최적 경로 확인 및 추가 추천 순서로 사용합니다. 기존 시간표 프로토타입도 함께 보존했습니다.

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

현재 `main`에는 FastAPI `backend/`가 없습니다. API 연동 대상은 아직 병합되지 않은 `feat/backend-api`입니다. 서버는 해당 브랜치의 실행 방법으로 별도 실행하세요. 프론트 자체 실행에는 형제 폴더나 Python이 필요하지 않습니다.

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

개발 중 `/api`는 Vite가 `JPASS_API_TARGET`으로 전달하며 기본값은 `http://127.0.0.1:8000`입니다. `.env.local` 변경 후 Vite를 재시작하세요. `-ApiTarget`은 해당 실행에만 적용됩니다. 빌드와 `npm run preview`에는 개발 프록시가 없으므로 배포할 때는 `/api` 리버스 프록시가 필요합니다.

## API 계약

**[Notion API 스키마 확정 — input/output docs](https://app.notion.com/p/3fd6b39941c2803f97bbe4d893075738)가 최우선 기준입니다.** 마지막 명세 대조일은 2026-10-27입니다. [동선 최적화 API](https://app.notion.com/p/3fd6b39941c2807aa445d3690556bd37)와 [26.10.25 회의](https://app.notion.com/p/4046b39941c280b3b609db1d0b15d53e)는 보조 근거입니다. 과거 AI 기획이나 백엔드 임의 확장을 합의된 계약으로 취급하지 않습니다.

| API | 프론트 처리 |
| --- | --- |
| `POST /api/routes/optimize` | Notion 입력 필드, 1부터 시작하는 `dayIndex`, 일차별 1–8개 장소. 실제 Google Place ID·좌표를 보내며 예제 ID는 차단. 응답은 요청 스냅샷의 Google Place ID로 매칭. |
| `POST /api/nearby/recommend` | `travelTypes`, `userRequest`, `stations`, `placeCount`, `radius`만 전송. `maxAnchorCount`는 제외. 명세의 단일 역 결과와 예제의 `results[]` 래퍼를 어댑터로 수용. |
| `POST /api/places/search` | **Notion 미확정인 임시 백엔드 계약**. `demo-api.ts`에 격리한 `userQuery`/옵션 어댑터로 검색·2단계 초기 후보·결과 화면 텍스트 추천 연결. 공항 전용 API는 구현하지 않음. |

서버의 방문 시각·시간·요금·교통편을 사용합니다. 일본 시간 표시에서 날짜 변경을 유지합니다. 현재 백엔드 시간은 역 사이 이동 기준이며 접근 도보·체류·고정 일정은 포함하지 않습니다. 지도 선은 역 좌표를 잇는 참고선이며 실제 철도 궤적이 아닙니다. 예제 모드 선은 예시 방문 순서입니다. 주변 추천 거리는 직선거리입니다.

## 화면 동작

일정은 1–7일, 하루 장소는 1–8개입니다. 검색은 버튼이나 Enter로 실행하며 타이핑 중 호출·자동 재시도·자동 페이지 넘김은 없습니다. 여러 결과를 선택해 한 번에 추가할 수 있습니다. 추천을 추가하면 재계산 전에도 대기 목록과 마커로 표시되며 취소할 수 있습니다. 경로 계산과 추가 추천은 사용자가 명시적으로 실행합니다.

날짜나 단계를 바꾼 뒤 늦게 도착한 응답은 현재 선택을 덮어쓰지 않습니다. 계산 중 단계를 이동하면 응답 적용은 포기하지만 서버 요청은 완료될 수 있고, 다음 계산은 그 요청이 끝난 뒤 가능합니다. 오류 시 이전 결과를 유지합니다. 일수를 줄여 숨긴 날짜는 복원할 수 있습니다.

일정·검색 캐시(최대 20개)는 메모리에만 보관하며 새로고침·모드 전환 시 초기화됩니다. PC 기준은 1366×768·1920×1080이며 좁은 미리보기에서는 PC 화면을 축소합니다. 고급 마커용 `DEMO_MAP_ID`를 사용하며 제품 배포 시 자체 Map ID를 검토하세요.

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
