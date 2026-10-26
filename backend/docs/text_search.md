# 자연어 장소 검색 API

`POST /api/places/search`는 사용자 문장을 Gemini로 일본어 검색어로 바꾼 뒤 Google Places Text Search (New)로 장소를 조회한다. 기존 FastAPI 앱에서 동선 최적화·주변 관광지 추천 API와 함께 사용할 수 있다.

## 실행과 호출

Google Cloud 프로젝트에서 결제를 연결하고 **Places API (New)**를 활성화한다. 저장소 루트 `.env`에 서버용 키를 설정한다.

```dotenv
GOOGLE_PLACE_API_KEY=발급받은_Google_Places_API_키
GEMINI_API_KEY=발급받은_Gemini_API_키
GEMINI_MODEL=gemini-3.5-flash-lite
```

`GEMINI_MODEL`은 선택 설정이며 생략하면 위 모델을 사용한다. 앱은 시작 시 루트 `.env`를 읽으며 기존 환경 변수는 덮어쓰지 않는다. 키는 서버에서 관리하고 요청 JSON에는 넣지 않는다.

```bash
cd backend
poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

장소 검색에는 브라우저가 필요 없다. 동선 최적화까지 실행하려면 [백엔드 실행 안내](../README.md)의 Xvfb 실행 명령을 사용한다.

`http://localhost:8000/docs`에서 `POST /api/places/search`를 펼쳐 **Try it out**으로 요청할 수 있다. Codespaces에서는 전달된 8000번 포트 주소 뒤에 `/docs`를 붙인다.

```bash
curl -sS -X POST http://localhost:8000/api/places/search \
  -H 'Content-Type: application/json' \
  --data-binary '{"userQuery":"후지사와역 근처 인기 있는 야키니쿠집 찾아줘"}'
```

## 요청

`userQuery`만 필수이며 나머지 값은 기본값을 사용한다.

| 필드 | 타입·기본값 | 의미 |
| --- | --- | --- |
| `userQuery` | string, 필수 | 사용자 검색 문장. 빈 문자열·공백만 있는 값은 불가 |
| `languageCode` | string, `"ko"` | Google 장소 응답의 선호 언어 |
| `regionCode` | string, `"JP"` | 두 자리 지역 코드. 해당 국가만 검색하도록 제한하는 조건은 아님 |
| `pageSize` | integer, `20` | 검색어 하나당 결과 수. `1~20` |
| `rankPreference` | string, `"RELEVANCE"` | 관련성순 `"RELEVANCE"` 또는 거리순 `"DISTANCE"` |
| `locationBias` | object 또는 null, `null` | 원·사각형 주변을 우선 검색. 영역 밖 결과도 가능 |

위치를 우선 검색하려면 다음처럼 보낸다.

```json
{
  "userQuery": "후지사와역 근처 인기 있는 야키니쿠집 찾아줘",
  "languageCode": "ko",
  "regionCode": "JP",
  "pageSize": 20,
  "rankPreference": "RELEVANCE",
  "locationBias": {
    "circle": {
      "center": {"latitude": 35.3389, "longitude": 139.4876},
      "radius": 1000.0
    }
  }
}
```

`locationBias`는 `circle` 또는 `rectangle` 중 하나를 사용한다. 원의 `radius`는 미터 단위이며, 사각형은 `rectangle.low`와 `rectangle.high`에 위도·경도를 넣는다. 검색어에 지역이 명시되면 위치 우선 조건보다 해당 지역이 우선할 수 있다.

`openNow`는 서버에서 항상 `false`로 전송한다. 반환 필드도 서버의 `FIELD_MASK`로 고정한다. 이 API는 첫 페이지만 검색하므로 `pageToken`을 받지 않는다. 재작성된 검색어마다 페이지 토큰이 다르므로 한 토큰을 여러 검색어에 공통 적용할 수 없다.

## 응답과 호출 횟수

아래 검색어와 장소는 구조 설명용 예시다.

```json
{
  "userQuery": "후지사와역 근처 인기 있는 야키니쿠집 찾아줘",
  "textQueries": ["藤沢駅 焼肉 人気店"],
  "clarificationQuestion": null,
  "results": [
    {
      "textQuery": "藤沢駅 焼肉 人気店",
      "response": {
        "places": [
          {
            "id": "example-place-id",
            "displayName": {"text": "예시 야키니쿠점", "languageCode": "ko"},
            "formattedAddress": "일본 가나가와현 후지사와시 예시 주소",
            "location": {"latitude": 35.3389, "longitude": 139.4876},
            "primaryType": "restaurant",
            "types": ["restaurant", "food"],
            "googleMapsUri": "https://maps.google.com/?cid=EXAMPLE"
          }
        ],
        "nextPageToken": "example-next-page-token"
      }
    }
  ]
}
```

