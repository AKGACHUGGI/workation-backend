"""동네에 물어보기 순수 함수 (v6.1 스펙 §3·§4·§6). 주문진읍 실측 jumunjin_raw 로 본다."""

import json

import pytest

from core.clova import CHAT_MODEL, ClovaError
from explore import ask, brief
from tests.explore_samples import jumunjin_raw

FACT_IDS = {
    "weekday_hours": ["F2-1", "F2-1h"],
    "weekend_hours": ["F2-2", "F2-2h"],
    "months": ["F3-1", "F3-2"],
    "quiet": ["F1-quiet"],
    "peer": ["F1-peer"],
    "evening": ["F1-evening", "F1-evening-sgg"],
    "type": ["F4-1", "F4-2", "F4-3"],
    "coast": ["F5-1"],
    "life": ["F7-1"],
    "similar": ["F6-1", "F6-2", "F6-3"],
}
RULES = {
    "weekday_hours": ["평일 차량은 12시에 가장 많고, 업무시간엔 9시가 가장 한산해요."],
    "weekend_hours": ["주말 차량은 14시에 가장 많고, 3시가 가장 한산해요."],
    "months": ["강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요."],
    "quiet": ["조용함은 B등급(31점)으로, 전국 203동 중 140위예요."],
    "peer": ["또래 밀도는 B등급(39점)으로, 전국 203동 중 124위예요."],
    "evening": [
        "저녁 활기는 A등급(62점)으로, 전국 203동 중 78위예요.",
        "강릉시 안에서는 저녁 활기 1위예요.",
    ],
    "type": ["로컬살이형 60점으로 가장 잘 맞고, 다음은 네트워킹형 50점이에요."],
    "coast": ["해안선까지 0.0km인 바닷가 동네예요."],
    "life": ["생활권 성격이 강한 동네예요."],
    "similar": ["지표가 비슷한 동네는 속초시 노학동, 서귀포시 대정읍, 서귀포시 영천동이에요."],
}


def reply(*items) -> str:
    return json.dumps(
        {"sentences": [{"text": t, "facts": f} for t, f in items]}, ensure_ascii=False
    )


def facts_of(raw, intents):
    return {f.id: f for f in ask.facts_for(raw, intents)}


def test_intent_tables_match_spec():
    assert len(ask.INTENTS) == 10 and len(ask.UNKNOWN) == 4
    assert set(ask.CANONICAL) == set(ask.INTENTS) == set(ask.INTENT_FACTS)
    assert set(ask.GUIDE) == set(ask.UNKNOWN)
    assert all(len(examples) == 3 for _, examples in ask.GUIDE.values())
    assert ask.GUIDE["unknown_place"][0] == (
        "이 지도는 동네의 붐빔과 분위기를 보고 있어서 식당·카페는 아직 몰라요. "
        "대신 점심·저녁에 이 동네가 얼마나 북적이는지는 알려 드릴 수 있어요."
    )


def test_normalize():
    assert ask.normalize(" 이 동네  조용해?! ~") == "이동네조용해"
    assert ask.normalize("WiFi 잘 돼.") == "wifi잘돼"


@pytest.mark.parametrize(
    ("ranked", "expected"),
    [
        ([], ["unknown_other"]),
        ([("quiet", 0.67)], ["unknown_other"]),
        ([("quiet", 0.84), ("quiet", 0.83), ("weekday_hours", 0.79)], ["quiet", "weekday_hours"]),
        ([("quiet", 0.84), ("weekday_hours", 0.78)], ["quiet"]),  # 차이 0.06
        ([("quiet", 0.70), ("evening", 0.67)], ["quiet"]),  # 2위가 기준 미만
        ([("unknown_place", 0.80), ("evening", 0.79)], ["unknown_place"]),
        ([("evening", 0.80), ("unknown_place", 0.79)], ["evening"]),
    ],
)
def test_pick_intents(ranked, expected):
    assert ask.pick_intents(ranked) == expected


def test_life_fact():
    fact = ask.life_fact(jumunjin_raw())
    assert (fact.id, fact.text, fact.values) == (
        "F7-1",
        "생활권 (생활권 지수 0.74)",
        frozenset({"0.74"}),
    )
    raw = jumunjin_raw()
    raw["facts"]["5115025000"]["life"] = []
    assert ask.life_fact(raw) is None


