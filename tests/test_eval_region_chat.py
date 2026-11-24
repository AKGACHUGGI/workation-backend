"""eval_region_chat: 시군구 질문 묶음을 흐름에 넣고 표로 찍는다 (시군구 채팅 스펙 §9)."""

from io import StringIO

from django.core.management import call_command

from explore import views
from explore.chat import arrange, flow
from tests.explore_chat_samples import NAMES, RAWS, REGION_RAW


def test_eval_prints_table(monkeypatch, settings):
    settings.REDIS_URL = ""
    monkeypatch.setattr(flow, "_saver", None)
    monkeypatch.setattr(
        views,
        "_chat_deps",
        lambda: flow.Deps(
            names=lambda: NAMES,
            raw=RAWS.get,
            find_intents=lambda q, scope="dong": (["find_quiet"], 0.9),
            single=lambda *a: (["한 동 답이에요."], "llm"),
            generate=None,
            vecs=lambda: {},
            pairs=lambda: arrange.Pairs([], []),
            region_raw={"45111": REGION_RAW}.get,
            walk_peaks=lambda code, month: None,
            best_months=lambda sgg: [],
        ),
    )
    out = StringIO()
    call_command("eval_region_chat", "45111", stdout=out)
    lines = out.getvalue().splitlines()
    assert lines[0].split("\t")[:3] == ["45111", "조용한 동네 어디야?", "rule"]
    assert lines[-1].startswith("LLM 문장 0/")
