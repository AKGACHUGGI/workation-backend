"""시군구 채팅 답 (시군구 채팅 스펙 §6). 순수 함수 + 답 캐시(메모리)."""

import pytest
from django.core.cache import cache

from explore.chat import region as r
from tests.explore_chat_samples import GYO, HYOJA, REGION_RAW, SONG, YEOUI


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def names(spots):
    return [s.dong.name for s in spots]


def test_spots_of():
    spots = {s.dong.code: s for s in r.spots_of(REGION_RAW)}
    hyoja = spots[HYOJA]
    assert hyoja.dong.values["quiet"] == 90 and hyoja.dong.grades["quiet"] == "S"
    assert hyoja.ranks["quiet"] == (1, 4)
    assert hyoja.dong.values["type:immersion"] == 70
    assert hyoja.dong.values["life"] == 0.9 and hyoja.dong.values["tourist"] == -0.9
    assert hyoja.life == "생활권"
    assert hyoja.dong.values["coast"] == r.arrange.INLAND
    assert spots[GYO].dong.values["coast"] == -3.2


@pytest.mark.parametrize(
    ("question", "intent", "axes"),
    [
        ("조용한 동네 어디야?", "find_quiet", ["quiet"]),
        ("바다 가까우면서 조용한 데", "find_coast", ["coast", "quiet"]),
        ("관광지 말고 생활권 동네는?", "find_life", ["life"]),
        ("관광지 동네는 어디야?", "find_life", ["tourist"]),
        ("집중해서 일하기 좋은 동네는?", "find_type", ["type:immersion"]),
        ("혼자 일하기 좋은 동네는?", "find_type", ["type:immersion"]),
        (
            "어떤 유형 동네가 있어?",
            "find_type",
            ["type:immersion", "type:networking", "type:local"],
        ),
        ("2030 많이 오는 데는?", "find_peer", ["peer"]),
        ("조용하고 저녁 활기도 있고 바다도 가까운 데", "find_quiet", ["quiet", "evening"]),
    ],
)
def test_condition_axes(question, intent, axes):
    assert r.condition_axes(question, intent) == axes


def test_plan_single_axis_adds_strengths():
    p = r.plan(r.spots_of(REGION_RAW), ["quiet"])
    assert names(p.spots) == ["효자2동", "여의동", "교1동"]
    # 저녁 활기는 조용함 질문과 반대 방향이라 부가 문장으로 쓰지 않는다
    assert p.axes == ["quiet", "peer"]


def test_plan_skips_opposite_strength():
    p = r.plan(r.spots_of(REGION_RAW), ["evening"])
    assert names(p.spots) == ["여의동", "교1동", "송천1동"]
    assert p.axes == ["evening", "peer"]  # 조용함 1위 교1동이어도 반대 방향이라 뺀다


def with_rank(code, metric, rank):
    """그 동의 지표 시군구 순위만 바꾼다 (점수는 그대로)."""
    dongs = [
        {
            **d,
            "grades": [
                [m, g, s, rank if (d["code"], m) == (code, metric) else k, of]
                for m, g, s, k, of in d["grades"]
            ],
        }
        for d in REGION_RAW["dongs"]
    ]
    return {**REGION_RAW, "dongs": dongs}


def test_plan_strength_needs_upper_half_of_sigungu():
    # 생활권 후보(효자2동 · 교1동 · 송천1동) 중 저녁 활기 1위 교1동이 시군구 3위/4면 쓰지 않고
    # 교1동은 시군구 1위인 또래 밀도로 쓴다
    p = r.plan(r.spots_of(with_rank(GYO, "evening", 3)), ["life"])
    assert p.axes == ["life", "peer"]
    p = r.plan(r.spots_of(with_rank(GYO, "evening", 2)), ["life"])
    assert p.axes == ["life", "evening"]


