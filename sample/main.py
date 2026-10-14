import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

if __package__:
    from .clients.location_api import get_coordinates, get_nearest_stations
    from .crawling import RouteSearchResult, crawl_route
else:
    from clients.location_api import get_coordinates, get_nearest_stations
    from crawling import RouteSearchResult, crawl_route

SAMPLE_DIR = Path(__file__).resolve().parent


class SegmentResult(RouteSearchResult):
    departure: str
    arrival: str


# 네즈신사 -> 오다이바 -> 도쿄돔 -> 에노시마 캔들 -> 메이지 신궁
TEST_SAMPLE_1 = [
    "네즈신사",
    "오다이바",
    "도쿄돔",
    "에노시마 캔들",
    "메이지 신궁",
]

# 도쿄스카이트리 -> 도쿄 국립박물관 -> 센소지 -> 스미다 수족관
TEST_SAMPLE_2 = [
    "도쿄스카이트리",
    "도쿄 국립박물관",
    "센소지",
    "스미다 수족관",
]


async def main(
    places: list[str],
    *,
    headless: bool = False,
    departure_time: datetime | None = None,
    output_path: Path = SAMPLE_DIR / "result" / "TEST_SAMPLE.json",
    screenshot_dir: Path | None = SAMPLE_DIR / "screenshot" / "TEST_SAMPLE_1",
) -> list[SegmentResult]:
    """장소명 목록을 주변 역으로 변환하고 구간별 모든 경로를 JSON으로 저장한다."""
    if len(places) < 2:
        raise ValueError("경로 검색에는 장소가 최소 2개 필요합니다.")

    load_dotenv(SAMPLE_DIR / ".env")
    api_key = os.getenv("GOOGLE_GEOCODING_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_GEOCODING_API_KEY를 설정해주세요.")

    station_names = []
    for index, place in enumerate(places, start=1):
        coordinates = get_coordinates(place, api_key, use_geocoding=True)
        if coordinates is None:
            raise RuntimeError(f"{index}번째 장소의 좌표를 찾지 못했습니다: {place}")
        stations = get_nearest_stations(
            coordinates["latitude"], coordinates["longitude"], limit=1
        )
        if not stations:
            raise RuntimeError(f"{index}번째 장소의 주변 역을 찾지 못했습니다: {place}")
        station_name = stations[0]["name"]
        station_names.append(station_name)
        print(f"장소 {index}: {place} → 주변 역: {station_name}")

    results: list[SegmentResult] = []
    for index, (departure, arrival) in enumerate(
        zip(station_names, station_names[1:]), start=1
    ):
        print(f"\n구간 {index}: {departure} → {arrival}")
        result = await crawl_route(
            departure=departure + "駅",
            arrival=arrival + "駅",
            headless=headless,
            departure_time=departure_time,
            screenshot_dir=(
                screenshot_dir / f"route_{index}"
                if screenshot_dir is not None
                else None
            ),
        )
        results.append({"departure": departure, "arrival": arrival, **result})

    result_json = json.dumps(results, ensure_ascii=False, indent=2)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(result_json, encoding="utf-8")
    print(result_json)
    print("저장 위치:", output_path)
    return results


if __name__ == "__main__":
    # 모든 구간을 같은 출발 시각으로 비교한다. hour/minute를 바꿔 테스트한다.
    departure_time = datetime.now(ZoneInfo("Asia/Tokyo")).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    asyncio.run(main(TEST_SAMPLE_2, headless=False, departure_time=departure_time))
