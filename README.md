# JPass-Opt
경제보복 프로젝트

동선 최적화 API의 서버 실행, Swagger 사용법과 **curl 요청 예시**는
[백엔드 실행 안내](backend/README.md)를 참고한다.

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
    schemas/route.py         요청·응답 모델과 입력 검증
    services/route_service.py 역 조회·경로 수집·최적화·응답 조립
    clients/google_maps.py  Google 관광지 좌표 조회
    clients/heartrails.py   HeartRails 주변 역·환승역 조회
    crawling/                브라우저 기반 경로 수집
    optimizer/               구간 데이터 검증·방문 순서 최적화·CLI
```

`POST /api/routes/optimize`에서 관광지 입력을 받아 전체 수집·최적화 서비스를 실행한다.
최적화에는 모든 방향별 구간이 필요하다.
백엔드 의존성은 `backend/pyproject.toml`과 `backend/poetry.lock`에서 관리한다.

최적화 엔진 CLI는 `backend/`에서 실행한다.

```sh
cd backend
poetry run python -m app.optimizer app/optimizer/examples/route-matrix.json --stations A B C
```

최적화 엔진의 상세 설명은 [optimizer README](backend/app/optimizer/README.md)를 참고한다.
주변 장소 검색의 반환 필드와 예시는 [주변 검색 안내](backend/docs/nearby_search.md)를 참고한다.
GPT·Gemini 텍스트·구조화 응답 호출은 [LLM 래퍼 안내](backend/app/llm/README.md)를 참고한다.
