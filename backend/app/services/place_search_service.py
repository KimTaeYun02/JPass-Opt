"""사용자 문장 → 일본어 검색어 증강 → 최대 세 번의 Google 장소 검색."""

import json
import logging
import os

import httpx
from google.genai.errors import APIError as GeminiAPIError
from pydantic import BaseModel
from requests import HTTPError, RequestException
from requests import Timeout as RequestsTimeout

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


class PlaceSearchProviderError(RuntimeError):
    """외부 서비스 호출 또는 응답 검증에 실패했다."""


class PlaceSearchProviderTimeout(PlaceSearchProviderError):
    """외부 서비스 응답 시간이 초과됐다."""


class PlaceSearchRateLimitError(PlaceSearchProviderError):
    """외부 서비스의 요청 한도를 초과했다."""


def rewrite_query(user_query: str, *, llm: GeminiWrapper) -> QueryRewrite:
    """LLM을 한 번 호출하고 증강된 검색어 목록 또는 null을 검증한다."""
    if not isinstance(user_query, str) or not user_query.strip():
        raise ValueError("user_query에 검색 요청을 입력해주세요.")

    result = llm.request(
        system_prompt=QUERY_REWRITE_PROMPT,
        user_prompt=json.dumps({"userQuery": user_query}, ensure_ascii=False),
        response_schema=QueryRewrite,
    )
    if isinstance(result, str):
        return QueryRewrite.model_validate_json(result)
    if isinstance(result, BaseModel):
        result = result.model_dump()
    return QueryRewrite.model_validate(result)


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
        _validate_google_response(data)
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


def _validate_google_response(data: object) -> None:
    """결과가 없으면 빈 객체도 허용하며 원본 필드는 그대로 보존한다."""
    if not isinstance(data, dict) or "error" in data:
        raise PlaceSearchProviderError("Google 장소 검색 응답이 올바르지 않습니다.")
    places = data.get("places", [])
    if not isinstance(places, list) or any(
        not isinstance(place, dict) for place in places
    ):
        raise PlaceSearchProviderError("Google 장소 목록 형식이 올바르지 않습니다.")
    if "nextPageToken" in data and not isinstance(data["nextPageToken"], str):
        raise PlaceSearchProviderError("Google 다음 페이지 토큰이 올바르지 않습니다.")


def search_places(request: PlaceSearchRequest) -> PlaceSearchResponse:
    """서버 설정과 클라이언트 수명을 관리하고 외부 오류를 구분한다."""
    places_key = os.getenv("GOOGLE_PLACE_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not places_key or not gemini_key:
        raise PlaceSearchConfigurationError("장소 검색에 필요한 API 키가 없습니다.")

    llm = None
    try:
        model_name = os.getenv("GEMINI_MODEL", "").strip() or "gemini-3.5-flash-lite"
        llm = GeminiWrapper(api_key=gemini_key, model_name=model_name)
        return run_search(request, api_key=places_key, llm=llm)
    except PlaceSearchProviderError:
        raise
    except (RequestsTimeout, httpx.TimeoutException) as exc:
        raise PlaceSearchProviderTimeout("외부 검색 서비스 응답 시간 초과") from exc
    except GeminiAPIError as exc:
        logger.warning("Gemini 검색어 변환 실패: status=%s", exc.code)
        if exc.code == 429:
            raise PlaceSearchRateLimitError("Gemini 요청 한도 초과") from exc
        if exc.code in (408, 504):
            raise PlaceSearchProviderTimeout("Gemini 응답 시간 초과") from exc
        raise PlaceSearchProviderError("Gemini 검색어 변환 실패") from exc
    except HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        logger.warning("Google 장소 검색 실패: status=%s", status)
        if status == 429:
            raise PlaceSearchRateLimitError("Google 장소 검색 요청 한도 초과") from exc
        if status in (408, 504):
            raise PlaceSearchProviderTimeout("Google 장소 검색 응답 시간 초과") from exc
        raise PlaceSearchProviderError("Google 장소 검색 실패") from exc
    except (RequestException, httpx.HTTPError) as exc:
        raise PlaceSearchProviderError("외부 검색 서비스 연결 실패") from exc
    except (ValueError, TypeError, KeyError, RuntimeError) as exc:
        raise PlaceSearchProviderError("외부 검색 서비스 응답 형식 오류") from exc
    finally:
        if llm is not None:
            try:
                llm.close()
            except Exception as exc:
                # 연결 정리 오류가 검색 결과나 이미 분류한 오류를 덮어쓰지 않는다.
                logger.warning("Gemini 연결 정리 실패: 오류=%s", type(exc).__name__)