def test_plan_filters():
    spots = r.spots_of(REGION_RAW)
    assert names(r.ranked(spots, "coast")) == ["교1동"]
    assert names(r.ranked(spots, "life")) == ["효자2동", "교1동", "송천1동"]
    assert names(r.ranked(spots, "tourist")) == ["여의동"]
    assert names(r.ranked(spots, "type:immersion")) == ["효자2동", "교1동"]  # 40점 이상만


def test_plan_life_and_types():
    spots = r.spots_of(REGION_RAW)
    p = r.plan(spots, ["life"])
    assert names(p.spots) == ["효자2동", "교1동", "송천1동"]
    assert p.axes == ["life", "evening"]
    types = ["type:immersion", "type:networking", "type:local"]
    p = r.plan(spots, types)
    assert names(p.spots) == ["효자2동", "여의동", "교1동"]
    assert p.axes == types


def test_plan_two_conditions():
    spots = r.spots_of(REGION_RAW)
    # 조용함 상위 절반(효자2동·여의동) ∩ 바닷가(교1동) = 없음
    assert r.plan(spots, ["quiet", "coast"]).spots == []
    # 조용함 상위 절반 ∩ 생활권 = 효자2동
    p = r.plan(spots, ["quiet", "life"])
    assert names(p.spots) == ["효자2동"]
    assert p.axes == ["quiet", "life"]


def test_plan_no_candidate():
    inland = {**REGION_RAW, "dongs": [{**d, "near": []} for d in REGION_RAW["dongs"]]}
    assert r.plan(r.spots_of(inland), ["coast"]).spots == []
    assert SONG  # 송천1동은 조용함 꼴찌라 후보 3곳에 들지 않는다
    assert "송천1동" not in names(r.plan(r.spots_of(REGION_RAW), ["quiet"]).spots)
    assert YEOUI


def texts(sentences):
    return [s["text"] for s in sentences]


def test_answer_rule_when_no_llm():
    out, source, codes = r.answer(None, REGION_RAW, "find_quiet", "조용한 동네 어디야?")
    assert source == "rule"
    assert codes == [HYOJA, YEOUI, GYO]
    assert texts(out) == [
        "조용함을 원한다면 효자2동이 맞아요.",
        "또래 밀도를 원한다면 교1동이 맞아요.",
    ]
    assert out[0]["evidence"] == (
        "조용함: 효자2동 S(전주시 1위/4) · 여의동 A(전주시 2위/4) · 교1동 A(전주시 3위/4)"
    )
    assert out[1]["evidence"] == (
        "또래 밀도: 교1동 S(전주시 1위/4) · 여의동 A(전주시 2위/4) · 효자2동 B(전주시 3위/4)"
    )


def test_answer_uses_llm_and_checks_direction():
    calls = []

    def generate(system, user, schema):
        calls.append((system, user))
        return schema(
            sentences=[
                {"text": "조용히 일하고 싶다면 효자2동이 맞아요.", "axis": "quiet"},
                {"text": "또래와 어울리기엔 효자2동이 좋아요.", "axis": "peer"},  # 방향 틀림
            ]
        )

    out, source, _ = r.answer(generate, REGION_RAW, "find_quiet", "조용한 동네 어디야?")
    assert source == "mixed"
    assert texts(out) == [
        "조용히 일하고 싶다면 효자2동이 맞아요.",
        "또래 밀도를 원한다면 교1동이 맞아요.",
    ]
    system, user = calls[0]
    assert system == r.SYSTEM
    assert "전주시 안에서 동네 찾기" in user
    assert "90" not in user  # 숫자는 프롬프트에 없다
    r.answer(generate, REGION_RAW, "find_quiet", "조용한 동네 어디야?")
    assert len(calls) == 1


