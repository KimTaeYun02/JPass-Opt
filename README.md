# JPass-Opt
경제보복 프로젝트

## 프론트 데모

`web/`에서 Node.js 22.20 이상으로 `npm ci`, `npm run dev`를 실행하세요.
`http://127.0.0.1:5173/demo.html?mode=example`은 백엔드·키 없이 예제로 실행됩니다.
`/demo.html`의 API 모드는 아직 병합되지 않은 `feat/backend-api` 서버가 별도로 필요합니다.

[프론트 실행·API 설정](web/README.md) · [오프라인 경로 실험](web/docs/routing-prototypes.md) · [AI 기획·검토 문서](docs/ai/README.md)

## 기존 경로 크롤러

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

`sample` 디렉터리에서 `xvfb-run -a poetry run python test.py`를 실행하면
검색 결과가 `sample/route_result.json`에 저장된다. 재실행하면 파일을 덮어쓴다.
`search_route(..., output_path=Path("다른/결과.json"))`으로 저장 위치를 변경할 수 있다.

`main.py`도 장소명 목록을 입력받는다. `TEST_SAMPLE_1`, `TEST_SAMPLE_2`를
수정하거나 `await main(["네즈신사", "도쿄돔", "메이지 신궁"])`처럼 호출한다.
`test.py`와 동일하게 `GOOGLE_GEOCODING_API_KEY`를 사용해 장소를 좌표로
변환하고 주변 역을 검색한다. `sample` 디렉터리에서
`xvfb-run -a poetry run python main.py`로 실행하면 기본 저장 위치는
`sample/result/TEST_SAMPLE_1.json`이다.
