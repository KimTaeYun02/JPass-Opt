"""동선 최적화 API의 요청·응답 모델."""

import re
from datetime import date
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from app.optimizer import MAX_ROUTE_STOPS


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class PlaceInput(BaseModel):
    name: str = Field(min_length=1)
    location: Location
    googlePlaceId: str = Field(min_length=1, pattern=r"\S")


class DayInput(BaseModel):
    dayIndex: int = Field(ge=1, strict=True)
    dailyStartTime: str = Field(
        default="08:00", pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
    )
    places: list[PlaceInput] = Field(min_length=1, max_length=MAX_ROUTE_STOPS)


class RouteRequest(BaseModel):
    startDate: date
    includeAccessWalking: bool = Field(default=False, strict=True)
    dayCount: int = Field(ge=1, strict=True)
    days: list[DayInput]

    @field_validator("startDate", mode="before")
    @classmethod
    def check_date_format(cls, value):
        if not isinstance(value, date) and (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
        ):
            raise ValueError("startDate는 YYYY-MM-DD 형식이어야 합니다.")
        return value

    @model_validator(mode="after")
    def check_days(self):
        if len(self.days) != self.dayCount or {
            day.dayIndex for day in self.days
        } != set(range(1, self.dayCount + 1)):
            raise ValueError(
                "days는 dayCount와 길이가 같고, dayIndex는 1부터 연속이어야 합니다."
            )
        if self.startDate.toordinal() + self.dayCount - 1 > date.max.toordinal():
            raise ValueError("여행의 마지막 날짜가 지원하는 날짜 범위를 초과했습니다.")
        return self


class Station(BaseModel):
    name: str
    location: Location
    prefecture: str | None = None


class Visit(BaseModel):
    visitId: str
    order: int = Field(ge=1)
    name: str
    location: Location
    googlePlaceId: str = Field(min_length=1, pattern=r"\S")
    station: Station
    estimatedArrivalAt: AwareDatetime
    estimatedDepartureAt: AwareDatetime | None = None


class TransportStep(BaseModel):
    fromStation: Station
    toStation: Station
    line: str


class RoutePoint(BaseModel):
    kind: Literal["place", "station"]
    name: str = Field(min_length=1)
    location: Location


class TravelSegment(BaseModel):
    fromPoint: RoutePoint
    toPoint: RoutePoint
    mode: Literal["walking", "transit"]
    durationMinutes: int = Field(ge=0, strict=True)
    distanceMeters: int | None = Field(default=None, ge=0, strict=True)
    path: list[Location] | None = Field(default=None, min_length=2, max_length=10000)

    @model_validator(mode="after")
    def check_walking(self):
        if self.mode == "walking" and (
            self.distanceMeters is None or self.path is None
        ):
            raise ValueError("도보 구간은 거리와 경로 좌표가 필요합니다.")
        return self


class Leg(BaseModel):
    fromVisitId: str
    toVisitId: str
    departureAt: AwareDatetime
    arrivalAt: AwareDatetime
    durationMinutes: int = Field(ge=0)
    fare: int = Field(ge=0)
    route: list[TransportStep]
    segments: list[TravelSegment] | None = None

    @model_validator(mode="after")
    def check_segments(self):
        if self.segments is None:
            return self
        if (
            not self.segments
            or sum(s.durationMinutes for s in self.segments) != self.durationMinutes
        ):
            raise ValueError("이동 구간의 시간 합계가 일치하지 않습니다.")
        if (
            self.segments[0].fromPoint.kind != "place"
            or self.segments[-1].toPoint.kind != "place"
        ):
            raise ValueError("관광지 간 이동은 장소에서 시작하고 끝나야 합니다.")
        for previous, current in zip(self.segments, self.segments[1:]):
            if (
                previous.toPoint.kind != current.fromPoint.kind
                or previous.toPoint.location != current.fromPoint.location
            ):
                raise ValueError("이동 구간이 연속되지 않습니다.")
        transit = [s for s in self.segments if s.mode == "transit"]
        if len(transit) > 1 or bool(transit) != bool(self.route):
            raise ValueError("역 간 이동과 노선 상세가 일치하지 않습니다.")
        if transit and (
            transit[0].fromPoint.kind != "station"
            or transit[0].toPoint.kind != "station"
            or transit[0].fromPoint.location != self.route[0].fromStation.location
            or transit[0].toPoint.location != self.route[-1].toStation.location
        ):
            raise ValueError("역 간 이동의 양 끝 좌표가 일치하지 않습니다.")
        if (
            self.arrivalAt - self.departureAt
        ).total_seconds() != self.durationMinutes * 60:
            raise ValueError("이동 시각과 소요 시간이 일치하지 않습니다.")
        return self

    @model_serializer(mode="wrap")
    def omit_legacy_extension(self, handler):
        data = handler(self)
        if self.segments is None:
            data.pop("segments", None)
        return data


class OptimizedDay(BaseModel):
    dayIndex: int
    date: date
    startAt: AwareDatetime
    endAt: AwareDatetime
    visits: list[Visit]
    legs: list[Leg]
    totalTravelMinutes: int = Field(ge=0)
    totalTransportFare: int = Field(ge=0)
    candidateSearchTruncated: bool | None = None

    @model_serializer(mode="wrap")
    def omit_legacy_extension(self, handler):
        data = handler(self)
        if self.candidateSearchTruncated is None:
            data.pop("candidateSearchTruncated", None)
        return data


class RouteResponse(BaseModel):
    startDate: date
    dayCount: int
    timezone: Literal["Asia/Tokyo"] = "Asia/Tokyo"
    currency: Literal["JPY"] = "JPY"
    dataSource: Literal["live"] = "live"
    timingBasis: Literal["stationTravelOnly", "placeTravelOnly"] = "stationTravelOnly"
    days: list[OptimizedDay]
    totalTravelMinutes: int = Field(ge=0)
    totalTransportFare: int = Field(ge=0)
