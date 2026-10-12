from clients.location_api import get_nearest_stations

if __name__ == "__main__":
    stations = get_nearest_stations(35.7201366, 139.7607695, limit=1)
    print(stations)
