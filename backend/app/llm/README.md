# LLM 래퍼

기존 Side-B처럼 만들고 `request()`를 호출하면 된다.
GPT와 Gemini는 클래스·키·모델만 바꾸면 된다.

```python
from app.llm import GeminiWrapper, GPTWrapper

with GPTWrapper(api_key=openai_key, model_name=openai_model) as llm:
    # GeminiWrapper(api_key=gemini_key, model_name=gemini_model)도 동일하다.
    text = llm.request("한국어로 답해줘.", "도쿄 여행 팁을 알려줘.")
    print(text)
```

JSON을 모델로 받고 싶을 때:

```python
from pydantic import BaseModel


class PlaceAnalysis(BaseModel):
    place_name: str


result = llm.request(
    system_prompt="일본어 장소명으로 정규화해줘.",
    user_prompt="도쿄의 센소지",
    response_schema=PlaceAnalysis,
    response_validator=PlaceAnalysis,
)
print(result.place_name)
```

`response_schema`에 Pydantic 모델을 주면 `response_validator`는 생략해도 된다.
일반 JSON 스키마만 주면 JSON 문법을 확인한 문자열을 반환한다.
검증 모델을 지정하면 항상 검증된 모델을 반환하므로 서비스에서 다시 파싱하지 않는다.

Gemini는 Pydantic 모델을 JSON Schema로 변환하고, JSON 스키마 딕셔너리는
SDK의 `response_json_schema`로 전달한다. `additionalProperties: false` 같은
제약을 보존하며, `response_validator`가 있으면 응답도 해당 모델로 검증한다.
전송 방식은 [Google Gen AI SDK의 JSON Schema 예제](https://googleapis.github.io/python-genai/#json-response-schema)를 따른다.

두 래퍼의 인자는 같다. `temperature`는 기본적으로 생략하며,
필요하면 지원하는 모델에서 `temperature=0.7`처럼 전달한다.
`max_output_tokens`는 기본 1024다. SDK별 오류는 `app.errors`의 공통 오류로 변환한다.

- `ProviderRateLimitError`: 요청 한도 초과(429)
- `ProviderTimeoutError`: 시간 초과, HTTP 408·504
- `ProviderResponseError`: 빈 응답·거절·차단·미완료, JSON·모델 검증 실패
- `ProviderError`: 그 외 외부 HTTP 오류·연결 실패. 위 오류들의 공통 부모다.

요청 구성은 오류 변환 범위 밖에서 처리하며, 일반 `ValueError`·`TypeError`·
`RuntimeError`를 외부 오류로 바꾸지 않는다. 호출하는 API에서 공통 오류를 HTTP 응답으로 매핑한다.

키와 모델은 호출하는 코드에서 읽어 전달한다. 루트 `.env`를 사용한다면:

```dotenv
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=your-openai-model-id
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=your-gemini-model-id
```

`with` 블록을 나가면 연결을 닫는다. 직접 생성했다면 `llm.close()`로 닫을 수도 있다.
연결 정리 실패는 예외 종류만 로그에 남기며, 반환값이나 원래 예외를 덮어쓰지 않는다.
