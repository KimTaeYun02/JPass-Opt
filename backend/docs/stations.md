# 계산 전 기준역 조회

`POST /api/stations/resolve`는 관광지별 Google Place ID와 좌표를 받아 기존 HeartRails 클라이언트로 가장 가까운 역 하나를 조회한다. 동선 계산·경로 크롤링·Google Places·Gemini 호출은 하지 않으며 API 키도 필요하지 않다. 프론트는 응답의 역을 `/api/nearby/recommend`에 보내고 Text Search 결과와 합칠 수 있다.

## 요청

```json
{
  "days": [{
    "dayIndex": 1,
    "places": [{
      "googlePlaceId": "selected-google-place-id",
      "location": {"lat": 35.6812, "lng": 139.7671}
    }]
  }]
}
```

- `days`: 1–7개. 일부 일차만 요청할 수 있으며 `dayIndex`는 1–7의 중복 없는 정수다.
- 일차별 `places`: 1–8개. 같은 일차의 `googlePlaceId`는 중복할 수 없고 공백뿐인 ID도 거절한다. 다른 일차의 같은 ID는 허용한다.
- `location`: 유한한 숫자 `lat`(-90–90), `lng`(-180–180). 문자열·불리언은 거절한다.
- 정의되지 않은 입력 필드와 위 조건을 벗어난 입력은 HTTP `422`로 반환하고 역 조회를 실행하지 않는다.

## 응답

```json
{
  "days": [{
    "dayIndex": 1,
    "places": [{
      "googlePlaceId": "selected-google-place-id",
      "status": "ok",
      "station": {
        "name": "東京",
        "location": {"lat": 35.6812, "lng": 139.7671},
        "line": "JR山手線"
      },
      "reason": null
    }]
  }]
}
```

유효한 요청은 HTTP `200`이며 `days`·`places`의 순서와 ID를 유지한다. 같은 역을 공유하는 관광지도 별도 항목으로 남긴다. `line`은 없으면 `null`이다. 프론트는 배열 순서에 의존하지 않고 Google Place ID로 대응시킨다.

| `status` | `station` | `reason` | 의미 |
| --- | --- | --- | --- |
| `ok` | 역 객체 | `null` | 최근접 역 조회 성공 |
| `not_found` | `null` | `null` | 역 목록이 비어 있음 |
| `error` | `null` | `timeout` | 외부 요청 시간 초과 |
| `error` | `null` | `unavailable` | 외부 통신 또는 예상하지 못한 클라이언트 오류 |
| `error` | `null` | `invalid_response` | 역 이름·좌표 등 외부 응답 형식 오류 |

장소 하나가 실패해도 나머지 결과는 반환한다. 원본 공급자 오류 메시지·인증 정보는 응답에 포함하지 않는다. 정확히 같은 좌표는 요청 안에서 한 번만 조회하며 다음 요청까지 캐시하지 않는다. 좌표를 반올림해서 합치지 않는다.

## 호출과 검증

서버 실행 후 저장소 루트에서:

```sh
python backend/scripts/test_api_http.py --api stations --base-url http://localhost:8000
```

예시 파일의 Place ID는 설명용 문자열이며 실제 Google Place ID를 검증하는 호출은 하지 않는다. 실제 일정에는 검색 결과의 ID·좌표를 사용한다. 위 명령은 HeartRails를 실제 호출한다.

외부 호출 없는 자동 검사는 `backend/`에서:

```sh
poetry run python -m unittest discover -s contract_checks -v
```

역 조회는 동기 핸들러의 작업 스레드에서 순차 실행하며 기존 HeartRails 클라이언트의 호출별 제한은 10초다. 전체 요청 deadline은 없고 최대 56개 좌표가 허용된다. 프론트는 현재 선택한 일차(최대 8곳)만 요청한다. 기본 프론트 대기는 120초이며 취소해도 서버에서 시작된 조회가 즉시 중단되는 것은 아니다.
