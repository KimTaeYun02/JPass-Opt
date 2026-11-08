"""실행: xvfb-run -a poetry run uvicorn app.main:app --host 0.0.0.0 --port 8000."""

import asyncio
import os
from contextlib import asynccontextmanager
from ipaddress import IPv6Address
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.nearby import router as nearby_router
from app.api.places import router as places_router
from app.api.routes import router as routes_router
from app.api.stations import router as stations_router
from app.logging_config import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing_settings = [
        name
        for name in ("GOOGLE_PLACE_API_KEY", "GEMINI_API_KEY", "GEMINI_MODEL")
        if not os.getenv(name, "").strip()
    ]
    if missing_settings:
        raise RuntimeError(
            f"필수 서버 환경 변수가 설정되지 않았습니다: {', '.join(missing_settings)}"
        )
    app.state.route_lock = asyncio.Lock()
    yield


def cors_origins() -> list[str]:
    raw = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173",
    )
    origins = []
    for index, value in enumerate(raw.split(","), start=1):
        origin = value.strip()
        try:
            url = urlsplit(origin)
            valid = (
                url.scheme in {"http", "https"}
                and url.hostname
                and url.port != 0
                and not url.username
                and not url.password
                and not url.path
                and not url.query
                and not url.fragment
                and not any(char.isspace() for char in origin)
                and not any(char in origin for char in "\\*?#%")
                and not any(ord(char) < 32 or ord(char) == 127 for char in origin)
                and not url.netloc.endswith(":")
                and origin.lower() == f"{url.scheme}://{url.netloc}".lower()
            )
            if valid:
                host = url.hostname.encode("idna").decode("ascii").lower()
                if ":" in host:
                    host = f"[{IPv6Address(host).compressed}]"
                port = url.port
                suffix = (
                    f":{port}"
                    if port is not None
                    and port != {"http": 80, "https": 443}[url.scheme]
                    else ""
                )
                origin = f"{url.scheme}://{host}{suffix}"
        except (ValueError, UnicodeError):
            valid = False
        if not valid:
            # Never include the original value: an operator may have pasted a secret URL.
            raise RuntimeError(
                f"CORS_ORIGINS의 {index}번째 항목: "
                "경로·쿼리 없이 http(s)://호스트[:포트]를 "
                "쉼표로 구분해 입력해야 합니다."
            )
        if origin not in origins:
            origins.append(origin)
    return origins


def create_app() -> FastAPI:
    configure_logging()
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    application = FastAPI(
        title="JPass 여행 API",
        version="0.1.0",
        description="자연어 장소 검색, 관광지 방문 순서 최적화, 역·환승역 좌표와 역 주변 관광지 추천",
        lifespan=lifespan,
    )
    origins = cors_origins()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["POST"],
        allow_headers=["Content-Type"],
    )
    application.include_router(routes_router)
    application.include_router(nearby_router)
    application.include_router(places_router)
    application.include_router(stations_router)
    return application


app = create_app()
