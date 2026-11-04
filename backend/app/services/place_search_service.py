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


def search_places(request: PlaceSearchRequest) -> PlaceSearchResponse:
    """서버 설정으로 LLM 검색어 재작성과 Google 장소 검색을 실행한다."""
    places_key = os.environ["GOOGLE_PLACE_API_KEY"].strip()
    gemini_key = os.environ["GEMINI_API_KEY"].strip()
    model_name = os.environ["GEMINI_MODEL"].strip()
    with GeminiWrapper(api_key=gemini_key, model_name=model_name) as llm:
        rewrite = cast(
            QueryRewrite,
            llm.request(
                system_prompt=QUERY_REWRITE_PROMPT,
                user_prompt=json.dumps(
                    {"userQuery": request.userQuery}, ensure_ascii=False
                ),
                response_schema=QueryRewrite,
            ),
        )
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
                api_key=places_key,
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
