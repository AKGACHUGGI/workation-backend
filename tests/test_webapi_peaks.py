"""보행 피크 · 가기 좋은 달 공용 함수. 동 상세 · 지역 상세 API 와 채팅이 같은 값을 쓴다."""

from tests.test_api import data, photos  # noqa: F401 (픽스처 재사용)
from webapi import data as webdata


def test_peaks_of_picks_earlier_hour_on_ties():
    c = [50] * 24
    c[11] = c[15] = 100
    c[9] = c[18] = 20
    assert webdata.peaks_of({"weekday": c}) == {
        "weekday": {"peakHour": 11, "lowHour": 9, "lowValue": 20, "flat": False}
    }


def test_peaks_of_flat_when_daytime_gap_under_15():
    """9~18시 최대 − 최소가 15 미만이면 낮 내내 비슷하다(flat). 한산 시각은 그대로 준다."""
    c = [10] * 24
    c[9:19] = [100] * 10
    c[18] = 86  # 차이 14
    assert webdata.peaks_of({"weekday": c})["weekday"] == {
        "peakHour": 9, "lowHour": 18, "lowValue": 86, "flat": True
    }  # fmt: skip
    c[18] = 85  # 차이 15
    assert webdata.peaks_of({"weekday": c})["weekday"]["flat"] is False


def test_peaks_of_flat_ignores_hours_outside_daytime():
    """금암동 12월 평일형: 낮은 90~100 사이, 새벽은 낮다.
    24시간 최대 피크가 낮 밖이어도 차이는 9~18시 안에서 잰다."""
    c = [5] * 24
    c[9:19] = [92, 96, 100, 98, 97, 95, 94, 93, 91, 90]
    c[21] = 120
    got = webdata.peaks_of({"weekday": c})["weekday"]
    assert got == {"peakHour": 21, "lowHour": 18, "lowValue": 90, "flat": True}


def test_best_months_lowest_two_sorted():
    assert webdata.best_months({1: 50.0, 2: 10.0, 3: 30.0, 8: 10.0}) == [2, 8]
    # 동점이 두 번째 자리에 걸리면 행 순서와 상관없이 이른 달
    assert webdata.best_months({9: 20.0, 3: 20.0, 1: 10.0}) == [1, 3]


def test_walk_peaks_matches_dong_detail(client, photos):  # noqa: F811
    body = client.get("/api/dongs/5182025300?month=2025-10").json()
    peaks = webdata.walk_peaks("5182025300", 10)
    assert peaks["weekday"] == body["peaks"]["weekday"]
    assert peaks["weekend"] == body["peaks"]["weekend"]
    assert isinstance(body["peaks"]["weekday"]["flat"], bool)
    assert webdata.walk_peaks("0000000000", 10) is None


def test_region_best_months_matches_region_detail(client, photos):  # noqa: F811
    body = client.get("/api/regions/51820?month=2025-10").json()
    assert webdata.region_best_months("51820") == body["bestMonths"] == [2, 3]
    assert webdata.region_best_months("99999") == []
