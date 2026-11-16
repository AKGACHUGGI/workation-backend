"""비교 답 (스펙 §6). 구조화 생성은 가짜 함수로 바꿔 끼운다."""

import pydantic
import pytest

from explore.chat import arrange as a
from explore.chat import compare as c
from explore.chat import llm
from tests.explore_chat_samples import GYO, HYOJA, RAWS, YEOUI

H, Y, G = (a.dong_of(RAWS[x]) for x in (HYOJA, YEOUI, GYO))
NAMES = ["효자2동", "여의동"]
ORDERS = a.orders([H, Y], ["quiet", "coast"])
BY_AXIS = {o.axis: o for o in ORDERS}
GOOD = {"text": "조용한 환경이 우선이면 효자2동이 맞아요.", "axis": "quiet", "pick": "효자2동"}
COAST = {"text": "바다 가까이 머물고 싶다면 여의동이 맞아요.", "axis": "coast", "pick": "여의동"}


def fake(*sentences, calls=None):
    def generate(system, user, schema):
        if calls is not None:
            calls.append(user)
        return schema.model_validate({"sentences": list(sentences)})

    return generate


def test_answer_model_limits_axis_and_pick():
    model = c.answer_model(["quiet", "coast"], NAMES)
    model.model_validate({"sentences": [GOOD]})
    with pytest.raises(pydantic.ValidationError):
        model.model_validate({"sentences": [{**GOOD, "axis": "peer"}]})
    with pytest.raises(pydantic.ValidationError):
        model.model_validate({"sentences": [{**GOOD, "pick": "교1동"}]})
    with pytest.raises(pydantic.ValidationError):
        model.model_validate({"sentences": [GOOD, COAST, GOOD]})  # 축 수보다 많음


def test_build_prompt_has_orders_not_user_words():
    user = c.build_prompt(
        "compare_summary", [H, Y], ORDERS, ["평일 9시 한산"], [a.Link("효자2동", "여의동", 2)]
    )
    assert "고른 동네: 효자2동, 여의동" in user
    assert "quiet: 조용함 높은 순: 효자2동(S) > 여의동(A)" in user
    assert "coast: 바다 가까운 순: 여의동(바닷가) > 효자2동(내륙)" in user
    assert "공통: 평일 9시 한산" in user
    assert "연결: 효자2동은 여의동과 비슷한 동 2위" in user
    assert "73" not in user  # 원래 점수는 넣지 않는다


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        (GOOD, []),
        ({**GOOD, "text": "조용한 곳을 원하면 여의동이 맞아요.", "pick": "여의동"}, ["방향"]),
        ({**GOOD, "text": "조용한 곳을 원하면 그쪽이 맞아요."}, ["이름"]),
        ({**GOOD, "text": "조용함 지표로 보면 효자2동이 맞아요."}, ["금지어"]),
        ({**GOOD, "text": "조용함 73점인 효자2동이 맞아요."}, ["숫자"]),
        ({**GOOD, "text": "S등급인 효자2동이 맞아요."}, ["숫자"]),
        ({**GOOD, "text": "조용한 곳은 효자2동"}, ["해요체"]),
        ({**GOOD, "text": "두 곳 모두 효자2동처럼 조용해요."}, ["금지어"]),
    ],
)
def test_problems(sentence, expected):
    got = c.problems(sentence, BY_AXIS, NAMES, set())
    assert len(got) == len(expected)
    assert all(p.startswith(e) for p, e in zip(got, expected, strict=True))


def test_problems_tie_and_duplicate():
    (evening,) = a.orders([Y, G], ["evening"])
    tie = {"text": "저녁 활기를 원하면 여의동이 맞아요.", "axis": "evening", "pick": "여의동"}
    assert c.problems(tie, {"evening": evening}, ["여의동", "교1동"], set())[0].startswith("방향")
    assert "같은 축 중복" in c.problems(GOOD, BY_AXIS, NAMES, {"quiet"})


def test_rule_sentences():
    quiet, coast = ORDERS
    assert c.rule_sentence(quiet) == "조용함을 원한다면 효자2동이 맞아요."
    assert c.rule_sentence(coast) == "바다 가까움을 원한다면 여의동이 맞아요."
    (evening,) = a.orders([Y, G], ["evening"])
    assert c.rule_sentence(evening) == "저녁 활기는 여의동·교1동이 같은 등급이에요."
    (inland,) = a.orders([H, G], ["coast"])
    assert c.rule_sentence(inland) == "바다 가까움은 효자2동·교1동이 비슷해요."


