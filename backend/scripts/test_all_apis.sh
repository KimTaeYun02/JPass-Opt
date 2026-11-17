#!/bin/sh
# Docker 재실행 후 전체 HTTP API를 확인한다. 실행: sh backend/scripts/test_all_apis.sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
cd "$project_root"

for dependency in docker python3; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        printf '필요한 명령을 찾을 수 없습니다: %s\n' "$dependency" >&2
        exit 2
    fi
done

results_dir="$project_root/backend/tests/api_http_results"
http_runner="$project_root/backend/scripts/test_api_http.py"
mkdir -p "$results_dir"
# 이번 실행에서 응답을 받지 못하면 지난 실행의 결과가 보이지 않게 한다.
for api in openapi places nearby routes; do
    : > "$results_dir/$api.response.json"
done

# 원본 예시를 유지하고, 매번 같은 파일에 실제 테스트 요청을 준비한다.
python3 - "$results_dir" <<'PY'
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

examples = Path("backend/examples/http")
results = Path(sys.argv[1])
tomorrow = datetime.now(ZoneInfo("Asia/Tokyo")).date() + timedelta(days=1)

for api in ("places", "nearby", "routes"):
    payload = json.loads((examples / f"{api}.json").read_text(encoding="utf-8"))
    if api == "nearby":
        payload["travelTypes"] = ["culture", "nature"]
        payload["placeCount"] = 3
    elif api == "routes":
        payload["startDate"] = tomorrow.isoformat()
    (results / f"{api}.request.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

print(f"동선 조회 날짜: {tomorrow} (일본 시간 기준 내일)")
PY

printf '\nDocker 이미지 빌드·컨테이너 재생성·서버 기동 대기\n'
if ! docker compose up --build -d --force-recreate --wait --wait-timeout 180 api; then
    docker compose logs --tail 100 api || true
    exit 1
fi

# .env의 API_PORT에 따라 실제 공개된 포트를 읽는다. API 키는 출력하지 않는다.
api_port=$(docker compose port api 8000 | sed -n '1s/.*://p')
case "$api_port" in
    ''|*[!0-9]*)
        printf 'API의 공개 포트를 확인할 수 없습니다.\n' >&2
        exit 2
        ;;
esac
base_url="http://127.0.0.1:$api_port"

printf '\n서버 확인: %s\n' "$base_url"
if ! python3 "$http_runner" --base-url "$base_url" \
    --output "$results_dir/openapi.response.json"; then
    docker compose logs --tail 100 api || true
    exit 1
fi

passed=0
failed=0
for api in places nearby routes; do
    printf '\n========== %s API ==========\n' "$api"
    if python3 "$http_runner" --base-url "$base_url" --api "$api" \
        --input "$results_dir/$api.request.json" \
        --output "$results_dir/$api.response.json"; then
        printf '[PASS] %s\n' "$api"
        passed=$((passed + 1))
    else
        printf '[FAIL] %s\n' "$api"
        failed=$((failed + 1))
    fi
done

printf '\n완료: 성공 %s개 / 실패 %s개\n결과 폴더: %s\n' \
    "$passed" "$failed" "$results_dir"
if [ "$failed" -gt 0 ]; then
    printf '\n최근 서버 로그\n'
    docker compose logs --tail 100 api || true
    exit 1
fi

printf '서버는 실행 상태로 유지됩니다. 로그 확인: docker compose logs -f api\n'
