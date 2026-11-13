"""explore/brief.py 사실 · 검증 · 규칙 문장 · 생성 (순수, 가짜 채팅 함수)."""

import json

import pytest

from core.clova import ClovaError
from explore import brief
from tests.explore_samples import jumunjin_raw

RULES = [
    "평일 차량은 12시에 가장 많고, 업무시간엔 9시가 가장 한산해요.",
    "강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요.",
    "로컬살이형 60점이고, 강릉시 안 저녁 활기 1위예요.",
]
GOOD = [
    ("평일엔 12시에 차가 가장 많고 9시가 가장 한산해요.", ["F2-1"]),
    ("강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요.", ["F3-1"]),
    ("저녁 활기는 강릉시 21동 중 1위예요.", ["F1-evening-sgg"]),
]


def facts():
    return brief.collect_facts(jumunjin_raw())


def by_id():
    return {f.id: f for f in facts()}


def reply(items):
    return json.dumps(
        {"sentences": [{"text": t, "facts": c} for t, c in items]}, ensure_ascii=False
    )


def test_collect_facts_rare_first():
    assert [f.id for f in facts()] == [
        "F1-evening-sgg", "F3-1", "F1-peer", "F1-quiet", "F1-evening", "F4-1",
        "F5-1", "F2-2", "F2-1", "F4-2", "F4-3", "F6-1", "F6-2", "F6-3",
    ]  # fmt: skip


def test_fact_texts_and_values():
    f = by_id()
    assert f["F1-evening"].text == "저녁 활기 A등급 62점, 전국 203동 중 78위"
    assert f["F1-evening"].values == {"A", "62", "203", "78"}
    assert f["F1-evening-sgg"].text == "강릉시 21동 중 저녁 활기 1위"
    assert f["F1-evening-sgg"].common == pytest.approx(12 / 203)
    assert f["F2-1"].text == (
        "평일 차량 이동 인구: 12시 2064명으로 가장 많고, "
        "업무시간(9~17시) 중 9시 1573명으로 가장 적음"
    )
    assert f["F2-2"].values == {"14", "3", "2833", "569"}
    assert f["F3-1"].text == "강릉시 월별 방문: 가장 한산한 달 2·4·9월, 가장 붐비는 달 8월"
    assert f["F3-1"].values == {"2", "4", "9", "8"}
    assert f["F4-1"].text == "로컬살이형 60점 (유형 1위)"
    assert f["F5-1"].text == "바닷가 (해안선까지 0.0km)"
    assert f["F6-1"].text == "비슷한 동 1: 속초시 노학동" and f["F6-1"].values == frozenset()
    # 시군구 1위가 아닌 지표는 -sgg 사실이 없다
    assert "F1-quiet-sgg" not in f and "F1-peer-sgg" not in f


def test_validate_passes_good_sentences():
    for text, cited in GOOD:
        assert brief.validate_sentence(text, cited, by_id()) == []


def test_validate_rejects_months_not_in_facts():
    # 점수 기준 한산한 달(4·10월)은 사실 목록에 없다
    problems = brief.validate_sentence("강릉시는 4·10월이 가장 한산해요.", ["F3-1"], by_id())
    assert problems == ["사실에 없는 값: ['10']"]


def test_validate_rejects_grade_and_number_not_cited():
    f = by_id()
    assert brief.validate_sentence("저녁 활기 S등급이에요.", ["F1-evening"], f) == [
        "사실에 없는 값: ['S']"
    ]
    # 62점은 F1-evening 값이라 F1-evening-sgg 만 인용하면 실패
    assert brief.validate_sentence("저녁 활기 62점, 강릉 1위예요.", ["F1-evening-sgg"], f) == [
        "사실에 없는 값: ['62']"
    ]


def test_validate_thousands_separator():
    text = "평일 12시엔 차량이 2,064명으로 가장 많아요."
    assert brief.validate_sentence(text, ["F2-1"], by_id()) == []


def test_validate_length_banned_and_ids():
    f = by_id()
    long = "가" * 61
    assert brief.validate_sentence(long, ["F3-1"], f) == ["60자 초과"]
    assert brief.validate_sentence("8월 여행지로 추천해요.", ["F3-1"], f) == ["금지어: ['추천']"]
    assert brief.validate_sentence("8월이 붐벼요.", ["F9-1"], f) == [
        "인용 id 없음: ['F9-1']",
        "사실에 없는 값: ['8']",
    ]
    assert brief.validate_sentence("한산해요.", [], f) == ["인용 id 없음: 빈 목록"]


