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
GOOD = {"text": "조용한 환경이 우선이면 효자2동이 맞아요.", "axis": "quiet"}
COAST = {"text": "바다 가까이 머물고 싶다면 여의동이 맞아요.", "axis": "coast"}


def fake(*sentences, calls=None):
    def generate(system, user, schema):
        if calls is not None:
            calls.append(user)
        return schema.model_validate({"sentences": list(sentences)})

    return generate


def test_answer_model_limits_axis_without_pick():
    model = c.answer_model(["quiet", "coast"])
    assert set(model.model_json_schema()["$defs"]["Sentence"]["properties"]) == {"text", "axis"}
    model.model_validate({"sentences": [GOOD]})
    with pytest.raises(pydantic.ValidationError):
        model.model_validate({"sentences": [{**GOOD, "axis": "peer"}]})
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
        ({**GOOD, "text": "조용한 곳을 원하면 여의동이 맞아요."}, ["방향"]),
        ({**GOOD, "text": "조용한 곳을 원하면 그쪽이 맞아요."}, ["이름"]),
        ({**GOOD, "text": "조용함 지표로 보면 효자2동이 맞아요."}, ["금지어"]),
        ({**GOOD, "text": "조용함 73점인 효자2동이 맞아요."}, ["숫자"]),
        ({**GOOD, "text": "S등급인 효자2동이 맞아요."}, ["숫자"]),
        ({**GOOD, "text": "조용한 곳은 효자2동"}, ["해요체"]),
        ({**GOOD, "text": "두 곳 모두 효자2동처럼 조용해요."}, ["금지어"]),
        ({**GOOD, "text": "효자2동이 여의동보다 조용해서 맞아요."}, ["이름"]),
    ],
)
def test_problems(sentence, expected):
    got = c.problems(sentence, BY_AXIS, NAMES, set())
    assert len(got) == len(expected)
    assert all(p.startswith(e) for p, e in zip(got, expected, strict=True))


def test_names_in_matches_longest_first():
    assert c.names_in("신교동이 맞아요", ["교동", "신교동"]) == ["신교동"]
    assert c.names_in("여의동보다 효자2동이 맞아요", NAMES) == ["여의동", "효자2동"]
    assert c.names_in("그쪽이 맞아요", NAMES) == []


def test_problems_tie_and_duplicate():
    (evening,) = a.orders([Y, G], ["evening"])
    tie = {"text": "저녁 활기를 원하면 여의동이 맞아요.", "axis": "evening"}
    assert c.problems(tie, {"evening": evening}, ["여의동", "교1동"], set())[0].startswith("방향")
    assert "같은 축 중복" in c.problems(GOOD, BY_AXIS, NAMES, {"quiet"})


def test_problems_ties_ok_passes_any_tied_top():
    (evening,) = a.orders([Y, G, H], ["evening"])  # 여의동 = 교1동 > 효자2동
    by_axis = {"evening": evening}
    names = ["여의동", "교1동", "효자2동"]
    for name in ("여의동", "교1동"):
        s = {"text": f"저녁 활기를 원하면 {name}이 맞아요.", "axis": "evening"}
        assert c.problems(s, by_axis, names, set(), ties_ok=True) == []
    low = {"text": "저녁 활기를 원하면 효자2동이 맞아요.", "axis": "evening"}
    assert c.problems(low, by_axis, names, set(), ties_ok=True)[0].startswith("방향")


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
    wrong = {**GOOD, "text": "조용한 곳을 원하면 여의동이 맞아요."}
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


def test_months_similarity_link():
    raws = [RAWS[HYOJA], RAWS[YEOUI]]
    assert c.months_sentences(raws)[0]["text"] == (
        "효자2동·여의동은 같은 전주시라 1·2·8월이 가장 한산해요."
    )
    vecs = {HYOJA: ((0.1, 0.1, 0.1, 0.5), False), YEOUI: ((0.2, 0.1, 0.1, 0.5), False),
            GYO: ((0.9, 0.9, 0.9, 0.1), False)}  # fmt: skip
    got = c.similarity_sentences(raws, a.common_items(raws), vecs, a.pair_distances(vecs))
    assert got[0]["text"] == "효자2동과 여의동은 분위기가 보통 정도로 닮은 편이에요."
    assert got[0]["evidence"] == "두 동네 닮음: 전국 동네 쌍 중 가까운 쪽 34%"
    # 시각별 한산은 빼고, 등급 글자 없이 문장으로
    assert (
        got[1]["text"]
        == "둘 다 로컬살이형이 가장 잘 맞고, 생활권 성격이 강하고, 저녁 활기 수준이 같아요."
    )
    assert c.link_sentence(a.Link("효자2동", "여의동", 2)) == {
        "text": "최근 보신 여의동과 비슷한 쪽은 효자2동이에요.",
        "evidence": "효자2동의 비슷한 동 2위: 여의동",
        "source": "rule",
    }


def test_common_sentence():
    items = [
        ("quiet:weekend:3", "주말 3시 한산"),
        ("place:coast", "바닷가"),
        ("grade:quiet:B", "조용함 B"),
    ]
    assert c.common_sentence(items, 2) == "둘 다 바닷가에 있고, 조용함 수준이 같아요."
    assert c.common_sentence(items, 3) == "셋 다 바닷가에 있고, 조용함 수준이 같아요."
    assert c.common_sentence([("quiet:weekend:3", "주말 3시 한산")], 2) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("이 동네는 조용한 편입니다.", "이 동네는 조용한 편이에요."),
        ("바닷가 동네입니다", "바닷가 동네예요."),
        ("차가 많습니다.", "차가 많아요."),
        ("주말엔 한산합니다!", "주말엔 한산해요!"),
        ("조용함을 원한다면 효자2동이 맞아요", "조용함을 원한다면 효자2동이 맞아요."),
        ("이미 괜찮아요.", "이미 괜찮아요."),
    ],
)
def test_tidy(text, expected):
    assert c.tidy(text) == expected


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


def test_fix_contrast_keeps_connector_only_after_other_dong():
    def item(text):
        return {"text": text, "evidence": None, "source": "llm"}

    got = c._fix_contrast(
        [
            item("반면 조용히 일하려면 효자2동이 맞아요."),
            item("반면 또래가 많은 곳은 효자2동이에요."),
            item("대신 저녁 활기는 여의동이 나아요."),
        ],
        NAMES,
    )
    assert [x["text"] for x in got] == [
        "조용히 일하려면 효자2동이 맞아요.",
        "또래가 많은 곳은 효자2동이에요.",
        "대신 저녁 활기는 여의동이 나아요.",
    ]