@pytest.mark.parametrize("intent", ask.INTENTS)
def test_facts_for_each_intent(intent):
    assert [f.id for f in ask.facts_for(jumunjin_raw(), [intent])] == FACT_IDS[intent]


def test_facts_for_two_intents_in_intent_order():
    ids = [f.id for f in ask.facts_for(jumunjin_raw(), ["quiet", "weekday_hours"])]
    assert ids == ["F1-quiet", "F2-1", "F2-1h"]


def test_examples_for():
    assert ask.examples_for(["weekday_hours"]) == [
        "주말엔 몇 시가 붐비고 몇 시가 한산해?",
        "1년 중 언제 가면 한산해?",
        "이 동네 조용해?",
    ]
    assert ask.examples_for(["months", "weekday_hours"]) == [
        "주말엔 몇 시가 붐비고 몇 시가 한산해?",
        "이 동네 조용해?",
        "또래 사람들이 많아?",
    ]
    assert ask.examples_for(["unknown_stay"]) == [
        "어떤 워케이션에 잘 맞아?",
        "1년 중 언제 가면 한산해?",
        "비슷한 동네는 어디야?",
    ]


@pytest.mark.parametrize("intent", ask.INTENTS)
def test_rule_answer_each_intent(intent):
    raw = jumunjin_raw()
    assert ask.rule_answer(raw, [intent], ask.facts_for(raw, [intent])) == RULES[intent]


def test_rule_answer_without_data():
    raw = jumunjin_raw()
    raw["rec"]["hours"], raw["rec"]["near"], raw["rec"]["similar"] = [], [], []
    raw["facts"]["5115025000"]["life"] = []
    intents = ["coast", "life", "similar", "weekday_hours"]
    assert ask.rule_answer(raw, intents, ask.facts_for(raw, intents)) == [
        "해안선에서 1km 넘게 떨어진 동네예요.",
        "이 동네는 그 내용을 계산할 데이터가 부족해요.",
    ]


def test_anchors():
    facts = facts_of(jumunjin_raw(), list(ask.INTENTS))
    assert ask.anchors(facts["F2-1"]) == {"12", "9", "2064", "1573", "17", "평일"}
    assert ask.anchors(facts["F1-evening-sgg"]) == {"1", "21", "저녁", "활기"}
    assert ask.anchors(facts["F4-1"]) == {"1", "60", "로컬살이형", "로컬살이"}
    assert ask.anchors(facts["F6-1"]) == {"노학동"}
    assert ask.anchors(facts["F7-1"]) == {"0.74", "생활권"}


def test_prompt_has_user_question_and_chosen_facts():
    raw = jumunjin_raw()
    facts = ask.facts_for(raw, ["months"])
    system, user = ask.build_prompt(raw, facts, "1월이랑 3월은 어느정도 차이야?")
    assert system == ask.SYSTEM
    assert user.startswith(
        "동네: 강원특별자치도 강릉시 주문진읍\n질문: 1월이랑 3월은 어느정도 차이야?\n사실:\n"
        "F3-1: 강릉시 월별 방문: 가장 한산한 달 2·4·9월, 가장 붐비는 달 8월\n"
        "F3-2: 강릉시 월별 방문 원값(클수록 붐빔): 1월 80.3, 2월 69.8, 3월 78.4,"
    )


def test_ask_prompt_repeats_format_at_end():
    raw = jumunjin_raw()
    _, user = ask.build_prompt(raw, ask.facts_for(raw, ["quiet"]), "조용해?")
    assert user.endswith(brief.FORMAT_TAIL)


def test_detail_facts_allow_month_and_hour_values():
    facts = facts_of(jumunjin_raw(), ["months", "weekday_hours"])
    ok = "강릉시는 1월이 80.3, 3월이 78.4로 비슷해요."
    assert ask.check_sentence(ok, ["F3-2"], facts) == []
    assert ask.check_sentence("1월은 80, 3월은 78 정도예요.", ["F3-2"], facts) == []
    assert ask.check_sentence("평일 15시엔 2036명이 다녀요.", ["F2-1h"], facts) == []
    assert "사실에 없는 값: ['999']" in ask.check_sentence(
        "평일 15시엔 999명이에요.", ["F2-1h"], facts
    )


