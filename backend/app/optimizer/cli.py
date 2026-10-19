"""Offline JSON CLI. Run from backend/: python -m app.optimizer --help."""

import argparse
import json
import sys
from pathlib import Path

from app.optimizer.matrix import optimize_matrix


def main(argv=None):
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="수집한 방향별 전체 구간으로 최적 동선을 계산합니다."
    )
    parser.add_argument("input", help="크롤러 matrix JSON 파일. -이면 표준 입력")
    parser.add_argument(
        "--stations", nargs="+", required=True, help="방문 역 목록(고유한 역 2~8개)"
    )
    parser.add_argument("--selection", choices=("first", "fastest"), default="first")
    parser.add_argument("--limit", type=int, default=1, help="최적 동선 개수: 1~3")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        raw = (
            sys.stdin.read()
            if args.input == "-"
            else Path(args.input).read_text(encoding="utf-8-sig")
        )
        report = optimize_matrix(
            args.stations, json.loads(raw), selection=args.selection, limit=args.limit
        )
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        sys.stdout.write(encoded)
    except (ValueError, OSError) as error:
        parser.exit(2, f"오류: {error}\n")


if __name__ == "__main__":
    main()
