> 2026-11-10 구현자 최종 조정: 아래는 Opus의 원래 설계 제안이다. 실제 소스는 메인 에이전트가 확인했다. 검증에 전달할 최종 구현 계약은 이 인용문을 우선한다. 연구에서 복수 역의 필요가 확인되어 최근접 한 역을 그대로 고정하자는 제안은 채택하지 않는다. 중복 없는 최대 3개 역의 유향 도보를 조회하고, 출발/도착 방향별로 접근 시간이 가장 짧은 역을 각각 선택한다. 모든 역 간 조합 최적화는 하지 않는다. 따라서 도보 호출은 최대 6n+12회(8곳이면60회), 크롤은 최대 n(n-1)회다. 직접 보행은 동일 역 쌍을 우선한 후 직선거리 1.2km 이하의 가까운 방향 쌍을 최대12회 비교한다. 결과에는 비교 범위 제한을 항상 설명한다. 전체 역 조합의 최적성이나 시간표 최적성을 주장하지 않는다.
>
> 캐시에는 반올림하지 않은 정확한 좌표와 방향을 사용한다. 역 병합은 이름+정확한 좌표+지역으로만 하며 100m 근접 병합은 하지 않는다. 동일 장소 좌표는 실제0분일 수 있으므로 양수를 강제하지 않는다. `route`는 기존 배열을 유지하고 직접 도보에서는 빈 배열이다. 모든 새 segment는 정수 durationMinutes를 갖고 합계가 leg와 일치하며 transit segment는 기존 route 전체의 합계 구간이다. Google polyline은 서버에서 검증·디코딩하여 segment.path 좌표 배열로 반환한다. 레거시 JSON에는 새 선택 필드를 출력하지 않는다. 새 UI는 API 모드에서 기본 false 체크박스로 제공하고, 켜진 요청에 레거시 응답이 돌아오면 명시적으로 거절한다. WIP 커밋은 만들지 않고 기존 작업을 보존한다. 버스·추천 API·체류시간은 이번 범위 밖이다.

출시 전 확인: 서버용 Routes 키 설정 후 실제 WALK 1쌍의 시간·거리·지도 경로를 확인해야 한다. 이 확인 전에는 도보 옵션을 프로덕션에 출시하지 않는다. 현재 실제 공급자 검증은 미수행이며 자동 테스트만 통과했다. 전체 요청의 540초 예산은 모든 일차가 공유하므로 장소가 많은 여러 날짜 요청은 504가 날 수 있다. 키는 선택 요청 시 검증하며 Google Routes로 좌표 쌍을 전송한다. 호출 제한은 일차별이고 총 비용 제한이 아니므로 프로젝트 쿼터도 설정한다.

파일 조회 도구(Read/Grep/Bash)가 이 세션에서 모두 비활성화되어 있어 소스를 재확인하지 못했습니다. 아래 계약은 프롬프트에 제시된 확정 아키텍처 사실에만 근거하며, 구현 전 `route_service.py`/`optimizer.py`의 쿼트 검증부만 실물 확인이 필요합니다.

---

# 설계 계약: 관광지↔역 접근 보행 (includeAccessWalking)

## 0. 핵심 권고: baseline+extras 폐기

제안된 2단계(기준 크롤 56 + 대안 역 12)는 **과설계**입니다. 폐기 근거:

- 대안 역 탐색의 가치는 "더 가까운 역이 더 빠른 환승을 줄 때"인데, HeartRails 최근접 역 대안은 보통 수백 m 차이이고 Ekispert 경로는 동일 노선으로 수렴합니다. 기대 이득이 호출 예산 +21%와 `candidateSearchTruncated`라는 새 정직성 부채를 정당화하지 못합니다.
- 대안 역 하한(access lower bound) 프루닝은 정확성 증명이 필요한 로직이고, 이 로직 자체가 테스트 부담의 대부분입니다.

**채택안: 단일 역 고정 + 실측 보행 + 조건부 직접 보행.** 역 선택 로직은 현행과 동일(최근접 1개)하게 두고, "역 선택"이 아니라 "보행 시간 실측"만 추가합니다. 대안 역은 명시적으로 범위 밖(SCOPE-OUT)으로 선언합니다.

