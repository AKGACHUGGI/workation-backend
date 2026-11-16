"""LangGraph 흐름 (스펙 §3·§7-1·§7-2-1). 의도 · 동 1개 답 · 생성은 가짜, 체크포인터는 메모리."""

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from explore import ask
from explore.chat import arrange, flow
from explore.chat.resolve import TOO_MANY
from tests.explore_chat_samples import GYO, HYOJA, NAMES, RAWS, YEOUI

VECS = {
    HYOJA: ((0.73, 0.56, 0.79, 0.7), False),
    YEOUI: ((0.57, 0.79, 0.96, 0.6), False),
    GYO: ((0.4, 0.6, 0.96, 0.8), False),
}
INTENTS = {
    "이 동네 조용해?": ["quiet"],
    "주말엔?": ["weekend_hours"],
    "여의동이랑 비교해 줘": ["compare_summary"],
    "비교해 줘": ["compare_summary"],
    "여의동, 교1동, 송천1동 다 비교해 줘": ["compare_summary"],
    "둘이 비슷해?": ["similar"],
}


@pytest.fixture(autouse=True)
def memory_saver(settings, monkeypatch):
    settings.REDIS_URL = ""
    monkeypatch.setattr(flow, "_saver", None)


def deps(generate=None, singles=None):
    def single(code, raw, intents, question):
        if singles is not None:
            singles.append((code, intents))
        return ["한 동 답이에요."], "llm"

    return flow.Deps(
        names=lambda: NAMES,
        raw=RAWS.get,
        find_intents=lambda q: (INTENTS.get(q, ["unknown_other"]), 0.9),
        single=single,
        generate=generate,
        vecs=lambda: VECS,
        pairs=lambda: arrange.pair_distances(VECS),
    )


def ask_(graph, question, cid=None, panel=HYOJA, criteria=None):
    criteria = criteria or arrange.clean_criteria(None)
    return flow.run(graph, cid, panel, question, criteria)


def test_settle_intents():
    assert flow.settle_intents(["unknown_other"], ["quiet"], True, 2) == ["quiet"]
    assert flow.settle_intents(["unknown_other"], [], True, 2) == ["compare_summary"]
    assert flow.settle_intents(["unknown_other"], ["unknown_place"], True, 2) == ["compare_summary"]
    assert flow.settle_intents(["unknown_other"], ["quiet"], False, 2) == ["unknown_other"]
    assert flow.settle_intents(["weekend_hours"], ["quiet"], True, 2) == ["weekend_hours"]


def test_three_turns_carry_dongs_and_intent():
    singles = []
    graph = flow.build(deps(singles=singles))
    cid, s1 = ask_(graph, "이 동네 조용해?")
    assert s1["dongs"] == [HYOJA]
    assert s1["sentences"] == [{"text": "한 동 답이에요.", "evidence": None, "source": "llm"}]
    assert singles == [(HYOJA, ["quiet"])]

    cid2, s2 = ask_(graph, "여의동이랑 비교하면?", cid)  # 의도를 못 찾음 → 직전 의도(quiet)
    assert cid2 == cid
    assert s2["dongs"] == [HYOJA, YEOUI]
    assert s2["intents"] == ["quiet"]
    assert s2["sentences"] == [
        {
            "text": "조용함을 원한다면 효자2동이 맞아요.",
            "evidence": "조용함: 효자2동 S(73점) · 여의동 A(57점)",
            "source": "rule",
        }
    ]

    _, s3 = ask_(graph, "주말엔?", cid)  # 동 이름 없음 → 두 동 이어받기
    assert s3["dongs"] == [HYOJA, YEOUI]
    assert [x["text"] for x in s3["sentences"]] == [
        "효자2동은 주말 14시에 차가 가장 많아요.",
        "여의동은 주말 14시에 차가 가장 많아요.",
    ]
    assert len(s3["turns"]) == 3


def test_question_is_not_checkpointed():
    graph = flow.build(deps())
    cid, state = ask_(graph, "아이와 함께 여의동이랑 비교해 줘")
    assert state["question"] == ""
    saved = list(flow.saver().list({"configurable": {"thread_id": cid}}))
    assert saved
    assert "아이와" not in repr(saved)


def test_other_panel_starts_new_conversation():
    graph = flow.build(deps())
    cid, _ = ask_(graph, "여의동이랑 비교해 줘")
    cid2, state = ask_(graph, "주말엔?", cid, panel=YEOUI)
    assert cid2 != cid
    assert state["dongs"] == [YEOUI]


def test_redis_error_runs_without_memory(monkeypatch):
    def broken():
        raise RedisConnectionError("down")

    monkeypatch.setattr(flow, "saver", broken)
    cid, state = ask_(flow.build(deps()), "여의동이랑 비교해 줘")
    assert len(cid) == 32
    assert state["dongs"] == [HYOJA, YEOUI]
    assert state["sentences"]


def test_guides():
    graph = flow.build(deps())
    _, other = ask_(graph, "경포동이랑 비교해 줘")
    assert other["guide"].startswith("지금은 같은 시군구 안의 동네끼리만")
    assert other["sentences"] == []
    _, single = ask_(graph, "비교해 줘")
    assert single["guide"] == ask.GUIDE["compare_summary"][0]
    _, unknown = ask_(graph, "맛집 있어?")
    assert unknown["guide"] == ask.GUIDE["unknown_other"][0]


def test_too_many_note_comes_first():
    _, state = ask_(flow.build(deps()), "여의동, 교1동, 송천1동 다 비교해 줘")
    assert state["dongs"] == [HYOJA, YEOUI, GYO]
    assert state["sentences"][0] == {"text": TOO_MANY, "evidence": None, "source": "rule"}


def test_compare_uses_criteria_and_cache():
    calls = []

    def generate(system, user, schema):
        calls.append(user)
        return schema.model_validate(
            {
                "sentences": [
                    {"text": "바다 가까이라면 여의동이 맞아요.", "axis": "coast", "pick": "여의동"}
                ]
            }
        )

    graph = flow.build(deps(generate=generate))
    criteria = arrange.clean_criteria({"chips": ["coast"]})
    _, first = ask_(graph, "여의동이랑 비교해 줘", criteria=criteria)
    assert first["sentences"][0]["text"] == "바다 가까이라면 여의동이 맞아요."
    assert first["source"] == "mixed"  # coast 는 LLM, 나머지 축은 규칙 문장
    assert "바다 가까운 순" in calls[0]
    _, again = ask_(graph, "여의동이랑 비교해 줘", criteria=criteria)  # 새 대화, 같은 비교
    assert again["sentences"] == first["sentences"]
    assert len(calls) == 1  # 1일 캐시


def test_similar_with_two_dongs():
    graph = flow.build(deps())
    cid, _ = ask_(graph, "여의동이랑 비교해 줘")
    _, state = ask_(graph, "둘이 비슷해?", cid)
    assert state["sentences"][0]["text"].startswith("효자2동과 여의동은 분위기가")


def test_respond_shape():
    graph = flow.build(deps())
    cid, state = ask_(graph, "여의동이랑 비교해 줘")
    body = flow.respond(cid, state, NAMES)
    assert body["conversationId"] == cid
    assert body["dongs"] == [{"id": HYOJA, "name": "효자2동"}, {"id": YEOUI, "name": "여의동"}]
    assert body["intent"] == "compare_summary"
    assert body["guide"] is None
    assert body["examples"] == list(flow.COMPARE_EXAMPLES)
    assert set(body["sentences"][0]) == {"text", "evidence", "source"}
