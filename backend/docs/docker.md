# Docker 실행·배포

저장소 루트의 [Dockerfile](../../Dockerfile)과 [compose.yaml](../../compose.yaml)로 세 API를 같은 컨테이너에서 실행한다. 호스트에는 Docker Engine과 Docker Compose 플러그인 또는 Docker Desktop이 필요하다. Python·Chromium·Xvfb는 이미지에 포함하고 Poetry는 이미지 빌드 단계에서만 사용하므로 호스트에 따로 설치하지 않는다.

아래 명령은 모두 `Dockerfile`, `compose.yaml`, `README.md`가 있는 **저장소 루트**에서 실행한다. `backend/` 안에서 시작했다면 먼저 `cd ..`로 이동한다.

## 1. 환경 변수 준비

[.env.example](../../.env.example)을 `.env`로 복사한다. 다음 명령의 `-n`은 기존 `.env`를 덮어쓰지 않게 한다.

```bash
cp -n .env.example .env
```

기존 `.env`가 있다면 그대로 열어 필요한 설정만 추가한다. `.env`를 편집기로 열어 장소 검색·주변 관광지 추천에 사용할 서버 키를 입력한다.

```dotenv
GOOGLE_PLACE_API_KEY=발급받은_Google_Places_API_키
GEMINI_API_KEY=발급받은_Gemini_API_키
GEMINI_MODEL=gemini-3.5-flash-lite
GEMINI_NEARBY_MODEL=gemini-3.5-flash-lite
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
API_PORT=8000
```

모델 설정은 선택 사항이다. `GEMINI_MODEL`은 기본 모델이며 생략하면 `gemini-3.5-flash-lite`를 사용한다. 주변 관광지 추천은 `GEMINI_NEARBY_MODEL` → `GEMINI_MODEL` → `gemini-3.5-flash-lite` 순서로 모델을 선택하며, 앞뒤 공백을 제거한 뒤 빈 값은 건너뛴다. nearby 모델만 바꾸려면 `GEMINI_NEARBY_MODEL`을 수정한다. 키가 없어도 앱과 동선 최적화 API는 사용할 수 있으며, 장소 검색·주변 추천은 `503`을 반환한다. Compose가 `env_file: .env`를 읽으므로 `.env` 파일 자체는 준비해야 한다. 키는 이미지에 복사하지 않고 컨테이너 실행 시 환경 변수로 주입한다. `.env`는 Git에 올리지 않는다.

`CORS_ORIGINS`에는 **브라우저에서 실제 접속하는 프론트엔드 주소**를 지정한다. 예를 들어 프론트가 `https://travel.example.com`에서 열리면 다음처럼 설정한다.

```dotenv
CORS_ORIGINS=https://travel.example.com
```

여러 주소는 쉼표로 구분한다. 주소에는 프로토콜·호스트·필요한 포트까지만 쓰고 경로와 끝의 `/`는 넣지 않는다. 이 값은 API 서버 주소가 아니라 허용할 프론트엔드의 origin이다. CORS 설정이 바뀌면 컨테이너를 다시 생성한다.

## 2. 빌드하고 백그라운드에서 실행

```bash
docker compose up --build -d
```

이 명령은 로컬 이미지 `jpass-api:local`을 빌드하고 Compose 서비스 `api`를 실행한다. 처음에는 Python 의존성·Chromium·운영체제 패키지를 내려받는다. 기본적으로 호스트 `8000`번 포트를 컨테이너 `8000`번 포트에 연결한다. 이미 다른 프로그램이 `8000`번을 사용하면 해당 프로그램을 종료하거나 `.env`의 `API_PORT=8001`처럼 호스트 포트를 바꾼다. 그 경우 접속 주소도 `http://localhost:8001`이 되며 컨테이너 내부 포트는 `8000`으로 유지된다.

상태와 로그를 확인한다.

```bash
docker compose ps
docker compose logs -f api
```

`logs -f`는 로그를 계속 표시한다. 여기서 `Ctrl+C`를 눌러도 실행 중인 컨테이너는 종료되지 않는다. 상태가 `starting`이면 잠시 뒤 다시 확인한다. `unhealthy` 또는 반복 재시작이면 `api` 로그에서 오류 원인을 확인한다.

실행한 컴퓨터의 브라우저에서 다음 주소를 연다.

