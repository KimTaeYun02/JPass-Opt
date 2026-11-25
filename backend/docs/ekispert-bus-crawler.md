# 기존 크롤러로 버스 구간 검증하기

공식 Ekispert 키 없이 기존 웹 크롤러의 버스 검색·추출 가능성을 점검하는 별도 도구다.
제품의 최적화 API에 버스 이동을 추가한 것은 아니다.

## 실행

저장소 루트에서 가상환경을 활성화하고 실행한다. 기본 실행은 네트워크를 사용하지 않는다.

```powershell
python backend/scripts/verify_ekispert_bus.py
```

실제 사이트에서 운영사까지 확인한 정류장 이름 두 개와 출발 시각을 지정한다.
아래 정류장 표기는 2026-11-14 실제 자동완성에서 확인했다. 실행 시 사용할 날짜·시각은 별도로 지정한다.

```powershell
python backend/scripts/verify_ekispert_bus.py --live `
  --departure-stop "金閣寺道／京都市営バス" `
  --arrival-stop "北大路バスターミナル(地下鉄北大路駅)／京都市営バス" `
  --departure "2026-11-16T09:00:00+09:00"
```

기본은 headless Chromium이다. 제품과 같은 화면 모드가 필요하면 `--headed`를 사용한다.
Linux 화면 모드에는 `xvfb-run -a python ... --headed`와 Xvfb가 필요하다.
주 프레임 문서가 403·429를 반환하면 그 실행은 종료한다. 부가 리소스의 거절을 문서 차단으로 분류하지 않는다. 차단 우회나 반복 조회 도구가 아니다.

## 결과 해석

| 상태 | 의미 |
| --- | --- |
| `NOT_RUN` | 기본 실행. 외부 조회 없음 |
| `INVALID_INPUT` | 운영사 접미사 또는 시간대 등 입력 누락. 외부 조회 없음 |
| `HTTP_403`, `HTTP_429` | 주 프레임 문서의 접근 거절·호출 제한. 한 번의 시도로 종료 |
| `TIMEOUT`, `PROBE_FAILED` | 제한 시간 초과 또는 기타 실패. 예외 원문은 출력하지 않음 |
| `NOT_CONFIRMED` | 결과가 있어도 정류장 전체 이름·DOM 구조·시간·운임·버스 이름 단서 중 조건 미충족 |
| `BUS_NAME_EVIDENCE_ONLY` | 이름이 일치하는 완전한 추출 결과에 `バス` 노선명이 있음. 공식 수단 타입·정류장 ID 검증은 아님 |

기본 실행과 `BUS_NAME_EVIDENCE_ONLY`는 종료 코드 0, 나머지는 1이다.
JSON의 일본어 문자는 Windows cp949 터미널에서도 출력되도록 ASCII 이스케이프로 직렬화한다. JSON으로 읽으면 원래 문자열로 복원된다.
운영사 접미사는 식별을 위해 비교하지만 이름만으로 정류장 고유 ID가 검증되지는 않는다.
미확인 운임을 0엔으로 바꾸지 않는다. 결과는 노선명·정류장명 등 제한된 필드만 출력하며 HTML·URL·키는 저장하지 않는다.

## 오프라인 검사

```powershell
$env:PYTHONPATH = "backend"
python -m unittest discover -s backend/contract_checks -p "test_ekispert_bus_probe.py" -v
```

Playwright Chromium 설치가 필요하다. 합성 HTML에 실제 추출 함수를 적용한다.
실제 응답을 저장한 fixture가 아니므로 테스트 통과만으로 현재 사이트의 버스 지원을 보장하지 않는다.

## 제품에 연결하기 전에 남은 작업

현재 크롤러는 버스 노선명과 정류장명을 읽을 수 있는 구조다. 반면 제품 서비스는 그 이름들을
철도역 좌표 조회에 넘기므로 버스정류장 좌표가 없으면 여전히 실패할 수 있다.
실제 검색 성공 → 자동완성·정류장 식별 확인 → 버스정류장 좌표 공급원 확보 → 구간별 수단·시간·좌표 계약 → API·지도 연결 순서로 진행한다.
최신 실조회 결과와 검토는 [검증 기록](../../docs/ai/reviews/ekispert-bus-crawler.md)을 참고한다.
이후 수행한 [화면 모드 버스 실조회·좌표·프론트 재생 검증](../../docs/ai/reviews/ekispert-bus-live-ui.md)에서는 실제 버스 검색과 정류장 지도 좌표를 확인했다. 자동 제품 연결 완료와는 구분한다.
