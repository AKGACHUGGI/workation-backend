"""브리핑 문장이 등급 사실(F1)을 인용할 때: 등급 쪽 표현, 뒤집힘 없음, 이유는 사실 안의 말만.
주문진읍(조용함 B · 또래 B · 저녁 활기 A). 다른 LLM 60문장 · HCX 실측(2026-11-17)에서 골랐다.
채팅의 등급 질문은 LLM 을 안 쓰고 템플릿으로 답한다 (test_explore_ask)."""

import pytest

from explore import ask, brief
from tests.explore_samples import jumunjin_raw

NO_LABEL = "결론 문구 없음"
REASON = "사실에 없는 이유"


@pytest.fixture(scope="module")
def facts():
    return {f.id: f for f in brief.collect_facts(jumunjin_raw())}


def test_fact_text_has_label(facts):
    assert facts["F1-quiet"].text.endswith("→ 보통 수준")
    assert facts["F1-evening"].text.endswith("→ 저녁에 활기 있는 편")


@pytest.mark.parametrize(
    ("text", "cited", "problem"),
    [
        # 등급 쪽 표현 + 사실 안의 이유 · 주어 · 지명
        ("저녁 활기가 A등급이라 저녁에 활기 있는 편이에요.", "F1-evening", None),
        ("이곳은 저녁 활기가 높은 편이에요.", "F1-evening", None),
        ("강원특별자치도 주문진읍은 저녁에 활기가 있는 편이에요.", "F1-evening", None),
        ("조용함 31점으로 보통 수준이에요.", "F1-quiet", None),
        ("이 동네의 또래 밀도는 보통이에요.", "F1-peer", None),
        # 반대 쪽 표현 (단어 목록 방식이 통과시켰던 것 포함)
        ("주문진읍은 꽤 조용한 편이에요.", "F1-quiet", NO_LABEL),
        ("크게 붐비지 않아 조용해요.", "F1-quiet", NO_LABEL),
        ("또래가 적지 않아요.", "F1-peer", NO_LABEL),
        ("저녁엔 대체로 한산해요.", "F1-evening", NO_LABEL),
        ("저녁 활기가 적은 편이에요.", "F1-evening", NO_LABEL),
        # 표현 뒤에서 뒤집힘
        ("저녁에 활기 있는 편은 아니에요.", "F1-evening", NO_LABEL),
        ("조용함은 보통보다 낮아요.", "F1-quiet", NO_LABEL),
        # 표현은 맞지만 지어낸 이유
        ("야경 명소가 많아서 저녁에 활기 있는 편이에요.", "F1-evening", REASON),
        ("저녁에 활기 있는 편인데 술집이 많기 때문이에요.", "F1-evening", REASON),
    ],
)
def test_grade_sentence(facts, text, cited, problem):
    problems = brief.validate_sentence(text, [cited], facts)
    if problem is None:
        assert problems == []
    else:
        assert any(p.startswith(problem) for p in problems), problems


def test_rule_answer_has_label():
    raw = jumunjin_raw()
    assert ask.rule_answer(raw, ["quiet"], ask.facts_for(raw, ["quiet"])) == [
        "조용함은 B등급(31점)으로 전국 203동 중 140위, 보통 수준이에요."
    ]
