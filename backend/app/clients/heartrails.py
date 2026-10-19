import requests


def get_nearest_stations(
    latitude: float,
    longitude: float,
    limit: int = 3,
) -> list[dict]:
    url = "https://www.heartrails-express.com/api/json"

    response = requests.get(
        url,
        params={
            "method": "getStations",
            "x": longitude,
            "y": latitude,
        },
        timeout=10,
    )
    response.raise_for_status()

    stations = response.json().get("response", {}).get("station", [])

    return [
        {
            "name": station["name"],
            "line": station["line"],
            "prefecture": station["prefecture"],
            "latitude": station["y"],
            "longitude": station["x"],
            "distance": station["distance"],
        }
        for station in stations[:limit]
    ]