| 주소 | 확인할 내용 |
| --- | --- |
| [http://localhost:8000/docs](http://localhost:8000/docs) | Swagger UI에서 API 목록·입력·출력을 보고 요청 실행 |
| [http://localhost:8000/openapi.json](http://localhost:8000/openapi.json) | 앱이 반환하는 OpenAPI JSON |

터미널에서는 다음 명령으로 OpenAPI 응답을 확인할 수 있다.

```bash
curl -fsS http://localhost:8000/openapi.json
```

컨테이너 healthcheck도 `/openapi.json`에 대한 앱의 응답을 확인한다. `healthy`는 외부 Google·Gemini API나 실제 브라우저 크롤링의 성공을 의미하지 않는다. 실제 요청 예시는 [백엔드 실행 안내](../README.md)와 [API 참조 문서](README.md)를 참고한다.

## 로컬 HTTP 테스트

컨테이너가 실행된 뒤 호스트의 Python 3으로 [test_api_http.py](../scripts/test_api_http.py)를 실행한다. 이 스크립트는 Python 표준 라이브러리만 사용하므로 호스트에 앱 의존성이나 Poetry를 설치할 필요가 없다. 아래 명령은 저장소 루트 기준이다.

```bash
python backend/scripts/test_api_http.py
```

기본 주소 `http://localhost:8000`의 `GET /openapi.json`을 호출해 HTTP 상태와 등록된 엔드포인트 목록을 확인한다. 이 요청은 Google·Gemini를 호출하거나 크롤링하지 않는다. 호스트 포트를 `8001`로 설정했다면 주소를 지정한다.

```bash
python backend/scripts/test_api_http.py --base-url http://localhost:8001
```

API별 실제 HTTP 요청은 `--api`로 선택한다. 스크립트는 아래 JSON 파일을 읽어 실행 중인 서버에 `POST`한다.

| `--api` | 요청 JSON | Endpoint |
| --- | --- | --- |
| `places` | [backend/examples/http/places.json](../examples/http/places.json) | `/api/places/search` |
| `nearby` | [backend/examples/http/nearby.json](../examples/http/nearby.json) | `/api/nearby/recommend` |
| `routes` | [backend/examples/http/routes.json](../examples/http/routes.json) | `/api/routes/optimize` |

```bash
python backend/scripts/test_api_http.py --api places
python backend/scripts/test_api_http.py --api nearby
python backend/scripts/test_api_http.py --api routes
```

외부 API를 호출하지 않고 실제 서버의 요청 검증을 확인하려면 빈 객체 `{}`가 들어 있는 [invalid.json](../examples/http/invalid.json)을 보낸다. 필수값이 없으므로 서버가 `422`로 거부하며 Google·Gemini 호출과 크롤링은 발생하지 않는다.

```bash
python backend/scripts/test_api_http.py --api places --input backend/examples/http/invalid.json --expect-status 422
```

`--expect-status`의 기본값은 `200`이다. 위처럼 `422`를 예상 상태로 지정하면 해당 오류 응답도 검증 성공으로 처리한다. `--api nearby`나 `--api routes`도 같은 빈 본문으로 요청 검증을 확인할 수 있다.

`places`와 `nearby`의 정상 요청은 실제 Google·Gemini API를 사용해 호출 한도와 비용이 발생할 수 있다. `routes`는 실제 역 조회와 브라우저 크롤링을 수행하므로 시간이 걸릴 수 있다. 키는 요청 JSON에 넣지 않고 앞에서 준비한 루트 `.env`를 Compose가 서버 컨테이너에 주입한다.

요청 문장·역·장소·날짜 등 본문을 바꾸려면 해당 JSON 파일을 편집한다. 별도 JSON을 사용하려면 `--input`으로 지정한다. 예를 들어 `path.json`에 커스텀 장소 검색 본문을 저장한 뒤 다음처럼 실행한다.

```bash
python backend/scripts/test_api_http.py --api places --input path.json
```

기본 대기 시간은 `300`초이며 `--timeout`으로 바꾼다. `--base-url`, `--input`, `--timeout`, `--output`은 함께 사용할 수 있다. 다음 예시는 호스트 `8001`번 포트로 요청하고 응답 JSON을 파일에도 저장한다.

```bash
python backend/scripts/test_api_http.py --api places --base-url http://localhost:8001 --input path.json --timeout 300 --output /tmp/jpass-response.json
```

HTTP 상태는 표준 오류(stderr), 응답 JSON은 표준 출력(stdout)에 표시한다. HTTP 오류 응답도 본문을 표시한다. 응답 상태가 `--expect-status`와 같으면 종료 코드 `0`, 다르면 `1`, 입력 파일·옵션 또는 연결 오류는 `2`다. 서버가 반환한 `422`, `503` 등의 `detail`도 출력에서 확인할 수 있다.

같은 JSON 파일을 `curl`로 보낼 수도 있다.

```bash
curl -sS -X POST http://localhost:8000/api/places/search -H 'Content-Type: application/json' --data-binary @backend/examples/http/places.json
```

요청 JSON 수정은 다음 HTTP 호출에 바로 반영된다. 서버 소스나 의존성을 수정했다면 `docker compose up --build -d`로 이미지를 다시 빌드한다. `.env`만 바꿨다면 다음 절의 컨테이너 재생성 명령을 사용한다.

기존 `backend/tests`는 서비스 함수를 직접 호출하는 수동 실행과 모킹 검증을 포함한다. 실행 중인 Docker HTTP 서버의 연결·라우팅·응답은 이 HTTP 스크립트로 확인한다. 런타임 이미지에는 `tests`와 Poetry가 없으므로 위 스크립트는 호스트에서 실행한다.

## 3. 수정 후 반영·종료

소스 코드나 의존성을 바꿨다면 저장소 루트에서 다시 빌드하고 실행한다. 기본 구성은 호스트의 소스 파일을 컨테이너에 실시간 연결하지 않는다.

```bash
docker compose up --build -d
```

`.env`만 바꿨다면 이미지를 다시 빌드할 필요 없이 컨테이너를 다시 생성한다. `restart`만 실행하면 Compose가 변경한 환경 변수를 다시 읽지 않는다.

```bash
docker compose up -d --force-recreate api
```

실행을 종료하고 Compose 컨테이너·네트워크를 정리한다.

```bash
docker compose down
```

`down`은 로컬 이미지와 `.env`, 저장소 파일을 지우지 않는다. 다시 시작하려면 `docker compose up -d`를 실행한다. `restart: unless-stopped`는 Docker가 다시 시작될 때 중지하지 않은 컨테이너를 재시작하는 정책이며, `down`으로 삭제한 컨테이너를 자동 생성하지 않는다.

## 로컬 Docker 문제 해결: 외부 DNS·연결 실패

장소 검색의 `502`는 여러 외부 서비스 오류를 묶은 응답이므로, 응답 문구만으로 DNS 문제를 단정하지 않는다. 한 로컬 환경에서는 내부 예외를 진단한 결과 Gemini 요청의 `httpx.ConnectError`와 `Temporary failure in name resolution`이 원인이었다. 서버 키가 존재하는 상태에서 Compose의 사용자 정의 네트워크 `jpass-opt_default`는 DNS 조회와 외부 HTTPS(443) 연결에 실패했고, Docker 기본 `bridge`에서는 DNS와 Google 443 연결이 성공했다. 이 환경에서는 Compose `down`·`up`으로 네트워크를 다시 만들어도 문제가 계속됐다.

다음 명령으로 실행 중인 API 컨테이너의 Gemini 호스트 이름 조회를 확인할 수 있다. DNS만 조회하므로 유료 API 요청을 보내지 않으며 키나 환경 변수 값을 출력하지 않는다.

```bash
docker compose exec -T api python -c 'import socket; print(socket.getaddrinfo("generativelanguage.googleapis.com", 443))'
```

주소 목록이 출력되면 이름 조회가 성공한 것이다. 이 확인만으로 외부 HTTPS 연결이나 실제 장소 검색의 성공까지 보장하지는 않는다.

**사용자 정의 네트워크에서 실패하고 기본 `bridge`에서는 연결되는 로컬 환경에서만** [compose.local.yaml](../../compose.local.yaml)을 선택한다. 이 파일은 `services.api.network_mode: bridge`를 지정하는 로컬용 덮어쓰기 설정이며, 기본 [compose.yaml](../../compose.yaml)은 유지한다.

```bash
docker compose -f compose.yaml -f compose.local.yaml up -d --force-recreate --wait
```

컨테이너가 준비되면 실제 요청을 다시 확인한다. 이 요청은 Google·Gemini API 호출 한도와 비용을 사용할 수 있다. 호스트 포트를 바꿨다면 앞 절처럼 `--base-url`도 지정한다.

```bash
python backend/scripts/test_api_http.py --api places
```

위 문제가 발생한 환경에서는 로컬 설정 적용 후 같은 장소 검색 요청이 HTTP `200`으로 성공했고, Gemini 검색어 증강과 Google Places 결과 반환까지 확인했다.

이 로컬 설정을 계속 사용한다면 후속 `up` 명령에도 두 `-f` 옵션을 함께 사용한다. 기본 `bridge`는 현재 단일 API 컨테이너를 위한 임시 우회 설정이다. 다른 서비스를 추가해 서비스 이름으로 통신해야 한다면 사용자 정의 네트워크의 자동 DNS가 필요하므로 이 설정을 그대로 확장하지 않는다. 네트워크 차이는 [Docker 공식 bridge 문서](https://docs.docker.com/engine/network/drivers/bridge/#differences-between-user-defined-bridges-and-the-default-bridge)를 참고한다.

## 컨테이너 구성과 동선 최적화

| 구성 | 현재 설정 |
| --- | --- |
| Python | `3.12` 기반 이미지 |
| 의존성 | `backend/poetry.lock`에 맞춰 런타임 의존성을 설치한 가상 환경 사용 |
| 브라우저 | 설치된 Playwright에 대응하는 Chromium과 실행용 시스템 패키지 포함 |
| 화면 환경 | Xvfb 안에서 앱을 실행해 기존 `headless=False` 크롤러 지원 |
| 앱 프로세스 | Uvicorn worker 1개 |
| 실행 사용자 | root가 아닌 UID `10001` |
| 프로세스 종료 | `tini -g`를 통해 종료 신호를 프로세스 그룹으로 전달 |
| 상태 확인 | HTTP `/openapi.json` healthcheck |
| Chromium 공유 메모리 | Compose의 `shm_size: 1gb` |
| 로그 보관 | Compose의 `json-file` 로그를 파일당 `10m`, 최대 3개로 순환 |

동선 최적화의 크롤링 잠금은 **앱 프로세스 하나에 적용**된다. worker를 늘리거나 컨테이너를 여러 개 실행하면 잠금도 각각 따로 생기므로 전체 최적화 작업 수를 하나로 제한하지 못한다. 기본 구성의 단일 worker를 유지하고, 복제 확장이 필요하면 작업 큐나 공유 잠금 등의 설계를 먼저 확인한다. 장소 검색·주변 추천은 이 동선 잠금을 사용하지 않는다.

## 다른 서버에 배포하기

배포할 서버에 Docker와 Compose를 설치하고 저장소를 가져온 뒤, 그 서버의 저장소 루트에서 `.env`를 준비하고 같은 명령을 실행한다.

```bash
docker compose up --build -d
docker compose ps
docker compose logs -f api
```

사용자의 PC에서 접속할 때 `localhost`는 사용자 PC를 가리킨다. 원격 서버에 실행했다면 `http://서버주소:8000/docs`처럼 **해당 서버의 IP 또는 도메인**을 사용한다. Codespaces라면 전달된 `8000`번 포트 주소 뒤에 `/docs`를 붙인다.

기본 `8000:8000` 포트 매핑은 서버의 네트워크 인터페이스에 API 포트를 게시한다. `API_PORT`를 바꿨다면 변경한 호스트 포트를 기준으로 서버 방화벽·클라우드 보안 그룹의 포트 허용을 설정한다. 도메인·TLS 인증서·HTTPS 역방향 프록시는 별도 서버 설정이다. HTTPS 프론트엔드와 연결할 때는 API도 HTTPS 주소로 제공해 브라우저의 혼합 콘텐츠 차단을 피한다. 이 저장소의 Compose 실행만으로 도메인이나 HTTPS가 자동 설정되지는 않는다.

## 선택: 이미지를 레지스트리에 공유하기

다른 서버에서 소스를 다시 빌드하지 않고 동일한 이미지를 받게 하려면 이미지 레지스트리에 올린다. 아래는 GitHub Container Registry 예시이며 직접 실행할 때만 업로드된다. `YOUR_OWNER`를 실제 **소문자** GitHub 계정·조직 이름으로 바꾸고 `v1`은 배포 버전 태그로 사용한다.

```bash
docker login ghcr.io
docker tag jpass-api:local ghcr.io/YOUR_OWNER/jpass-api:v1
docker push ghcr.io/YOUR_OWNER/jpass-api:v1
```

Docker Hub를 선택한다면 로그인은 `docker login`, 이미지 이름은 `YOUR_DOCKERHUB_ID/jpass-api:v1` 형식을 사용한다. 비공개 이미지라면 받는 서버에도 해당 레지스트리의 읽기 권한이 필요하다. API 키는 이미지가 아닌 배포 서버의 `.env`로 각각 전달한다.

이미지를 받는 서버에서 `compose.yaml`의 `image` 값을 배포한 태그로 바꾼 뒤, 그 저장소 루트에서 실행한다. 다음 명령은 이미지를 받아 사용하며 로컬 소스를 빌드하지 않는다.

```bash
docker compose pull api
docker compose up -d --no-build api
```

Kubernetes나 Argo CD로 배포할 때도 이 이미지를 후속 배포 설정의 컨테이너 이미지로 재사용할 수 있다. 환경 변수·서비스·Ingress와 복제 수는 해당 환경의 배포 설정으로 관리한다.
