# AI 작업 참고 문서와 검토 기록

AI와 함께 기획·구현·검토할 때 사용한 프로젝트 문서를 모았습니다. 실행 방법은 [프론트 README](../../web/README.md), API 계약은 [Notion input/output docs](https://app.notion.com/p/3fd6b39941c2803f97bbe4d893075738)를 우선합니다. 오래된 제안이나 AI의 PASS는 현재 계약·구현 완료·실제 서비스 검증을 뜻하지 않습니다.

## 과거 기획·조사

기존 작업 폴더의 `docs/` 문서를 `planning/`으로 수록했습니다. 일부 기능은 제안 단계입니다. 저장소 링크와 개인 PC의 비공개 참고 파일 표기만 정리했습니다.

| 문서 | 용도·상태 |
| --- | --- |
| [프론트 데모 계획](planning/FRONTEND_DEMO_PLAN.md) | 2026-10-25 화면·API 제안. 후속 3단계 흐름·현재 구현 우선. |
| [프로그램 요구사항](planning/PROGRAM_SPEC.md) | 초기 장기 기획. NAVITIME·RAG·패스·고정 일정 등 미구현 구상 포함. |
| [Held–Karp 이전](planning/HELD_KARP_MIGRATION.md) | 프론트 오프라인 계산 설계. 백엔드 API 알고리즘과 구분. |
| [Notion 경로 자료 설계](planning/NOTION_ROUTING_DESIGN.md) | 불완전 자료·추정치·출발 시각 재조회 실험. |
| [경로 확장 조사](planning/ROUTING_EXPANSION_RESEARCH.md) | 당시 성능·공급자 조사. 현재 가격·약관 보증 아님. |

## 설계·검토

`design/`·`reviews/`는 현재 작업의 하위 프로세스로 호출한 Claude의 설계·정적 검토 결과를 선별해 수록했습니다. 기존 작업 폴더 `tmp/`의 같은 이름을 사용했으며 모델이 직접 실행한 테스트 결과가 아닙니다. 중간 실패 응답·원시 프롬프트·인증 로그·회의록 전체 복제·개인 전역 지침은 포함하지 않습니다.

| 설계 | 검토 |
| --- | --- |
| [지도 연결](design/demo-google-design.md) | [지도 재검토](reviews/demo-google-rereview.md) |
| [마커 버블 유지](design/demo-bubbles-design.md) | [버블 재검토](reviews/demo-bubbles-rereview.md) |
| [추천 다중 선택](design/demo-selection-design.md) | [선택 검토](reviews/demo-selection-review.md) |
| [API 연동](design/api-integration-design.md) | 현재 계약은 Notion·프론트 README 우선. 당시 백엔드 변경 제안은 이 PR에서 제외. |
| [이번 패키징 계약](design/frontend-packaging.md) | [패키징 검토·실행 결과](reviews/frontend-packaging.md) |

[이전 화면 흐름 QA](reviews/flow-qa-final.md)에는 포커스·입력 조합 등의 수동 검증 항목도 남아 있습니다. 과거 소스에 대한 추정을 현재 확정 결함으로 취급하지 마세요. 최종 통합 판단과 실제 실행 결과는 패키징 검토에 기록합니다.

현재 자동 CLI 보고서는 `web/.local-run/reports/`에 생성됩니다. 과거 문서의 `sample/` 결과 경로는 당시 기록입니다. 재현 명령은 [오프라인 경로 실험](../../web/docs/routing-prototypes.md)을 참고하세요.
