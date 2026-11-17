import json
import logging
from contextlib import contextmanager
from copy import deepcopy
from typing import Self

import httpx
import requests
from google import genai
from google.genai import types
from google.genai.errors import APIError as GeminiAPIError
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
)
from pydantic import BaseModel, ValidationError

from app.errors import (
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    provider_error_for_status,
)
from app.llm.config import LLMProvider, LLMSettings

logger = logging.getLogger(__name__)


@contextmanager
def _provider_request(provider: str):
    """SDK 오류의 원인을 서버 로그에 남기고 공통 오류로 바꾼다."""
    try:
        yield
    except (
        GeminiAPIError,
        APIStatusError,
        httpx.HTTPStatusError,
        requests.HTTPError,
        APITimeoutError,
        httpx.TimeoutException,
        requests.Timeout,
        APIConnectionError,
        httpx.RequestError,
        requests.RequestException,
        APIResponseValidationError,
        json.JSONDecodeError,
        ValidationError,
    ) as exc:
        status: int | None = None
        error: ProviderError
        if isinstance(exc, GeminiAPIError):
            status = exc.code
            error = provider_error_for_status(
                status, f"{provider} 요청에 실패했습니다."
            )
        elif isinstance(
            exc, (APIStatusError, httpx.HTTPStatusError, requests.HTTPError)
        ):
            status = exc.response.status_code if exc.response is not None else None
            error = provider_error_for_status(
                status, f"{provider} 요청에 실패했습니다."
            )
        elif isinstance(
            exc, (APITimeoutError, httpx.TimeoutException, requests.Timeout)
        ):
            error = ProviderTimeoutError(f"{provider} 응답 시간이 초과됐습니다.")
        elif isinstance(
            exc, (APIConnectionError, httpx.RequestError, requests.RequestException)
        ):
            error = ProviderError(f"{provider}에 연결할 수 없습니다.")
        else:
            error = ProviderResponseError(
                f"{provider}가 유효하지 않은 응답을 반환했습니다."
            )
        logger.error(
            "%s.%s: %s",
            type(exc).__module__,
            type(exc).__qualname__,
            " ".join(str(exc).splitlines()),
        )
        raise error from exc


class _LLMWrapper:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.client.close()
        except Exception as exc:
            # 연결 정리 실패가 성공 결과나 원래 오류를 덮어쓰지 않게 한다.
            logger.warning("LLM 연결 정리 실패: %s", type(exc).__name__)


def _response_model(response_schema, response_validator):
    if response_validator is not None:
        return response_validator
    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
        return response_schema
    return None


def _parse_response(text, parsed, response_validator, *, expect_json: bool):
    try:
        if response_validator is not None and parsed is not None:
            if isinstance(parsed, BaseModel):
                parsed = parsed.model_dump()
            return response_validator.model_validate(parsed)
        if not text or not text.strip():
            raise ProviderResponseError("LLM이 빈 응답을 반환했습니다.")
        if response_validator is not None:
            return response_validator.model_validate_json(text)
        if expect_json:
            json.loads(text)
        return text
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ProviderResponseError("LLM 응답이 요청한 형식과 다릅니다.") from exc


def _openai_schema(schema: dict) -> dict:
    """호출자의 스키마를 보존하며 OpenAI strict 형식으로 변환한다."""
    result = deepcopy(schema)

    def convert(node: dict) -> None:
        # 중복 여부 등 업무 제약은 응답 모델의 validator에서도 검사한다.
        node.pop("uniqueItems", None)
        node.pop("default", None)
        properties = node.get("properties")
        if node.get("type") == "object" or isinstance(properties, dict):
            node.setdefault("additionalProperties", False)
            if isinstance(properties, dict):
                node["required"] = list(properties)
        for key in ("properties", "$defs", "definitions"):
            for child in node.get(key, {}).values():
                if isinstance(child, dict):
                    convert(child)
        if isinstance(node.get("items"), dict):
            convert(node["items"])
        for key in ("anyOf", "oneOf", "allOf"):
            for child in node.get(key, []):
                if isinstance(child, dict):
                    convert(child)

    convert(result)
    return result


