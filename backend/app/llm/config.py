"""요청에 사용할 모델과 공급자의 API 키를 읽는다."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

LLMProvider = Literal["gpt", "gemini"]


def provider_for_model(model_name: str) -> LLMProvider:
    """모델 ID로 공급자를 구분한다. 별칭은 request(provider=...)로 지정한다."""
    if model_name.removeprefix("models/").startswith("gemini-"):
        return "gemini"
    if model_name.startswith(("gpt-", "chatgpt-", "ft:")) or re.match(
        r"^o\d+(?:-|$)", model_name
    ):
        return "gpt"
    raise ValueError(
        "모델의 공급자를 구분할 수 없습니다. "
        "GPT·Gemini 모델 ID를 사용하거나 request(provider=...)를 지정해주세요."
    )


@dataclass(frozen=True)
class LLMSettings:
    provider: LLMProvider
    api_key: str = field(repr=False)
    model_name: str

    @classmethod
    def from_env(
        cls,
        *,
        model_name: str | None = None,
        model_env: str | None = None,
        provider: LLMProvider | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "LLMSettings":
        environment = os.environ if environ is None else environ

        def value(name: str) -> str:
            return environment.get(name, "").strip()

        selected_model = (
            (model_name or "").strip()
            or (value(model_env) if model_env else "")
            or value("OPENAI_MODEL")
        )
        if provider is not None and provider not in ("gpt", "gemini"):
            raise ValueError("provider는 gpt 또는 gemini여야 합니다.")
        if provider is None:
            try:
                provider = (
                    provider_for_model(selected_model) if selected_model else "gpt"
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc

        key_name = "OPENAI_API_KEY" if provider == "gpt" else "GEMINI_API_KEY"
        api_key = value(key_name)

        missing = []
        if not api_key:
            missing.append(key_name)
        if not selected_model:
            missing.append("OPENAI_MODEL")
        if missing:
            raise RuntimeError(
                "필수 LLM 환경 변수가 설정되지 않았습니다: " + ", ".join(missing)
            )

        return cls(
            provider=provider,
            api_key=api_key,
            model_name=selected_model,
        )
