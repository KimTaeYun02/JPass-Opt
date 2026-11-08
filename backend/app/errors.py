"""외부 API 클라이언트가 서비스와 HTTP 계층에 전달하는 공통 오류."""


class ProviderError(RuntimeError):
    """외부 서비스 호출에 실패했다."""


class ProviderTimeoutError(ProviderError):
    """외부 서비스 응답 시간이 초과됐다."""


class ProviderRateLimitError(ProviderError):
    """외부 서비스의 요청 한도를 초과했다."""


class ProviderResponseError(ProviderError):
    """외부 서비스가 유효하지 않은 응답을 반환했다."""


def provider_error_for_status(status: int | None, message: str) -> ProviderError:
    """외부 HTTP 상태를 공통 오류로 분류한다."""
    if status == 429:
        return ProviderRateLimitError(message)
    if status in (408, 504):
        return ProviderTimeoutError(message)
    return ProviderError(message)
