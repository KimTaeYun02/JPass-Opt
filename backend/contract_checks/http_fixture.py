"""실제 HTTP 계약 검사용 앱. 외부 공급자 경계만 제어된 응답으로 대체한다."""

import json
import os
import socket
import threading
import time
from contextlib import ExitStack, asynccontextmanager, contextmanager
from unittest.mock import patch

import uvicorn

from app.main import create_app

PLACES = [
    {
        "id": f"contract-{name.lower()}",
        "displayName": {"text": f"Tokyo garden {name}"},
        "location": {"latitude": lat, "longitude": 139.76},
        "formattedAddress": "Japan",
        "primaryType": "park",
    }
    for name, lat in [("A", 35.68), ("B", 35.681)]
]


@contextmanager
def contract_env():
    """설정 정책을 유지하면서 외부 인증에 쓰이지 않는 테스트 값을 제공한다."""
    with (
        patch.dict(
            os.environ,
            {
                "GOOGLE_PLACE_API_KEY": "contract-only-not-a-key",
                "GEMINI_API_KEY": "contract-only-not-a-key",
                "OPENAI_MODEL": "gemini-contract-only-model",
                "LLM_NEARBY_MODEL": "",
                "CORS_ORIGINS": "http://localhost:5173",
            },
        ),
        patch("app.main.load_dotenv"),
    ):
        yield


@asynccontextmanager
async def controlled_browser_page(**kwargs):
    yield object()


@contextmanager
def controlled_api():
    audit = {"nearest": [], "text": [], "nearby": [], "crawl": [], "blocked": []}

    class ControlledLLM:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

        def request(
            self, *, user_prompt, response_schema, response_validator=None, **kwargs
        ):
            prompt = json.loads(user_prompt)
            if "userQuery" in prompt:
                result = {
                    "textQueries": None
                    if "ambiguous" in prompt["userQuery"]
                    else ["東京 庭園"]
                }
            elif "stations" in prompt:
                result = {"candidateIds": [prompt["stations"][0]["candidateId"]]}
            else:
                result = {"indices": list(range(prompt["placeCount"]))}
            validator = response_validator or response_schema
            return validator.model_validate(result)

        def close(self):
            pass

    def nearest(lat, lng, limit=3):
        audit["nearest"].append((lat, lng, limit))
        return [
            {
                "name": "A" if lat == 35.68 else "B",
                "line": "test line",
                "prefecture": "Tokyo",
                "latitude": lat,
                "longitude": lng,
            }
        ]

    def search_text(**kwargs):
        audit["text"].append(kwargs)
        return {"places": PLACES}

    def search_nearby(**kwargs):
        audit["nearby"].append(kwargs)
        return [PLACES[1]]

    async def crawl(page, origin, destination, **kwargs):
        audit["crawl"].append((origin, destination))
        minutes, fare = (9, 180) if origin == "B" else (17, 321)
        return {
            "routes": [
                {
                    "time": minutes,
                    "fee": fare,
                    "route": [
                        {"station": origin, "line": "test line"},
                        {"station": destination, "line": None},
                    ],
                }
            ]
        }

    def block_network(*args, **kwargs):
        audit["blocked"].append(True)
        raise AssertionError("Unstubbed outbound provider call")

    with ExitStack() as stack:
        stack.enter_context(contract_env())
        for target, replacement in [
            ("app.services.route_service.get_nearest_stations", nearest),
            ("app.services.route_service.browser_page", controlled_browser_page),
            ("app.services.route_service.crawl_route", crawl),
            ("app.services.place_search_service.search_text", search_text),
            ("app.services.nearby_service.search_nearby", search_nearby),
            ("app.services.place_search_service.LLMWrapper", ControlledLLM),
            ("app.services.nearby_service.LLMWrapper", ControlledLLM),
            ("requests.sessions.Session.request", block_network),
            ("httpx.HTTPTransport.handle_request", block_network),
            ("httpx.AsyncHTTPTransport.handle_async_request", block_network),
        ]:
            stack.enter_context(patch(target, replacement))
        yield create_app(), audit


@contextmanager
def serve_api(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]})
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("Contract HTTP server did not start")
                time.sleep(0.02)
            yield origin
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Contract HTTP server did not stop")
