"""동 1개 답 캐시 (explore/views.py:answer_one). Redis 대신 테스트 설정의 LocMemCache."""

import pytest
from django.core.cache import cache

from explore import ask, views
from explore.chat import llm
from tests.explore_samples import jumunjin_raw

CODE = "5115025000"


@pytest.fixture(autouse=True)
def empty_cache():
    cache.clear()
    yield
    cache.clear()


def stub(monkeypatch, result):
    calls = []

    def answer(raw, intents, generate, *, question):
        calls.append(generate)
        return result

    monkeypatch.setattr(ask, "answer", answer)
    return calls


def key(question):
    digest = ask.prompt_hash(ask.facts_for(jumunjin_raw(), ["months"], question), question)
    return f"explore:ask:a:{CODE}:months:{digest}"


def test_ttl_is_seven_days():
    assert views.ANSWER_TTL == 7 * 24 * 60 * 60


def test_generated_answer_is_cached_and_reused(monkeypatch):
    calls = stub(monkeypatch, ask.Answer(["2월이 한산해요."], "llm", "HCX-007", "h"))
    first = views.answer_one(CODE, jumunjin_raw(), ["months"], "언제 한산해?")
    again = views.answer_one(CODE, jumunjin_raw(), ["months"], "언제 한산해?")
    assert first == again == (["2월이 한산해요."], "llm")
    assert len(calls) == 1
    assert cache.get(key("언제 한산해?")) == [["2월이 한산해요."], "llm"]


def test_fallback_answer_is_not_cached(monkeypatch):
    rule = ask.Answer(["강릉시는 2·4·9월이 가장 한산해요."], "rule", None, "h", fallback=True)
    calls = stub(monkeypatch, rule)
    views.answer_one(CODE, jumunjin_raw(), ["months"], "언제 한산해?")
    views.answer_one(CODE, jumunjin_raw(), ["months"], "언제 한산해?")
    assert len(calls) == 2
    assert cache.get(key("언제 한산해?")) is None


def test_rule_answer_that_is_not_fallback_is_cached(monkeypatch):
    calls = stub(monkeypatch, ask.Answer(["규칙 답이에요."], "rule", None, "h"))
    views.answer_one(CODE, jumunjin_raw(), ["months"], "언제 한산해?")
    views.answer_one(CODE, jumunjin_raw(), ["months"], "언제 한산해?")
    assert len(calls) == 1


def test_generator_is_llm_when_key_set(monkeypatch, settings):
    calls = stub(monkeypatch, ask.Answer(["답이에요."], "llm", "HCX-007", "h"))
    settings.CLOVA_API_KEY = "test-key"
    views.answer_one(CODE, jumunjin_raw(), ["months"], "언제 한산해?")
    settings.CLOVA_API_KEY = ""
    views.answer_one(CODE, jumunjin_raw(), ["coast"], "바다 가까워?")
    assert calls == [llm.generate, None]
