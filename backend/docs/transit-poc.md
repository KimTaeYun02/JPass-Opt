# Google 도보 + Ekispert 공식 대중교통 PoC

2026-11-13. 현재 제품 API·크롤러·프론트 흐름과 별도로 실행하는 소규모 연결 검증이다. 공식 키가 없어 실호출은 아직 실행하지 않았다. 키 없는 기본 실행과 오프라인 테스트가 통과해도 실제 버스 노선·정류장·계약 권한의 검증 완료를 뜻하지 않는다.

## 공급자별 역할

| 구간 | 공급자 |
| --- | --- |
| 출발 관광지 → 승차 정류장 | 기존 Google Routes `WALK` 클라이언트 |
| 승차 정류장 → 하차 정류장 | Ekispert 공식 `search/course/extreme` |
| 하차 정류장 → 도착 관광지 | 기존 Google Routes `WALK` 클라이언트 |

Google 공식 FAQ의 일본 제한은 Routes API **대중교통**에 관한 것이다. Ekispert는 별도의 교통 데이터 API다. 일본 Google 도보는 11월 10일 실호출에서 성공했고, 이번 검증에서 Google `TRANSIT`은 호출하지 않는다. [Google FAQ](https://developers.google.com/maps/faq#transit_directions_countries)

## 필요한 키

저장소 루트 `.env`에 `EKISPERT_API_KEY`와 `GOOGLE_ROUTES_API_KEY`를 설정한다. 이미 프로세스 환경에 있으면 그 값을 우선한다. 스크립트가 루트 `.env`를 명시적으로 읽으며 `backend/.env`를 자동으로 합치지 않는다. `.env.example`에는 빈 자리만 제공한다. 키를 CLI 인수·브라우저 URL·프론트 환경변수에 넣지 않는다.

Ekispert **무료 플랜**의 경로 기능은 웹 길찾기 URL 생성이며 이 PoC에 필요한 `geo/station`·`search/course/extreme`이 무료 플랜 API 목록에 없다. Standard 또는 해당 기능을 허용하는 평가판 키가 필요하다. [무료 플랜 API 목록](https://docs.ekispert.com/v1/le/api/), [Standard API 목록](https://docs.ekispert.com/v1/api/)

평가판 신청 페이지는 회사명·이름·이메일·사용 도메인 또는 글로벌 IP를 요구하고 키를 3영업일 이내 이메일로 발급한다고 안내한다. 발급 자격은 실제 신청으로 확인해야 하며, 개인·해외 팀의 승인을 보장하지 않는다. 등록 도메인과 다른 localhost 사용은 403이 될 수 있으므로 **서버에서 호출할 로컬 개발·송신 IP 조건**을 발급처에 확인한다. Referer·Origin 등을 위조해 통과시키지 않는다. [신청](https://api-info.ekispert.com/form/trial/), [키·도메인 안내](https://docs.ekispert.com/v1/get-started/guide/)

신청 가이드라인은 평가판 제공 지역을 도쿄·가나가와·오사카·홋카이도로 적고, 별도 제한 문서는 그중 일부 API에 대한 제한으로 설명한다. 따라서 교토 전체가 모든 API에서 불가능하다고 단정하지 않는다. 우선 두 문서에 모두 포함된 **오사카**를 기본 검증 지역으로 삼는다. 평가판 결과를 공개 데모에 바로 노출하지 않는다. [평가판 제한](https://docs.ekispert.com/v1/get-started/restriction/), [신청 가이드라인 4·5절](https://api-info.ekispert.com/form/trial/)

## 실행

저장소 루트의 PowerShell 기준이다. 설치된 프로젝트 `.venv`를 사용한다.

```powershell
# 설정 확인만 수행. 네트워크 요청 0회.
.\.venv\Scripts\python.exe backend/scripts/verify_transit_poc.py

# 키 발급·설정 이후에만 실행. 유효한 미래 출발 시각으로 바꾼다.
.\.venv\Scripts\python.exe backend/scripts/verify_transit_poc.py --live --departure "2026-11-14T09:00:00+09:00"
```

`--departure`는 분 단위 RFC 3339 형식이며 `+09:00`, 초 `00`을 요구한다. 시각을 생략한 실호출은 실행하지 않는다. 키가 없으면 `MISSING_CREDENTIALS`와 키 설정 여부만 출력하고 종료 코드 1로 끝난다. 이는 사전 조건 안내이며 외부 서비스 실패가 아니다. 기본 실행에서 두 키가 있으면 `PREFLIGHT`/종료 코드 0이다.

기본 좌표는 오사카성 공원 주변 `(34.6865,135.5253)` → 나카노시마 주변 `(34.6935,135.5030)`의 공개 대표점이다. 실제 장소 ID·출입구가 검증된 좌표가 아니며, 이 조합의 버스 운행 성공을 아직 주장하지 않는다. CLI는 이 한 쌍만 검사한다.

## 호출 순서·예산

1. 두 대표점에서 반경 1km, `type=bus.local`, 최대 2개 정류장을 각각 조회한다. `geoPoint`와 `gcs`에 `wgs84`를 명시한다. 각 응답의 **첫 후보만** 선택한다. [geo/station](https://docs.ekispert.com/v1/api/geo/station.html)
2. Google로 출발지→승차 정류장 도보를 조회한다.
3. 도보 소요 시간을 더한 시각에 정류장 코드 쌍으로 Ekispert 경로를 한 번 조회한다. 콜론 구분자를 유지한 `viaList`를 사용한다. [경로 검색](https://docs.ekispert.com/v1/api/search/course/extreme.html), [API 인코딩 규칙](https://docs.ekispert.com/v1/api/)
4. 응답의 출발·도착 정류장 코드, WGS84 좌표, 시간 순서, `bus.local` 구간 존재, 실제 시간표 신뢰도(`onTimetable`)를 확인한다. 평균 시간표만 있는 응답은 이번 PoC에서 성공으로 처리하지 않는다.
5. 검증된 경우에만 Google로 하차 정류장→목적지 도보를 조회한다. 내부 환승 도보는 Ekispert 경로 시간에 이미 들어 있으므로 추가로 Google을 호출하거나 중복 합산하지 않는다.

| 공급자 | 실행당 상한 |
| --- | --- |
| Ekispert 주변 정류장 | 2회 |
| Ekispert 정류장 간 경로 | 1회 |
| Google 도보 | 2회 |

동일 좌표의 Google 도보는 기존 클라이언트가 실제 요청 없이 처리할 수 있으므로 `calls.walk`는 호출 시도 수의 상한이다. 자동 재시도·다른 후보 쌍 탐색·다음 페이지 조회는 없다. 한 쌍이 실패해도 지역 전체에 버스가 없다는 뜻은 아니다. Ekispert 경로가 실패하면 이미 쓴 출발 도보 1회는 취소되지 않는다.

## 출력과 성공 기준

- `PASS`: 실제 응답에서 코드·좌표가 연결되고, local bus와 시간표를 확인하고 양 끝 도보가 모두 유효함. **전체 최적화·프론트 연동·모든 버스 운행 보장은 아님.**
- `FAIL`/종료 코드 2: 고정된 사유 코드와 요청 수만 반환한다. 예: `no_local_bus_stop`, `no_local_bus_leg`, `endpoint_id_mismatch`, `timetable_not_verified`, `provider_duration_mismatch`, `ekispert_http_403`. HTTP 상태는 숫자만 보존하므로 인증·실행 환경의 403과 한도 초과의 429를 구분할 수 있다. `bus_detail_missing`은 버스 상세 유형을 확인하지 못했다는 뜻이며 버스가 없다는 판정과 구분한다.
- `totalTravelMinutes`: 출발 도보 + 정류장 도착 이후 대기·환승을 포함한 대중교통 경과 시간 + 도착 도보.
- `transitWaitingAndTransferMinutes`: 구간 시각 사이 간격의 합계. 순수 대기만을 뜻하지 않으며 환승 여유도 포함할 수 있음.
- `providerElapsedDeltaMinutes`: 첫 구간 출발→마지막 구간 도착 경과 시간에서 공급자 `timeOnBoard + timeWalk + timeOther`를 뺀 값. 절댓값이 1분보다 크면 중단한다. 최초 정류장 대기는 이 비교에서 제외하지만 `transitWaitingAndTransferMinutes`와 총시간에는 포함한다.

Ekispert는 HTTPS 공식 호스트로만 요청하고 리다이렉트를 따라가지 않는다. 키가 들어간 URL·원본 응답·원본 예외는 출력하지 않는다. 결과도 정류장 코드·수단·시각·시간·거리·호출 수만 출력한다. 원본 교통 데이터나 도보 polyline을 저장하지 않는다. 출력이 필요하면 Git에서 제외된 로컬 경로에 보관한다.

테스트:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s contract_checks -p test_transit_poc.py -v
```

정류장 코드·버스 존재·방향별 도보·출발시각 이월·기다림·내부 도보 중복 합산·응답 형식·인증 없는 0호출·요청 상한을 공급자 대체 테스트로 확인한다. 제품의 `/api/routes/optimize`에 공식 API를 연결하거나 기존 크롤러를 변경하지 않는다.
