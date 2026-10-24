"""실행: xvfb-run -a poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.nearby import router as nearby_router
from app.api.routes import router as routes_router
from app.logging_config import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.route_lock = asyncio.Lock()
    yield


def create_app() -> FastAPI:
    configure_logging()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    application = FastAPI(
        title="JPass 여행 API",
        version="0.1.0",
        description="관광지 방문 순서 최적화, 역·환승역 좌표와 역 주변 관광지 추천",
        lifespan=lifespan,
    )
    origins = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
        ).split(",")
        if origin.strip()
    ]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["POST"],
        allow_headers=["Content-Type"],
    )
    application.include_router(routes_router)
    application.include_router(nearby_router)
    return application


app = create_app()
