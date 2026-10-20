# JPass-Opt
경제보복 프로젝트

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

크롤러, 외부 API 클라이언트, 최적화 엔진은 `backend/app/`에서 관리한다.

```text
backend/
  app/
    clients/google_maps.py  Google 관광지 좌표 조회
    clients/heartrails.py   HeartRails 주변 역 조회
    crawling/                브라우저 기반 경로 수집
    optimizer/               구간 데이터 검증·방문 순서 최적화·CLI
  tests/test_optimizer.py    최적화 엔진 검증
```

현재 API 서버와 전체 수집·최적화 서비스는 아직 구현하지 않았다.
최적화에는 모든 방향별 구간이 필요하다.
백엔드 의존성은 `backend/pyproject.toml`과 `backend/poetry.lock`에서 관리한다.

최적화 CLI와 테스트는 `backend/`에서 실행한다. 주변 검색 테스트는 루트 `.env`의
`GOOGLE_PLACE_API_KEY`를 사용한다.

```sh
cd backend
poetry run python -m app.optimizer app/optimizer/examples/route-matrix.json --stations A B C
poetry run python -m unittest discover -s tests -p 'test_optimizer.py' -v
poetry run python -m tests.test_nearby_search
```

최적화 엔진의 상세 설명은 [optimizer README](backend/app/optimizer/README.md)를 참고한다.
주변 장소 검색의 반환 필드와 예시는 [주변 검색 안내](backend/docs/nearby_search.md)를 참고한다.
GPT·Gemini 텍스트·구조화 응답 호출은 [LLM 래퍼 안내](backend/app/llm/README.md)를 참고한다.
