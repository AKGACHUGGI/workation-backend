"""db/seed/ask_vocab.csv 와 db/tools/build_embeddings.py ask 모드. CLOVA 대신 함수를 바꿔 끼운다.
db/tools 는 패키지가 아니라 파일 경로로 불러온다 (표준 라이브러리만 쓰는 스크립트)."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "db" / "seed" / "ask_vocab.csv"

# 스펙 §3-1 표의 대표 질문 (CLOVA 에 넘기는 문장) 그대로
CANONICAL = {
    "weekday_hours": "평일엔 몇 시가 붐비고 몇 시가 한산해?",
    "weekend_hours": "주말엔 몇 시가 붐비고 몇 시가 한산해?",
    "months": "1년 중 언제 가면 한산해?",
    "quiet": "이 동네 조용해?",
    "peer": "또래 사람들이 많아?",
    "evening": "저녁에 활기 있어?",
    "type": "어떤 워케이션에 잘 맞아?",
    "coast": "바다가 가까워?",
    "life": "생활권이야, 관광지야?",
    "similar": "비슷한 동네는 어디야?",
    "compare_summary": "두 동네를 비교해 줘",
    # 시군구 채팅 스펙 §5
    "find_quiet": "조용한 동네 어디야?",
    "find_peer": "또래가 많은 동네는?",
    "find_evening": "저녁에 활기 있는 데는?",
    "find_type": "집중해서 일하기 좋은 동네는?",
    "find_coast": "바다 가까운 동네 있어?",
    "find_life": "관광지 말고 생활권 동네는?",
    "region_months": "몇 월에 가면 한산해?",
    "region_profile": "여긴 어떤 곳이야?",
}


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location(
        "build_embeddings", ROOT / "db" / "tools" / "build_embeddings.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_follows_spec(tool):
    items = tool.read_ask(SEED)
    assert {i: p for i, p, c in items if c} == CANONICAL
    assert {i for i, _, _ in items} == set(CANONICAL) | {
        "unknown_place",
        "unknown_stay",
        "unknown_transport",
        "unknown_other",
    }
    # 스펙 §3-2 표의 예시 표현이 들어 있다
    phrases = {p: i for i, p, _ in items}
    assert phrases["맛집 있어?"] == "unknown_place"
    assert phrases["방값 비싸?"] == "unknown_stay"
    assert phrases["주차 돼?"] == "unknown_transport"
    assert phrases["치안 괜찮아?"] == "unknown_other"
    assert phrases["둘 중 어디가 나아?"] == "compare_summary"
    assert phrases["둘이 비슷해?"] == "similar"
    # 시군구 의도: 성격 질문 · 맛집 질문과 덜 겹치는 표현
    # '유형이 많은지' 는 시군구 성격 질문, find_type 엔 동을 찾는 표현만
    assert phrases["어떤 워케이션 유형이 많아?"] == "region_profile"
    assert phrases["몰입형으로 일할 동네 어디야?"] == "find_type"
    assert phrases["밤에도 사람 많은 동네는?"] == "find_evening"
    assert phrases["저녁에 북적이는 데 어디야?"] == "find_evening"
    for gone in ("어떤 유형 동네가 있어?", "밤에 놀 만한 데는?", "퇴근 후 놀기 좋은 동네 어디야?"):
        assert gone not in phrases


def test_each_intent_has_5_to_12_phrases(tool):
    counts = Counter(i for i, _, _ in tool.read_ask(SEED))
    assert all(5 <= n <= 12 for n in counts.values()), counts


def test_read_ask_rejects_bad_csv(tool, tmp_path):
    rows = SEED.read_text(encoding="utf-8").splitlines()
    no_canonical = tmp_path / "a.csv"
    no_canonical.write_text(
        "\n".join(r.replace(",true", ",false") if r.startswith("quiet,") else r for r in rows),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="quiet 대표 질문"):
        tool.read_ask(no_canonical)
    duplicate = tmp_path / "b.csv"
    duplicate.write_text("\n".join([*rows, "peer,바다가 가까워?,false"]), encoding="utf-8")
    with pytest.raises(SystemExit, match="중복 표현"):
        tool.read_ask(duplicate)


def test_ask_writes_json_and_reuses_embeddings(tool, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tool, "load_key", lambda: "test-key")
    monkeypatch.setattr(tool, "embed", lambda key, text: calls.append(text) or [0.5] * 1024)
    items = tool.read_ask(SEED)
    out = tmp_path / "ask_vocab.json"
    old = [
        {"intent": i, "phrase": p, "canonical": c, "embedding": [0.25] * 1024}
        for i, p, c in items[1:]
    ]
    old.append(
        {"intent": "quiet", "phrase": "빠진 표현", "canonical": False, "embedding": [0.1] * 1024}
    )
    out.write_text(json.dumps({"model": tool.MODEL, "items": old}), encoding="utf-8")

    tool.ask(SEED, out)

    assert calls == [items[0][1]]  # 첫 표현만 임베딩이 없다
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["model"] == "clova-embedding-v2"
    assert [(r["intent"], r["phrase"], r["canonical"]) for r in data["items"]] == items
    assert data["items"][0]["embedding"] == [0.5] * 1024
    assert data["items"][1]["embedding"] == [0.25] * 1024


def test_ask_embeds_all_when_model_differs(tool, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tool, "load_key", lambda: "test-key")
    monkeypatch.setattr(tool, "embed", lambda key, text: calls.append(text) or [0.5] * 1024)
    items = tool.read_ask(SEED)
    out = tmp_path / "ask_vocab.json"
    old = [
        {"intent": i, "phrase": p, "canonical": c, "embedding": [0.25] * 1024} for i, p, c in items
    ]
    out.write_text(json.dumps({"model": "other-model", "items": old}), encoding="utf-8")

    tool.ask(SEED, out)

    assert len(calls) == len(items)


def test_committed_json_matches_csv(tool):
    """CSV 만 고치고 build_embeddings.py ask 를 다시 안 돌리면 여기서 걸린다."""
    data = json.loads((ROOT / "explore" / "data" / "ask_vocab.json").read_text(encoding="utf-8"))
    assert data["model"] == tool.MODEL
    rows = [(r["intent"], r["phrase"], r["canonical"]) for r in data["items"]]
    assert rows == tool.read_ask(SEED)
    assert all(len(r["embedding"]) == 1024 for r in data["items"])
