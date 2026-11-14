# 프론트 패키징 설계 계약

기준일: 2026-10-28. Claude Opus 사전 정적 설계 검토 후 실제 소스와 대조해 확정한 작업 범위입니다.

## 변경 대상과 인터페이스

- `origin/main`의 `b72b820`에서 `feat/frontend-demo` worktree를 분리합니다. 팀원 백엔드 브랜치와 더티 체크아웃은 보존합니다.
- `web/`에 소스·설정·lockfile·테스트·픽스처만 포함합니다. `.env.local`, 의존성, 빌드, 로그는 반입하지 않습니다.
- `start-demo.ps1 [-ApiTarget <HTTP(S) origin>] [-Port <1..65535>] [-ValidateOnly]`는 프론트 전용입니다. 스크립트 상대 경로에서 Node로 Vite를 직접 실행합니다. CLI 포트를 전달하고 `--strictPort`를 사용합니다. 호출자 cwd와 환경을 복원합니다.
- npm 명령은 플랫폼 공통 Vite 명령을 유지합니다. 개발 `/api` 프록시의 기본값은 `http://127.0.0.1:8000`입니다. `-ApiTarget`은 완전한 URL을 그대로 전달합니다.
- 오프라인 JavaScript CLI의 자동 보고서는 스크립트 상대 `web/.local-run/reports/`에 저장합니다. 알고리즘과 원본 데이터는 바꾸지 않습니다.
- 운영 문서는 `web/README.md`·`web/docs/`, AI 참고 문서는 `docs/ai/{planning,design,reviews}`로 나눕니다. Notion input/output docs가 API 최우선 기준입니다.

## 불변조건과 수용 기준

백엔드·`route_optimizer/`·`sample/`·Notion·UI·API DTO는 이번 정리에서 변경하지 않습니다. 독립 checkout에서 키 없이 `npm ci`, 전체 테스트, 두 HTML 빌드가 성공해야 합니다. 폴더명과 cwd를 바꿔 도우미의 계획·실제 자식 프로세스 인자·환경 복원 및 CLI 보고서 위치를 확인합니다. Vite의 dotenv·환경변수 우선순위도 외부 HTTP 없이 검사합니다. 로컬 키·빌드·의존성·보고서가 Git에 포함되지 않아야 합니다. 상대 문서 링크와 개인 절대 경로를 점검합니다.

초안 PR은 main을 대상으로 만들고 백엔드 별도 실행 필요와 브라우저·실제 공급자 QA 미실시를 명시합니다. GitHub Actions 추가는 이번 범위에서 제외하며 CI 성공을 주장하지 않습니다.

## 사전 검토와 다른 결정의 근거

- 검토의 스킴 없는 `host:port` 제안은 채택하지 않았습니다. 기존 `vite.config.ts`가 `loadEnv(mode, process.cwd(), '')`로 모든 접두어를 읽고 완전한 URL을 그대로 사용하므로 이 계약을 바꿀 이유가 없습니다. 환경변수·dotenv 테스트로 확인합니다.
- `demo.html`은 이미 `build.rolldownOptions.input`에 등록되어 있습니다. 실제 빌드 파일을 추가로 확인합니다.
- `sample` 경로를 바꾸는 대상은 Python이 아닌 로컬 JavaScript 보고서 생성기입니다. 키·Python 없이 실행할 수 있으므로 임시 복사본에서 실제 CLI로 검증합니다.
- 루트 ignore는 그대로 두고 기존 `web/.gitignore`의 의존성·빌드·`*.local`·`.local-run` 규칙과 루트 `.env.*` 규칙을 확인합니다. 중복 규칙은 추가하지 않습니다.

## 회귀 위험과 검증 한계

경로 공백, 다른 clone 이름, 포트 충돌의 실패 전달, 런처 환경 복원, 생성물이 기존 sample을 덮어쓰는 문제를 중점 검증합니다. 과거 기획은 아카이브로 표시해 완료 범위를 혼동하지 않게 합니다. 브라우저 접근 거부 경계를 우회하지 않으며 실제 지도·포커스·Google/Gemini·크롤러·프록시 왕복은 검증하지 않습니다.
