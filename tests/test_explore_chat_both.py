"""기준이 둘인 답 (시군구 채팅 스펙 §7). 차량(그래프) · 보행(동 상세 곡선) / 지도 탐색 · 방문."""

from explore import ask
from explore.chat import both
from tests.explore_chat_samples import HYOJA, RAWS, REGION_RAW, YEOUI

WALK = {
    "weekday": {"peakHour": 11, "lowHour": 18, "lowValue": 90},
    "weekend": {"peakHour": 12, "lowHour": 9, "lowValue": 88},
}


def test_hours_both_names_each_basis():
    out = both.hours_both([(RAWS[HYOJA], WALK)], "weekday", "10월")
    assert out == [
        {
            "text": (
                "효자2동은 평일 차량이 12시에 가장 많고, "
                "걸어 다니는 사람은 10월 기준 11시에 가장 붐비고 18시에 가장 한산해요."
            ),
            "evidence": "차량: 연간 · 지도 탐색 그래프 / 보행: 10월 · 24시간 붐빔 곡선과 같은 값",
            "source": "rule",
        }
    ]


def test_hours_both_without_month_is_car_only():
    out = both.hours_both([(RAWS[HYOJA], None), (RAWS[YEOUI], None)], "weekend", None)
    assert [s["text"] for s in out] == [
        "효자2동은 주말 차량이 14시에 가장 많아요.",
        "여의동은 주말 차량이 14시에 가장 많아요.",
    ]
    assert out[0]["evidence"] == "차량: 연간 · 지도 탐색 그래프"


def test_hours_both_walk_only_and_none():
    rec = {**RAWS[HYOJA]["rec"], "hours": []}
    out = both.hours_both([({**RAWS[HYOJA], "rec": rec}, WALK)], "weekend", "연간")
    assert out[0]["text"] == (
        "효자2동은 주말에 걸어 다니는 사람이 연간 기준 12시에 가장 붐비고 9시에 가장 한산해요."
    )
    out = both.hours_both([({**RAWS[HYOJA], "rec": rec}, None)], "weekend", None)
    assert out[0]["text"] == ask.NO_DATA


def test_months_both():
    out = both.months_both(REGION_RAW, [2, 3])
    assert out[0]["text"] == (
        "지도 탐색 기준으로는 1·2·8월이 한산하고, 방문 기준 가기 좋은 달은 2·3월이에요."
    )
    assert out[0]["evidence"] == (
        "지도 탐색: 연간 한산한 달 / 방문: 지역 상세 '가기 좋은 달'과 같은 값"
    )
    assert both.months_both(REGION_RAW, [])[0]["text"] == "전주시는 1·2·8월이 가장 한산해요."
    quiet_none = {**REGION_RAW, "months": [[m, 80.0, False] for m in range(1, 13)]}
    assert both.months_both(quiet_none, [2, 3])[0]["text"] == "전주시는 2·3월이 가기 좋은 달이에요."
    assert both.months_both(quiet_none, [])[0]["text"] == ask.NO_DATA