def test_compose_all_pass():
    sentences, source = c.compose(fake(GOOD, COAST), "u", [H, Y], ORDERS)
    assert source == "llm"
    assert sentences == [
        {
            "text": GOOD["text"],
            "evidence": "조용함: 효자2동 S(73점) · 여의동 A(57점)",
            "source": "llm",
        },
        {
            "text": COAST["text"],
            "evidence": "바다 가까움: 여의동 해안선 0.4km · 효자2동 내륙",
            "source": "llm",
        },
    ]


def test_compose_replaces_wrong_and_fills_missing():
    wrong = {**GOOD, "text": "조용한 곳을 원하면 여의동이 맞아요.", "pick": "여의동"}
    sentences, source = c.compose(fake(wrong), "u", [H, Y], ORDERS)
    assert source == "rule"
    assert [s["text"] for s in sentences] == [
        "조용함을 원한다면 효자2동이 맞아요.",
        "바다 가까움을 원한다면 여의동이 맞아요.",
    ]
    sentences, source = c.compose(fake(COAST), "u", [H, Y], ORDERS)
    assert source == "mixed"
    assert [s["source"] for s in sentences] == ["rule", "llm"]  # 축 순서대로


def test_compose_without_llm_or_on_error():
    assert c.compose(None, "u", [H, Y], ORDERS)[1] == "rule"

    def boom(system, user, schema):
        raise TimeoutError("10초")

    sentences, source = c.compose(boom, "u", [H, Y], ORDERS)
    assert source == "rule" and len(sentences) == 2


def test_hours_months_similarity_link():
    raws = [RAWS[HYOJA], RAWS[YEOUI]]
    assert c.hours_sentences(raws, "weekday")[0] == {
        "text": "효자2동은 평일 12시에 가장 붐비고, 업무시간엔 9시가 한산해요.",
        "evidence": "평일 차량 이동 인구 가장 많은 시각: 12시 100명",
        "source": "rule",
    }
    assert c.hours_sentences(raws, "weekend")[1]["text"] == "여의동은 주말 14시에 차가 가장 많아요."
    assert c.months_sentences(raws)[0]["text"] == (
        "효자2동·여의동은 같은 전주시라 1·2·8월이 가장 한산해요."
    )
    vecs = {HYOJA: ((0.1, 0.1, 0.1, 0.5), False), YEOUI: ((0.2, 0.1, 0.1, 0.5), False),
            GYO: ((0.9, 0.9, 0.9, 0.1), False)}  # fmt: skip
    got = c.similarity_sentences(
        raws, ["평일 9시 한산", "저녁 활기 S"], vecs, a.pair_distances(vecs)
    )
    assert got[0]["text"] == "효자2동과 여의동은 분위기가 보통 정도로 닮은 편이에요."
    assert got[0]["evidence"] == "두 동네 닮음: 전국 동네 쌍 중 가까운 쪽 34%"
    assert got[1]["text"] == "공통점은 평일 9시 한산 · 저녁 활기 S예요."
    assert c.link_sentence(a.Link("효자2동", "여의동", 2)) == {
        "text": "최근 보신 여의동과 비슷한 쪽은 효자2동이에요.",
        "evidence": "효자2동의 비슷한 동 2위: 여의동",
        "source": "rule",
    }


def test_cache_key_changes_with_prompt_version(monkeypatch):
    key = c.cache_key([YEOUI, HYOJA], "compare_summary", ["quiet", "coast"], "u")
    assert key.startswith(f"explore:chat:a:{YEOUI}+{HYOJA}:compare_summary:quiet+coast:")
    assert key.endswith(f":{llm.MODEL}:{c.PROMPT_VERSION}")
    monkeypatch.setattr(c, "PROMPT_VERSION", "test")
    assert c.cache_key([YEOUI, HYOJA], "compare_summary", ["quiet", "coast"], "u") != key


def test_llm_client_settings(settings):
    settings.CLOVA_API_KEY = ""
    assert not llm.enabled()
    settings.CLOVA_API_KEY = "test-key"
    assert llm.enabled()
    assert llm.client().model_name == "HCX-007"
