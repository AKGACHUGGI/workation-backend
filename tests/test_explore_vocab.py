"""질문 의도 사전 파일 (explore/vocab.py). 작은 JSON 을 만들어 읽고, 실제 파일은 읽히는지만 본다."""

import json

import pytest

from explore import vocab

ITEMS = [
    {
        "intent": "quiet",
        "phrase": "이 동네 조용해?",
        "canonical": True,
        "embedding": [3.0, 0.0, 0.0],
    },
    {"intent": "quiet", "phrase": "시끄러워?", "canonical": False, "embedding": [1.0, 1.0, 0.0]},
    {
        "intent": "coast",
        "phrase": "바다가 가까워?",
        "canonical": True,
        "embedding": [0.0, 2.0, 0.0],
    },
    {
        "intent": "find_quiet",
        "phrase": "조용한 동네 어디야?",
        "canonical": True,
        "embedding": [0.0, 0.0, 5.0],
    },
]


@pytest.fixture
def small(tmp_path):
    path = tmp_path / "ask_vocab.json"
    path.write_text(
        json.dumps({"model": "m", "items": ITEMS}, ensure_ascii=False), encoding="utf-8"
    )
    return vocab.Vocab.load(path)


def test_nearest_is_cosine_ranked(small):
    got = small.nearest([1.0, 0.0, 0.0], k=2)
    assert [(i, p) for i, p, _ in got] == [("quiet", "이 동네 조용해?"), ("quiet", "시끄러워?")]
    assert got[0][2] == pytest.approx(1.0)
    assert got[1][2] == pytest.approx(0.5**0.5)


def test_nearest_filters_intents(small):
    got = small.nearest([1.0, 0.0, 0.0], k=5, intents=("coast", "find_quiet"))
    assert [i for i, _, _ in got] == ["coast", "find_quiet"]
    assert got[0][2] == pytest.approx(0.0)


def test_entries_and_version(small, tmp_path):
    assert small.entries() == [
        ("quiet", "이 동네 조용해?", True),
        ("quiet", "시끄러워?", False),
        ("coast", "바다가 가까워?", True),
        ("find_quiet", "조용한 동네 어디야?", True),
    ]
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"model": "m", "items": ITEMS[:1]}), encoding="utf-8")
    assert vocab.Vocab.load(other).version != small.version


def test_committed_file_loads():
    rows = vocab.entries()
    assert len(rows) > 100
    assert {i for i, _, _ in rows} >= {"quiet", "find_quiet", "unknown_other"}
    assert vocab.version()
    intent, phrase, sim = vocab.nearest(vocab.get().units[0], k=1)[0]
    assert (intent, phrase) == rows[0][:2]
    assert sim == pytest.approx(1.0)