def test_rule_sentences_jumunjin():
    assert brief.rule_sentences(jumunjin_raw(), facts()) == RULES


def test_rule_sentences_pass_validation():
    cited = [["F2-1"], ["F3-1"], ["F4-1", "F1-evening-sgg"]]
    for text, ids in zip(RULES, cited, strict=True):
        assert brief.validate_sentence(text, ids, by_id()) == []


def test_rule_third_without_sigungu_first_uses_best_grade():
    raw = jumunjin_raw()
    raw["rec"]["grades"][2][5] = 2  # 저녁 활기 강릉 2위로
    third = brief.rule_sentences(raw, brief.collect_facts(raw))[2]
    assert third == "로컬살이형 60점으로, 저녁 활기 A(62점)이에요."


def test_josa():
    assert brief.josa("강릉시") == "강릉시는"
    assert brief.josa("고성군") == "고성군은"
    assert brief.josa("서귀포시") == "서귀포시는"


def test_facts_hash_stable_and_sensitive():
    assert brief.facts_hash(facts()) == brief.facts_hash(facts())
    raw = jumunjin_raw()
    raw["rec"]["grades"][2][2] = 63
    assert brief.facts_hash(brief.collect_facts(raw)) != brief.facts_hash(facts())


def test_parse_reply_with_code_fence():
    text = "```json\n" + reply(GOOD) + "\n```"
    assert brief.parse_reply(text) == GOOD


def test_parse_reply_missing_last_brace():
    # HCX-005 가 마지막 } 를 자주 빠뜨린다 (2026-11-13 실측)
    text = reply(GOOD)
    assert brief.parse_reply(text[:-1]) == GOOD
    assert brief.parse_reply(text[:-2]) == GOOD  # ]} 둘 다 없음


def test_prompt_repeats_format_at_end():
    _, user = brief.build_prompt(jumunjin_raw(), facts())
    assert user.endswith(brief.FORMAT_TAIL)


@pytest.mark.parametrize("text", ["문장만 있어요", '{"sentences": []}', '{"x": 1}', "{bad"])
def test_parse_reply_rejects(text):
    with pytest.raises(ValueError):
        brief.parse_reply(text)


class FakeChat:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, system, user):
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_generate_llm():
    chat = FakeChat(reply(GOOD))
    result = brief.generate(jumunjin_raw(), facts(), chat)
    assert result == brief.Brief([t for t, _ in GOOD], "llm", "HCX-005")
    assert chat.calls == 1


def test_generate_mixed_replaces_only_bad_sentence():
    items = [GOOD[0], ("강릉시는 4·10월이 가장 한산해요.", ["F3-1"]), GOOD[2]]
    result = brief.generate(jumunjin_raw(), facts(), FakeChat(reply(items)))
    assert result.sentences == [GOOD[0][0], RULES[1], GOOD[2][0]]
    assert (result.source, result.model) == ("mixed", "HCX-005")


def test_generate_bad_json_is_all_rule():
    result = brief.generate(jumunjin_raw(), facts(), FakeChat("죄송해요"))
    assert result == brief.Brief(RULES, "rule", None)


def test_generate_retries_three_times_then_rule():
    chat = FakeChat(ClovaError("timeout"))
    result = brief.generate(jumunjin_raw(), facts(), chat)
    assert chat.calls == 4 and result.source == "rule"


def test_generate_recovers_after_failures():
    chat = FakeChat(ClovaError("429"), ClovaError("timeout"), reply(GOOD))
    assert brief.generate(jumunjin_raw(), facts(), chat).source == "llm"
    assert chat.calls == 3


def test_generate_without_chat_is_rule():
    assert brief.generate(jumunjin_raw(), facts(), None) == brief.Brief(RULES, "rule", None)


def test_prompt_lists_facts_rare_first():
    system, user = brief.build_prompt(jumunjin_raw(), facts())
    assert "60자" in system and '"sentences"' in system
    assert user.startswith(
        "동네: 강원특별자치도 강릉시 주문진읍\n사실 (드문 것부터):\nF1-evening-sgg: "
    )
