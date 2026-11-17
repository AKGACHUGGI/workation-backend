"""동네에 물어보기 의도 사전 함수 (explore/sources.py, 테스트 DB).
ask_vocab 은 vector 컬럼이라 테스트 DB 에 없다 → vector 를 뺀 같은 표를 테스트 안에서 만든다.
nearest_vocab(pgvector)은 여기서 확인하지 않는다 (질문 답 테스트에서 바꿔 끼운다)."""

import pytest
from django.db import connection

from explore import sources


@pytest.fixture
def ask_vocab(db):
    with connection.cursor() as cur:
        cur.execute(
            """CREATE TABLE ask_vocab (
                 vocab_id SERIAL PRIMARY KEY, intent VARCHAR(24) NOT NULL,
                 phrase VARCHAR(100) NOT NULL UNIQUE, canonical BOOLEAN NOT NULL DEFAULT false,
                 updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )


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
