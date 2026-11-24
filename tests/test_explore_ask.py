"""동네에 물어보기 순수 함수 (v6.1 스펙 §3·§4·§6). 주문진읍 실측 jumunjin_raw 로 본다."""

import pytest
from pydantic import ValidationError

from explore import ask, brief
from explore.chat import llm
from tests.explore_samples import jumunjin_no_type_raw, jumunjin_raw

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
    "weekday_hours": ["평일 차량은 낮 12시에 가장 많고, 업무시간엔 오전 9시가 가장 한산해요."],
    "weekend_hours": ["주말 차량은 오후 2시에 가장 많고, 새벽 3시가 가장 한산해요."],
    "months": ["강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요."],
    "quiet": ["조용함은 B등급(31점)으로 전국 203동 중 140위, 보통 수준이에요."],
    "peer": ["또래 밀도는 B등급(39점)으로 전국 203동 중 124위, 보통 수준이에요."],
    "evening": [
        "저녁 활기는 A등급(62점)으로 전국 203동 중 78위, 저녁에 활기 있는 편이에요.",
        "강릉시 안에서는 저녁 활기 1위예요.",
    ],
    "type": ["로컬살이형 60점으로 가장 잘 맞고, 다음은 네트워킹형 50점이에요."],
    "coast": ["해안선까지 0.0km인 바닷가 동네예요."],
    "life": ["생활권 성격이 강한 동네예요."],
    "similar": ["지표가 비슷한 동네는 속초시 노학동, 서귀포시 대정읍, 서귀포시 영천동이에요."],
}


def fake(*items, calls=None):
    """llm.generate 대역: 받은 스키마로 검증한 객체를 돌려준다 (스키마 밖이면 ValidationError)."""

    def generate(system, user, schema):
        if calls is not None:
            calls.append((system, user, schema))
        return schema.model_validate({"sentences": [{"text": t, "facts": f} for t, f in items]})

    return generate


def facts_of(raw, intents):
    return {f.id: f for f in ask.facts_for(raw, intents)}


def test_intent_tables_match_spec():
    assert len(ask.INTENTS) == 10 and len(ask.UNKNOWN) == 4
    assert set(ask.CANONICAL) == set(ask.INTENTS) == set(ask.INTENT_FACTS)
    assert set(ask.GUIDE) == set(ask.UNKNOWN) | {"compare_summary"}
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
    assert ask.anchors(facts["F2-1"]) == {"12", "9", "2064", "1573", "17", "5", "평일"}
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


def test_prompt_has_no_json_instruction():
    raw = jumunjin_raw()
    system, user = ask.build_prompt(raw, ask.facts_for(raw, ["quiet"]), "조용해?")
    assert "JSON" not in system
    assert not user.endswith(brief.FORMAT_TAIL)


def test_hour_facts_use_clock_labels():
    raw = jumunjin_raw()
    facts = facts_of(raw, ["weekday_hours", "weekend_hours"])
    assert facts["F2-1"].text == (
        "평일 차량 이동 인구: 낮 12시(12시) 2064명으로 가장 많고, "
        "업무시간(오전 9시~오후 5시) 중 오전 9시(9시) 1573명으로 가장 적음"
    )
    assert facts["F2-2"].text == (
        "주말 차량 이동 인구: 오후 2시(14시) 2833명으로 가장 많고, "
        "새벽 3시(3시) 569명으로 가장 적음"
    )
    assert facts["F2-2"].values == frozenset({"14", "2", "3", "2833", "569"})
    assert facts["F2-1h"].text.startswith(
        "평일 시간대별 차량 이동 인구: 밤 12시(0시) 637명, 새벽 1시(1시) 511명,"
    )
    assert {"15", "3", "0", "12"} <= facts["F2-1h"].values


