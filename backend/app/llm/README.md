# LLM 래퍼

기존 Side-B처럼 만들고 `request()`를 호출하면 된다.
GPT와 Gemini는 클래스·키·모델만 바꾸면 된다.

```python
from app.llm import GeminiWrapper, GPTWrapper

llm = GPTWrapper(api_key=openai_key, model_name=openai_model)
# llm = GeminiWrapper(api_key=gemini_key, model_name=gemini_model)

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
일반 JSON 스키마만 주면 JSON 문자열을 반환한다.

Gemini는 Pydantic 모델을 JSON Schema로 변환하고, JSON 스키마 딕셔너리는
SDK의 `response_json_schema`로 전달한다. `additionalProperties: false` 같은
제약을 보존하며, `response_validator`가 있으면 응답도 해당 모델로 검증한다.
전송 방식은 [Google Gen AI SDK의 JSON Schema 예제](https://googleapis.github.io/python-genai/#json-response-schema)를 따른다.

두 래퍼의 인자는 같다. `temperature`는 기본적으로 생략하며,
필요하면 지원하는 모델에서 `temperature=0.7`처럼 전달한다.
`max_output_tokens`는 기본 1024다. API 오류와 응답 검증 오류는 그대로 전달한다.
빈 응답·거절·미완료 응답은 `RuntimeError`로 알린다.

키와 모델은 호출하는 코드에서 읽어 전달한다. 루트 `.env`를 사용한다면:

```dotenv
OPENAI_API_KEY=your-openai-key
OPENAI_MODEL=your-openai-model-id
GEMINI_API_KEY=your-gemini-key
GEMINI_MODEL=your-gemini-model-id
```

호출을 모두 마치면 `llm.close()`로 연결을 닫는다.