| 필드 | 의미 |
| --- | --- |
| `userQuery` | 입력한 사용자 문장 |
| `textQueries` | 중복을 제거한 일본어 검색어 `1~3`개 또는 null |
| `clarificationQuestion` | 추가로 확인할 질문 또는 null |
| `results` | 검색어별 결과. 각 `response`에는 Google 원본 JSON을 보존 |

`textQueries`와 `clarificationQuestion` 중 하나만 `null`이 아닌 구조다. 확인 질문이 반환되면 `textQueries`는 `null`, `results`는 `[]`이며 Google Places를 호출하지 않는다. 유효한 확인 질문은 오류가 아닌 `200` 응답이다.

서비스는 Gemini를 한 번 호출하고, 검색어 목록의 스키마와 실제 반복문의 `queries[:3]`으로 **요청 한 번당 Places 호출을 최대 3회**로 제한한다. 각 검색어의 첫 페이지만 조회하며 자동 페이지 조회·재시도·추가 LLM 시도는 없다. 검색어 간 장소를 합치거나 중복 제거하지 않는다.

기본 반환 필드는 장소 ID, 이름, 주소, 좌표, 대표 유형·유형 목록, Google 지도 링크, 다음 페이지 토큰이다. `places`는 결과가 없으면 생략될 수 있어 `response.get("places", [])`로 읽는다. Google에 데이터가 없는 필드도 생략될 수 있다. 화면에 표시할 이름은 `displayName.text`이며, `name`은 `places/PLACE_ID` 형태의 리소스 이름이다.

반환 필드를 바꾸려면 `app/clients/google_maps.py`의 `search_text()` 내부 `FIELD_MASK`를 수정한다. 예를 들어 평점은 `,places.rating,places.userRatingCount`를 추가한다. `FIELD_MASK = "*"`로 전체 필드를 요청할 수도 있으며 선택 필드에 따라 Google 과금 등급이 달라진다.

## 코드 구조

```text
app/
├── api/places.py                   # HTTP 요청·응답과 오류 상태 코드
├── schemas/place_search.py         # API 입력·출력, 위치 조건, LLM 결과 검증
├── services/place_search_service.py # Gemini 재작성 → 최대 3개 Places 검색
├── prompts/place_search.py         # textwrap.dedent로 정리한 영어 프롬프트
└── clients/google_maps.py          # Google Text Search 요청과 원본 JSON 반환
tests/
├── test_text_search.py             # 요청 모델을 만들어 실제 서비스를 호출하는 수동 실행
├── test_text_search_flow.py        # 재작성 결과·호출 횟수 제한 검증
└── test_places_api.py              # HTTP 입력·응답·오류 검증
```

요청은 `api/places.py → 요청 모델 검증 → place_search_service.py → Gemini·Google Places → 응답 모델` 순서로 처리한다. 프롬프트, 요청 검증, 검색 흐름, 외부 호출을 각 모듈에서 수정할 수 있다. 앱은 테스트 모듈을 가져오지 않으며 수동 테스트가 운영 서비스를 호출한다.

서버를 켜지 않고 실제 검색 흐름을 확인하려면 `tests/test_text_search.py`의 요청값을 수정하고 실행한다. 결과는 터미널과 `tests/text_search_results.json`에 저장하며, 이 실행도 외부 API를 사용한다.

```bash
cd backend
poetry run python -m tests.test_text_search
```

외부 API를 호출하지 않는 검증은 다음처럼 실행한다.

```bash
poetry run python -m unittest tests.test_text_search_flow tests.test_places_api
```

Google만 직접 호출하거나 수동으로 다음 페이지를 조회할 때는 내부 클라이언트의 `search_text()`를 사용한다. 같은 `text_query`와 검색 조건을 유지하고 해당 응답의 `nextPageToken`을 `page_token`으로 전달한다. 자연어 검색 API는 페이지 토큰을 재사용하지 않는다.

## 오류 응답

| HTTP 상태 | 의미 |
| --- | --- |
| `422` | 검색 문장·결과 수·정렬·위치 조건 등 요청값 오류 |
| `429` | Gemini 또는 Google Places의 사용량·호출 제한 |
| `502` | 외부 API 실패 또는 유효하지 않은 LLM 응답 |
| `503` | 서버 API 키 미설정 |
| `504` | 외부 API 응답 시간 초과 |

입력 검증 오류는 `{"detail": [...]}`, 서비스 오류는 `{"detail": "설명"}` 형식이다. 서비스 오류에는 외부 응답 본문·키 등 내부 정보를 노출하지 않는다.

공식 문서: [Text Search 가이드](https://developers.google.com/maps/documentation/places/web-service/text-search?hl=ko), [요청·응답 REST 스펙](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places/searchText), [Place 응답 필드](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places).