---

## TARGETS

| ID | 목표 | 완료 기준 |
|----|------|-----------|
| T1 | HeartRails 후보 중복 제거 | 동일 물리 역의 노선별 중복 행을 limit 슬라이스 **이전**에 병합. `limit=1` 경로의 반환 역은 변경 없음(선두 유지) |
| T2 | 유향 보행 실측 | Google Routes `computeRoutes` WALK로 place→station, station→place 각각 조회. 장소당 2회 |
| T3 | 보행 포함 비용 합산 | leg 총시간 = 출발 보행 + 역간 환승 + 도착 보행 |
| T4 | 조건부 직접 보행 간선 | 직선거리 ≤ 1.2 km 쌍에만 place→place WALK 조회, 일당 최대 12회. `min(환승경로, 직접보행)` 채택 |
| T5 | 응답 확장 | `timingBasis='placeTravelOnly'`, `legs[].segments[]` 추가. 레거시 응답 바이트 동일성 유지 |
| T6 | UI 옵트인 | 체크박스 기본 false, 예시 모드 제외, 토글 시 revision 증가 |

**SCOPE-OUT (명시적 제외):** 대안 역 조합 탐색, 체류시간(dwell), 시간대 의존 환승, 버스 모드, 추천 API 변경, Google Routes TRANSIT(일본 미지원).

---

## INTERFACES

### 요청

```
RouteRequest.includeAccessWalking: bool   # strict optional, default False
```

`false`(또는 미지정)일 때 기존 요청 바디와 **완전히 동일한 처리 경로**를 탄다. 신규 필드가 기존 strict 파서를 깨지 않도록 Pydantic 모델에 기본값 포함 추가만 수행.

### 응답

```
RouteResponse.timingBasis: 'stationTravelOnly' | 'placeTravelOnly'
```

- `includeAccessWalking=false` → `'stationTravelOnly'` (현행 그대로)
- `includeAccessWalking=true` 성공 → `'placeTravelOnly'`
- 부분 실패로 보행을 하나라도 채우지 못하면 **응답하지 않고 실패**(아래 INV-6)

```
legs[].route          # 기존 구조 유지(fromStation, toStation, line). 환승 상세 전용.
                      # direct-walk leg에서는 null.
legs[].segments[]     # includeAccessWalking=true 일 때만 존재. 순서 보장.
  fromPoint: { kind: 'place'|'station', name: str, location: {lat,lng} }
  toPoint:   { kind: 'place'|'station', name: str, location: {lat,lng} }
  mode:      'walking' | 'transit'
  durationMinutes: int | null      # null은 transit 하위 스텝에서만 허용
  distanceMeters:  int | null      # walking은 항상 non-null
  encodedPolyline: str | null      # walking은 non-null, transit은 항상 null
```

`Visit.station`은 **추천 참조값**으로 의미 유지. 실제 승하차 역은 `segments`의 `kind='station'` 포인트가 단일 진실 원천. 프론트엔드의 "주변 역 추출" 로직이 관광지를 역으로 오인하지 않도록 `kind` 판별 필수.

### 설정

```
GOOGLE_ROUTES_API_KEY   # 기동 시 optional, 보행 요청 시에만 required
```

미설정 상태에서 `includeAccessWalking=true` 수신 → 503 계열 도메인 오류. 키 값은 로그/응답/stdout에 **절대** 미출력.

### 캐시

유향 보행 캐시 키: `(from_lat, from_lng, to_lat, to_lng)` 좌표 6자리 반올림. 역방향은 **별개 엔트리**(보행 비대칭 — 일방통행, 계단, 지하도).

---

## INVARIANTS

