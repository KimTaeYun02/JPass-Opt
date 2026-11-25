# 관광지 ↔ 역 접근 이동과 기존 API 재사용 조사

확인일: 2026-11-09. 로컬 `feat/frontend-demo`, HEAD `fa6b9c9`와 현재 작업 트리 기준이다. 원격 최신 여부를 새로 확인한 조사는 아니다. 운영 코드·API 계약·키·배포 설정은 변경하지 않았다. 사용자가 제공한 주간 회의록의 관광지 ↔ 역 이동 개선을 조사했다. 아래 제안은 Notion input/output 계약에 반영되기 전의 검토안이다.

## 판단

기존 역 조회, 역 간 경로 수집, 방문 순서 계산을 재사용할 수 있다. 빠져 있는 것은 관광지 ↔ 교통 거점의 접근 이동과 관광지 사이 직접 도보다. **가까운 역 하나를 고르는 방식에서, 실제 접근 이동까지 포함한 관광지 간 비용을 비교하는 방식으로 바꿔야 한다.**

먼 역 문제에는 두 종류가 있다. 다른 역을 선택하면 해결되는 경우와, 철도역 자체가 멀어서 버스가 필요한 경우다. 도보 시간 추가만으로 두 번째 경우까지 해결했다고 판단하면 안 된다.

## 현재 코드에서 확인한 재사용 범위

| 구성 요소 | 현재 기능 | 재사용 / 변경 지점 |
| --- | --- | --- |
| `/api/stations/resolve` | 관광지 좌표마다 최근접 역 하나 반환 | 조회·오류 격리 구조 재사용. 복수 후보 반환은 응답 계약 확장 필요 |
| `clients/heartrails.py:get_nearest_stations` | 근처 역 목록과 `distance`를 받고 앞에서 `limit`개를 잘라 반환 | 기존 HTTP 호출 재사용. 노선별 중복을 합친 뒤 후보 수를 제한해야 함. 도보 경로·시간은 제공하지 않음 |
| `services/route_service.py:find_stations` | 관광지마다 `limit=1`로 역 고정 | 후보 선택 로직 변경 필요 |
| `crawling/ekispert.py:crawl_route` | 역명·버스정류장명 입력 UI에서 경로의 총 시간·요금·노선명 수집 | 철도 경로 수집 재사용. 좌표 출발/도착, 버스정류장 좌표, 교통수단 타입은 현재 수집하지 않음 |
| `services/route_service.py:find_route_station` | 경로 안의 모든 지점 좌표를 HeartRails 역 검색으로 보완 | 버스정류장 결과가 들어오면 철도 역 조회로 못 찾을 위험. 버스 결과의 끝까지 연결된 지원은 미검증 |
| `clients/google_maps.py` | Text Search, Nearby Search, Geocoding | 장소·좌표 유지. 현재 Routes/Directions 호출은 없음 |
| `services/nearby_service.py:distance_meters` | 두 좌표의 구면 직선거리 | 후보를 좁힐 때 재사용. 보행 거리·시간으로 표시하면 안 됨 |
| `optimizer/optimizer.py` | 고정된 방향별 비용을 사용하는 Held–Karp 동적 계획법 | 순서 계산 핵심 재사용 가능. 비용표 생성과 역을 전제하는 quote 검증·응답 어댑터는 확장 필요 |
| `web/src/demo-map-controller.ts` | 도보/노선 구간의 양 끝 좌표로 색상별 선 표시 | 색상 체계 재사용. 실제 도보 경로선은 좌표 배열을 받도록 확장 필요 |

주요 코드 근거: [HeartRails 클라이언트](../../../backend/app/clients/heartrails.py), [기준역 서비스](../../../backend/app/services/station_service.py), [경로 서비스](../../../backend/app/services/route_service.py), [크롤러](../../../backend/app/crawling/ekispert.py), [Google 클라이언트](../../../backend/app/clients/google_maps.py), [최적화 엔진](../../../backend/app/optimizer/optimizer.py), [경로 응답 모델](../../../backend/app/schemas/route.py).

현재 특히 주의할 동작:

- 같은 역에 연결된 서로 다른 관광지는 역 간 비용을 `0분·0엔`으로 넣는다. 실제 관광지 간 도보가 0이라는 뜻이 아니다.
- 역 쌍별 경로를 모두 해당 일차의 같은 시작 시각으로 조회한다. 선택 순서와 체류 후 실제 출발시각을 따라 재조회하는 시간 의존 최적화는 아니다.
- `TransportStep`은 `fromStation`, `toStation`, `line`만 가진다. 관광지·버스정류장·교통수단 타입·구간별 시간·도로 경로선을 일반적으로 표현하지 못한다.
- 현재 목적함수는 총 시간 → 총요금 → 방문 ID 정렬이다. 시간·도보량·요금의 가중합은 현재 기능이 아니다.

