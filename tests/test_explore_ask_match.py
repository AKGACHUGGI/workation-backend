"""질문 → 의도 (v6.1 스펙 §4-1). CLOVA 임베딩 · ask_vocab SQL 은 바꿔 끼운다."""

import pytest
from django.core.cache import cache
from django.db import DatabaseError

from core import clova
from explore import ask_match, sources

VOCAB = [
    ("quiet", "이 동네 조용해?", True),
    ("quiet", "시끄럽지 않아?", False),
    ("weekday_hours", "평일엔 몇 시가 붐비고 몇 시가 한산해?", True),
    ("unknown_place", "맛집 있어?", False),
    ("unknown_place", "카페 많아?", False),
]


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def vocab(monkeypatch):
    monkeypatch.setattr(sources, "vocab_version", lambda: "1763012345")
    monkeypatch.setattr(sources, "load_vocab", lambda: VOCAB)


def no_embed(text):
    raise clova.ClovaError("CLOVA_API_KEY 가 설정되지 않았습니다.")


def db_down(*args):
    raise DatabaseError("relation ask_vocab does not exist")


@pytest.mark.parametrize(
    ("norm", "expected"),
    [
        ("여기조용해", [("quiet", 1.0)]),
        ("맛집알려줘", [("unknown_place", 1.0)]),
        ("평일엔어때", [("weekday_hours", 1.0)]),
        ("날씨는", []),
    ],
)
def test_match_text(norm, expected):
    assert ask_match.match_text(norm, VOCAB) == expected


def test_embedding_path_and_cache(monkeypatch, vocab):
    calls = []
    monkeypatch.setattr(clova, "embed", lambda text: calls.append(text) or [0.1] * 1024)
    monkeypatch.setattr(
        sources,
        "nearest_vocab",
        lambda vec, k=5: [
            ("quiet", "이 동네 조용해?", 0.84),
            ("weekday_hours", "오전에 한산해?", 0.80),
        ],
    )
    assert ask_match.find_intents("오전에 조용해?") == (["quiet", "weekday_hours"], 0.84)
    assert ask_match.find_intents("오전에  조용해 ") == (["quiet", "weekday_hours"], 0.84)
    assert calls == ["오전에 조용해?"]  # 정규형이 같으면 캐시


def test_below_threshold_is_unknown_other(monkeypatch, vocab):
    monkeypatch.setattr(clova, "embed", lambda text: [0.1] * 1024)
    monkeypatch.setattr(sources, "nearest_vocab", lambda vec, k=5: [("quiet", "조용해?", 0.61)])
    assert ask_match.find_intents("주식 오를까?") == (["unknown_other"], 0.61)


def test_embedding_failure_uses_text_and_is_not_cached(monkeypatch, vocab):
    monkeypatch.setattr(clova, "embed", no_embed)
    assert ask_match.find_intents("맛집 알려줘") == (["unknown_place"], 1.0)
    assert ask_match.find_intents("날씨는?") == (["unknown_other"], 0.0)
    assert not any("explore:ask:q:" in k for k in cache._cache)  # LocMem 내부 키


def test_nearest_vocab_db_error_uses_text(monkeypatch, vocab):
    monkeypatch.setattr(clova, "embed", lambda text: [0.1] * 1024)
    monkeypatch.setattr(sources, "nearest_vocab", db_down)
    assert ask_match.find_intents("여기 조용해?") == (["quiet"], 1.0)


def test_vocab_tables_down_is_unknown_other(monkeypatch):
    monkeypatch.setattr(sources, "vocab_version", db_down)
    monkeypatch.setattr(sources, "load_vocab", db_down)
    assert ask_match.find_intents("여기 조용해?") == (["unknown_other"], 0.0)
