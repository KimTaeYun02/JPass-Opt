# 로컬 Argo CD로 API Pod 배포하기

현재 `jpass-local` minikube에 Argo CD를 설치하고, 원격 Git의 `feat/k8s-deployment` 브랜치에 있는 `k8s/ghcr` 설정을 읽어 API를 배포한다. 명령은 저장소 루트에서 실행한다.

```text
Git의 Kubernetes 설정
  → Argo CD가 비교
  → Sync 실행
  → Deployment의 원하는 상태 적용
  → GHCR 이미지로 API Pod 실행
```

## API Pod 내리기

```bash
kubectl --context jpass-local -n jpass scale deployment/jpass-api --replicas=0
kubectl --context jpass-local -n jpass get pods
```

Pod가 사라지고 `No resources found`가 나오면 내려간 것이다. Deployment·Service·ConfigMap·Secret은 유지한다. `delete pod`만 실행하면 Deployment가 원하는 개수를 맞추려고 대체 Pod를 만든다.

이 실습은 수동 Sync로 시작한다. 수동으로 복제 수를 `0`으로 바꾸면 Argo CD에는 Git의 `replicas: 1`과 다른 상태인 `OutOfSync`로 보인다. 이후 Sync하면 다시 1개가 실행된다. 나중에 자동 Sync와 self-heal을 켜면 수동 변경도 되돌릴 수 있으므로, 계속 내려두려면 Git의 복제 수도 `0`으로 변경해야 한다.

## 파일과 준비물

| 항목 | 역할 |
| --- | --- |
| [install/kustomization.yaml](../../k8s/argocd/install/kustomization.yaml) | 공식 Argo CD `v3.5.6` 설치 |
| [project.yaml](../../k8s/argocd/project.yaml) | 읽을 저장소와 배포할 namespace 지정 |
| [application.yaml](../../k8s/argocd/application.yaml) | Git 브랜치·설정 경로·배포 대상 연결 |
| [apply_argocd_repo_secret.py](../scripts/apply_argocd_repo_secret.py) | GitHub CLI 인증을 Argo CD의 비공개 Git 읽기 인증으로 전달 |

기존 [Kubernetes 설정](kubernetes.md)의 API 키 Secret·GHCR 인증은 `jpass` namespace에 준비되어 있어야 한다. Git 인증용 `gh` CLI도 필요하다. Secret 생성 스크립트는 Python 표준 라이브러리만 사용한다.

## 1. Argo CD 설치

```bash
kubectl --context jpass-local apply --server-side --force-conflicts -k k8s/argocd/install
kubectl --context jpass-local -n argocd rollout status deployment/argocd-server --timeout=300s
kubectl --context jpass-local -n argocd rollout status deployment/argocd-repo-server --timeout=300s
kubectl --context jpass-local -n argocd rollout status statefulset/argocd-application-controller --timeout=300s
kubectl --context jpass-local -n argocd get pods
```

