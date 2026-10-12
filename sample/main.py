import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if __package__:
    from .clients.location_api import get_nearest_stations
    from .crawling import crawl_route
else:
    from clients.location_api import get_nearest_stations
    from crawling import crawl_route

SAMPLE_DIR = Path(__file__).resolve().parent

# 네즈신사 -> 오다이바 -> 도쿄돔 -> 에노시마 캔들 -> 메이지 신궁
TEST_SAMPLE_1 = [
    {"lat": 35.7201366, "lng": 139.7607695},
    {"lat": 35.62058340000001, "lng": 139.7805445},
    {"lat": 35.7056396, "lng": 139.7518913},
    {"lat": 35.2997417, "lng": 139.478425},
    {"lat": 35.6763976, "lng": 139.6993259},
]

# 도쿄스카이트리 -> 도쿄 국립박물관 -> 센소지 -> 스미다 수족관
TEST_SAMPLE_2 = [
    {"lat": 35.7100627, "lng": 139.8107004},
    {"lat": 35.7188351, "lng": 139.7765215},
    {"lat": 35.7147651, "lng": 139.7966553},
    {"lat": 35.7099301, "lng": 139.8095855},
]


async def main(
    locations: list[dict[str, float]],
    *,
    headless: bool = False,
    departure_time: datetime | None = None,
    output_path: Path = SAMPLE_DIR / "result" / "TEST_SAMPLE_1.json",
    screenshot_dir: Path | None = SAMPLE_DIR / "screenshot" / "TEST_SAMPLE_1",
) -> list[dict]:
    if len(locations) < 2:
        raise ValueError("경로 검색에는 장소가 최소 2개 필요합니다.")

    station_names = []
    for index, location in enumerate(locations, start=1):
        stations = get_nearest_stations(location["lat"], location["lng"], limit=1)
        if not stations:
            raise RuntimeError(
                f"{index}번째 장소의 주변 역을 찾지 못했습니다: {location}"
            )
        station_name = stations[0]["name"]
        station_names.append(station_name)
        print(f"장소 {index}의 주변 역: {station_name}")

    results = []
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
