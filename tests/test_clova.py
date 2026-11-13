"""core.clova 의 chat 시간 제한 인자 · embed 캐시. 실제 호출 없이 _post 를 바꿔 끼운다."""

import hashlib

import pytest
from django.core.cache import cache

from core import clova


def test_chat_timeout_default_and_override(monkeypatch):
    seen = []

    def fake_post(path, payload, timeout):
        seen.append(timeout)
        return {"message": {"content": "ok"}}

    monkeypatch.setattr(clova, "_post", fake_post)
    assert clova.chat("s", "u") == "ok"
    clova.chat("s", "u", timeout=20)
    assert seen == [15, 20]


@pytest.fixture
def embed_calls(monkeypatch):
    cache.clear()
    calls = []

    def fake_post(path, payload, timeout):
        calls.append(payload["text"])
        return {"embedding": [0.1, 0.2]}

    monkeypatch.setattr(clova, "_post", fake_post)
    yield calls
    cache.clear()


def test_embed_caches_same_text(embed_calls):
    assert clova.embed("조용한 동네") == [0.1, 0.2]
    assert clova.embed("조용한 동네") == [0.1, 0.2]
    clova.embed("바닷가")
    assert embed_calls == ["조용한 동네", "바닷가"]
    key = "emb:clova-embedding-v2:" + hashlib.sha1("조용한 동네".encode()).hexdigest()
    assert cache.get(key) == [0.1, 0.2]


def test_embed_failure_is_not_cached(monkeypatch, embed_calls):
    def down(path, payload, timeout):
        raise clova.ClovaError("CLOVA HTTP 500")

    monkeypatch.setattr(clova, "_post", down)
    with pytest.raises(clova.ClovaError):
        clova.embed("조용한 동네")
    key = "emb:clova-embedding-v2:" + hashlib.sha1("조용한 동네".encode()).hexdigest()
    assert cache.get(key) is None