| ID | 불변식 |
|----|--------|
| INV-1 | `includeAccessWalking=false` 응답은 바이트 수준에서 기존과 동일. 신규 필드 미출력(직렬화 시 exclude) |
| INV-2 | `timingBasis`는 비용 의미의 유일한 식별자. 기존 `'stationTravelOnly'` 총계를 보행 포함 값으로 재해석하지 않음 |
| INV-3 | 두 장소가 동일 역에 매핑되어도 leg 총시간 ≠ 0. 환승 0 + 보행 2구간이므로 **반드시 양수** |
| INV-4 | `sum(segments[].durationMinutes) == leg.durationMinutes` 정확히 성립. 각 구간을 ceil로 정수 분 변환 후 **합산**(leg 레벨 재반올림 금지) |
| INV-5 | 옵티마이저 비용 행렬은 정수 분/엔화 유지. Held-Karp 코드 **무변경** |
| INV-6 | "경로 없음"(provider가 정상 응답 + 경로 0건)과 "provider 오류"(인증/네트워크/쿼터) 구분. 후자는 0 대체나 폴백 없이 요청 실패 |
| INV-7 | 모든 유향 쌍은 유효한 완성 경로를 가짐. T4 프루닝으로 직접 보행을 조회하지 않은 쌍은 환승 경로가 그 쌍의 확정 비용 |
| INV-8 | T4 캡(12회) 초과로 조회를 포기한 쌍이 존재하면 해당 일자에 `candidateSearchTruncated=true` 표기 및 UI 노출 |
| INV-9 | T1 중복 제거는 **선두 보존**. `limit=1` 호출의 반환값이 현행과 동일함을 회귀 테스트로 고정 |

### 호출 예산 (8개 장소/일)

$$\text{Ekispert} = 56 \text{ (불변)}, \quad \text{Routes WALK} \le 2 \times 8 + 12 = 28$$

전체 84회. 기존 56회 대비 +50%, 2단계 안(최대 100+)보다 낮음.

---

## 옵티마이저 통합 (단순안)

**코어 미니마이저 추출 불필요.** 쿼트는 여전히 관광지 visit ID로 키잉되므로 Held-Karp 입력 형태가 바뀌지 않습니다. 변경점은 두 곳뿐:

1. 쿼트 비용 계산 함수: 보행 분을 더함
2. 쿼트 검증 함수: `segments` 존재 여부로 분기. 존재 시 `route` null 허용 + INV-4 검증, 부재 시 기존 검증 그대로

가짜 역 노드 삽입, 제네릭 간선 래퍼, 별도 쿼트 타입 — 모두 불필요합니다. 단일 쿼트 타입에 `segments` optional 필드 추가가 최소 diff입니다.

---

## ACCEPTANCE

- [ ] A1: 기존 통합 테스트 전체 통과, 응답 스냅샷 무변경
- [ ] A2: `includeAccessWalking=true` 요청이 `placeTravelOnly` + 모든 leg에 `segments` 포함 응답
- [ ] A3: 동일 역 매핑 쌍에서 leg 총시간 > 0, `segments`가 walk/transit(0분)/walk 3구간
- [ ] A4: 역방향 보행 시간이 비대칭인 fixture에서 A→B ≠ B→A 반영
- [ ] A5: 모든 leg에서 INV-4 합산 등식 성립
- [ ] A6: `GOOGLE_ROUTES_API_KEY` 미설정 + `true` → 503, 키 문자열 미노출. 미설정 + `false` → 정상 200
- [ ] A7: Routes 401/네트워크 타임아웃 → 요청 실패(폴백 금지). "경로 없음" → 해당 쌍 직접보행 제외 후 환승 경로 채택
- [ ] A8: HeartRails 노선 중복 fixture에서 중복 제거 후 후보 수 감소, `limit=1` 반환 역 불변
- [ ] A9: 프론트엔드 `demo-api.ts`가 `segments` 미포함 레거시 응답을 기존대로 파싱
- [ ] A10: 지도에 보행 polyline(실제 경로), 환승은 기존 직선 참조선 유지. `kind='place'` 포인트가 역 목록에 유입되지 않음
- [ ] A11: 체크박스 토글 시 revision 증가, 이전 결과 stale 처리
- [ ] A12: 미커밋 상태의 `demo-map-controller.ts` / `demo-reference.css` / `demo-route-render.ts` 작업물 보존

---

## TEST_PLAN

### 백엔드 단위 (fixture 기반, 유료 호출 0)

