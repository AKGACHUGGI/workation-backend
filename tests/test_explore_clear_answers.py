"""manage.py clear_answers. DB 와 Redis 대신 함수를 바꿔 끼운다."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from explore import sources
from explore.management.commands import clear_answers


class FakeRedis:
    def __init__(self):
        self.patterns = []

    def delete_pattern(self, pattern):
        self.patterns.append(pattern)
        return 3


@pytest.fixture
def cleared(monkeypatch):
    seen = []
    monkeypatch.setattr(sources, "clear_answers", lambda source=None: seen.append(source) or 5)
    return seen


def run(*args):
    out = StringIO()
    call_command("clear_answers", *args, stdout=out)
    return out.getvalue()


def test_clears_rows_and_redis_answer_keys(monkeypatch, cleared):
    redis = FakeRedis()
    monkeypatch.setattr(clear_answers, "cache", redis)
    assert "dong_answer 5행 · 답 캐시 3개 지움" in run("--source", "rule")
    assert cleared == ["rule"]
    assert redis.patterns == ["explore:ask:a:*"]


def test_without_redis_warns(cleared):
    text = run()
    assert cleared == [None]
    assert "Redis 가 아니라서" in text and "답 캐시 0개" in text


def test_rejects_unknown_source(cleared):
    with pytest.raises(CommandError):
        run("--source", "guide")
