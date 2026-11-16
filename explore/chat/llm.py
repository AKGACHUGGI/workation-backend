"""비교 답 구조화 생성 (스펙 §6-1). LangChain ChatClovaX(HCX-007) + json_schema 구조화 출력.

HCX-005 는 구조화 출력을 지원하지 않아 비교 답만 HCX-007 을 쓴다(실측 3/3 성공 · 1.9초).
CLOVA 는 parallel_tool_calls · toolChoice 를 받지 않아(400) json_schema 경로만 쓴다.
키는 settings.CLOVA_API_KEY 를 직접 넘긴다(ChatClovaX 기본 환경 변수 이름과 다르다).
"""

from __future__ import annotations

from django.conf import settings
from langchain_naver import ChatClovaX
from pydantic import BaseModel

MODEL = "HCX-007"
TIMEOUT = 10  # 초. 사용자가 기다리는 요청


def enabled() -> bool:
    return bool(getattr(settings, "CLOVA_API_KEY", ""))


def client() -> ChatClovaX:
    return ChatClovaX(
        model=MODEL,
        api_key=settings.CLOVA_API_KEY,
        thinking={"effort": "none"},
        temperature=0.3,
        max_tokens=512,
        timeout=TIMEOUT,
        max_retries=0,
    )


def generate(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
    structured = client().with_structured_output(schema, method="json_schema")
    return structured.invoke([("system", system), ("user", user)])
