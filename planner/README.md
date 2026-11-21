# 저호출 동선 최적화 v2

v2는 별도 FastAPI 앱과 CLI에서 합성 시간표 또는 저장된 경로 응답을 평가한다. 방문 순서는 관광지 좌표와 일정 제약으로 좁히고, 선택한 순서에 필요한 철도 구간만 체류와 도보를 반영한 준비 시각에 평가한다. 기존 팀 백엔드는 v1 경로를 제공하며, v2의 라이브 모드는 비활성 상태다.

## 재현

저장소 루트에서 Python 3.12 가상환경을 활성화한 뒤 실행한다.

```powershell
python -m pip install -r planner/requirements.txt
python -m planner.cli
python -m unittest discover -s planner/tests -v
python -m uvicorn planner.app:app --host 127.0.0.1 --port 8010 --workers 1
```

서버는 `GET /api/v2/health`, `POST /api/v2/sessions`, `POST /api/v2/routes/plan`을 제공한다. 합성 요청 예시는 [eight-node-input.json](../sample/low-call/eight-node-input.json)에 있다. 서버가 발급한 세션 ID를 넣어 요청한다. 서버 원장은 로컬 `tmp/low-call-ledger.sqlite3`에 저장된다.

기록된 시각표와 장소별 체류를 재평가하려면 새 출력 경로를 지정한다.

```powershell
python -m planner.collect_sequential --run sample/low-call/divergent-revalidation/run-20261119 --output tmp/v2-replay
```

[TypeScript 클라이언트](web/src/low-call-api.ts)는 독립적으로 이전했다. Node.js 22.18 이상에서 세 계약 테스트 파일을 실행할 수 있다.

```powershell
node --test planner/web/tests/low-call-api.test.mjs planner/web/tests/low-call-policy.test.mjs planner/web/tests/low-call-replay.test.mjs
```

[도쿄 재생 비교](../sample/low-call/divergent-revalidation/run-20261119/analysis/comparison.json), [방문 순서 벤치마크](../sample/low-call/order-policy/result.json), [패스 평가 벤치마크](../sample/low-call/policy-study/result.json)는 시험 입력과 비교 결과를 담고 있다. 원본 캡처와 해시가 일치하도록 JSON의 줄바꿈을 보존한다.

## 도보·버스 실험

`options.allowMultimodal: true`로 구간별 탑승역·하차역을 고른다. 직선거리 800m 이내이고 검증 자료에서 보행이 12분 이하인 구간은 다음 방문 조건을 만족하는 경우 우선 걷는다. 나머지 구간은 기본 철도를 평가한다. 도보가 길거나 이동이 오래 걸리거나, 환승이 두 번 이상이거나, 다음 예약에 늦거나, 첫 조회가 실패하면 대체역을 비교한다. 검증할 버스 연결이 있으면 직통 버스나 버스를 타고 다른 역에 접근하는 경로를 계산한다. 다음 관광지의 방문 시작·종료 시각이 같다면 확인된 운임이 싼 후보를 고른다.

직통 도보는 철도 조회를 생략한다. 버스 검색과 대체역 철도는 남은 기본 철도 구간의 몫을 예약한 뒤 같은 상한에서 사용하며, 계획당 버스 검색은 최대 2회다. `railAttempts`는 이 공통 예산의 사용량이고, 옵션을 켠 응답에는 `railOnlyAttempts`와 `busAttempts`가 있다. 구간별 `steps`에는 도보·대기·버스·철도가 들어간다. 버스 시간 근거가 없으면 `busStatus: unknown`으로 표시하고 Google 지도 확인 링크를 제공한다. 확인된 버스 운임만 합산하며 미확인 운임은 `null`이다.

아래 명령은 합성 데이터로 직통 도보, 직통 버스, 철도 구간을 한 일정에서 실행한다.

```powershell
python -X utf8 -m planner.cli --input planner/fixtures/multimodal-input.json --mobility planner/fixtures/multimodal-mobility.json
```

HTTP 앱은 `JPASS_MOBILITY_FILE`로 같은 형식의 자료를 받는다. 기존 철도 응답과 함께 오프라인에서 평가하며, `planner.replay_cli`에서는 `--mobility`로 재생 자료를 전달한다. 합성용 `source: fixture`와 실측 재생용 `source: replay`를 구분한다.

### 재생 자료 만들기

1. 자체 Valhalla의 보행 프로필로 방향별 `origin`·`destination` 좌표 쌍을 계산한다. `python -m planner.prepare_walks --pairs pairs.json --graph-version <지도 버전> --output walks.json`은 로컬 `127.0.0.1:8002`에만 연결한다. 기존 재생 자료에 더하려면 `--bundle`을 지정한다. [Valhalla 보행 경로 명세](https://valhalla.github.io/valhalla/api/route/api-reference/)
2. 버스 연결의 양쪽 정류장에 운영사, 전체 정류장명, Ekispert 코드와 좌표를 넣는다. 버스에서 철도로 갈아타는 연결에는 하차 후 걸어서 연결되는 역의 ID와 좌표도 넣는다.
3. 기존 `planner.capture`로 정류장에 도착하는 시각의 검색 결과를 한 번 수집한다. 결과 요약에 약칭이 표시되는 경우 `--calendar-names`와 `--stop-codes`를 함께 지정한다. `python -m planner.prepare_bus --bundle walks.json --capture bus-capture.json --verified-line "<화면에서 버스로 확인한 노선>" --output mobility.json`으로 가져온다. 정확히 같은 방향과 준비 시각의 저장 검색 결과를 평가에 사용한다. GTFS와 유료 API는 필요 없다.

2026년 11월 21일 교토 시영버스 금각사도 → 기타오지 터미널을 새로 검색했다. 운영사와 정류장 코드 `42705 → 42682`가 일치했고, 11월 22일 13:55 준비 시각의 결과 4개에서 날짜와 시각을 확인했다. 검색 제출 1회에 브라우저 HTTP 요청 522개가 관측됐다.

교토 OSM 그래프를 로컬 Valhalla 3.9.3에 올려 양쪽 도보를 계산하고, 저장한 204번을 v2 API의 재생 요청으로 불러왔다. 금각사 주변 대표점 13:49 출발 → 도보 6분 → 대기 5분 → 버스 12분 → 도보 15분 → 교토 식물원 주변 대표점 14:27 도착으로 총 38분·230엔이었다. 재생 중 외부 요청은 0회였다. 정확한 관광지 입구 대신 검증용 대표 좌표를 사용했다. 출발을 1분 늦추면 버스 기록 미확인으로 처리했고, 새 크롤러 검색은 별도 수집 명령으로 실행한다. [© OpenStreetMap 기여자](https://www.openstreetmap.org/copyright)
