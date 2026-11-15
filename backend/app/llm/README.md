# 공통 LLM 래퍼

서비스는 `LLMWrapper`를 생성하고, **request마다 모델을 선택**한다.
생성 시에는 모델을 고정하거나 SDK 클라이언트를 만들지 않는다.
GPT·Gemini의 입력 인자, 반환 타입, 오류 타입은 동일하다.

```python
from app.llm import LLMWrapper

with LLMWrapper() as llm:
    # 별도 지정이 없으면 OPENAI_MODEL 사용
    text = llm.request("한국어로 짧게 답해줘.", "도쿄 여행 팁을 알려줘.")

    # LLM_NEARBY_MODEL이 비어 있으면 OPENAI_MODEL 사용
    nearby = llm.request(
        system_prompt="한국어로 여행지를 추천해줘.",
        user_prompt="도쿄의 산책할 만한 곳",
        model_env="LLM_NEARBY_MODEL",
    )

    # 같은 인스턴스에서 모델 ID를 직접 지정할 수도 있다.
    # model_name=사용할_모델_ID를 request에 전달한다.
```

## 환경변수와 선택 순서

기존 API 키와 기본 모델 환경변수를 그대로 사용한다.

```dotenv
GOOGLE_PLACE_API_KEY=your-google-places-key
GEMINI_API_KEY=your-gemini-key
OPENAI_MODEL=gemini-3.5-flash-lite
OPENAI_API_KEY=your-openai-key
# 선택 사항: GPT 또는 Gemini 모델 ID. 비어 있으면 OPENAI_MODEL 사용.
LLM_NEARBY_MODEL=
```

모델 우선순위는 `request(model_name=...)` → `model_env`로 지정한 환경변수 →
`OPENAI_MODEL`이다. 앞뒤 공백은 제거하며 미설정·빈 문자열·공백뿐인 값은 건너뛴다.

- 장소 검색: 기본 `OPENAI_MODEL`.
- 주변 추천의 앵커 선정·순위 선정: `LLM_NEARBY_MODEL`, 없으면 `OPENAI_MODEL`.
- `gemini-...`, `models/gemini-...` 모델은 `GEMINI_API_KEY` 사용.
- `gpt-...`, `chatgpt-...`, `o숫자...`, `ft:...` 모델은 `OPENAI_API_KEY` 사용.
- 공급자를 구분할 수 없는 별칭은 `request(provider="gpt" 또는 "gemini")`로 명시한다.
- 환경변수에 지정할 때는 공급자를 구분할 수 있는 모델 ID를 사용한다.

기본 모델은 `OPENAI_MODEL`로 지정하며, 값으로 GPT·Gemini 모델 ID를 사용할 수 있다.
앱 시작 시 기본·주변 추천 모델에 필요한 API 키를 검증한다. 두 공급자의 모델을
함께 사용하면 두 키 모두 필요하며, 사용하지 않는 공급자의 키는 필수가 아니다.
검증은 외부 API를 호출하지 않는다. 실제 모델 접근 가능 여부는 요청 때 확인된다.

`LLM_PROVIDER`, `LLM_MODEL`, `LLM_MAX_OUTPUT_TOKENS`는 읽지 않는다.
토큰 수는 `request(max_output_tokens=...)`로 코드에서 지정하며 기본값은 1024다.
추론 모델에서는 추론 토큰도 이 한도에 포함된다. `temperature`는 기본적으로
생략하며 지원하는 모델에서만 명시한다.

앱은 시작 시 루트 `.env`를 읽는다. 독립 스크립트에서는 먼저 `load_dotenv()`로
읽는다. Docker Compose에서 `.env`를 바꿨다면 컨테이너를 다시 생성한다.

## 구조화 출력

```python
from pydantic import BaseModel


class PlaceAnalysis(BaseModel):
    place_name: str


with LLMWrapper() as llm:
    result = llm.request(
        system_prompt="일본어 장소명으로 정규화해줘.",
        user_prompt="도쿄의 센소지",
        response_schema=PlaceAnalysis,
        model_env="LLM_NEARBY_MODEL",
    )
    print(result.place_name)
```

| 입력 | 반환 |
| --- | --- |
| 스키마 없음 | `str` |
| `response_schema=모델` | 검증된 해당 Pydantic 모델 |
| JSON 스키마 dict + `response_validator=모델` | 검증된 해당 Pydantic 모델 |
| JSON 스키마 dict만 지정 | JSON 문법을 확인한 `str` |

형식은 동일하며 생성 내용과 추천 순서는 모델마다 달라질 수 있다.
스키마 dict만 지정하면 전체 JSON Schema 제약을 로컬에서 검증하지 않는다.
업무 제약이 있으면 `response_validator`도 전달한다.

GPT는 Responses API의 `text.format`에 전송할 스키마 사본을 만든다.
객체 필드를 required로 만들고 기본 `additionalProperties: false`를 설정하며,
`default`, `uniqueItems`는 전송용 사본에서 제거한다. 원본은 변경하지 않는다.
현재 앵커·순위 모델의 중복 검사 validator는 그대로 적용한다.
변환 기준은 [OpenAI Docs의 구조화 출력 규칙](https://developers.openai.com/api/docs/guides/structured-outputs#supported-schemas)이다.
임의의 JSON Schema 전체가 모든 모델에서 지원되지는 않는다.

Gemini는 SDK의 `response_json_schema`로 스키마를 전달한다.
[Google Gen AI SDK 예제](https://googleapis.github.io/python-genai/#json-response-schema)를 따른다.

## 오류와 연결 정리

- `ProviderPaymentRequiredError`: 결제 설정·크레딧 오류(402)
- `ProviderRateLimitError`: 요청 한도 초과(429)
- `ProviderTimeoutError`: 시간 초과, HTTP 408·504
- `ProviderResponseError`: 빈 응답·거절·차단·미완료, JSON·모델 검증 실패
- `ProviderError`: 그 외 외부 HTTP 오류·연결 실패. 위 오류들의 공통 부모

동일한 공급자·모델의 SDK 클라이언트는 인스턴스 안에서 재사용한다.
`with` 블록을 나가면 사용한 모든 클라이언트를 닫는다.
정리 실패가 반환값이나 원래 예외를 덮지 않는다.
기존 `GPTWrapper`, `GeminiWrapper` 직접 사용도 유지한다.