공식 설치 파일을 버전으로 고정하고 server-side apply로 적용한다. Argo CD CRD 크기 때문에 공식 안내도 server-side apply를 사용한다. `--force-conflicts`는 설치 파일이 정의한 필드의 소유권을 가져오므로 기존 Argo CD에 적용할 때는 변경 내용을 확인한다. [Argo CD 설치 안내](https://argo-cd.readthedocs.io/en/stable/getting_started/#1-install-argo-cd)

Argo CD는 같은 minikube 안의 `argocd` namespace에서 실행된다. API Pod가 있는 `jpass` namespace와 구분된다.

## 2. 비공개 Git 저장소 인증

```bash
python backend/scripts/apply_argocd_repo_secret.py
```

스크립트는 `gh auth token` 결과를 메모리에서 받아 `argocd/jpass-git-repository` Secret에 적용한다. 토큰을 화면·명령 인자·파일에 출력하지 않는다. 이 인증은 Git 읽기에 사용한다. 저장소 읽기 권한이 있는 GitHub CLI 로그인이 필요하며, 인증이 만료되면 다시 로그인하고 스크립트를 실행한다. Codespaces가 제공한 토큰은 해당 세션의 수명·권한을 따르므로 운영에서는 별도 GitHub App 또는 저장소 읽기 인증을 준비한다.

세 가지 인증의 역할은 다음과 같다.

| Secret | Namespace | 사용하는 곳 |
| --- | --- | --- |
| `jpass-api-secrets` | `jpass` | 앱의 Google·Gemini 호출 |
| `ghcr-pull-secret` | `jpass` | 노드의 GHCR 이미지 다운로드 |
| `jpass-git-repository` | `argocd` | Argo CD의 Git 설정 읽기 |

## 3. 배포 설정을 Git에 반영

Argo CD는 로컬 작업 파일 대신 원격 Git을 읽는다. 브랜치에 `k8s/local`·`k8s/ghcr` 파일이 커밋·push되어 있어야 한다. `.env`와 실제 Secret 값은 커밋하지 않는다.

아직 반영하지 않았다면 변경 내용을 검토하고 해당 기능 브랜치에 반영한다.

```bash
git add README.md backend/README.md backend/docs/README.md backend/docs/kubernetes.md backend/docs/argocd.md backend/scripts/apply_k8s_secret.py backend/scripts/apply_ghcr_secret.py backend/scripts/apply_argocd_repo_secret.py k8s
git diff --cached --stat
git commit -m "feat: add local Kubernetes and Argo CD deployment"
git push origin feat/k8s-deployment
```

Application은 `main` 대신 `feat/k8s-deployment`를 추적한다. 나중에 머지하고 추적 브랜치를 바꾸려면 `application.yaml`의 `targetRevision`을 변경한다.

## 4. Application 연결

```bash
kubectl --context jpass-local apply -k k8s/argocd
kubectl --context jpass-local -n argocd get applications
```

Application의 주요 설정:

| 필드 | 값 |
| --- | --- |
| 이름 | `jpass-api` |
| Git 저장소 | `https://github.com/KimTaeYun02/JPass-Opt.git` |
| 브랜치 | `feat/k8s-deployment` |
| 경로 | `k8s/ghcr` |
| 배포 대상 | 같은 클러스터의 `jpass` namespace |
| Sync 정책 | 수동 |

Application은 API 이미지의 소스 코드를 빌드하지 않는다. 이미 GHCR에 올라간 이미지 주소를 Git의 YAML에서 읽어 배포한다.

모델 설정은 Git의 [ConfigMap](../../k8s/local/configmap.yaml)에 명시한다. 공통 기본 모델인 `GEMINI_MODEL`이 미설정·빈 문자열·공백뿐이면 앱 시작이 실패한다. 주변 추천용 `GEMINI_NEARBY_MODEL`은 선택 사항이며, 미설정·빈 문자열·공백뿐이면 `GEMINI_MODEL`을 사용한다. 코드에 고정된 모델명으로 대체하지 않는다. 이 검증을 반영한 이미지를 GHCR에 올리고 태그·digest도 갱신해야 적용된다.

## 5. 웹 UI 열기

첫 번째 터미널에서 포트 전달을 켜둔다.

```bash
kubectl --context jpass-local -n argocd port-forward service/argocd-server 8080:443
```

`https://localhost:8080`에 접속한다. 기본 설치는 자체 서명 인증서를 사용한다. Codespaces에서는 Ports의 `8080`에 HTTPS 프로토콜을 지정하고 전달된 주소를 연다.

로그인 계정은 `admin`이다. 초기 비밀번호는 사용자 터미널에서 다음 명령으로 확인한다.

```bash
kubectl --context jpass-local -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
```

이 명령은 실제 비밀번호를 출력하므로 비밀번호를 Git이나 문서에 복사하지 않는다. 접속 후 계정 설정에서 비밀번호를 변경할 수 있다. [초기 로그인 안내](https://argo-cd.readthedocs.io/en/stable/getting_started/#4-log-in-using-the-cli)

## 6. Sync해서 API Pod 올리기

UI에서 `jpass-api`를 열고 **SYNC → SYNCHRONIZE**를 실행한다. Git 설정과 실행 상태가 다르면 `OutOfSync`, 적용이 맞으면 `Synced`, 앱이 준비되면 `Healthy`로 표시된다.

`Synced`는 앱의 정상 기동을 보장하지 않는다. `GEMINI_MODEL`이 누락되거나 비어 있으면 Pod 객체는 생성되지만 앱이 종료되어 `Ready`가 되지 않으며 컨테이너 재시작이 반복될 수 있다. API Pod 로그에서 설정 오류를 확인하고 Git의 ConfigMap을 수정한 뒤 Sync하고 Pod를 다시 시작한다. API 키 누락은 앱의 시작 검증 대상이 아니며, API 요청 시 `503`으로 처리한다.

같은 동작을 Kubernetes API로 요청할 수도 있다.

```bash
kubectl --context jpass-local -n argocd patch application jpass-api --type merge -p '{"operation":{"sync":{}}}'
kubectl --context jpass-local -n jpass rollout status deployment/jpass-api --timeout=240s
kubectl --context jpass-local -n jpass get pods
kubectl --context jpass-local -n argocd get applications
```

API Pod가 `1/1 Running`이면 별도 터미널에서 포트 전달과 HTTP 테스트를 실행한다.

```bash
kubectl --context jpass-local -n jpass port-forward service/jpass-api 8001:8000
```

```bash
python backend/scripts/test_api_http.py --base-url http://localhost:8001
python backend/scripts/test_api_http.py --api places --base-url http://localhost:8001
```

## 이후 변경과 오류 확인

일반 설정은 Git 파일을 수정하고 commit·push한 뒤 UI에서 Refresh·Sync한다. 새 이미지는 GHCR에 업로드하고 `k8s/ghcr/kustomization.yaml`의 태그·digest를 함께 갱신한다. API 키와 다운로드 토큰은 기존 Secret 적용 스크립트로 갱신한다.

```bash
kubectl --context jpass-local -n argocd get applications
kubectl --context jpass-local -n argocd describe application jpass-api
kubectl --context jpass-local -n argocd logs deployment/argocd-repo-server --tail=100
kubectl --context jpass-local -n jpass get pods
```

`ComparisonError`이면 저장소 인증·브랜치·원격의 `k8s/ghcr` 경로를 확인한다. `ImagePullBackOff`이면 GHCR 다운로드 인증을 확인한다. Argo CD UI 포트 전달과 API 포트 전달은 각각 별도 터미널에서 유지한다. `Ctrl+C`는 해당 포트 전달만 끝낸다.
