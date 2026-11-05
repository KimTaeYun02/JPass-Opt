"""동선 계산 전 관광지별 기준역 조회 계약."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StationLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False, strict=True)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False, strict=True)


class StationPlace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    googlePlaceId: str = Field(min_length=1, pattern=r"\S")
    location: StationLocation


class StationDayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dayIndex: int = Field(ge=1, le=7, strict=True)
    places: list[StationPlace] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_places(self):
        ids = [place.googlePlaceId for place in self.places]
        if len(ids) != len(set(ids)):
            raise ValueError("같은 일차의 관광지 ID는 중복될 수 없습니다.")
        return self


class StationResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[StationDayInput] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def unique_days(self):
        indices = [day.dayIndex for day in self.days]
        if len(indices) != len(set(indices)):
            raise ValueError("일차는 중복될 수 없습니다.")
        return self


class ResolvedStation(BaseModel):
    name: str = Field(min_length=1, pattern=r"\S")
    location: StationLocation
    line: str | None = None


class StationPlaceResult(BaseModel):
    googlePlaceId: str
    status: Literal["ok", "not_found", "error"]
    station: ResolvedStation | None = None
    reason: Literal["timeout", "unavailable", "invalid_response"] | None = None


class StationDayResult(BaseModel):
    dayIndex: int
    places: list[StationPlaceResult]


class StationResolveResponse(BaseModel):
    days: list[StationDayResult]
