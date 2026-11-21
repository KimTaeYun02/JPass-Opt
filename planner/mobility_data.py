"""Directed pedestrian routes and captured bus searches for offline v2."""

from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import DTO, Evidence, Location, Money, StationRef, Text, Walking, instant


def coordinates(location: Location):
    return location.lat, location.lng


class WalkRecord(DTO):
    origin: Location
    destination: Location
    route: Walking

    @model_validator(mode="after")
    def routed(self):
        if self.route.status != "routed":
            raise ValueError("보행 경로 자료에는 경로를 계산한 값만 넣을 수 있습니다.")
        return self


class BusStop(DTO):
    stopId: Text
    operator: Text
    name: Text
    location: Location

    def identity(self):
        return self.operator, self.stopId


class BusConnection(DTO):
    origin: Location
    destination: Location
    boarding: BusStop
    alighting: BusStop
    station: StationRef | None = None

    @model_validator(mode="after")
    def endpoints(self):
        if self.boarding.identity() == self.alighting.identity():
            raise ValueError("버스 승·하차 정류장은 서로 달라야 합니다.")
        if self.station is not None and (
            self.station.providerStationId is None or self.station.location != self.destination
        ):
            raise ValueError("버스 환승역은 확인된 역 ID와 일치하는 위치가 필요합니다.")
        return self


class BusTrip(DTO):
    departureAt: str
    arrivalAt: str
    line: Text
    fareYen: Money | None


class BusSearch(DTO):
    boarding: BusStop
    alighting: BusStop
    preparedAt: str
    timetable: Literal["verified", "unknown"]
    evidence: Evidence
    trips: list[BusTrip] = Field(max_length=8)

    @model_validator(mode="after")
    def chronology(self):
        prepared = instant(self.preparedAt)
        for trip in self.trips:
            departure, arrival = instant(trip.departureAt), instant(trip.arrivalAt)
            if departure < prepared or arrival <= departure or arrival - prepared > timedelta(days=1):
                raise ValueError("조회 이후의 유효한 버스 승하차 시각이 필요합니다.")
        return self


class MobilityFile(DTO):
    source: Literal["fixture", "replay"]
    walks: list[WalkRecord] = Field(default_factory=list)
    connections: list[BusConnection] = Field(default_factory=list)
    searches: list[BusSearch] = Field(default_factory=list)


class MobilityData:
    def __init__(self, data: MobilityFile):
        self.source = data.source
        self.walks = {(coordinates(row.origin), coordinates(row.destination)): row.route for row in data.walks}
        self.searches = {(row.boarding.identity(), row.alighting.identity(), instant(row.preparedAt)): row
                         for row in data.searches}
        if len(self.walks) != len(data.walks) or len(self.searches) != len(data.searches):
            raise ValueError("같은 방향의 보행 또는 버스 검색 자료가 중복됩니다.")
        stops = {}
        for row in (*data.connections, *data.searches):
            for stop in (row.boarding, row.alighting):
                if stops.setdefault(stop.identity(), stop) != stop:
                    raise ValueError("동일한 운영사·정류장 ID에 이름이나 좌표가 다릅니다.")
        self.connections = tuple(data.connections)

    @classmethod
    def load(cls, path: Path):
        return cls(MobilityFile.model_validate_json(path.read_bytes()))

    def walk(self, origin: Location, destination: Location):
        return self.walks.get((coordinates(origin), coordinates(destination)))

    def search(self, connection: BusConnection, prepared):
        row = self.searches.get((connection.boarding.identity(), connection.alighting.identity(), prepared))
        if row is None or row.boarding != connection.boarding or row.alighting != connection.alighting:
            return None
        return row
