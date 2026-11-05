"""동선 최적화 API의 요청·응답 모델."""

import re
from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

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


class Leg(BaseModel):
    fromVisitId: str
    toVisitId: str
    departureAt: AwareDatetime
    arrivalAt: AwareDatetime
    durationMinutes: int = Field(ge=0)
    fare: int = Field(ge=0)
    route: list[TransportStep]


class OptimizedDay(BaseModel):
    dayIndex: int
    date: date
    startAt: AwareDatetime
    endAt: AwareDatetime
    visits: list[Visit]
    legs: list[Leg]
    totalTravelMinutes: int = Field(ge=0)
    totalTransportFare: int = Field(ge=0)


class RouteResponse(BaseModel):
    startDate: date
    dayCount: int
    timezone: Literal["Asia/Tokyo"] = "Asia/Tokyo"
    currency: Literal["JPY"] = "JPY"
    dataSource: Literal["live"] = "live"
    timingBasis: Literal["stationTravelOnly"] = "stationTravelOnly"
    days: list[OptimizedDay]
    totalTravelMinutes: int = Field(ge=0)
    totalTransportFare: int = Field(ge=0)