def test_answer_single_candidate_and_none():
    out, source, codes = r.answer(None, REGION_RAW, "find_coast", "바다 가까운 동네 있어?")
    assert (texts(out), source, codes) == (
        ["바다 가까움을 원한다면 교1동이 맞아요."],
        "rule",
        [GYO],
    )
    assert out[0]["evidence"] == "바다 가까움: 교1동 해안선 3.2km"
    out, _, codes = r.answer(None, REGION_RAW, "find_quiet", "조용하면서 바다 가까운 데")
    assert codes == [HYOJA, GYO]  # 문장에 쓴 조건별 1위 동 (자세히 → · 예시 칩)
    assert texts(out) == [
        "조용함과 바다 가까움을 다 갖춘 곳은 없어요.",
        "조용함을 원한다면 효자2동이 맞아요.",
        "바다 가까움을 원한다면 교1동이 맞아요.",
    ]
    out, _, codes = r.answer(None, REGION_RAW, "find_quiet", "조용한 생활권 동네")
    assert texts(out) == ["조용함과 생활권 분위기를 다 원한다면 효자2동이 맞아요."]
    assert codes == [HYOJA]
    inland = {**REGION_RAW, "dongs": [{**d, "near": []} for d in REGION_RAW["dongs"]]}
    out, _, codes = r.answer(None, inland, "find_coast", "바다 가까운 동네 있어?")
    assert (texts(out), codes) == (["전주시에는 바다 가까운 동네가 없어요."], [])


def test_answer_profile():
    out, source, codes = r.answer(None, REGION_RAW, "region_profile", "여긴 어떤 곳이야?")
    assert (source, codes) == ("rule", [])
    assert texts(out) == [
        "전주시는 현지 주민처럼 지내기 좋은 동네가 가장 많아요.",
        "생활권 성격의 동네가 더 많고, 바다 가까운 동네는 1곳이에요.",
    ]
    assert out[0]["evidence"] == (
        "로컬살이형 2곳 · 몰입형 1곳 · 네트워킹형 1곳 · 생활권 3곳 · 관광지 1곳"
    )


def with_types(keep=()):
    """유형 점수를 모두 30(기준 아래)으로, keep = [(동 코드, 유형 이름)] 만 원래 점수."""
    dongs = [
        {**d, "types": [[k, n, s if (d["code"], n) in keep else 30] for k, n, s in d["types"]]}
        for d in REGION_RAW["dongs"]
    ]
    return {**REGION_RAW, "dongs": dongs}


def test_type_question_uses_only_types_with_a_top():
    raw = with_types([(YEOUI, "네트워킹형")])
    p = r.plan(r.spots_of(raw), r.TYPE_AXES)
    assert (names(p.spots), p.axes) == (["여의동"], ["type:networking"])
    out, _, codes = r.answer(None, raw, "find_type", "어떤 워케이션 유형이 많아?")
    assert texts(out) == ["사람들과 어울리며 일하기엔 여의동이 맞아요."]
    assert out[0]["evidence"] == "네트워킹형: 여의동 65점"
    assert codes == [YEOUI]


def test_type_question_without_any_type():
    out, _, codes = r.answer(None, with_types(), "find_type", "어떤 워케이션 유형이 많아?")
    assert (texts(out), codes) == (["전주시에는 유형이 뚜렷한 동네가 없어요."], [])


def test_grade_ties_follow_sigungu_rank():
    # 교1동(코드가 앞)과 송천1동이 조용함 60점 동점, 시군구 순위는 송천1동 3위 · 교1동 4위
    quiet = {GYO: ["quiet", "A", 60, 4, 4], SONG: ["quiet", "A", 60, 3, 4]}
    dongs = [
        {**d, "grades": [quiet.get(d["code"], g) if g[0] == "quiet" else g for g in d["grades"]]}
        for d in REGION_RAW["dongs"]
    ]
    spots = r.spots_of({**REGION_RAW, "dongs": dongs})
    assert names(r.ranked(spots, "quiet")) == ["효자2동", "여의동", "송천1동", "교1동"]


