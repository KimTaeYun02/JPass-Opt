import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from clients.location_api import get_coordinates, get_nearest_stations
from crawling import RouteSearchResult, crawl_route

SAMPLE_DIR = Path(__file__).resolve().parent


async def search_route(
    departure_place: str,
    arrival_place: str,
    headless: bool = False,
    departure_time: datetime | None = None,  # noqa: F821
    screenshot_dir: Path | None = None,
    *,
    output_path: Path = SAMPLE_DIR / "route_result.json",
) -> RouteSearchResult:
    load_dotenv(SAMPLE_DIR / ".env")
    api_key = os.getenv("GOOGLE_GEOCODING_API_KEY")

    departure_coordinates = get_coordinates(
        departure_place, api_key, use_geocoding=True
    )
    departure_stations = get_nearest_stations(
        departure_coordinates["latitude"], departure_coordinates["longitude"], limit=1
    )
    arrival_coordinates = get_coordinates(arrival_place, api_key, use_geocoding=True)
    arrival_stations = get_nearest_stations(
        arrival_coordinates["latitude"], arrival_coordinates["longitude"], limit=1
    )
    print("Departure Station:", departure_stations[0]["name"])
    print("Arrival Station:", arrival_stations[0]["name"])
    result = await crawl_route(
        departure=departure_stations[0]["name"] + "駅",
        arrival=arrival_stations[0]["name"] + "駅",
        headless=headless,
        departure_time=departure_time,
        screenshot_dir=(
            screenshot_dir / "route" if screenshot_dir is not None else None
        ),
    )
    result_json = json.dumps(result, ensure_ascii=False, indent=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result_json, encoding="utf-8")
    print(result_json)
    print("저장 위치:", output_path.resolve())
    return result


if __name__ == "__main__":
    import asyncio

    departure_place = "네즈신사"
    arrival_place = "도쿄돔"
    asyncio.run(search_route(departure_place, arrival_place, headless=False))
