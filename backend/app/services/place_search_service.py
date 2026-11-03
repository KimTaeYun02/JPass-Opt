"""사용자 문장 → 일본어 검색어 증강 → 최대 세 번의 Google 장소 검색."""

import json
import logging
import os
from typing import cast

from app.clients.google_maps import search_text
from app.llm import GeminiWrapper
from app.prompts.place_search import QUERY_REWRITE_PROMPT
from app.schemas.place_search import (
    PlaceSearchRequest,
    PlaceSearchResponse,
    PlaceSearchResult,
    QueryRewrite,
)

logger = logging.getLogger(__name__)


class PlaceSearchConfigurationError(RuntimeError):
    """검색에 필요한 서버 API 키가 없다."""


def rewrite_query(user_query: str, *, llm: GeminiWrapper) -> QueryRewrite:
    """LLM을 한 번 호출하고 증강된 검색어 목록 또는 null을 검증한다."""
    if not isinstance(user_query, str) or not user_query.strip():
        raise ValueError("user_query에 검색 요청을 입력해주세요.")

    return cast(
        QueryRewrite,
        llm.request(
            system_prompt=QUERY_REWRITE_PROMPT,
            user_prompt=json.dumps({"userQuery": user_query}, ensure_ascii=False),
            response_schema=QueryRewrite,
        ),
    )


def run_search(
    request: PlaceSearchRequest, *, api_key: str, llm: GeminiWrapper
) -> PlaceSearchResponse:
    """검증된 요청으로 검색한다. HTTP나 환경 변수와 독립적인 서비스 흐름."""
    request = PlaceSearchRequest.model_validate(request)
    rewrite = rewrite_query(request.userQuery, llm=llm)
    if rewrite.textQueries is None:
        logger.info("장소 검색어 증강 완료: 검색어 생성 불가, Google 검색=0회")
        return PlaceSearchResponse(
            userQuery=request.userQuery,
            textQueries=None,
            results=[],
        )

    # 프롬프트나 스키마 제한과 별개로 실제 호출을 세 번 이하로 고정한다.
    queries = rewrite.textQueries[:3]
    logger.info("장소 검색어 증강 완료: Google 검색=%d회 예정", len(queries))
    location_bias = (
        request.locationBias.model_dump(exclude_none=True)
        if request.locationBias is not None
        else None
    )
    results = []
    for index, query in enumerate(queries, start=1):
        data = search_text(
            text_query=query,
            api_key=api_key,
            language_code="ko",
            region_code="JP",
            page_size=request.pageSize,
            rank_preference=request.rankPreference,
            location_bias=location_bias,
        )
        results.append(PlaceSearchResult(textQuery=query, response=data))
        logger.info(
            "Google 장소 검색 완료: %d/%d, 장소=%d개",
            index,
            len(queries),
            len(data.get("places", [])),
        )

    return PlaceSearchResponse(
        userQuery=request.userQuery,
        textQueries=queries,
        results=results,
    )


def search_places(request: PlaceSearchRequest) -> PlaceSearchResponse:
    """서버 설정과 클라이언트 수명을 관리하고 외부 오류를 구분한다."""
    places_key = os.getenv("GOOGLE_PLACE_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not places_key or not gemini_key:
        raise PlaceSearchConfigurationError("장소 검색에 필요한 서버 설정이 없습니다.")

    model_name = os.environ["GEMINI_MODEL"].strip()
    with GeminiWrapper(api_key=gemini_key, model_name=model_name) as llm:
        return run_search(request, api_key=places_key, llm=llm)