def test_hour_facts_keep_brief_id_and_common():
    raw = jumunjin_raw()
    before = {f.id: f for f in brief.collect_facts(raw)}
    after = {f.id: f for f in ask.hour_facts(raw, list(before.values()))}
    assert list(after) == list(before)
    for fid in ("F2-1", "F2-2"):
        assert after[fid].common == before[fid].common
        assert after[fid].text != before[fid].text
    assert after["F1-quiet"] is before["F1-quiet"]


def test_brief_facts_stay_24_hour():
    texts = {f.id: f.text for f in brief.collect_facts(jumunjin_raw())}
    assert texts["F2-2"] == (
        "주말 차량 이동 인구: 14시 2833명으로 가장 많고, 3시 569명으로 가장 적음"
    )


def test_system_asks_for_clock_labels():
    assert "'오후 3시'처럼 쓴다" in ask.SYSTEM


def test_answer_model_limits_facts_and_count():
    model = ask.answer_model(["F2-1", "F2-1h"])
    one = {"text": "평일엔 9시가 한산해요.", "facts": ["F2-1"]}
    model.model_validate({"sentences": [one]})
    bad = [
        {"sentences": []},
        {"sentences": [one] * 4},
        {"sentences": [{"text": "평일엔 9시가 한산해요.", "facts": []}]},
        {"sentences": [{"text": "평일엔 9시가 한산해요.", "facts": ["F3-1"]}]},
    ]
    for body in bad:
        with pytest.raises(ValidationError):
            model.model_validate(body)


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
        ("140위로 비교적 낮은 소음도예요.", ["F1-quiet"], "데이터에 없는 말: ['소음']"),
        ("강릉시 안에서는 21동 중 저녁 1위예요.", ["F1-evening-sgg"], None),
        ("오전에 일하기 괜찮은 동네예요.", ["F2-1"], "근거어 없음"),
        ("평일 9시가 한산해요. www.example.com 참고", ["F2-1"], "URL·이메일·전화번호 모양"),
        ("평일 9시 문의는 010-1234-5678 이에요.", ["F2-1"], "URL·이메일·전화번호 모양"),
        ("평일 9시가 한산해서 good 이에요.", ["F2-1"], "영문 단어: ['good']"),
        ("평일엔 10시가 가장 한산해요.", ["F2-1"], "사실에 없는 값: ['10']"),
        ("생활권 성격이 강한 동네예요.", ["F7-1"], None),
        ("관광지 성격이 강해요.", ["F7-1"], "근거어 없음"),
        ("강릉시는 1년 중 2·4·9월이 가장 한산해요.", ["F3-1"], None),  # 관용 표현
        ("강릉시는 1월이 가장 한산해요.", ["F3-1"], "사실에 없는 값: ['1']"),
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


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("주말엔 오후 3시가 가장 한산해요.", "시각 틀림: 오후 3시"),  # 사실은 새벽 3시
        ("주말엔 오후 14시가 가장 붐벼요.", "시각 표기: 오후 14시"),
        ("주말엔 새벽 3시가 가장 한산해요.", None),
        ("주말엔 3시가 가장 한산해요.", None),  # 접두어 없으면 숫자 검사만
        ("주말엔 오후 2시가 가장 붐벼요.", None),
        ("주말엔 14시가 가장 붐벼요.", None),
    ],
)
def test_check_sentence_clock(text, problem):
    facts = facts_of(jumunjin_raw(), ["weekend_hours"])
    problems = ask.check_sentence(text, ["F2-2"], facts)
    if problem is None:
        assert problems == []
    else:
        assert problem in problems


def test_check_sentence_clock_skips_facts_without_hours():
    facts = facts_of(jumunjin_raw(), ["months"])
    # 달 사실만 인용하면 시각 검사를 하지 않는다 (숫자 검사가 따로 막는다)
    assert not any(
        p.startswith("시각 틀림")
        for p in ask.check_sentence("2월 오후 2시는 한산해요.", ["F3-1"], facts)
    )


