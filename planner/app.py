"""Standalone offline API. Default fixture; explicit replay provider injection."""

from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .fixture_provider import FixtureProvider
from .ledger import Ledger, PlanError
from .mobility_data import MobilityData
from .models import PlanRequest
from .pass_order_hints import load_pass_order_hints
from .service import Planner

ROOT = Path(__file__).resolve().parent


def create_app(*, ledger=None, provider=None, timeout_seconds=300, monotonic=None, pass_order_hints=None, mobility=None):
    if pass_order_hints is None and (configured_path := os.getenv("JPASS_PASS_ORDER_HINTS")):
        pass_order_hints = load_pass_order_hints(Path(configured_path))
    if mobility is None and (mobility_path := os.getenv("JPASS_MOBILITY_FILE")):
        mobility = MobilityData.load(Path(mobility_path))
    ledger = ledger if ledger is not None else Ledger(ROOT.parent / "tmp" / "low-call-ledger.sqlite3")
    provider = provider if provider is not None else FixtureProvider(ROOT / "fixtures" / "tokyo-demo.json")
    kwargs = {"monotonic": monotonic} if monotonic is not None else {}
    planner = Planner(ledger, provider, timeout_seconds=timeout_seconds, pass_order_hints=pass_order_hints, mobility=mobility, **kwargs)

    @asynccontextmanager
    async def lifespan(application):
        ledger.abandon_running()
        yield

    application = FastAPI(title="JR Pass low-call offline v2", version="2.0.0", lifespan=lifespan)
    application.state.planner = planner

    @application.exception_handler(PlanError)
    async def plan_error(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status)

    @application.exception_handler(RequestValidationError)
    async def bad_request(request, error):
        return JSONResponse({"error": "INVALID_REQUEST"}, status_code=400)

    @application.get("/api/v2/health")
    async def health():
        return {"status": "ok", "dataSource": provider.dataSource, "liveSupported": False, "workersSupported": 1}

    @application.post("/api/v2/sessions", status_code=201)
    async def session():
        result = ledger.create_session()
        result["dataSource"] = provider.dataSource
        return result

    @application.post("/api/v2/routes/plan")
    async def plan(request: Request):
        try:
            raw = await request.json()
            parsed = PlanRequest.model_validate(raw)
        except (ValidationError, json.JSONDecodeError, ValueError, TypeError):
            raise PlanError("INVALID_REQUEST")
        return await planner.plan(parsed, raw)

    return application


app = create_app()