EVERY = ["효자2동", "여의동", "교1동", "송천1동"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("조용히 일하려면 효자2동이 나아요.", []),
        ("또래와 어울리기엔 교1동이 제격이에요.", []),
        ("효자2동은 송천1동보다 조용해서 좋아요.", ["이름"]),
        ("조용히 일하려면 효자2동이 가장 나아요.", ["최상급"]),
        ("전주에서 제일 조용한 곳은 효자2동이 좋아요.", ["시군구 이름", "최상급"]),
        ("저녁 활기는 전주시의 여의동이 나아요.", ["시군구 이름"]),
        ("몰입형으로 일하려면 효자2동이 맞아요.", ["유형 이름"]),
        ("관광지가 많은 곳을 원한다면 효자2동이 맞아요.", ["넓힘"]),
        ("관광 명소를 즐기고 싶다면 효자2동이 맞아요.", ["넓힘"]),
        ("생활권이 잘 갖춰져 있다면 효자2동이 맞아요.", ["넓힘"]),
        ("편의시설이 필요하다면 효자2동이 맞아요.", ["넓힘"]),
        ("생활권 분위기의 중심지를 찾는다면 효자2동이 맞아요.", ["넓힘"]),
        ("여행하듯 지내고 싶다면 효자2동이 맞아요.", []),
        ("현지 생활처럼 지내고 싶다면 효자2동이 맞아요.", []),
    ],
)
def test_region_problems(text, expected):
    got = r.problems(text, "전주시", EVERY[:3], EVERY)
    assert [p.split(":")[0] for p in got] == expected


@pytest.mark.parametrize(
    ("text", "axis", "expected"),
    [
        ("조용히 일하고 싶다면 효자2동이 맞아요.", "quiet", []),
        ("조용하면서 바다가 가까운 곳을 찾는다면 효자2동이 나아요.", "coast", ["조용"]),
        ("조용하게 혼자 있고 싶으면 효자2동이 맞아요.", "quiet", ["혼자"]),
        ("저녁에 활기를 찾는다면 여의동 쪽이 나아요.", "evening", []),
        ("또래가 많고 밤에도 활기찬 곳은 교1동이 좋아요.", "peer", ["밤", "활기"]),
        ("혼자 집중해서 일하고 싶다면 효자2동이 맞아요.", "type:immersion", []),
        ("현지 주민처럼 지내고 싶다면 효자2동이 맞아요.", "type:local", []),
        ("현지 주민처럼 지내고 싶다면 효자2동이 맞아요.", "life", []),
        ("여행하듯 지내고 싶다면 효자2동이 맞아요.", "tourist", []),
        ("바다 가까이 머물고 싶다면 여의동이 좋아요.", "coast", []),
    ],
)
def test_mixed_axes(text, axis, expected):
    got = r.mixed(text, axis)
    assert got == ([f"섞임: {expected}"] if expected else [])


def test_system_has_axis_phrases_and_tone_rules():
    for phrase in ("현지 생활처럼 지내고 싶다면", "여행하듯 지내고 싶다면", "비슷한 나이대"):
        assert phrase in r.SYSTEM
    assert all(rule in r.SYSTEM for rule in r.compare.TONE_RULES)


def test_rule_sentences_have_no_meta_or_hedge():
    """규칙 대체 문장도 같은 검사를 통과한다."""
    spots = r.spots_of(REGION_RAW)
    dongs = [s.dong for s in spots]
    axes = ["quiet", "peer", "evening", "coast", "life", "tourist", *r.TYPE_AXES]
    for o in r.arrange.orders(dongs, axes):
        text = r.rule_sentence(o)
        found = r.problems(text, "전주시", EVERY, EVERY) + r.compare.tone(text, EVERY)
        assert [p for p in found if not p.startswith("이름")] == [], text
        assert r.mixed(text, o.axis) == [], text


