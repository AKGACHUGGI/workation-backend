"""질문 속 달·시각 → 코드가 미리 비교·계산한 사실 (2026-11-13: CLOVA 가 비교·계산을 자주 틀려서)."""

import pytest

from explore import ask, ask_calc
from tests.explore_samples import jumunjin_raw


@pytest.mark.parametrize(
    ("question", "months", "hours"),
    [
        ("1월이랑 3월은 어느정도 차이야?", [1, 3], []),
        ("12월에 가면 붐벼?", [12], []),
        ("오후 3시에 차 많아?", [], [15]),
        ("3시쯤 어때?", [], [15]),  # 숫자만 1~6시 → 오후로 본다
        ("오전 3시에도 차 다녀?", [], [3]),
        ("아침 9시쯤 카공하기 어때?", [], [9]),
        ("저녁 7시랑 밤 10시 중 언제가 한산해?", [], [19, 22]),
        ("15시 vs 17시", [], [15, 17]),
        ("이 동네 조용해?", [], []),
        ("1월 2월 3월 4월 다 알려줘", [1, 2, 3], []),  # 최대 3개
    ],
)
def test_extract_slots(question, months, hours):
    assert ask_calc.extract_slots(question) == ask_calc.Slots(months, hours)


def texts(question):
    return [f.text for f in ask_calc.computed_facts(jumunjin_raw(), question)]


def test_month_pair_almost_same():
    # 강릉시 1월 80.3 · 3월 78.4 → 차이 2% 남짓 = 거의 같음
    assert texts("1월이랑 3월은 어느정도 차이야?") == [
        "강릉시 1월 80.3 · 3월 78.4 (클수록 붐빔): 거의 비슷함 (차이 2%)"
    ]


def test_month_pair_clear_difference():
    assert texts("8월이랑 2월 차이?") == [
        "강릉시 8월 100.0 · 2월 69.8 (클수록 붐빔): 8월이 43% 더 붐빔"
    ]


def test_single_month_rank():
    assert texts("8월에 가면 붐벼?") == ["강릉시 8월 100.0: 1년 중 1번째로 붐비는 달"]


def test_hour_vs_peak_weekday_by_default():
    # 주문진 평일 15시 2024 · 12시 2064(하루 최대)
    assert texts("오후 3시에 차 많아?") == [
        "평일 15시 차량 이동 인구 2024명: 하루 최대(12시 2064명)의 98%, 많은 편"
    ]


def test_hour_weekend_and_low():
    assert texts("주말 오전 3시엔?") == [
        "주말 3시 차량 이동 인구 569명: 하루 최대(14시 2833명)의 20%, 적은 편"
    ]


def test_computed_facts_values_allow_numbers():
    fact = ask_calc.computed_facts(jumunjin_raw(), "1월이랑 3월은 어느정도 차이야?")[0]
    assert {"1", "3", "80.3", "78.4", "80", "78", "2"} <= set(fact.values)


def test_computed_rules():
    raw = jumunjin_raw()
    assert ask_calc.computed_rules(raw, "1월이랑 3월은 어느정도 차이야?") == [
        "강릉시는 1월(80.3)과 3월(78.4)이 거의 비슷해요."
    ]
    assert ask_calc.computed_rules(raw, "8월이랑 2월 차이?") == [
        "강릉시는 8월이 2월보다 43% 더 붐벼요."
    ]
    assert ask_calc.computed_rules(raw, "오후 3시에 차 많아?") == [
        "평일 15시 차량은 하루 가장 많은 12시의 98%로 많은 편이에요."
    ]


def test_facts_for_adds_computed_facts_and_rule_answer_uses_them():
    raw = jumunjin_raw()
    q = "1월이랑 3월은 어느정도 차이야?"
    facts = ask.facts_for(raw, ["months"], q)
    assert [f.id for f in facts] == ["F3-1", "F3-2", "F8-1"]
    assert ask.rule_answer(raw, ["months"], facts, q) == [
        "강릉시는 1월(80.3)과 3월(78.4)이 거의 비슷해요."
    ]


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("1월과 3월은 거의 비슷해요.", None),
        ("1월과 3월은 거의 비슷합니다.", None),  # 합니다체도 존댓말로 받는다
        ("1월과 3월은 거의 비슷하다.", "존댓말 아님"),
        ("1월이 3월보다 2배 붐벼요.", "배수 표현"),
    ],
)
def test_check_sentence_style(text, problem):
    raw = jumunjin_raw()
    facts = {f.id: f for f in ask.facts_for(raw, ["months"], "1월이랑 3월은 어느정도 차이야?")}
    problems = ask.check_sentence(text, ["F8-1"], facts)
    if problem is None:
        assert problems == []
    else:
        assert problem in problems


def test_answer_reads_plain_text_reply():
    # HCX-005 는 JSON 지시를 자주 무시한다 → 일반 문장도 읽고, 근거는 넘긴 사실 전체로 본다
    raw = jumunjin_raw()
    q = "오후 3시에 차 많아?"
    out = ask.answer(
        raw, ["weekday_hours"], lambda s, u: "오후 3시는 2,024명으로 많은 편입니다.", question=q
    )
    assert (out.sentences, out.source) == (["오후 3시는 2,024명으로 많은 편입니다."], "llm")


def test_answer_plain_text_wrong_number_is_rule():
    raw = jumunjin_raw()
    q = "오후 3시에 차 많아?"
    out = ask.answer(raw, ["weekday_hours"], lambda s, u: "오후 3시는 999명이에요.", question=q)
    assert out.sentences == ["평일 15시 차량은 하루 가장 많은 12시의 98%로 많은 편이에요."]
    assert out.source == "rule"


def test_parse_plain_splits_sentences():
    assert ask.parse_plain("첫 문장이에요. 둘째 문장이에요!\n셋째예요?") == [
        "첫 문장이에요.",
        "둘째 문장이에요!",
        "셋째예요?",
    ]


def test_parse_plain_drops_citation_marks_and_quotes():
    reply = '"1월과 3월은 거의 비슷해요." (F8-1)\n(facts: F3-2, F8-1)'
    assert ask.parse_plain(reply) == ["1월과 3월은 거의 비슷해요."]


def test_failed_sentence_dropped_when_same_topic_already_answered():
    raw = jumunjin_raw()
    q = "1월이랑 3월은 어느정도 차이야?"
    reply = "1월과 3월은 거의 비슷해요. 두 달 지수는 80.3과 78.4이고 2배 차이예요."
    out = ask.answer(raw, ["months"], lambda s, u: reply, question=q)
    assert out.sentences == ["1월과 3월은 거의 비슷해요."]
    assert out.source == "mixed"
