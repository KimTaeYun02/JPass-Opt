"""자연어 장소 검색 API의 요청·응답과 검색어 변환 모델."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlaceCoordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class CircleBias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    center: PlaceCoordinate
    radius: float = Field(
        ge=0,
        le=50000,
        allow_inf_nan=False,
        description="검색 중심에서의 반경(미터). 영역 밖 장소도 반환될 수 있습니다.",
    )


class RectangleBias(BaseModel):
    model_config = ConfigDict(extra="forbid")

    low: PlaceCoordinate = Field(description="검색 영역의 남서쪽 좌표")
    high: PlaceCoordinate = Field(description="검색 영역의 북동쪽 좌표")

    @model_validator(mode="after")
    def check_viewport(self) -> "RectangleBias":
        if self.low.latitude > self.high.latitude:
            raise ValueError("low.latitude는 high.latitude보다 클 수 없습니다.")
        if self.low.longitude == 180 and self.high.longitude == -180:
            raise ValueError("경도 범위가 비어 있는 사각형은 사용할 수 없습니다.")
        return self


class LocationBias(BaseModel):
    """원 또는 사각형 중 하나로 지정하는 검색 위치 우선 조건."""

    model_config = ConfigDict(extra="forbid")

    circle: CircleBias | None = None
    rectangle: RectangleBias | None = None

    @model_validator(mode="after")
    def check_shape(self) -> "LocationBias":
        if (self.circle is None) == (self.rectangle is None):
            raise ValueError(
                "locationBias에는 circle 또는 rectangle 하나만 지정해주세요."
            )
        return self


class PlaceSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userQuery: str = Field(
        min_length=1,
        pattern=r"\S",
        description="찾고 싶은 장소를 자연어로 입력합니다.",
        examples=["에노시마 근처 고깃집과 카페를 찾아줘"],
    )
    languageCode: str = Field(
        default="ko", min_length=1, pattern=r"\S", description="Google 응답 언어"
    )
    regionCode: str = Field(
        default="JP",
        min_length=1,
        pattern=r"\S",
        description="Google 응답의 지역 코드. 검색 국가를 제한하는 조건은 아닙니다.",
    )
    pageSize: int = Field(
        default=20,
        ge=1,
        le=20,
        strict=True,
        description="검색어별 첫 페이지의 최대 장소 수",
    )
    rankPreference: Literal["RELEVANCE", "DISTANCE"] = "RELEVANCE"
    locationBias: LocationBias | None = Field(
        default=None,
        description="주변 장소를 우선 검색할 원 또는 사각형. 지역 밖 결과도 포함될 수 있습니다.",
    )

    @field_validator("userQuery", "languageCode", "regionCode")
    @classmethod
    def trim_input(cls, value: str) -> str:
        return value.strip()


class QueryRewrite(BaseModel):
    """LLM 검색어 변환 응답. 초과 검색어는 로컬에서도 세 개로 자른다."""

    model_config = ConfigDict(extra="forbid")

    textQueries: list[str] | None = Field(min_length=1, max_length=3)
    clarificationQuestion: str | None

    @field_validator("textQueries", mode="before")
    @classmethod
    def limit_queries(cls, value: Any) -> Any:
        return value[:3] if isinstance(value, list) else value

    @field_validator("textQueries")
    @classmethod
    def normalize_queries(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        queries = [value.strip() for value in values]
        if any(not query for query in queries):
            raise ValueError("LLM 검색어는 빈 문자열일 수 없습니다.")
        return list(dict.fromkeys(queries))

    @field_validator("clarificationQuestion")
    @classmethod
    def normalize_question(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("추가 질문은 빈 문자열일 수 없습니다.")
        return value.strip()

    @model_validator(mode="after")
    def check_output(self) -> "QueryRewrite":
        if (self.textQueries is None) == (self.clarificationQuestion is None):
            raise ValueError("검색어 목록과 추가 질문 중 하나만 있어야 합니다.")
        return self


class PlaceSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    textQuery: str = Field(min_length=1, pattern=r"\S")
    response: dict[str, Any] = Field(
        description=(
            "Google Places Text Search의 원본 JSON 응답입니다. places와 nextPageToken 등 "
            "반환된 필드를 그대로 보존하며, 장소 데이터가 없으면 일부 키가 생략될 수 있습니다."
        )
    )


class PlaceSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    userQuery: str
    textQueries: list[str] | None = Field(
        min_length=1,
        max_length=3,
        description="변환된 일본어 검색어. 추가 질문이 필요하면 null",
    )
    clarificationQuestion: str | None = Field(
        description="검색에 필요한 추가 질문. 검색어가 생성되면 null"
    )
    results: list[PlaceSearchResult] = Field(
        default_factory=list,
        max_length=3,
        description="검색어별 첫 페이지 응답. 추가 질문이 필요하면 빈 배열",
    )