class GPTWrapper(_LLMWrapper):
    def __init__(self, api_key: str, model_name: str):
        self.model_name = model_name
        self.client = OpenAI(api_key=api_key, timeout=30)

    def request(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_output_tokens: int = 1024,
        response_schema=None,
        response_validator: type[BaseModel] | None = None,
    ) -> BaseModel | str:
        validator = _response_model(response_schema, response_validator)
        schema = response_schema if response_schema is not None else validator
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            schema = schema.model_json_schema()
        options = {
            "model": self.model_name,
            "instructions": system_prompt,
            "input": user_prompt,
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if temperature is not None:
            options["temperature"] = temperature

        if schema is not None:
            options["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "response",
                    "schema": _openai_schema(schema),
                    "strict": True,
                }
            }
        with _provider_request("GPT"):
            response = self.client.responses.create(**options)

        if response.status != "completed":
            raise ProviderResponseError(
                f"GPT 응답이 완료되지 않았습니다: {response.status}"
            )
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "refusal":
                        raise ProviderResponseError("GPT가 요청을 거절했습니다.")
        return _parse_response(
            response.output_text,
            getattr(response, "output_parsed", None),
            validator,
            expect_json=schema is not None,
        )


class GeminiWrapper(_LLMWrapper):
    def __init__(self, api_key: str, model_name: str):
        self.model_name = model_name
        self.client = genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=30_000)
        )

    def request(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_output_tokens: int = 1024,
        response_schema=None,
        response_validator: type[BaseModel] | None = None,
    ) -> BaseModel | str:
        validator = _response_model(response_schema, response_validator)
        schema = response_schema if response_schema is not None else validator
        if isinstance(schema, type) and issubclass(schema, BaseModel):
            schema = schema.model_json_schema()
        # JSON Schema는 전용 필드로 보내 additionalProperties 등의 키를 보존한다.
        is_json_schema = isinstance(schema, dict)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json"
            if schema is not None
            else "text/plain",
            response_schema=None if is_json_schema else schema,
            response_json_schema=schema if is_json_schema else None,
        )
        with _provider_request("Gemini"):
            response = self.client.models.generate_content(
                model=self.model_name, contents=user_prompt, config=config
            )
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise ProviderResponseError("Gemini가 요청을 차단했습니다.")
        for candidate in response.candidates or []:
            if candidate.finish_reason not in (None, types.FinishReason.STOP):
                raise ProviderResponseError(
                    f"Gemini 응답이 완료되지 않았습니다: {candidate.finish_reason}"
                )
        return _parse_response(
            response.text, response.parsed, validator, expect_json=schema is not None
        )


class LLMWrapper:
    """요청마다 모델을 선택하고 공급자와 관계없이 같은 타입을 반환한다."""

    def __init__(self):
        self._adapters: dict[tuple[LLMProvider, str], _LLMWrapper] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        for adapter in self._adapters.values():
            adapter.close()
        self._adapters.clear()

    def request(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_output_tokens: int = 1024,
        response_schema: type[BaseModel] | dict | None = None,
        response_validator: type[BaseModel] | None = None,
        *,
        model_name: str | None = None,
        model_env: str | None = None,
    ) -> BaseModel | str:
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens < 1
        ):
            raise ValueError("max_output_tokens는 1 이상의 정수여야 합니다.")
        settings = LLMSettings.from_env(
            model_name=model_name, model_env=model_env
        )
        cache_key = (settings.provider, settings.model_name)
        adapter = self._adapters.get(cache_key)
        if adapter is None:
            adapter_type = GPTWrapper if settings.provider == "gpt" else GeminiWrapper
            adapter = adapter_type(
        
                api_key=settings.api_key, model_name=settings.model_name
            )
            self._adapters[cache_key] = adapter
        logger.info(
            "LLM 요청: 공급자=%s, 모델=%s", settings.provider, settings.model_name
        )
        return adapter.request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_schema=response_schema,
            response_validator=response_validator,
        )
