"""역 주변 관광지 추천 API의 요청·응답 모델."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.constants.place_types import TRAVEL_TYPE
from app.schemas.route import Location


class StationInput(BaseModel):
    name: str | None = None
    stationId: str | None = None
    line: str | None = None
    location: Location


class StationOutput(BaseModel):
    name: str | None = None
    stationId: str | None = None
    location: Location


class AnchorStationSelection(BaseModel):
    """LLM 내부 응답. HTTP 응답에는 포함하지 않는다."""

    model_config = ConfigDict(extra="forbid")

    candidateIds: list[str] = Field(
        min_length=1, json_schema_extra={"uniqueItems": True}
    )

    @field_validator("candidateIds")
    @classmethod
    def check_unique_candidates(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("앵커 역 후보 ID는 중복해서 선택할 수 없습니다.")
        return values


class NearbyRequest(BaseModel):
    travelTypes: list[str] = Field(
        min_length=1,
        json_schema_extra={"items": {"type": "string", "enum": TRAVEL_TYPE}},
    )
    userRequest: str = ""
    stations: list[StationInput] = Field(min_length=1)
    placeCount: int = Field(ge=1, le=20, strict=True)
    radius: float = Field(gt=0, le=50000, allow_inf_nan=False)
    maxAnchorCount: int | None = Field(default=None, ge=1, strict=True)

    @field_validator("travelTypes")
    @classmethod
    def check_travel_types(cls, values: list[str]) -> list[str]:
        unsupported = [value for value in values if value not in TRAVEL_TYPE]
        if unsupported:
            raise ValueError(
                f"지원하지 않는 여행 유형입니다: {', '.join(unsupported)}. "
                f"허용 값: {', '.join(TRAVEL_TYPE)}"
            )
        return list(dict.fromkeys(values))


class RecommendedPlace(BaseModel):
    googlePlaceId: str = Field(min_length=1, pattern=r"\S")
    name: str = Field(min_length=1, pattern=r"\S")
    location: Location
    address: str | None = None
    primaryType: str | None = None
    searchTags: list[str] = Field(min_length=1)
    distanceMeters: float = Field(ge=0, allow_inf_nan=False)


class StationRecommendations(BaseModel):
    station: StationOutput
    radius: float
    requestedCount: int = Field(ge=1)
    returnedCount: int = Field(ge=0)
    recommendedPlaces: list[RecommendedPlace]


class NearbyResponse(BaseModel):
    results: list[StationRecommendations] = Field(min_length=1)