def numbered_raw():
    """중화산2동처럼 이름에 숫자가 든 동. 비슷한 동 1위도 이도2동으로."""
    raw = jumunjin_raw()
    raw["rec"]["dong"]["name"] = "중화산2동"
    raw["rec"]["similar"][0][2] = "이도2동"
    return raw


def test_place_names():
    assert ask.place_names(numbered_raw()) == frozenset({"중화산2동", "이도2동"})
    assert ask.place_names(jumunjin_raw()) == frozenset()


@pytest.mark.parametrize(
    ("text", "cited", "problem"),
    [
        ("중화산2동은 생활권 성격이 강해요.", ["F7-1"], None),
        ("비슷한 동네는 속초시 이도2동이에요.", ["F6-1"], None),
        ("중화산2동은 생활권 지수 3이에요.", ["F7-1"], "사실에 없는 값: ['3']"),
    ],
)
def test_check_sentence_ignores_digits_in_place_names(text, cited, problem):
    raw = numbered_raw()
    facts = facts_of(raw, ["life", "similar"])
    problems = ask.check_sentence(text, cited, facts, ask.place_names(raw))
    if problem is None:
        assert problems == []
    else:
        assert problem in problems
    # 이름을 넘기지 않으면 지금처럼 이름 속 2 를 잡는다
    assert any("'2'" in p for p in ask.check_sentence(text, cited, facts))


def test_compose_passes_place_names():
    raw = numbered_raw()
    facts = ask.facts_for(raw, ["life"])
    out, source = ask.compose(
        raw, ["life"], [("중화산2동은 생활권 성격이 강해요.", ["F7-1"])], facts
    )
    assert (out, source) == (["중화산2동은 생활권 성격이 강해요."], "llm")


def test_answer_all_generated():
    calls = []
    out = ask.answer(
        jumunjin_raw(),
        ["weekday_hours"],
        fake(("평일엔 9시가 가장 한산해서 오전 업무에 맞아요.", ["F2-1"]), calls=calls),
        question="q",
    )
    assert out.sentences == ["평일엔 9시가 가장 한산해서 오전 업무에 맞아요."]
    assert (out.source, out.model, out.fallback) == ("llm", llm.MODEL, False)
    assert out.facts_hash == ask.prompt_hash(ask.facts_for(jumunjin_raw(), ["weekday_hours"]), "q")
    schema = calls[0][2]
    for fid in ("F2-1", "F2-1h"):  # 넘긴 사실 id 는 고를 수 있고
        schema.model_validate({"sentences": [{"text": "x", "facts": [fid]}]})
    with pytest.raises(ValidationError):  # 넘기지 않은 id 는 못 고른다
        schema.model_validate({"sentences": [{"text": "x", "facts": ["F3-1"]}]})


def test_answer_replaces_failed_sentence_with_its_intent_rule():
    out = ask.answer(
        jumunjin_raw(),
        ["months", "weekday_hours"],
        fake(
            ("평일엔 9시가 가장 한산해요.", ["F2-1"]),
            ("2월이 한산해요. http://x.kr", ["F3-1"]),
        ),
        question="q",
    )
    assert out.sentences == ["평일엔 9시가 가장 한산해요.", *RULES["months"]]
    assert (out.source, out.fallback) == ("mixed", False)


@pytest.mark.parametrize("intent", ["quiet", "peer", "evening"])
def test_graded_intent_is_template_without_chat(intent):
    calls = []
    out = ask.answer(jumunjin_raw(), [intent], fake(calls=calls), question="q")
    assert calls == []
    assert (out.sentences, out.source, out.model) == (RULES[intent], "rule", None)
    assert out.fallback is False


def test_graded_and_other_intent_template_first_then_llm():
    calls = []
    generate = fake(("평일엔 9시가 가장 한산해요.", ["F2-1"]), calls=calls)
    out = ask.answer(jumunjin_raw(), ["quiet", "weekday_hours"], generate, question="q")
    assert out.sentences == [*RULES["quiet"], "평일엔 9시가 가장 한산해요."]
    assert out.source == "mixed"
    assert "F1-quiet" not in calls[0][1]  # 등급 사실은 LLM 에 넘기지 않는다