def test_answer_replaces_mixed_axis_sentence():
    def generate(system, user, schema):
        return schema(
            sentences=[
                {"text": "조용하면서 또래가 많은 곳을 찾는다면 효자2동이 맞아요.", "axis": "quiet"},
                {"text": "또래가 많은 곳을 찾는다면 교1동이 좋아요.", "axis": "peer"},
            ]
        )

    out, _, _ = r.answer(generate, REGION_RAW, "find_quiet", "조용한 동네 어디야?")
    assert [s["source"] for s in out] == ["rule", "llm"]


def coastal(km):
    """동 네 곳 해안선 거리를 바꾼다 (None 은 내륙)."""
    dongs = [
        {**d, "near": [] if k is None else [k]}
        for d, k in zip(REGION_RAW["dongs"], km, strict=True)
    ]
    return {**REGION_RAW, "dongs": dongs}


def test_all_tied_condition_is_rule_and_not_asked():
    raw = coastal([0.0, 0.0, 0.0, None])  # 효자2동 · 여의동 · 교1동 모두 해안선 0.0km
    calls = []

    def generate(system, user, schema):
        calls.append(user)
        return schema(
            sentences=[
                {"text": "조용히 일하고 싶다면 효자2동이 맞아요.", "axis": "quiet"},
                {"text": "저녁에 활기를 찾는다면 여의동이 나아요.", "axis": "evening"},
            ]
        )

    out, source, _ = r.answer(generate, raw, "find_coast", "바다 가까운 동네 있어?")
    assert texts(out) == [
        "바다 가까움을 원한다면 교1동·여의동·효자2동이 맞아요.",
        "조용히 일하고 싶다면 효자2동이 맞아요.",
        "저녁에 활기를 찾는다면 여의동이 나아요.",
    ]
    assert [s["source"] for s in out] == ["rule", "llm", "llm"]
    assert source == "mixed"
    assert "coast:" not in calls[0]


def test_partial_tie_lets_llm_name_any_tied_top():
    raw = coastal([5.0, 0.0, 0.0, None])  # 여의동 = 교1동 > 효자2동

    def generate(system, user, schema):
        text = "바다 곁에 머물고 싶다면 교1동이 좋아요."
        return schema(sentences=[{"text": text, "axis": "coast"}])

    out, _, _ = r.answer(generate, raw, "find_coast", "바다 가까운 동네 있어?")
    assert out[0] == {
        "text": "바다 곁에 머물고 싶다면 교1동이 좋아요.",
        "evidence": "바다 가까움: 교1동 해안선 0.0km · 여의동 해안선 0.0km · 효자2동 해안선 5.0km",
        "source": "llm",
    }


def test_llm_contrast_word_is_dropped():
    def generate(system, user, schema):
        return schema(
            sentences=[
                {"text": "조용히 일하고 싶다면 효자2동이 맞아요.", "axis": "quiet"},
                {"text": "반면 또래와 어울리기엔 교1동이 좋아요.", "axis": "peer"},
            ]
        )

    out, _, _ = r.answer(generate, REGION_RAW, "find_quiet", "조용한 동네 어디야?")
    assert texts(out)[1] == "또래와 어울리기엔 교1동이 좋아요."


def test_prompt_uses_plain_type_words():
    p = r.plan(r.spots_of(REGION_RAW), ["type:immersion"])
    orders = r.arrange.orders([s.dong for s in p.spots], p.axes)
    user = r.build_prompt("전주시", p, orders)
    assert "몰입형" not in user
    assert "혼자 집중해서 일하기" in user


def test_answer_rejects_sigungu_superlative():
    def generate(system, user, schema):
        return schema(
            sentences=[
                {"text": "전주시에서 가장 조용한 곳은 효자2동이에요.", "axis": "quiet"},
                {"text": "또래와 어울리기엔 교1동이 좋아요.", "axis": "peer"},
            ]
        )

    out, source, _ = r.answer(generate, REGION_RAW, "find_quiet", "조용한 동네 어디야?")
    assert [s["source"] for s in out] == ["rule", "llm"]
    assert out[0]["text"] == "조용함을 원한다면 효자2동이 맞아요."
    assert source == "mixed"
