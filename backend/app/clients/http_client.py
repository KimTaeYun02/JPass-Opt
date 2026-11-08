"""HTTP 요청과 JSON 파싱 오류를 공통 외부 서비스 오류로 변환한다."""

import requests

from app.errors import (
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    provider_error_for_status,
)


def request_json(
    method: str,
    url: str,
    *,
    provider: str,
    timeout: float,
    params: dict | None = None,
    headers: dict[str, str] | None = None,
    json: dict | None = None,
) -> object:
    """JSON을 반환한다. 서비스별 응답 구조 검증은 호출자가 담당한다."""
    try:
        response = requests.request(
            method,
            url,
            params=params,
            headers=headers,
            json=json,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise ProviderTimeoutError(f"{provider} 응답 시간 초과") from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        raise provider_error_for_status(status, f"{provider} 실패") from exc
    except requests.RequestException as exc:
        raise ProviderError(f"{provider} 연결 실패") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderResponseError(
            f"{provider} 응답 JSON 형식이 올바르지 않습니다."
        ) from exc
