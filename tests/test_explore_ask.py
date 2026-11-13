"""동네에 물어보기 순수 함수 (v6.1 스펙 §3·§4·§6). 주문진읍 실측 jumunjin_raw 로 본다."""

import pytest

from explore import ask
from tests.explore_samples import jumunjin_raw

FACT_IDS = {
    "weekday_hours": ["F2-1"],
    "weekend_hours": ["F2-2"],
    "months": ["F3-1"],
    "quiet": ["F1-quiet"],
    "peer": ["F1-peer"],
    "evening": ["F1-evening", "F1-evening-sgg"],
    "type": ["F4-1", "F4-2", "F4-3", "F4-4"],
    "coast": ["F5-1"],
    "life": ["F7-1"],
    "similar": ["F6-1", "F6-2", "F6-3"],
}


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
    assert ids == ["F1-quiet", "F2-1"]


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
