"""질문 → 의도 (v6.1 스펙 §4-1). CLOVA 임베딩 · 사전 파일(explore.vocab)은 바꿔 끼운다."""

import pytest
from django.core.cache import cache

from core import clova
from explore import ask, ask_match, vocab

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
def small_vocab(monkeypatch):
    monkeypatch.setattr(vocab, "version", lambda: "1763012345")
    monkeypatch.setattr(vocab, "entries", lambda: VOCAB)


def no_embed(text):
    raise clova.ClovaError("CLOVA_API_KEY 가 설정되지 않았습니다.")


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


def test_embedding_path_and_cache(monkeypatch, small_vocab):
    calls = []
    monkeypatch.setattr(clova, "embed", lambda text: calls.append(text) or [0.1] * 1024)
    monkeypatch.setattr(
        vocab,
        "nearest",
        lambda vec, k=5, intents=None: [
            ("quiet", "이 동네 조용해?", 0.84),
            ("weekday_hours", "오전에 한산해?", 0.80),
        ],
    )
    assert ask_match.find_intents("오전에 조용해?") == (["quiet", "weekday_hours"], 0.84)
    assert ask_match.find_intents("오전에  조용해 ") == (["quiet", "weekday_hours"], 0.84)
    assert calls == ["오전에 조용해?"]  # 정규형이 같으면 캐시


def test_below_threshold_is_unknown_other(monkeypatch, small_vocab):
    monkeypatch.setattr(clova, "embed", lambda text: [0.1] * 1024)
    monkeypatch.setattr(
        vocab, "nearest", lambda vec, k=5, intents=None: [("quiet", "조용해?", 0.61)]
    )
    assert ask_match.find_intents("주식 오를까?") == (["unknown_other"], 0.61)


def test_embedding_failure_uses_text_and_is_not_cached(monkeypatch, small_vocab):
    monkeypatch.setattr(clova, "embed", no_embed)
    assert ask_match.find_intents("맛집 알려줘") == (["unknown_place"], 1.0)
    assert ask_match.find_intents("날씨는?") == (["unknown_other"], 0.0)
    assert not any("explore:ask:q:" in k for k in cache._cache)  # LocMem 내부 키


REGION_VOCAB_ROWS = [
    ("find_quiet", "조용한 동네 어디야?", True),
    ("quiet", "이 동네 조용해?", True),
    ("unknown_place", "맛집 있어?", True),
]


def test_scope_filters_text_vocab(monkeypatch):
    monkeypatch.setattr(vocab, "version", lambda: "1")
    monkeypatch.setattr(vocab, "entries", lambda: REGION_VOCAB_ROWS)
    monkeypatch.setattr(clova, "embed", no_embed)
    assert ask_match.find_intents("조용한 동네 어디야?", "region") == (["find_quiet"], 1.0)
    # 동 채팅 범위에는 시군구 의도가 없다
    assert ask_match.find_intents("조용한 동네 어디야?", "dong") == (["quiet"], 1.0)


def test_scope_passes_intents_to_nearest(monkeypatch, small_vocab):
    seen = []
    monkeypatch.setattr(clova, "embed", lambda text: [0.1] * 1024)

    def nearest(vec, k=5, intents=None):
        seen.append(intents)
        return [("find_quiet", "조용한 동네 어디야?", 0.9)]

    monkeypatch.setattr(vocab, "nearest", nearest)
    assert ask_match.find_intents("한적한 데 어디?", "region") == (["find_quiet"], 0.9)
    assert seen == [ask.REGION_VOCAB]
    ask_match.find_intents("한적한 데 어디?", "dong")
    assert seen[-1] == ask.DONG_VOCAB