| 테스트 | 검증 |
|--------|------|
| `test_heartrails_candidate_dedup` | 동일 역 노선 3행 → 1개로 병합, 선두 보존 (INV-9) |
| `test_same_station_leg_nonzero` | 동일 역 쌍 총시간 양수, 3구간 구성 (INV-3) |
| `test_reverse_walking_asymmetry` | 유향 캐시가 역방향을 별 엔트리로 처리 |
| `test_segment_sum_equals_leg` | 모든 leg에서 합산 등식, ceil 경계값(59.4s, 60.6s) 포함 (INV-4) |
| `test_direct_walk_dominates` | 300 m 인접 쌍에서 직접 보행 선택, `route=null` |
| `test_direct_walk_cap_truncated` | 13번째 후보 쌍에서 `candidateSearchTruncated=true` (INV-8) |
| `test_provider_error_fails_loud` | 401/타임아웃 → 예외 전파, 0 대체 없음 (INV-6) |
| `test_no_route_vs_error` | 정상 응답 0건 → 직접보행 후보 제외, 환승 채택 |
| `test_legacy_response_unchanged` | `false` 요청 직렬화 결과 스냅샷 일치 (INV-1) |
| `test_call_budget` | 8장소 요청의 provider 호출 횟수 ≤ 84, 보행 ≤ 28 |
| `test_key_not_in_output` | 로그/응답 직렬화에 키 문자열 부재 |
| `test_same_name_station_ambiguity` | 동명 이역(예: 동일 명칭 다른 좌표)에서 좌표로 구분, 이름 병합 금지 |

### 프론트엔드

| 테스트 | 검증 |
|--------|------|
| `demo-api` strict parse | `segments` 유/무 양쪽 수용, 알 수 없는 필드 거부 유지 |
| `demo-route-render` | 보행 구간 라벨 렌더, `unknown access` 표기가 `segments` 존재 시 소거 |
| `demo-map-controller` | walking polyline 디코드 렌더, transit 직선 참조선 유지, `kind` 기반 역 추출 |
| stale state | 체크박스 토글 후 이전 revision 응답 폐기 |

### 수동 (무료 경로만)

로컬 fixture 서버로 체크박스 on/off 왕복, 지도 보행 경로 시각 확인. **Google/Gemini 유료 라이브 호출 금지.**

---

## RISKS

| 위험 | 영향 | 완화 |
|------|------|------|
| Ekispert는 공식 API가 아닌 크롤러 | 좌표 없음, 버스 타입 미구분. 구조 변경 시 파손 | 보행 기능이 Ekispert 응답 구조에 **새 의존을 추가하지 않도록** 격리. "버스 해결됨" 주장 금지 |
| Google Routes WALK 지연 | 28회 순차 호출 시 응답 시간 급증 | 유향 캐시 + 동시 호출(제한된 동시성). 타임아웃 예산을 요청 레벨에 명시 |
| `placeTravelOnly` 총계를 기존 값과 비교 | 사용자가 "느려졌다"고 오인 | UI에 basis 라벨 명시 노출. 기존 결과 재해석 금지 (INV-2) |
| 직접 보행 1.2 km 임계값이 임의적 | 1.3 km 쌍에서 최적해 누락 | 임계값을 설정 상수로 노출 + 주석으로 상한 명시. 프루닝 발생 시 INV-8 표기 |
| 동명 역 병합 과다 | 서로 다른 물리 역을 하나로 병합 | 중복 제거 키는 이름 단독이 아니라 **이름 + 좌표 근접**(≈100 m) 조합 |
| `GOOGLE_ROUTES_API_KEY` 노출 | 과금 유출 | 설정 객체에서 `SecretStr`, repr 마스킹, 오류 메시지에 값 미포함 |
| 미커밋 UI 작업물 손실 | 진행 중 지도/결과 UX 작업 유실 | 구현 착수 전 WIP 커밋. 공유 stash 스택 사용 금지 |

---

**건너뛴 것:** 대안 역 조합 탐색, 코어 미니마이저 제네릭화, dwell/시간대 의존 모델. 추가 시점 — 대안 역은 실측 데이터로 "최근접 역 고정이 X% 이상 손실"을 입증한 뒤, 제네릭화는 두 번째 비역(non-station) 노드 타입이 실제로 등장할 때.
