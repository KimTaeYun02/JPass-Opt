# 방문 순서 최적화

`backend/app/optimizer/`은 Python 표준 라이브러리만 사용하는 최적화 엔진이다.
기존 TypeScript Held–Karp DP를 Python으로 옮겼으므로 Node.js가 필요 없다.
크롤링으로 수집해 둔 JSON을 입력받아 결과를 반환하는 함수와 CLI만 제공한다.
서버 실행이나 추가 패키지 설치 없이 사용할 수 있다.

각 구간의 고정 `time` 합계를 최소화하고 동률이면 `fee`, 그다음 방문 ID의
JavaScript UTF-16 정렬 순서로 비교한다. 시작·마지막 역은 자유이고 출발역으로
돌아오지 않는다. 패스, 관광지 접근 도보, 체류시간, 최초 열차 대기시간을 추가하지
않으며 크롤러 값에 포함된 구간 내부 환승·대기와 운임은 그대로 사용한다.

- 고유한 역 2~8개, 상위 방문 순서 1~3개를 지원한다.
- 전체 방향별 구간 `n(n-1)`개가 필요하다. 5개 역이면 20개 구간이다.
- `selection="first"`가 기본이다. 이전 검증과 같이 각 구간의 `routes[0]`을 쓴다.
- `selection="fastest"`는 반환된 후보 중 시간·운임·후보 인덱스 순으로 하나를 고른다.
  수집된 후보와 고정 구간 비용 안에서 최적성을 보장한다.
- `first`의 선택 경로가 `null` 비용이면 오류다. `fastest`는 비용이 없거나 `null`인
  후보를 제외하고 `skippedRoutes`에 알린다. 사용 가능한 후보가 없는 구간은 오류다.
  다른 형식 오류와 누락된 방향, 중복 구간을 추정값으로 채우지 않는다.
- `time`/`fee`는 bool·실수가 아닌 정수이고 각 값과 가능한 경로 합계가
  JavaScript 안전한 정수 범위 `0..2**53-1` 안에 있어야 한다.
- 크롤러 입력에서는 역 이름이 방문 ID다. 같은 역에 연결된 여러 관광지는 이
  인터페이스에서 합치거나 중복 입력하지 않는다. 그런 경우 별도 장소 ID와
  역 매핑을 지정하는 저수준 quote 인터페이스가 필요하다.

### Python에서 호출

`backend/`에서 실행한다. 크롤러와 웹 서버를 설치하지 않아도 이 코드는 동작한다.

```python
import json
from pathlib import Path
from app.optimizer import optimize_matrix

matrix = json.loads(
    Path("app/optimizer/examples/route-matrix.json").read_text(encoding="utf-8")
)
result = optimize_matrix(["A", "B", "C"], matrix, selection="first", limit=1)
print(result["plans"][0]["order"])  # ['A', 'B', 'C']
```

`plans[]`는 `order`, `time`, `fee`, `legs`, `algorithm`, `dpStates`,
`dpTransitions`, `evaluatedOrders`를 반환한다. `legs[]`는 `from`, `to`,
`routeIndex`(0부터 시작), 선택한 원본 `result`를 포함한다.
입력과 결과 객체를 공유하지 않으므로 결과를 편집해도 원본은 바뀌지 않는다.

### JSON 파일로 실행

Python 3.14 이상을 사용한다. 아래 명령은 모두 `backend/` 기준이다.

```sh
python -m app.optimizer app/optimizer/examples/route-matrix.json --stations A B C --limit 3 --output tests/optimized.json
```

실행하면 방문 순서(`order`), 총 소요시간(`time`, 분), 총 운임(`fee`, 엔),
구간별 선택 경로(`legs`)가 터미널에 출력되고 `--output` 경로에도 저장된다.
예제의 최적 결과는 `A → B → C`, **15분·300엔**이다.

입력 JSON 구조는 다음과 같다. 아래는 한 구간의 형태이며 예제 파일에는
A·B·C 사이의 전체 6개 방향 구간이 들어 있다.

```json
[
  {
    "departure": "A",
    "arrival": "B",
    "routes": [
      {
        "time": 10,
        "fee": 200,
        "route": [
          {"station": "A", "line": "노선명"},
          {"station": "B", "line": null}
        ]
      }
    ]
  }
]
```

입력은 기존 크롤러의 `[{"departure": ..., "arrival": ..., "routes": [...]}]`
배열이다. 파일 대신 `-`를 지정하면 표준 입력으로 받는다. 표준 입출력은 UTF-8이며
성공하면 JSON과 종료 코드 0, 잘못된 입력이면 표준 오류와 종료 코드 2를 반환한다.
최적화는 외부 경로 검색을 호출하지 않는다.

### 검증

```sh
python -m unittest discover -s tests -p 'test_optimizer.py' -v
poetry run ruff check app tests
```

독립적인 전체 순열 계산으로 무작위 비대칭·동률 비용표의 상위 1~3개 결과를
대조한다. UTF-16 정렬, 정수 범위 초과, 누락·중복·null, 원본 보존, CLI 실행을 검증한다.
실제 수집 JSON은 저장소에 포함하지 않는다. `JPASS_ROUTE_MATRIX` 환경변수로
20구간 JSON 경로를 지정하면 이전의 **143분·2,374엔** 결과 재현 테스트도 실행한다.

## 파일별 역할과 실행 흐름

```text
backend/app/optimizer/
  __init__.py                공개 함수 제공
  __main__.py                python -m app.optimizer 진입점
  cli.py                     JSON 파일 읽기, 옵션 처리, 결과 출력
  matrix.py                  크롤러 스키마 검증 및 구간 후보 선택
  optimizer.py               Held–Karp DP와 결과 복원
  examples/route-matrix.json  실행 가능한 3개 역 예제
```

테스트는 `backend/tests/test_optimizer.py`에 있다.

실행 순서는 `__main__.py → cli.main() → optimize_matrix() →
optimize_visit_orders() → _held_karp()`다. 계산 결과는 반대 순서로 반환된다.

`matrix.py`는 각 출발·도착 쌍의 `routes`에서 후보 하나를 선택하고 내부
`{from, to, result}` 형태로 바꾼다. `first`는 첫 후보, `fastest`는 시간과
운임 순으로 가장 작은 후보를 선택한다. 입력의 `route`에 포함된 환승역은
방문지로 추가하지 않고 구간 상세 정보로 보존한다.

`optimizer.py`의 DP 상태는 `(방문한 역의 집합, 마지막 역)`이다. 방문 집합은
비트마스크(각 비트로 역 방문 여부를 나타내는 정수)로 저장한다. 같은 상태의
부분 동선은 이후 선택지가 같으므로 상위 `limit`개만 유지한다. 아직 방문하지
않은 역으로 이동하는 시간과 운임을 누적하고 모든 역을 방문한 상태에서
상위 결과를 모은다. `previous` 연결을 거꾸로 따라가 방문 순서를 복원한다.

비교 기준은 총 시간, 총 운임, 방문 ID 순서다. 운임은 시간이 같을 때의
동률 해소 기준이며 현재 최저요금 전용·가중합·패스 할인 계산은 구현하지 않았다.
`limit=3`이면 서로 다른 방문 순서를 최대 3개 반환한다.

`__init__.py`는 외부 코드가 `from app.optimizer import optimize_matrix`로
호출하도록 공개 함수를 모은다. 네트워크 요청이나 크롤러 실행은 하지 않는다.
