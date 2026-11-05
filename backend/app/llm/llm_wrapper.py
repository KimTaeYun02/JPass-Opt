from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel


def _response_model(response_schema, response_validator):
    if response_validator is not None:
        return response_validator
    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
        return response_schema
    return None


def _parse_response(text, parsed, response_validator):
    if response_validator is not None and parsed is not None:
        if isinstance(parsed, BaseModel):
            parsed = parsed.model_dump()
        return response_validator.model_validate(parsed)
    if not text or not text.strip():
        raise RuntimeError("LLM이 빈 응답을 반환했습니다.")
    if response_validator is not None:
        return response_validator.model_validate_json(text)
    return text


class GPTWrapper:
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
        options = {
            "model": self.model_name,
            "instructions": system_prompt,
            "input": user_prompt,
            "max_output_tokens": max_output_tokens,
            "store": False,
        }
        if temperature is not None:
            options["temperature"] = temperature

        if isinstance(schema, type) and issubclass(schema, BaseModel):
            response = self.client.responses.parse(**options, text_format=schema)
        else:
            if schema is not None:
                options["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "response",
                        "schema": schema,
                        "strict": True,
                    }
                }
            response = self.client.responses.create(**options)

        if response.status != "completed":
            raise RuntimeError(f"GPT 응답이 완료되지 않았습니다: {response.status}")
        for item in response.output:
            if item.type == "message":
                for content in item.content:
                    if content.type == "refusal":
                        raise RuntimeError("GPT가 요청을 거절했습니다.")
        return _parse_response(
            response.output_text, getattr(response, "output_parsed", None), validator
        )

    def close(self):
        self.client.close()


class GeminiWrapper:
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
        response = self.client.models.generate_content(
            model=self.model_name, contents=user_prompt, config=config
        )
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise RuntimeError("Gemini가 요청을 차단했습니다.")
        for candidate in response.candidates or []:
            if candidate.finish_reason not in (None, types.FinishReason.STOP):
                raise RuntimeError(
                    f"Gemini 응답이 완료되지 않았습니다: {candidate.finish_reason}"
                )
        return _parse_response(response.text, response.parsed, validator)

    def close(self):
        self.client.close()