@pytest.mark.parametrize(
    "items",
    [
        (),  # 0문장
        (("평일엔 9시가 한산해요.", ["F2-1"]),) * 4,  # 4문장
        (("평일엔 9시가 한산해요.", ["F9-9"]),),  # 넘기지 않은 사실 id
    ],
)
def test_answer_schema_mismatch_is_fallback_rule(items):
    out = ask.answer(jumunjin_raw(), ["weekday_hours"], fake(*items), question="q")
    assert (out.sentences, out.source, out.model) == (RULES["weekday_hours"], "rule", None)
    assert out.fallback is True


def test_answer_error_and_no_key_are_fallback_rule():
    def fail(system, user, schema):
        raise TimeoutError("CLOVA 시간 초과")

    raw = jumunjin_raw()
    out = ask.answer(raw, ["weekday_hours"], fail, question="q")
    assert (out.sentences, out.fallback) == (RULES["weekday_hours"], True)
    none = ask.answer(raw, ["weekday_hours"], None, question="q")
    assert (none.source, none.fallback) == ("rule", True)


def test_answer_without_facts_skips_generate():
    raw = jumunjin_raw()
    raw["facts"]["5115025000"]["life"] = []
    calls = []
    out = ask.answer(raw, ["life"], fake(calls=calls), question="q")
    assert calls == [] and out.sentences == ["이 동네는 그 내용을 계산할 데이터가 부족해요."]
    assert out.fallback is False


def test_type_rule_without_representative_type():
    raw = jumunjin_no_type_raw()
    assert ask.rule_answer(raw, ["type"], ask.facts_for(raw, ["type"])) == [
        "뚜렷하게 맞는 유형은 없어요.",
        "몰입형 20점 · 네트워킹형 35점 · 로컬살이형 39점이에요.",
    ]


def test_compare_summary_is_a_guide_for_one_dong():
    text, examples = ask.GUIDE["compare_summary"]
    assert "다른 동네 이름과 함께" in text
    assert ask.examples_for(["compare_summary"]) == examples


def test_prompt_hash_changes_with_answer_version(monkeypatch):
    facts = ask.facts_for(jumunjin_raw(), ["months"])
    before = ask.prompt_hash(facts, "q")
    monkeypatch.setattr(ask, "ANSWER_VERSION", ask.ANSWER_VERSION + 1)
    assert ask.prompt_hash(facts, "q") != before


def test_answer_version_is_4():
    # 시각 사실 문구와 검사 규칙(시각 · 동 이름 · '1년')을 바꿨다 (2026-11-17)
    assert ask.ANSWER_VERSION == 4


def test_region_vocab_scopes():
    assert ask.REGION_CANONICAL == {
        "find_quiet": "조용한 동네 어디야?",
        "find_peer": "또래가 많은 동네는?",
        "find_evening": "저녁에 활기 있는 데는?",
        "find_type": "집중해서 일하기 좋은 동네는?",
        "find_coast": "바다 가까운 동네 있어?",
        "find_life": "관광지 말고 생활권 동네는?",
        "region_months": "몇 월에 가면 한산해?",
        "region_profile": "여긴 어떤 곳이야?",
    }
    assert set(ask.REGION_VOCAB) == {*ask.REGION_INTENTS, *ask.HOURS, *ask.UNKNOWN}
    assert not set(ask.DONG_VOCAB) & set(ask.REGION_INTENTS)
    assert "compare_summary" in ask.DONG_VOCAB
    assert ask.REGION_FIRST == [
        "조용한 동네 어디야?",
        "바다 가까운 동네 있어?",
        "몇 월에 가면 한산해?",
    ]
