"""Send HTTP requests to a running API server using only Python's standard library.

From the repository root:
    python backend/scripts/test_api_http.py
    python backend/scripts/test_api_http.py --api places
"""

import argparse
import json
import math
import sys
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ENDPOINTS = {
    "places": "/api/places/search",
    "nearby": "/api/nearby/recommend",
    "routes": "/api/routes/optimize",
    "stations": "/api/stations/resolve",
}
EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "http"


def positive_timeout(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("timeout은 0보다 큰 유한한 초 단위 값입니다.")
    return seconds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api", choices=ENDPOINTS)
    parser.add_argument(
        "--input", type=Path, help="POST할 JSON 파일. 생략하면 API별 예시 사용"
    )
    parser.add_argument("--timeout", type=positive_timeout, default=300.0)
    parser.add_argument("--output", type=Path, help="응답 JSON을 저장할 파일")
    parser.add_argument("--expect-status", type=int, default=200)
    args = parser.parse_args()

    try:
        base = urlsplit(args.base_url)
    except ValueError as exc:
        parser.error(f"base-url 형식이 올바르지 않습니다: {exc}")
    if base.scheme not in ("http", "https") or not base.netloc:
        parser.error(
            "base-url은 http:// 또는 https://로 시작하는 서버 주소여야 합니다."
        )
    if args.input is not None and args.api is None:
        parser.error("--input을 사용할 때는 --api도 지정해주세요.")
    if not 100 <= args.expect_status <= 599:
        parser.error("expect-status는 100~599 범위의 HTTP 상태 코드입니다.")

    body = None
    endpoint = "/openapi.json"
    if args.api is not None:
        endpoint = ENDPOINTS[args.api]
        source = args.input if args.input is not None else EXAMPLES / f"{args.api}.json"
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                parser.error("요청 본문은 JSON 객체여야 합니다.")
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(
                "utf-8"
            )
        except (OSError, UnicodeError, ValueError) as exc:
            parser.error(f"요청 JSON 파일을 읽을 수 없습니다: {exc}")

    request = Request(
        args.base_url.rstrip("/") + endpoint,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST" if args.api is not None else "GET",
    )
    print(f"{request.method} {request.full_url}", file=sys.stderr)
    try:
        try:
            response = urlopen(request, timeout=args.timeout)
        except HTTPError as exc:
            response = exc
        with response:
            status, raw = response.status, response.read()
    except (HTTPException, URLError, OSError, ValueError) as exc:
        print(f"서버 요청 실패: {exc}", file=sys.stderr)
        return 2

    print(f"HTTP {status} (expected {args.expect_status})", file=sys.stderr)
    text = raw.decode("utf-8", errors="replace")
    try:
        result = json.loads(text)
    except ValueError:
        result = text

    valid_openapi = (
        isinstance(result, dict)
        and isinstance(result.get("paths"), dict)
        and "openapi" in result
    )
    if args.api is None and status == 200 and valid_openapi:
        result = {"status": "ok", "availableApis": sorted(result["paths"])}
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    print(serialized, end="")

    if args.output is not None:
        try:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        except OSError as exc:
            print(f"응답 파일 저장 실패: {exc}", file=sys.stderr)
            return 2
        print(f"응답 저장: {args.output}", file=sys.stderr)

    if args.api is None and status == 200 and not valid_openapi:
        print("OpenAPI JSON 형식이 올바르지 않습니다.", file=sys.stderr)
        return 1
    return 0 if status == args.expect_status else 1


if __name__ == "__main__":
    sys.exit(main())
