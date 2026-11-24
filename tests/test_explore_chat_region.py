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
    assert p.axes == ["quiet", "evening", "peer"]


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
