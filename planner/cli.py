"""Offline CLI. A real session ID is issued locally; the input is a template."""

import argparse
import asyncio
import json
from pathlib import Path
import tempfile

from .fixture_provider import FixtureProvider
from .ledger import Ledger
from .mobility_data import MobilityData
from .models import PlanRequest
from .pass_order_hints import load_pass_order_hints
from .service import Planner


async def run(args):
    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    provider = FixtureProvider(args.fixture)
    with tempfile.TemporaryDirectory(prefix="jpass-low-call-") as folder:
        # CLI runs are isolated experiments, unlike persistent HTTP server sessions.
        ledger = Ledger(Path(folder) / "ledger.sqlite3")
        raw["sessionId"] = ledger.create_session()["sessionId"]
        planner = Planner(ledger, provider, pass_order_hints=load_pass_order_hints(args.pass_order_hints),
                          mobility=MobilityData.load(args.mobility) if args.mobility else None)
        result = await planner.plan(PlanRequest.model_validate(raw), raw)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    print(json.dumps({"dataSource": result["dataSource"], "completion": result["completion"],
        "timing": result["timing"], "railAttempts": result["ledger"]["railAttempts"],
        "stationAttempts": result["ledger"]["stationAttempts"], "ragRequests": result["ledger"]["ragRequests"],
        "improvement": result["improvement"], "report": args.report}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="호출 예산 제한 일정 평가: 가상 자료 전용")
    parser.add_argument("--input", default="sample/low-call/eight-node-input.json")
    parser.add_argument("--fixture", default=str(Path(__file__).parent / "fixtures" / "tokyo-demo.json"))
    parser.add_argument("--pass-order-hints", type=Path, help="패스 후보 선정용 로컬 참고 자료 JSON")
    parser.add_argument("--mobility", type=Path, help="직통 도보·버스 후보를 담은 가상 자료 JSON")
    parser.add_argument("--report")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
