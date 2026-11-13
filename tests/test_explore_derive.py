"""explore/derive.py 순수 계산. 손으로 계산 가능한 4동 + 강릉·주문진읍 실측 일부."""

import pytest

from explore import derive
from tests.explore_samples import GANGNEUNG_RAW, JUMUNJIN_WEEKDAY, JUMUNJIN_WEEKEND


def four_dongs():
    """손으로 계산 가능한 4동. 오전 보행 지수 백분위 50 · 100 · 75 · 25."""
    dong_metrics = {
        1: {"quiet": 80, "peer": 20, "evening": 40, "local_life": 0.9},
        2: {"quiet": 40, "peer": 60, "evening": 80, "local_life": 0.5},
        3: {"quiet": 20, "peer": 90, "evening": 70, "local_life": 0.75},
        4: {"quiet": 60, "peer": 40, "evening": 20, "local_life": 0.6},
    }
    morning = {1: 0.4, 2: 0.8, 3: 0.6, 4: 0.2}
    return dong_metrics, morning


def by_type(scores):
    return {s.type: s for s in scores}


def test_pct_counts_values_at_or_below():
    assert derive.pct([0.4, 0.8, 0.6, 0.2], 0.6) == 75
    assert derive.pct([1, 1, 2], 1) == pytest.approx(66.667, abs=1e-3)


def test_types_are_three_with_threshold():
    assert derive.TYPES == ("immersion", "networking", "local")
    assert derive.TYPE_KO == {
        "immersion": "몰입형",
        "networking": "네트워킹형",
        "local": "로컬살이형",
    }
    assert derive.TYPE_MIN == 40


def test_type_scores_four_dongs():
    out = derive.type_scores(*four_dongs())
    # 몰입형 = 0.6·quiet + 0.4·(100 − 오전 백분위), 네트워킹형 = 0.5·peer + 0.5·evening,
    # 로컬살이형 = 생활권 지수 백분위
    assert {t: s.score for t, s in by_type(out[1]).items()} == {
        "immersion": 68, "networking": 30, "local": 100,
    }  # fmt: skip
    assert {t: s.score for t, s in by_type(out[2]).items()} == {
        "immersion": 24, "networking": 70, "local": 25,
    }  # fmt: skip
    assert {t: s.score for t, s in by_type(out[3]).items()} == {
        "immersion": 22, "networking": 80, "local": 75,
    }  # fmt: skip
    assert [s.type for s in out[1]] == ["local", "immersion", "networking"]
    assert [s.type for s in out[3]] == ["networking", "local", "immersion"]
    assert [s.rank for s in out[4]] == [1, 2, 3]
    assert [s.type for s in out[4]] == ["immersion", "local", "networking"]


def test_type_reasons():
    t = by_type(derive.type_scores(*four_dongs())[1])
    assert t["immersion"].reason == "조용함 80 · 오전 한산 50"
    assert t["networking"].reason == "또래 밀도 20 · 저녁 활기 40"
    assert t["local"].reason == "생활권 지수 0.90"


def test_networking_rounds_half_to_even():
    # 주문진읍 또래 39 · 저녁 62 → 50.5 → 50 (스펙 값)
    dong_metrics = {1: {"quiet": 31, "peer": 39, "evening": 62, "local_life": 0.7365}}
    out = derive.type_scores(dong_metrics, {1: 0.5})
    assert by_type(out[1])["networking"].score == 50


def test_ties_follow_type_order():
    # A: 몰입형 0.6·50 + 0.4·50 = 50, 네트워킹형 50, 로컬살이형 백분위 50 → 셋 동점
    dong_metrics = {
        "A": {"quiet": 50, "peer": 50, "evening": 50, "local_life": 0.3},
        "B": {"quiet": 90, "peer": 90, "evening": 90, "local_life": 0.8},
    }
    out = derive.type_scores(dong_metrics, {"A": 0.1, "B": 0.9})
    assert [(s.type, s.score, s.rank) for s in out["A"]] == [
        ("immersion", 50, 1),
        ("networking", 50, 2),
        ("local", 50, 3),
    ]


def test_months_use_raw_values():
    assert derive.quiet_months(GANGNEUNG_RAW) == [2, 4, 9]
    assert derive.busiest_month(GANGNEUNG_RAW) == 8


def test_quiet_months_tie_takes_earlier_month():
    raw = {m: 90.0 for m in range(1, 13)} | {11: 50.0, 5: 60.0, 7: 60.0, 3: 60.0}
    assert derive.quiet_months(raw) == [3, 5, 11]


def test_busiest_month_empty_is_none():
    assert derive.busiest_month({}) is None


def test_hour_stat_jumunjin():
    assert derive.hour_stat(JUMUNJIN_WEEKDAY, "weekday") == (12, 9)
    assert derive.hour_stat(JUMUNJIN_WEEKEND, "weekend") == (14, 3)


def test_hour_stat_weekday_quiet_stays_in_work_hours_and_ties_take_earlier():
    driv = [10.0] * 24
    driv[3] = 1.0  # 새벽은 평일 한산 후보가 아니다
    driv[11] = driv[15] = 5.0
    driv[20] = 50.0
    driv[21] = 50.0
    assert derive.hour_stat(driv, "weekday") == (20, 11)
    assert derive.hour_stat(driv, "weekend") == (20, 3)


def test_hour_stat_skips_missing_hours():
    driv = [None] * 24
    driv[10], driv[13] = 7.0, 3.0
    assert derive.hour_stat(driv, "weekday") == (10, 13)
    assert derive.hour_stat([None] * 24, "weekend") == (None, None)


def test_coastal_and_life():
    assert derive.is_coastal(0.0) and derive.is_coastal(1.0)
    assert not derive.is_coastal(1.01) and not derive.is_coastal(None)
    assert derive.life_tag(0.7) == "local" and derive.life_tag(0.6999) == "tourist"


def test_metric_vec_matches_dong_feature_definition():
    assert derive.metric_vec(31, 39, 62, 0.73654) == (0.31, 0.39, 0.62, 0.7365)


def test_similar_dongs_same_coast_only_three_and_not_self():
    vecs = {
        1: (0.0, 0.0, 0.0, 0.0),
        2: (0.1, 0.0, 0.0, 0.0),
        3: (0.2, 0.0, 0.0, 0.0),
        4: (0.05, 0.0, 0.0, 0.0),  # 내륙 — 1 과 가장 가깝지만 후보 아님
        5: (0.3, 0.0, 0.0, 0.0),
        6: (0.1, 0.0, 0.0, 0.0),  # 2 와 거리 같음 → id 순
    }
    coastal = {1: True, 2: True, 3: True, 4: False, 5: True, 6: True}
    out = derive.similar_dongs(vecs, coastal)
    assert [o for o, _ in out[1]] == [2, 6, 3]
    assert out[1][0][1] == pytest.approx(0.1)
    assert all(o != d and coastal[o] == coastal[d] for d, rows in out.items() for o, _ in rows)
    assert out[4] == []  # 같은 바닷가 여부의 다른 동이 없으면 비어 있다
