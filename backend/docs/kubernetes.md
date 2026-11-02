# 로컬 Kubernetes 실행: minikube

현재 Docker 이미지를 minikube에 불러오고, ConfigMap·Secret을 연결한 API Pod 1개를 실행한다. 저장소 루트에서 명령을 실행한다. Docker Compose API는 `8000`, Kubernetes API 테스트는 `8001`을 사용하므로 함께 실행할 수 있다.

## 준비물과 파일

Docker, minikube, kubectl, Python 3이 필요하다. Python 스크립트는 표준 라이브러리만 사용하며 Poetry가 필요하지 않다. 아래 구성은 CPU 2개·메모리 4GB를 minikube에 할당한다. 이미지·캐시를 위한 디스크와 첫 실행의 인터넷 연결도 필요하다. [minikube 시작 안내](https://minikube.sigs.k8s.io/docs/start/)

| 파일 | 역할 |
| --- | --- |
| [kustomization.yaml](../../k8s/local/kustomization.yaml) | 아래 리소스를 한 번에 적용하는 진입점 |
| [namespace.yaml](../../k8s/local/namespace.yaml) | JPass 리소스를 모을 `jpass` namespace |
| [configmap.yaml](../../k8s/local/configmap.yaml) | 모델 이름·CORS 같은 일반 설정 |
| [deployment.yaml](../../k8s/local/deployment.yaml) | 이미지·Pod 수·환경 변수 연결·상태 확인·자원 설정 |
| [service.yaml](../../k8s/local/service.yaml) | API Pod에 연결하는 내부 Service |
| [apply_k8s_secret.py](../scripts/apply_k8s_secret.py) | 루트 `.env`의 API 키 두 개를 로컬 Secret에 적용 |

실제 Secret YAML이나 API 키는 Git에 저장하지 않는다. Secret은 다음 단계의 스크립트로 별도 생성하며 Kustomize 리소스 목록에는 없다.

## 1. 클러스터 시작

```bash
minikube start -p jpass-local --driver=docker --container-runtime=containerd --network=bridge --cpus=2 --memory=4096 --kubernetes-version=v1.37.0 --interactive=false
kubectl --context jpass-local get nodes
```

노드의 `STATUS`가 `Ready`가 되면 다음으로 진행한다. `jpass-local`은 이 프로젝트의 로컬 클러스터 이름이다. 명령마다 `--context jpass-local`을 지정해 적용 대상을 명확히 한다.

현재 작업 환경에서는 Docker 사용자 정의 네트워크의 외부 연결이 실패했기 때문에 `--network=bridge`로 이미 연결이 확인된 기본 Docker 네트워크를 사용한다. 이 옵션은 minikube 노드 컨테이너의 Docker 네트워크 설정이다. API Pod의 네트워크는 Kubernetes CNI가 관리하며 `hostNetwork`를 사용하지 않는다. 다른 환경에서 기본 minikube 네트워크가 정상이라면 `--network=bridge`를 생략할 수 있다. [minikube Docker 드라이버](https://minikube.sigs.k8s.io/docs/drivers/docker/), [시작 옵션](https://minikube.sigs.k8s.io/docs/commands/start/)

## 2. 이미지 준비

기존 이미지가 있다면 빌드는 생략할 수 있다. 소스를 바꿨다면 다시 빌드한다.

```bash
docker build -t jpass-api:local .
minikube image load jpass-api:local -p jpass-local
```

호스트 Docker 이미지와 minikube 노드의 이미지 저장소는 별개라서 `image load`가 필요하다. 로컬 Deployment는 `imagePullPolicy: Never`이므로 레지스트리에서 이미지를 받지 않는다. 이미지를 불러오지 않으면 `ErrImageNeverPull`이 발생한다. [minikube 이미지 로딩 안내](https://minikube.sigs.k8s.io/docs/handbook/pushing/)

## 3. namespace와 API 키 준비

루트 `.env`에 `GOOGLE_PLACE_API_KEY`, `GEMINI_API_KEY`가 설정되어 있어야 한다. 기존 Compose 실행에 사용한 값을 그대로 사용한다.

```bash
kubectl --context jpass-local apply -f k8s/local/namespace.yaml
python backend/scripts/apply_k8s_secret.py
```

스크립트는 Docker Compose가 해석한 환경 값을 메모리에서 읽고, API 키 두 개만 `jpass/jpass-api-secrets`에 server-side apply한다. 키를 화면·명령 인자·파일에 출력하지 않는다. `GEMINI_MODEL`, `CORS_ORIGINS`, `API_PORT`는 Secret에 복사하지 않는다. API 키가 비어 있으면 적용하지 않고 종료한다. 이미 존재하는 `.env`를 수정하거나 컨테이너를 실행하지 않는다.

일반 설정은 [configmap.yaml](../../k8s/local/configmap.yaml)에서 바꾼다. Kubernetes는 Compose의 `env_file`을 자동으로 읽지 않는다. Deployment가 ConfigMap과 Secret을 참조하면 컨테이너의 환경 변수로 전달된다. 현재 앱이 읽는 환경 변수 이름을 유지하므로 `Settings` 리팩터링 없이 실행할 수 있다.

## 4. API 배포

```bash
kubectl --context jpass-local apply -k k8s/local
kubectl --context jpass-local -n jpass rollout status deployment/jpass-api --timeout=180s
kubectl --context jpass-local -n jpass get pods,service
```

Pod가 `Running`, `READY`가 `1/1`인지 확인한다. `kubectl kustomize k8s/local`로 비밀값이 없는 배포 파일을 미리 확인할 수도 있다.

## 5. HTTP 테스트

첫 번째 터미널에서 포트 전달을 실행하고 켜둔다.

```bash
kubectl --context jpass-local -n jpass port-forward service/jpass-api 8001:8000
```

두 번째 터미널에서 서버 연결과 실제 API를 확인한다.

```bash
# 외부 API 호출 없이 서버 응답 확인
python backend/scripts/test_api_http.py --base-url http://localhost:8001

# 실제 API 요청: 필요한 항목을 선택해 실행
python backend/scripts/test_api_http.py --api places --base-url http://localhost:8001
python backend/scripts/test_api_http.py --api nearby --base-url http://localhost:8001
python backend/scripts/test_api_http.py --api routes --base-url http://localhost:8001
```

검색·추천은 Google·Gemini 사용량이 발생하며, 동선 최적화는 실제 역 조회·브라우저 크롤링을 수행한다. 요청 본문은 [HTTP 예시 디렉터리](../examples/http)에서 바꾼다. 응답 저장·대기 시간·입력 검증 테스트는 [HTTP 테스트 안내](docker.md#로컬-http-테스트)를 참고한다.

Swagger UI는 `http://localhost:8001/docs`다. Codespaces에서는 VS Code의 Ports 탭에서 **8001**을 열고 전달된 주소 뒤에 `/docs`를 붙인다. 포트 전달은 해당 터미널이 실행 중일 때만 유지된다. Pod를 교체한 뒤에는 포트 전달도 다시 실행한다. Service는 `ClusterIP`이므로 이 설정만으로 인터넷에 API가 공개되지는 않는다.

## 설정·이미지 변경 반영

아래의 `k8s/local` 명령은 직접 불러온 로컬 이미지를 사용한다. GHCR 배포로 전환한 뒤에는 [GHCR 배포 절차](#ghcr에-업로드한-이미지로-실행)의 `k8s/ghcr` 경로를 사용한다. `apply -k k8s/local`을 다시 실행하면 로컬 이미지 설정으로 돌아간다.

일반 설정 변경:

```bash
kubectl --context jpass-local apply -k k8s/local
kubectl --context jpass-local -n jpass rollout restart deployment/jpass-api
kubectl --context jpass-local -n jpass rollout status deployment/jpass-api --timeout=360s
```

API 키 변경:

```bash
python backend/scripts/apply_k8s_secret.py
kubectl --context jpass-local -n jpass rollout restart deployment/jpass-api
```

ConfigMap·Secret을 환경 변수로 전달하므로 값만 바꿔도 이미 실행 중인 프로세스의 환경 변수가 바뀌지는 않는다. 컨테이너를 다시 실행해야 한다.

소스나 의존성 변경:

```bash
docker build -t jpass-api:local .
minikube image load jpass-api:local -p jpass-local --overwrite=true
kubectl --context jpass-local -n jpass rollout restart deployment/jpass-api
kubectl --context jpass-local -n jpass rollout status deployment/jpass-api --timeout=360s
```

## GHCR에 업로드한 이미지로 실행

이미지 저장소의 `ghcr.io/kimtaeyun02/jpass-api:v1`을 현재 minikube에서 직접 받아 실행한다. 앞의 namespace·ConfigMap·API 키 Secret은 재사용하며 `minikube image load` 단계가 필요하지 않다. [k8s/ghcr/kustomization.yaml](../../k8s/ghcr/kustomization.yaml)이 로컬 구성을 가져온 뒤 이미지 주소·다운로드 정책·인증 참조를 변경한다.

| 항목 | 설정 |
| --- | --- |
| 이미지 | `ghcr.io/kimtaeyun02/jpass-api:v1`과 업로드 확인한 digest로 고정 |
| 다운로드 정책 | `Always`: Pod를 시작할 때 레지스트리에 접근 |
| 다운로드 인증 | `imagePullSecrets`의 `ghcr-pull-secret` |
| 배포 대상 | 현재 연결한 로컬 `jpass-local` 클러스터의 `jpass` namespace |

호스트에서 `docker login ghcr.io`로 로그인한 뒤, 다음 명령으로 GHCR 인증만 클러스터에 복사한다. [apply_ghcr_secret.py](../scripts/apply_ghcr_secret.py)는 Docker 설정 또는 credential helper에서 GHCR 자격 증명만 읽어 메모리에서 Secret을 만든다. 다른 레지스트리의 로그인 정보는 복사하지 않고, 토큰 값이나 Secret 파일을 출력·저장하지 않는다.

```bash
python backend/scripts/apply_ghcr_secret.py
kubectl --context jpass-local apply -k k8s/ghcr
kubectl --context jpass-local -n jpass rollout status deployment/jpass-api --timeout=240s
kubectl --context jpass-local -n jpass get pods
```

Secret은 같은 `jpass` namespace에 생성한다. GHCR 다운로드용 Secret과 앱에서 Google·Gemini를 호출하는 `jpass-api-secrets`는 역할이 다르다. 비공개 레지스트리 인증 방식은 [Kubernetes 공식 안내](https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/)를 참고한다.

Pod 교체가 끝나면 포트 전달을 다시 실행하고 기존 HTTP 테스트 명령을 사용한다.

```bash
kubectl --context jpass-local -n jpass port-forward service/jpass-api 8001:8000
```

```bash
python backend/scripts/test_api_http.py --base-url http://localhost:8001
python backend/scripts/test_api_http.py --api places --base-url http://localhost:8001
```

새 버전은 별도 태그로 업로드하고 `k8s/ghcr/kustomization.yaml`의 `newTag`와 `digest`를 함께 갱신한 뒤 `apply -k k8s/ghcr`을 실행한다. digest는 다음 명령에서 확인한다.

```bash
docker buildx imagetools inspect ghcr.io/kimtaeyun02/jpass-api:v1
```

GHCR의 공개 여부 변경이나 토큰 교체 후에는 다운로드 인증을 다시 준비한다. 운영 클러스터에서는 해당 클러스터의 context·설정·인증을 별도로 준비한다. 이 GHCR 구성도 현재는 로컬 minikube 검증용이며, 외부 접속은 여전히 포트 전달로 한다.

## 상태와 오류 확인

```bash
kubectl --context jpass-local -n jpass get pods
kubectl --context jpass-local -n jpass logs -f deployment/jpass-api --tail=100
kubectl --context jpass-local -n jpass get events --sort-by=.metadata.creationTimestamp
```

`ErrImageNeverPull`이면 이미지 로딩을, `CreateContainerConfigError`이면 API 키 Secret 생성과 키 이름을 확인한다. GHCR 배포의 `ImagePullBackOff`이면 이미지 주소와 `ghcr-pull-secret`의 인증을 확인한다. 외부 연결 오류가 의심되면 API 키를 노출하지 않고 DNS만 확인한다.

```bash
kubectl --context jpass-local -n jpass exec deployment/jpass-api -- python -c 'import socket; print(socket.getaddrinfo("generativelanguage.googleapis.com", 443))'
```

## 현재 실행 설정

| 항목 | 설정·이유 |
| --- | --- |
| Pod·worker | Pod 1개, 이미지 기본 Uvicorn worker 1개 |
| 교체 방식 | `Recreate`: 배포 교체 때 기존 Pod를 종료한 후 새 Pod 실행. 교체 중 API 중단 발생 |
| 크롤링 잠금 | 프로세스 안에서만 공유되므로 Pod 복제 수를 늘리기 전에 작업 큐·공유 잠금 설계 필요 |
| 상태 확인 | startup·readiness·liveness probe에서 `/openapi.json` 호출. Google·Gemini·크롤링 실행 없음 |
| 자원 | 요청 CPU 250m·메모리 512Mi, 상한 CPU 1개·메모리 2Gi. 실제 크롤링 부하를 보며 조정 |
| 브라우저 공유 메모리 | `/dev/shm`에 메모리 `emptyDir` 최대 1Gi 연결. 사용량은 메모리 사용량에 포함 |
| 종료 대기 | 최대 300초. 처리 중 요청이 항상 완료된다는 보장은 아님 |

Dockerfile의 `HEALTHCHECK`와 별개로 Kubernetes probe를 명시했다. `Ready`는 API 서버가 응답한다는 의미이며 외부 API 키의 유효성이나 실제 크롤링 성공까지 확인하지 않는다. [probe 설명](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/), [메모리 emptyDir](https://kubernetes.io/docs/concepts/storage/volumes/#emptydir)

## 중지·재시작

클러스터를 보존하면서 실행을 멈추려면 다음 명령을 사용한다.

```bash
minikube stop -p jpass-local
```

다시 실행할 때는 처음의 `minikube start` 명령을 사용한다. 보존된 리소스의 상태를 확인하고 포트 전달을 다시 실행한다.

## 이후 운영 배포·Argo CD 연결

`k8s/local`과 `k8s/ghcr`은 로컬 minikube용이다. 운영에서는 운영 환경의 ConfigMap·Secret·외부 접속·HTTPS 설정을 별도로 구성한다. 실제 키나 레지스트리 토큰이 들어간 Secret 파일을 Git에 커밋하지 않는다. 로컬 Secret 생성 스크립트는 로컬 테스트를 위한 도구다.

Git의 배포 설정을 Argo CD로 연결하고 Pod를 올리는 절차는 [Argo CD 실습 안내](argocd.md)를 참고한다. Secret 값은 별도의 스크립트로 준비한다.

## 확인한 범위

로컬 minikube에서 서버 측 manifest 검증, Pod `1/1 Running`, OpenAPI HTTP `200`, 실제 Gemini 검색어 증강·Google Places 검색 HTTP `200`, 세 API의 빈 본문 HTTP `422`를 확인했다. Pod의 비 root 사용자(UID `10001`)로 Xvfb·Chromium을 실행해 외부 접속 없이 화면 캡처가 생성되는 것도 확인했다. 주변 추천·동선 최적화의 실제 정상 결과까지 검증한 것은 아니며 위의 API별 명령으로 추가 확인할 수 있다.

GHCR 구성에서는 Kubernetes의 `Successfully pulled image` 이벤트, 업로드한 digest를 사용하는 새 Pod의 준비 상태, 해당 Pod의 OpenAPI HTTP `200`을 확인했다.
