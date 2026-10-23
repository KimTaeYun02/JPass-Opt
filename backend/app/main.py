"""실행: xvfb-run -a poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000."""

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.route_lock = asyncio.Lock()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="JPass 동선 최적화 API",
        version="0.1.0",
        description="관광지 방문 순서 최적화와 역·환승역 좌표 제공",
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
    application.include_router(router)
    return application


app = create_app()