def test_prompt_hash_depends_on_question():
    facts = ask.facts_for(jumunjin_raw(), ["months"])
    assert ask.prompt_hash(facts, "1월 vs 3월?") == ask.prompt_hash(facts, "1월 vs 3월")
    assert ask.prompt_hash(facts, "1월 vs 3월?") != ask.prompt_hash(facts, "언제 한산해?")


@pytest.mark.parametrize(
    ("text", "cited", "problem"),
    [
        ("평일엔 9시가 가장 한산해서 오전 업무에 맞아요.", ["F2-1"], None),
        ("조용한 편이라 집중하기 괜찮아요.", ["F1-quiet"], None),
        ("강릉시 안에서는 21동 중 저녁 1위예요.", ["F1-evening-sgg"], None),
        ("오전에 일하기 괜찮은 동네예요.", ["F2-1"], "근거어 없음"),
        ("평일 9시가 한산해요. www.example.com 참고", ["F2-1"], "URL·이메일·전화번호 모양"),
        ("평일 9시 문의는 010-1234-5678 이에요.", ["F2-1"], "URL·이메일·전화번호 모양"),
        ("평일 9시가 한산해서 good 이에요.", ["F2-1"], "영문 단어: ['good']"),
        ("평일엔 10시가 가장 한산해요.", ["F2-1"], "사실에 없는 값: ['10']"),
        ("생활권 성격이 강한 동네예요.", ["F7-1"], None),
        ("관광지 성격이 강해요.", ["F7-1"], "근거어 없음"),
    ],
)
def test_check_sentence(text, cited, problem):
    facts = facts_of(jumunjin_raw(), list(ask.INTENTS))
    problems = ask.check_sentence(text, cited, facts)
    if problem is None:
        assert problems == []
    else:
        assert problem in problems


def test_check_sentence_allows_km():
    facts = facts_of(jumunjin_raw(), ["coast"])
    assert ask.check_sentence("해안선까지 0.0km인 바닷가예요.", ["F5-1"], facts) == []


def test_answer_all_generated():
    out = ask.answer(
        jumunjin_raw(),
        ["weekday_hours"],
        lambda s, u: reply(("평일엔 9시가 가장 한산해서 오전 업무에 맞아요.", ["F2-1"])),
        question="q",
    )
    assert out.sentences == ["평일엔 9시가 가장 한산해서 오전 업무에 맞아요."]
    assert (out.source, out.model) == ("llm", CHAT_MODEL)
    assert out.facts_hash == ask.prompt_hash(ask.facts_for(jumunjin_raw(), ["weekday_hours"]), "q")


def test_answer_replaces_failed_sentence_with_its_intent_rule():
    out = ask.answer(
        jumunjin_raw(),
        ["quiet", "weekday_hours"],
        lambda s, u: reply(
            ("평일엔 9시가 가장 한산해요.", ["F2-1"]),
            ("아주 조용한 동네예요. http://x.kr", ["F1-quiet"]),
        ),
        question="q",
    )
    assert out.sentences == ["평일엔 9시가 가장 한산해요.", *RULES["quiet"]]
    assert out.source == "mixed"


@pytest.mark.parametrize(
    "chat_reply",
    [
        reply(),  # 0문장
        reply(*[("평일엔 9시가 한산해요.", ["F2-1"])] * 4),  # 4문장
        "죄송합니다",  # JSON 없음
    ],
)
def test_answer_bad_reply_is_rule(chat_reply):
    out = ask.answer(jumunjin_raw(), ["weekday_hours"], lambda s, u: chat_reply, question="q")
    assert (out.sentences, out.source, out.model) == (RULES["weekday_hours"], "rule", None)


def test_answer_chat_error_and_no_key_are_rule():
    def fail(system, user):
        raise ClovaError("CLOVA HTTP 500")

    raw = jumunjin_raw()
    assert ask.answer(raw, ["evening"], fail, question="q").sentences == RULES["evening"]
    assert ask.answer(raw, ["evening"], None, question="q").source == "rule"


def test_answer_without_facts_skips_chat():
    raw = jumunjin_raw()
    raw["facts"]["5115025000"]["life"] = []
    calls = []
    out = ask.answer(raw, ["life"], lambda s, u: calls.append(u) or reply(), question="q")
    assert calls == [] and out.sentences == ["이 동네는 그 내용을 계산할 데이터가 부족해요."]
