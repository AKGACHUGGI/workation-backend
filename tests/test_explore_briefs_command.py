"""manage.py build_briefs. Neo4j · DB · CLOVA 대신 함수를 바꿔 끼운다."""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core import clova
from explore import brief, queries, sources
from explore.graphdb import GraphUnavailable
from explore.management.commands import build_briefs
from tests.explore_samples import jumunjin_raw

GOOD = {
    "sentences": [
        {"text": "평일엔 12시에 차가 가장 많고 9시가 가장 한산해요.", "facts": ["F2-1"]},
        {"text": "강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요.", "facts": ["F3-1"]},
        {"text": "저녁 활기는 강릉시 21동 중 1위예요.", "facts": ["F1-evening-sgg"]},
    ]
}


@pytest.fixture
def saved(monkeypatch):
    rows = []
    monkeypatch.setattr(sources, "load_brief_targets", lambda: [(116, "5115025000")])
    monkeypatch.setattr(sources, "load_brief_hashes", lambda: {})
    monkeypatch.setattr(sources, "save_brief", lambda *args: rows.append(args))
    monkeypatch.setattr(queries, "fetch_dong", lambda code: jumunjin_raw())
    monkeypatch.setattr(sources, "delete_old_answers", lambda: 2)
    return rows


def run(*args):
    out = StringIO()
    call_command("build_briefs", *args, stdout=out)
    return out.getvalue()


def test_with_clova(settings, monkeypatch, saved):
    settings.CLOVA_API_KEY = "test-key"
    monkeypatch.setattr(build_briefs, "chat", lambda s, u: json.dumps(GOOD, ensure_ascii=False))
    text = run()
    assert "llm 1 · mixed 0 · rule 0" in text
    assert "60일 지난 질문 답 2개 지움" in text
    dong_id, sentences, source, model, digest = saved[0]
    assert (dong_id, source, model) == (116, "llm", "HCX-005")
    assert sentences[1] == "강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요."
    assert digest == brief.facts_hash(brief.collect_facts(jumunjin_raw()))


def test_without_clova_key_rule_only_and_warns(settings, saved):
    settings.CLOVA_API_KEY = ""
    text = run()
    assert "CLOVA_API_KEY 없음" in text and "rule 1" in text
    assert "20% 초과" in text
    assert saved[0][2] == "rule" and saved[0][3] is None


def test_same_facts_hash_is_skipped(settings, monkeypatch, saved):
    settings.CLOVA_API_KEY = ""
    digest = brief.facts_hash(brief.collect_facts(jumunjin_raw()))
    monkeypatch.setattr(sources, "load_brief_hashes", lambda: {116: digest})
    assert "건너뜀 1" in run()
    assert saved == []
    run("--force")
    assert len(saved) == 1


def test_neo4j_down_fails(monkeypatch, saved):
    def down(code):
        raise GraphUnavailable("refused")

    monkeypatch.setattr(queries, "fetch_dong", down)
    with pytest.raises(CommandError, match="sync_graph"):
        run()


def test_chat_uses_20s_timeout(monkeypatch):
    seen = {}

    def fake_post(path, payload, timeout):
        seen.update(timeout=timeout, temperature=payload["temperature"])
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(clova, "_post", fake_post)
    build_briefs.chat("s", "u")
    assert seen == {"timeout": 20, "temperature": 0.3}
