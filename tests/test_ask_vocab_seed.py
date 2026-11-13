"""db/seed/ask_vocab.csv 와 db/tools/build_embeddings.py ask 모드. CLOVA 대신 함수를 바꿔 끼운다.
db/tools 는 패키지가 아니라 파일 경로로 불러온다 (표준 라이브러리만 쓰는 스크립트)."""

import csv
import importlib.util
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


def test_ask_embeds_only_new_phrases(tool, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(tool, "load_key", lambda: "test-key")
    monkeypatch.setattr(tool, "embed", lambda key, text: calls.append(text) or [0.5] * 1024)
    items = tool.read_ask(SEED)
    have = tmp_path / "have.csv"
    with have.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["phrase"], *([p] for _, p, _ in items[1:])])

    tool.ask(SEED, str(tmp_path / "out"), have)

    assert calls == [items[0][1]]  # 첫 표현만 임베딩이 없다
    sql = "".join(f.read_text() for f in sorted(tmp_path.glob("out_*.sql")))
    assert sql.index("UPDATE ask_vocab SET canonical = false") < sql.index("INSERT INTO ask_vocab")
    assert "DELETE FROM ask_vocab WHERE phrase NOT IN (" in sql
    assert sql.count("INSERT INTO ask_vocab") == len(items)
    assert sql.count("embedding = EXCLUDED.embedding") == 1
    assert "'생활권이야, 관광지야?', true" in sql
