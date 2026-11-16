"""비교 재료 정리 (스펙 §5-2·§5-3)."""

import pytest

from explore.chat import arrange as a
from tests.explore_chat_samples import GYO, HYOJA, RAWS, YEOUI

H, Y, G = (a.dong_of(RAWS[c]) for c in (HYOJA, YEOUI, GYO))


def by_name(*dongs):
    return {d.name: d for d in dongs}


def test_dong_of_values():
    assert H.values == {
        "quiet": 73,
        "peer": 56,
        "evening": 79,
        "coast": a.INLAND,
        "type:local": 60,
        "type:networking": 50,
        "type:immersion": 36,
        "life": 0.7,
    }
    assert H.grades == {"quiet": "S", "peer": "A", "evening": "S"}
    assert Y.values["coast"] == -0.4


def test_orders_and_top():
    quiet, coast = a.orders([H, Y], ["quiet", "coast"])
    assert quiet == a.Order("quiet", [("효자2동", 73), ("여의동", 57)], "효자2동")
    assert coast.top == "여의동"


def test_tie_has_no_top():
    (evening,) = a.orders([Y, G], ["evening"])
    assert evening.top is None
    assert evening.ranked == [("여의동", 96), ("교1동", 96)]


def test_order_line():
    quiet, coast = a.orders([H, Y], ["quiet", "coast"])
    assert a.order_line(quiet, by_name(H, Y)) == "조용함 높은 순: 효자2동(S) > 여의동(A)"
    assert a.order_line(coast, by_name(H, Y)) == "바다 가까운 순: 여의동(바닷가) > 효자2동(내륙)"
    (evening,) = a.orders([Y, G], ["evening"])
    assert a.order_line(evening, by_name(Y, G)) == "저녁 활기 높은 순: 여의동(S) = 교1동(S)"


def test_evidence():
    quiet, coast, local, life = a.orders([H, Y], ["quiet", "coast", "type:local", "life"])
    names = by_name(H, Y)
    assert a.evidence(quiet, names) == "조용함: 효자2동 S(73점) · 여의동 A(57점)"
    assert a.evidence(coast, names) == "바다 가까움: 여의동 해안선 0.4km · 효자2동 내륙"
    assert a.evidence(local, names) == "로컬살이형: 효자2동 60점 · 여의동 40점"
    assert a.evidence(life, names) == "생활권 분위기: 효자2동 0.70 · 여의동 0.60"


def test_common_is_rare_first_without_region():
    assert a.common([RAWS[HYOJA], RAWS[YEOUI]]) == [
        "평일 9시 한산",
        "1위 로컬살이형",
        "저녁 활기 S",
        "생활권",
    ]


def test_links():
    assert a.links([RAWS[HYOJA], RAWS[YEOUI]], [YEOUI]) == [a.Link("효자2동", "여의동", 2)]
    assert a.links([RAWS[HYOJA], RAWS[YEOUI]], [GYO]) == []


VECS = {
    "a": ((0.1, 0.1, 0.1, 0.5), False),
    "b": ((0.2, 0.1, 0.1, 0.5), False),
    "c": ((0.9, 0.9, 0.9, 0.1), False),
    "d": ((0.5, 0.5, 0.5, 0.5), True),
}


def test_likeness_percentile_and_bucket():
    pairs = a.pair_distances(VECS)
    assert len(pairs.same) == 3 and len(pairs.every) == 6
    assert a.likeness("a", "b", VECS, pairs) == (34, "보통 정도로 닮은 편")
    assert a.likeness("a", "c", VECS, pairs) == (100, "꽤 다른 편")
    # 바닷가 여부가 다르면 전체 쌍과 비교
    assert a.likeness("a", "d", VECS, pairs) == (50, "보통 정도로 닮은 편")
    assert a.likeness("a", "zz", VECS, pairs) is None


def test_clean_criteria():
    got = a.clean_criteria(
        {
            "chips": ["quiet", "quiet", "coast"],
            "interest": {
                "metric": {"quiet": 0.6, "vibe": "x"},
                "terrain": {"coast": 0.5},
                "recent": ["1", "2", "3", "4", 5],
            },
        }
    )
    assert got == {
        "chips": ["quiet", "coast"],
        "metric": {"quiet": 0.6},
        "terrain": {"coast": 0.5},
        "recent": ["1", "2", "3"],
    }
    assert a.clean_criteria(None) == {"chips": [], "metric": {}, "terrain": {}, "recent": []}
    with pytest.raises(ValueError):
        a.clean_criteria({"chips": ["cheap"]})
    with pytest.raises(ValueError):
        a.clean_criteria("quiet")


def crit(**kw):
    return {"chips": [], "metric": {}, "terrain": {}, "recent": [], **kw}


def test_choose_axes_priority():
    # 칩 > 지표 관심
    assert a.choose_axes(crit(chips=["coast", "quiet"], metric={"peer": 0.6}), [H, Y], False) == [
        "coast",
        "quiet",
        "peer",
    ]
    # 웹 키 vibe → evening, crowd 는 동 축이 없어 버림, 남은 자리는 등급 차 큰 축
    assert a.choose_axes(crit(metric={"crowd": 0.9, "vibe": 0.5, "quiet": 0.2}), [H, Y], False) == [
        "evening",
        "quiet",
        "peer",
    ]
    # 바다 관심 > 최근 본 동 연결 > 등급 차
    assert a.choose_axes(crit(terrain={"coast": 0.5, "city": 0.2}), [H, Y], True) == [
        "coast",
        "link",
        "peer",
    ]
    # 아무 기준 없음: 등급 단계 차 → 점수 차 순 (peer 1단계·23점, quiet 1단계·16점, evening 0단계)
    assert a.choose_axes(crit(), [H, Y], False) == ["peer", "quiet", "evening"]
