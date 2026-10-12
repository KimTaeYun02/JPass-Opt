import requests


def get_coordinates(
    place_name: str,
    api_key: str,
    use_geocoding: bool = True,
) -> dict | None:
    if use_geocoding:
        url = "https://maps.googleapis.com/maps/api/geocode/json"

        response = requests.get(
            url,
            params={
                "address": place_name,
                "key": api_key,
                "language": "ko",
                "region": "jp",
                "components": "country:JP",
            },
            timeout=10,
        )
    else:
        url = "https://places.googleapis.com/v1/places:searchText"

        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": api_key,
                "X-Goog-FieldMask": "places.location",
            },
            json={
                "textQuery": place_name,
                "languageCode": "ko",
                "regionCode": "JP",
                "pageSize": 1,
            },
            timeout=10,
        )

    response.raise_for_status()
    data = response.json()

    if use_geocoding:
        status = data.get("status")

        if status == "ZERO_RESULTS":
            return None

        if status != "OK":
            raise RuntimeError(
                f"Geocoding API Error: {status} {data.get('error_message', '')}"
            )

        location = data["results"][0]["geometry"]["location"]

        return {
            "latitude": location["lat"],
            "longitude": location["lng"],
        }

    places = data.get("places", [])

    if not places:
        return None

    return places[0]["location"]


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
