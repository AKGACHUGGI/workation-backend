"""LangGraph 흐름 (스펙 §3·§7-1·§7-2-1). 의도 · 동 1개 답 · 생성은 가짜, 체크포인터는 메모리."""

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redisvl.exceptions import RedisSearchError

from explore import ask
from explore.chat import arrange, flow
from explore.chat.resolve import TOO_MANY
from tests.explore_chat_samples import GYO, HYOJA, NAMES, RAWS, REGION_RAW, YEOUI

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
REGION_FOUND = {
    "조용한 동네 어디야?": ["find_quiet"],
    "몇 월에 가면 한산해?": ["region_months"],
    "몇 시에 붐벼?": ["weekday_hours"],
    "맛집 있어?": ["unknown_place"],
}
INTENTS["저녁엔?"] = ["evening"]
WALK = {
    "weekday": {"peakHour": 11, "lowHour": 18, "lowValue": 90},
    "weekend": {"peakHour": 12, "lowHour": 9, "lowValue": 88},
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

    def find_intents(q, scope="dong"):
        table = REGION_FOUND if scope == "region" else INTENTS
        return table.get(q, ["unknown_other"]), 0.9

    return flow.Deps(
        names=lambda: NAMES,
        raw=RAWS.get,
        find_intents=find_intents,
        single=single,
        generate=generate,
        vecs=lambda: VECS,
        pairs=lambda: arrange.pair_distances(VECS),
        region_raw={"45111": REGION_RAW}.get,
        walk_peaks=lambda code, month: WALK,
        best_months=lambda sgg: [2, 3],
    )


def ask_(graph, question, cid=None, panel=HYOJA, criteria=None, month=None, walk=False):
    criteria = criteria or arrange.clean_criteria(None)
    inp = {
        "surface": "dong",
        "panel": panel,
        "sigungu": None,
        "month": month,
        "walk": walk,
        "question": question,
        "criteria": criteria,
    }
    return flow.run(graph, cid, inp)


def ask_region(graph, question, cid=None, sgg="45111"):
    inp = {
        "surface": "region",
        "panel": None,
        "sigungu": sgg,
        "month": 10,
        "walk": True,
        "question": question,
        "criteria": arrange.clean_criteria(None),
    }
    return flow.run(graph, cid, inp)


def test_settle_intents():
    assert flow.settle_intents(["unknown_other"], ["quiet"], True, 2) == ["quiet"]
    assert flow.settle_intents(["unknown_other"], [], True, 2) == ["compare_summary"]
    assert flow.settle_intents(["unknown_other"], ["unknown_place"], True, 2) == ["compare_summary"]
    # 비교 중이어도 동 이름이 없으면("안녕") 직전 비교를 되풀이하지 않고 안내
    assert flow.settle_intents(["unknown_other"], ["quiet"], False, 2) == ["unknown_other"]
    prev = ["compare_summary"]
    assert flow.settle_intents(["unknown_other"], prev, False, 2) == ["unknown_other"]
    assert flow.settle_intents(["unknown_other"], ["quiet"], False, 1) == ["unknown_other"]
    assert flow.settle_intents(["unknown_place"], ["quiet"], False, 2) == ["unknown_place"]
    assert flow.settle_intents(["weekend_hours"], ["quiet"], True, 2) == ["weekend_hours"]


def test_unknown_follow_up_in_comparison_guides():
    graph = flow.build(deps())
    cid, _ = ask_(graph, "여의동이랑 비교해 줘")
    _, state = ask_(graph, "안녕", cid)
    assert state["intents"] == ["unknown_other"]
    assert state["dongs"] == [HYOJA, YEOUI]
    assert state["guide"] == ask.GUIDE["unknown_other"][0]
    assert state["sentences"] == []


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
        "효자2동은 주말 차량이 14시에 가장 많아요.",
        "여의동은 주말 차량이 14시에 가장 많아요.",
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


@pytest.mark.parametrize(
    "error",
    [
        RedisConnectionError("down"),
        # search 모듈 없는 Redis (Homebrew redis 등): 체크포인터 setup 이 FT.INFO 에서 실패
        RedisSearchError("unknown command 'FT.INFO'"),
    ],
)
def test_redis_error_runs_without_memory(monkeypatch, error):
    def broken():
        raise error

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
            {"sentences": [{"text": "바다 가까이라면 여의동이 맞아요.", "axis": "coast"}]}
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


def test_dong_hours_both_with_month():
    graph = flow.build(deps())
    _, state = ask_(graph, "주말엔?", month=10, walk=True)
    assert [x["text"] for x in state["sentences"]] == [
        "효자2동은 주말 차량이 14시에 가장 많고, "
        "걸어 다니는 사람은 10월 기준 12시에 가장 붐비고 9시에 가장 한산해요."
    ]
    assert state["scope"] == "dong"


def test_region_find_then_compare_then_follow():
    graph = flow.build(deps())
    cid, s1 = ask_region(graph, "조용한 동네 어디야?")
    assert s1["scope"] == "region"
    assert s1["dongs"] == [HYOJA, YEOUI, GYO]
    assert s1["sentences"][0]["text"] == "조용함을 원한다면 효자2동이 맞아요."

    _, s2 = ask_region(graph, "효자2동이랑 여의동 비교해 줘", cid)
    assert (s2["scope"], s2["dongs"]) == ("dong", [HYOJA, YEOUI])
    assert s2["intents"] == ["compare_summary"]  # 직전 find_quiet 를 잇지 않는다

    _, s3 = ask_region(graph, "저녁엔?", cid)  # 동 대화 이어받기
    assert (s3["scope"], s3["dongs"], s3["intents"]) == ("dong", [HYOJA, YEOUI], ["evening"])

    _, s4 = ask_region(graph, "조용한 동네 어디야?", cid)  # 찾기 말 → 다시 시군구 답
    assert s4["scope"] == "region"


def test_region_months_hours_and_unknown():
    graph = flow.build(deps())
    _, state = ask_region(graph, "몇 월에 가면 한산해?")
    assert state["sentences"][0]["text"] == (
        "지도 탐색 기준으로는 1·2·8월이 한산하고, 방문 기준 가기 좋은 달은 2·3월이에요."
    )
    assert state["dongs"] == []
    _, state = ask_region(graph, "몇 시에 붐벼?")
    assert state["guide"] == ask.REGION_HOURS_GUIDE
    _, state = ask_region(graph, "맛집 있어?")
    assert state["guide"] == ask.GUIDE["unknown_place"][0]


def test_region_respond_examples_and_scope():
    graph = flow.build(deps())
    cid, state = ask_region(graph, "조용한 동네 어디야?")
    body = flow.respond(cid, state, NAMES)
    assert body["scope"] == "region"
    assert body["examples"] == [
        "효자2동은 저녁에 어때?",
        "효자2동이랑 여의동 비교해 줘",
        "몇 월에 가면 한산해?",
    ]
    _, state = ask_region(graph, "맛집 있어?")
    assert flow.respond(cid, state, NAMES)["examples"] == ask.REGION_FIRST


def test_other_sigungu_starts_new_region_conversation():
    graph = flow.build(deps())
    cid, _ = ask_region(graph, "조용한 동네 어디야?")
    cid2, _ = ask_region(graph, "조용한 동네 어디야?", cid, sgg="51150")
    assert cid2 != cid


def test_region_guide_turn_keeps_scope():
    graph = flow.build(deps())
    cid, _ = ask_region(graph, "조용한 동네 어디야?")
    _, guided = ask_region(graph, "강릉시 경포동은 어때?", cid)
    assert guided["guide"] is not None
    assert (guided["scope"], guided["dongs"]) == ("region", [HYOJA, YEOUI, GYO])
    _, state = ask_region(graph, "저녁엔?", cid)  # 후보 3곳 비교로 가지 않는다
    assert state["scope"] == "region"
