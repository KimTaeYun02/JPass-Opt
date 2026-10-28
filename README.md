# JPass-Opt
경제보복 프로젝트

동선 최적화·역 주변 관광지 추천·자연어 장소 검색 API의 서버 실행, API 키 설정, Swagger 사용법과 **curl 요청 예시**는
[백엔드 실행 안내](backend/README.md)를 참고한다.

전체 API 계약과 처리 흐름, AI 연동용 OpenAPI JSON은 [API 참조 문서](backend/docs/README.md)에서 확인한다.

`crawl_route()`는 검색 결과에 표시된 모든 경로를 화면 순서대로 반환한다.

```json
{
  "routes": [
    {
      "time": 21,
      "fee": 490,
      "route": [
        {"station": "출발역", "line": "이동 노선"},
        {"station": "도착역", "line": null}
      ]
    },
    {
      "time": 23,
      "fee": 210,
      "route": [
        {"station": "출발역", "line": "다른 이동 노선"},
        {"station": "도착역", "line": null}
      ]
    }
  ]
}
```

`time`은 분, `fee`는 엔 단위이며, 파싱할 수 없으면 `null`이다.
각 역의 `line`은 다음 역까지 이동하는 노선이고, 도보는 `"도보"`로 표시한다.
첫 경로는 `result["routes"][0]`으로 접근한다.
최저 운임 경로는 운임이 있는 경로를 대상으로 선택할 수 있다.

```python
cheapest = min(
    (route for route in result["routes"] if route["fee"] is not None),
    key=lambda route: route["fee"],
    default=None,
)
```

## 백엔드 구조

API 서버, 입출력 모델, 최적화 서비스와 외부 조회 모듈은 `backend/app/`에서 관리한다.

```text
backend/
  app/
    main.py                  FastAPI 앱 생성·라우터 연결
    api/routes.py            POST /api/routes/optimize
    api/nearby.py            POST /api/nearby/recommend
    api/places.py            POST /api/places/search
    schemas/route.py         동선 최적화 요청·응답 모델과 입력 검증
    schemas/nearby.py        역 주변 관광지 추천 요청·응답 모델
    schemas/place_search.py  자연어 장소 검색·위치·LLM 결과 모델
    services/route_service.py 역 조회·경로 수집·최적화·응답 조립
    services/nearby_service.py 앵커 역 선택·주변 검색·추천 결과 조립
    services/place_search_service.py 검색어 재작성·최대 3개 Places 검색
    prompts/place_search.py  dedent로 정리한 검색어 재작성 프롬프트
    prompts/nearby_ranking.py 주변 장소 순위 선정 프롬프트
    clients/google_maps.py  Google Places 주변 검색·Text Search
    clients/heartrails.py   HeartRails 주변 역·환승역 조회
    llm/                     Gemini·GPT 호출 래퍼
    crawling/                브라우저 기반 경로 수집
    optimizer/               구간 데이터 검증·방문 순서 최적화·CLI
```

`POST /api/routes/optimize`에서 관광지 입력을 받아 전체 수집·최적화 서비스를 실행한다.
최적화에는 모든 방향별 구간이 필요하다.
`POST /api/nearby/recommend`는 Gemini로 후보 역 중 추천 거점을 선택하고 Google Places로 주변 관광지를 검색한다.
단일 태그는 검색 순서를 유지하고, 복수 태그의 후보가 2개 이상이면 같은 Gemini 클라이언트로 사용자 요청에 맞는 장소와 순서를 정한다.
후보가 요청 개수 이하여도 순위를 정하며, 0~1개일 때만 생략한다. LLM이 반환한 인덱스는 서버에서 검증하고 원래 장소 객체로 복원하며 API 응답 형식은 유지한다.
`POST /api/places/search`는 사용자 문장을 일본어 검색어로 증강·정규화해 Google Places를 최대 3회 조회한다.
검색 의도가 모호해 유용한 검색어를 만들 수 없으면 추가 질문 없이 `textQueries: null`, `results: []`를 HTTP `200`으로 반환하고 Places는 호출하지 않는다. 요청·응답과 수동 테스트는 [장소 검색 API 안내](backend/docs/text_search.md)를 참고한다.
백엔드 의존성은 `backend/pyproject.toml`과 `backend/poetry.lock`에서 관리한다.

최적화 엔진 CLI는 `backend/`에서 실행한다.

```sh
cd backend
poetry run python -m app.optimizer app/optimizer/examples/route-matrix.json --stations A B C
```

최적화 엔진의 상세 설명은 [optimizer README](backend/app/optimizer/README.md)를 참고한다.
주변 관광지 추천의 입력·출력과 처리 흐름은 [주변 추천 API 안내](backend/docs/nearby.md)를 참고한다.
GPT·Gemini 텍스트·구조화 응답 호출은 [LLM 래퍼 안내](backend/app/llm/README.md)를 참고한다.
