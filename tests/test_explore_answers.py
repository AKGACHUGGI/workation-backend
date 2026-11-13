"""동네에 물어보기 저장 함수 (explore/sources.py, 테스트 DB).
ask_vocab 은 vector 컬럼이라 테스트 DB 에 없다 → vector 를 뺀 같은 표를 테스트 안에서 만든다.
nearest_vocab(pgvector)은 여기서 확인하지 않는다 (질문 답 테스트에서 바꿔 끼운다)."""

import json

import pytest
from django.db import connection

from explore import sources
from regions.models import Dong, Region

H1, H2 = "a" * 64, "b" * 64


@pytest.fixture
def jumunjin(db):
    Region.objects.create(
        region_id=1, sido_name="강원특별자치도", sigungu_name="강릉시", sigungu_code="51150"
    )
    Dong.objects.create(dong_id=116, region_id=1, dong_name="주문진읍", adm_code="5115025000")


@pytest.fixture
def ask_vocab(db):
    with connection.cursor() as cur:
        cur.execute(
            """CREATE TABLE ask_vocab (
                 vocab_id SERIAL PRIMARY KEY, intent VARCHAR(24) NOT NULL,
                 phrase VARCHAR(100) NOT NULL UNIQUE, canonical BOOLEAN NOT NULL DEFAULT false,
                 updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )


def rows(sql, params=()):
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_save_then_load_answer(jumunjin):
    assert sources.load_answer("5115025000", "weekday_hours", H1) is None
    sentences = ["평일 차량은 12시에 가장 많고, 업무시간엔 9시가 가장 한산해요."]
    sources.save_answer("5115025000", "weekday_hours", H1, sentences, "rule", None)
    assert sources.load_answer("5115025000", "weekday_hours", H1) == (sentences, "rule")
    assert sources.load_answer("5115025000", "weekday_hours", H2) is None
    assert sources.load_answer("5115025000", "quiet", H1) is None


def test_save_answer_keeps_first_row(jumunjin):
    sources.save_answer("5115025000", "quiet", H1, ["첫 답이에요."], "llm", "HCX-005")
    sources.save_answer("5115025000", "quiet", H1, ["두 번째 답이에요."], "rule", None)
    assert sources.load_answer("5115025000", "quiet", H1) == (["첫 답이에요."], "llm")
    assert rows("SELECT model FROM dong_answer") == [("HCX-005",)]


def test_save_answer_failure_is_ignored(jumunjin, caplog):
    sources.save_answer("5115025000", "quiet", H1, ["안내"], "guide", None)  # CHECK 위반
    sources.save_answer("9999999999", "quiet", H1, ["없는 동"], "rule", None)
    assert "dong_answer 저장 실패" in caplog.text
    # savepoint 덕분에 같은 트랜잭션에서 다음 쿼리가 된다
    assert rows("SELECT count(*) FROM dong_answer") == [(0,)]


def test_load_answer_failure_returns_none(jumunjin, caplog):
    with connection.cursor() as cur:
        cur.execute("ALTER TABLE dong_answer RENAME TO dong_answer_x")
    assert sources.load_answer("5115025000", "quiet", H1) is None
    assert "dong_answer 조회 실패" in caplog.text


def test_log_question(db, caplog):
    parsed = {"scope": "dong_ask", "dong": "5115025000", "intents": ["quiet"], "top_sim": 0.84}
    sources.log_question("이 동네 조용해?" + "요" * 400, parsed)
    ((query, saved),) = rows("SELECT query, parsed::text FROM nl_search_log")
    assert len(query) == 300 and json.loads(saved) == parsed
    with connection.cursor() as cur:
        cur.execute("ALTER TABLE nl_search_log RENAME TO nl_search_log_x")
    sources.log_question("또 물어봐요", parsed)  # 예외 없이 지나간다
    assert "nl_search_log 기록 실패" in caplog.text


def test_delete_old_answers(jumunjin):
    sources.save_answer("5115025000", "quiet", H1, ["오래된 답"], "rule", None)
    sources.save_answer("5115025000", "quiet", H2, ["새 답"], "rule", None)
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE dong_answer SET created_at = now() - interval '61 days' WHERE facts_hash = %s",
            [H1],
        )
    assert sources.delete_old_answers() == 1
    assert rows("SELECT facts_hash FROM dong_answer") == [(H2,)]
    assert sources.delete_old_answers() == 0


def test_clear_answers_by_source(jumunjin):
    sources.save_answer("5115025000", "quiet", H1, ["규칙"], "rule", None)
    sources.save_answer("5115025000", "peer", H1, ["생성"], "llm", "HCX-005")
    assert sources.clear_answers("rule") == 1
    assert rows("SELECT intents FROM dong_answer") == [("peer",)]
    assert sources.clear_answers() == 1


def test_load_vocab_and_version(ask_vocab):
    assert sources.vocab_version() == "0"
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO ask_vocab (intent, phrase, canonical, updated_at) VALUES
                 ('quiet', '이 동네 조용해?', true, '2026-11-13 00:00:00+00'),
                 ('quiet', '시끄러운 동네야?', false, '2026-11-13 00:00:10+00'),
                 ('coast', '바다가 가까워?', true, '2026-11-12 00:00:00+00')"""
        )
    assert sources.load_vocab() == [
        ("coast", "바다가 가까워?", True),
        ("quiet", "시끄러운 동네야?", False),
        ("quiet", "이 동네 조용해?", True),
    ]
    assert sources.vocab_version() == "1794528010"
