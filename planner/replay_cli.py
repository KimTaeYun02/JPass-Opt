"""Plan or serve explicitly configured recordings; never contact the supplier."""

import argparse
import asyncio
import json
from pathlib import Path
import tempfile

from .ledger import Ledger
from .mobility_data import MobilityData
from .models import PlanRequest
from .pass_order_hints import load_pass_order_hints
from .replay import ReplayProvider
from .service import Planner


async def run(args):
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    provider = ReplayProvider(args.recording)
    with tempfile.TemporaryDirectory(prefix="jpass-replay-") as folder:
        ledger = Ledger(Path(folder) / "ledger.sqlite3")
        raw["sessionId"] = ledger.create_session()["sessionId"]
        planner = Planner(ledger, provider, pass_order_hints=load_pass_order_hints(args.pass_order_hints),
                          mobility=MobilityData.load(args.mobility) if args.mobility else None)
        result = await planner.plan(PlanRequest.model_validate(raw), raw)
    output = Path(args.report)
    if output.exists():
        raise ValueError("기존 보고서를 덮어쓰지 않습니다. 새 --report 경로를 지정하세요.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"dataSource": result["dataSource"], "completion": result["completion"],
                      "stopReasons": result["stopReasons"], "railAttempts": result["ledger"]["railAttempts"],
                      "httpRequestsObserved": result["ledger"]["httpRequestsObserved"], "report": str(output)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="저장된 팀 크롤러 응답만 재생; 외부 통신 없음")
    parser.add_argument("--recording", required=True)
    parser.add_argument("--input")
    parser.add_argument("--report")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--ledger", default="tmp/low-call-replay-ledger.sqlite3")
    parser.add_argument("--pass-order-hints", type=Path, help="패스 후보 선정용 로컬 참고 자료 JSON")
    parser.add_argument("--mobility", type=Path, help="유향 보행 경로와 시각이 검증된 로컬 버스 재생 자료 JSON")
    args = parser.parse_args()
    if args.serve:
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(provider=ReplayProvider(args.recording), ledger=Ledger(args.ledger),
                               pass_order_hints=load_pass_order_hints(args.pass_order_hints),
                               mobility=MobilityData.load(args.mobility) if args.mobility else None),
                    host="127.0.0.1", port=args.port, workers=1)
    else:
        if not args.input or not args.report:
            parser.error("--input, --report가 필요합니다.")
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
