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

`sample/main.py`가 저장하는 JSON은 구간별
`{"departure": "출발역", "arrival": "도착역", "routes": [...]}` 객체의 배열이다.
기존 결과 파일은 스크립트를 다시 실행하면 새 구조로 저장된다.

저장소 루트에서 `xvfb-run -a sample/.venv/bin/python -m sample.test`를 실행하면
검색 결과가 `sample/route_result.json`에 저장된다. 재실행하면 파일을 덮어쓴다.
`search_route(..., output_path=Path("다른/결과.json"))`으로 저장 위치를 변경할 수 있다.

`main.py`도 장소명 목록을 입력받는다. `TEST_SAMPLE_1`, `TEST_SAMPLE_2`를
수정하거나 `await main(["네즈신사", "도쿄돔", "메이지 신궁"])`처럼 호출한다.
`test.py`와 동일하게 `GOOGLE_GEOCODING_API_KEY`를 사용해 장소를 좌표로
변환하고 주변 역을 검색한다. 저장소 루트에서
`xvfb-run -a sample/.venv/bin/python -m sample.main`으로 실행하면 기본 저장 위치는
`sample/result/TEST_SAMPLE_1.json`이다.

## 백엔드 구조

크롤러, 외부 API 클라이언트, 최적화 엔진은 `backend/app/`에서 관리한다.

```text
backend/
  app/
    clients/location_api.py  관광지 좌표·주변 역 조회
    crawling/                브라우저 기반 경로 수집
    optimizer/               구간 데이터 검증·방문 순서 최적화·CLI
  tests/test_optimizer.py    최적화 엔진 검증
sample/
  main.py                    관광지 목록으로 인접 구간 수집 예제
  test.py                    두 관광지 사이 경로 검색 예제
```

현재 API 서버와 전체 수집·최적화 서비스는 아직 구현하지 않았다.
`sample/main.py`는 인접 구간만 수집하며, 최적화에는 모든 방향별 구간이 필요하다.
샘플 실행은 저장소 루트를 기준으로 하며, 루트 `.env`의
`GOOGLE_GEOCODING_API_KEY`를 사용한다. 의존성 설정은 기존
`sample/pyproject.toml`을 유지했고 백엔드용 TOML은 별도로 작성할 수 있다.

최적화 CLI와 테스트도 저장소 루트에서 실행한다.

```sh
python -m backend.app.optimizer backend/app/optimizer/examples/route-matrix.json --stations A B C
python -m unittest discover -s backend/tests -v
```

최적화 엔진의 상세 설명은 [optimizer README](backend/app/optimizer/README.md)를 참고한다.