## 무료 HeartRails 실조회 결과

프로젝트의 기존 `get_nearest_stations` 함수를 세 대표 좌표에 호출했다. Google·Gemini 유료 요청은 실행하지 않았다. 좌표는 장소 주변 대표점이며, 출입구나 Google Place ID를 새로 검증한 값은 아니다.

| 대표점 `(lat, lng)` | 받은 결과 일부 | 의미 |
| --- | --- | --- |
| 도쿄역 주변 `(35.681236, 139.767125)` | 상위 10행이 모두 `東京`, 같은 좌표, `90m`. 노선명만 다름 | `limit=3` 변경만으로 서로 다른 역 3곳을 얻을 수 없음 |
| 청수사 주변 `(34.994857, 135.785047)` | `御陵` 2행 `1520m`, `清水五条` `1520m`, `蹴上` `1540m` | 현재 첫 행 선택은 `御陵`. 거리 동률의 반환 순서만으로 접근하기 좋은 역을 결정하지 말아야 함 |
| 금각사 주변 `(35.039370, 135.729243)` | `北野白梅町` `1340m`, `等持院・立命館大学衣笠キャンパス前` `1380m`, `龍安寺` `1470m` | 철도역 후보가 여럿이어도 접근 부담은 남을 수 있음 |

이는 후보 선택 문제를 확인한 결과다. 각 역까지 실제 보행거리, 보행 가능 여부, 버스가 더 빠른지는 이번 조회로 검증하지 않았다. `distance`는 문자열(`90m` 등)이며 공식 문서는 10m 정밀도 거리로 설명한다. 보행 경로 거리라는 보장은 없다. 또한 반환 목록의 개수·범위를 임의로 늘리는 공급자 파라미터는 이 API 문서에 없다. `limit`은 우리 코드의 잘라내기 옵션이다. [HeartRails 공식 API: 최근접 역](https://express.heartrails.com/api.html)

중복 제거는 역명만으로 하지 않는다. 동일 이름의 다른 역을 합치지 않도록 좌표·지역을 함께 고려하고 노선 정보를 보존해야 한다. 같은 역명이라도 좌표가 다른 출입구·운영사 지점은 별도 검증 대상이다. 현재 클라이언트는 잘라내기 전에 전체 응답을 받으므로, 응답 내 중복 제거 자체는 추가 HTTP 요청을 요구하지 않는다.

## 기존 Google Places 호출을 확장하는 방법

**가능한 재사용:** Nearby/Text Search에 `routingParameters.origin`과 `travelMode: "WALK"`를 넣고, field mask에 `routingSummaries`를 요청하면 검색된 장소별 도보 시간·거리를 함께 받을 수 있다. 기본 교통수단은 자동차이므로 `WALK`를 반드시 명시한다. [공식 사용법](https://developers.google.com/maps/documentation/places/web-service/routing-summary), [교통수단 옵션](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/RoutingParameters)

우리 앱에는 역을 출발점으로 둔 추천 카드의 **“이 역에서 도보 몇 분”**에 잘 맞는다. 하지만 다음 제약이 있다.

- 검색 응답에 포함된 장소까지의 요약이다. 이미 고른 임의 관광지·역의 모든 조합을 정확히 지정하는 경로 조회 API가 아니다.
- 시간·거리·Google Maps 링크를 반환하며, 지도에 그릴 polyline이나 상세 도보 단계는 제공하지 않는다. 링크에서 다시 계산한 경로가 요약과 같다는 보장도 없다. [응답 모델](https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/RoutingSummary)
- 역 → 관광지 방향의 요약을 관광지 → 역이나 왕복 시간으로 무조건 복사하지 않는다. 빈 요약 항목도 처리해야 한다.
- 현재 `search_nearby`는 Google 응답의 `places`만 반환하므로 별도 `routingSummaries` 배열을 그대로 추가하면 버려진다. `places[i]`와 요약의 같은 인덱스를 먼저 결합하고, 중복 제거·필터·LLM 정렬 후에도 Place ID로 관계를 유지해야 한다.
- 추가 필드는 과금 등급에 영향을 줄 수 있다. 공식 데이터 필드 표의 `routingSummaries`는 Text/Nearby Search Enterprise + Atmosphere로 분류된다. 기존 검색과 같은 비용이라고 가정하지 않는다. [공식 필드·과금 표](https://developers.google.com/maps/documentation/places/web-service/data-fields)

## 추가 Google Routes API가 필요한 범위

선택된 두 지점 사이를 직접 지정해 **도보 시간·거리와 도로를 따라가는 polyline**까지 받으려면 `computeRoutes`의 `WALK`가 맞는다. 후보 역까지의 접근 이동과 같은 역을 공유하는 관광지 간 직접 도보를 계산하는 데 사용할 수 있다. 일본 도보 길찾기는 공식 커버리지 표에 지원으로 표시된다. 아직 프로젝트에는 구현되어 있지 않으며, 기존 키의 Routes API 허용·활성화·실제 응답은 확인하지 않았다. [Compute Routes](https://developers.google.com/maps/documentation/routes/compute_route_directions), [교통수단](https://developers.google.com/maps/documentation/routes/reference/rest/v2/RouteTravelMode), [일본 지원 표](https://developers.google.com/maps/coverage)

**Google Routes로 일본 버스·철도까지 한 번에 대체하면 안 된다.** 공식 FAQ는 일본의 Google Transit 파트너를 Routes API 대중교통 지원 대상에서 제외한다. Google Maps 일반 앱에서 일본 대중교통 길찾기가 되는 것과 외부 개발자용 API 지원은 다르다. [공식 FAQ: 대중교통 지원 국가](https://developers.google.com/maps/faq)

Google 도보 경로는 beta이고 보행로 정보가 누락될 수 있다는 안내를 사용자에게 표시하도록 공식 문서가 요구한다. 역 대표 좌표·관광지 중심 좌표는 출입구가 아닐 수 있으므로 반환된 실제 시작/끝 위치와 현장 접근성도 검증해야 한다. [교통수단 주의사항](https://developers.google.com/maps/documentation/routes/reference/rest/v2/RouteTravelMode), [응답의 시작/끝 위치](https://developers.google.com/maps/documentation/routes/reference/rest/v2/TopLevel/computeRoutes)

## Ekispert를 재사용하는 방법과 한계

현재 연동은 **`roote.ekispert.net` 웹 화면 크롤러**다. 별도의 공식 Ekispert API 계약·클라이언트가 이미 붙어 있는 것은 아니다. 입력창이 역명·버스정류장명을 받도록 작성되어 있지만, 현재 서비스는 HeartRails에서 얻은 철도역 이름을 입력하고 결과 좌표도 HeartRails로 찾는다. 따라서 “사이트에 버스 검색이 있음”과 “우리 API가 버스를 정상 반환함”은 구분해야 한다. 버스 노선명으로 수단을 추측하지 말고 제공자가 식별한 타입을 보존해야 한다.

공식 Ekispert API에는 다음 기능이 문서화되어 있다. 이는 새 연동 후보이며 기존 크롤러의 기능으로 간주하지 않는다.

- `geo/station`: 좌표·반경 기반 역/버스정류장 후보, 교통수단 필터, 좌표 반환. [공식 문서](https://docs.ekispert.com/v1/api/geo/station.html)
- `search/course/extreme`: 역 코드·좌표·주소를 경유 목록으로 받는 대중교통 경로 탐색. [공식 문서](https://docs.ekispert.com/v1/api/search/course/extreme.html)
- `train`, `bus`, `walk` 등의 교통수단 분류. 버스정류장도 이 서비스의 “역” 개념에 포함된다. [교통수단](https://docs.ekispert.com/v1/dictionary/type/), [역 개념](https://docs.ekispert.com/v1/dictionary/station/)

중요한 한계: 공식 좌표 입력 경로의 **좌표 ↔ 최근접 역 시간은 직선거리와 분당 80m로 계산**한다고 명시되어 있다. 좌표 입력만 도입하면 실제 보행 도로 경로까지 해결된다고 보면 안 된다. Google 도보 시간을 더하는 설계라면 제공자의 접근 시간과 중복 합산하지 않도록 구간을 분리해야 한다. [공식 좌표 설명](https://docs.ekispert.com/v1/dictionary/coordinate-information/)

이번에는 웹 조사 도구에서 `roote.ekispert.net`이 403을 반환해 라이브 버스 화면·크롤러 검증을 수행하지 않았다. 이 결과를 사용자 PC나 서버의 크롤러도 반드시 실패한다는 뜻으로 해석하지 않는다. 공식 API 키·계약·요금·해당 관광지 버스 커버리지는 미확인이다.

## 권장 구현 순서

1. **역 후보를 먼저 정상화한다.** HeartRails 전체 반환 목록을 받은 후 중복을 정리하고 후보 역을 유지한다. 최근접 역 1개를 관광지의 유일한 출발·도착역으로 고정하지 않는다. 추천 기준역과 최적화 때 실제 선택되는 역의 의미도 구분한다.
2. **접근 도보와 직접 도보를 조회한다.** 최적화용 임의 두 지점에는 Routes WALK를 검토한다. 추천 카드만 필요할 때는 기존 Places routing summaries 확장을 비교한다. 같은 역에 속한 관광지 사이도 실제 도보로 계산한다.
3. **관광지 간 비용표를 만든다.** 아래 대안 중 유효한 것을 비교한다. “기준역까지 걸어가기”가 항상 답이라는 전제를 제거한다.

   ```text
   관광지 A → 관광지 B 직접 도보
   관광지 A → 후보역 a 접근 + a → b 대중교통 + 후보역 b → 관광지 B 접근
   버스 데이터가 검증되면: 관광지 A → 정류장 접근 + 버스/철도 + 하차 지점 → 관광지 B
   ```

4. **기존 Held–Karp에 새 비용표를 전달한다.** 핵심 순서 탐색은 유지하고 수집·변환·검증을 고친다. 고정 비용표에서의 최적화이며, 실제 누적 출발시각에 따른 시간표 최적성까지 보장하지 않는다. 역이 각 관광지마다 고정이고 접근 비용도 일정하면 추가 도보가 방문 순서에 미치는 영향은 제한적일 수 있다. 복수 역 선택·직접 도보 비교가 중요하다.
5. **버스가 필요한 대표 사례를 별도로 검증한다.** 우선 금각사·청수사처럼 역에서 먼 지점의 정류장 검색 → 버스 경로 → 정류장 좌표 → 프론트 표시까지 한 구간이 통과하는지 확인한다. 현 크롤러 확장으로 가능한지 판단한 후, 불가능하거나 유지 부담이 크면 공식 Ekispert API 전환 범위를 정한다. 도보까지의 1차 개선을 버스 해결 완료로 보고하지 않는다.
6. **응답 확장안을 팀과 맞춘다.** 현재 관광지 ID·좌표 입력은 재사용 가능하다. 출력에는 관광지/역/정류장을 연결할 구간, 제공자 수단 타입, 시간·거리, 경로선, 실조회/추정/조회 실패 구분이 필요하다. 기존 `timingBasis: stationTravelOnly`를 실제 전체 이동시간처럼 사용하지 않는다. 이 문서는 최종 필드명을 확정하지 않는다.

## 호출량과 수용 기준

현재 서로 다른 기준역이 8개면 역 간 방향 쌍은 최대 56개다. 관광지마다 서로 다른 후보 역 3곳을 무작정 조합하면 관광지 쌍별 후보 조합은 `8 × 7 × 3 × 3 = 504`개까지 늘어난다. 후보 생성만 늘리고 크롤링을 전부 실행하는 구현은 피한다. 동일 조회는 요청 안에서 재사용하고, 후보 제한·대표 사례 측정으로 품질과 지연을 함께 확인한다. 장기 저장은 공급자별 허용 범위를 확인한 뒤 결정한다.

다음 구현의 최소 검증 기준:

- 같은 역·다른 관광지는 실제 이동이 있으면 0분으로 처리되지 않는다.
- 같은 이름의 다른 지역 역을 합치지 않고, 노선만 다른 동일 좌표 역은 중복 후보로 세지 않는다.
- 가까운 직선거리와 짧은 도보시간을 같은 것으로 취급하지 않는다.
- 관광지 → 역, 역 → 관광지의 방향과 시간을 각각 보존한다.
- 직접 도보가 더 적합한 후보가 있으면 역 경유를 강제하지 않는다.
- 버스정류장 좌표를 못 찾거나 도보 경로가 없으면 미확인/실패 상태로 처리하고 0분으로 채우지 않는다.
- Ekispert 경로 내부의 환승 도보와 관광지 접근 도보를 중복 합산하지 않는다.
- 최종 경로의 시간·요금 합계, 지도 표시, API의 계산 범위 표기가 일치한다.

이번 확인은 코드 검토·공식 문서 대조·무료 역 조회다. 실제 Google 도보, Places routing summaries, Ekispert 버스의 통합 실호출 검증은 남아 있다.
