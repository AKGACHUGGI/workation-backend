"""W-06 지자체 진단 리포트 (/api/reports/{sggId} · /pdf).

계약: workation-web features/report/types.ts
"""

import pytest

from metrics.models import Metric
from tests.test_api import data  # noqa: F401 (픽스처 재사용)
from webapi import report as report_mod
from webapi import report_pdf

KEYS = {
    "id",
    "name",
    "province",
    "year",
    "preset",
    "peakConcentration",
    "overnight",
    "ranking",
    "metricRanks",
    "months",
    "seasonType",
    "seasonNarrative",
    "diagnosis",
    "generatedAt",
}


@pytest.fixture
def annual(data):  # noqa: F811
    """연간(month 0) 지표 점수. 픽스처에는 숙박 성향만 연간 값이 있다."""
    scores = {
        1: {"quiet": 80, "ease": 90, "peer": 40, "evening": 30},
        2: {"quiet": 20, "ease": 10, "peer": 70, "evening": 60},
    }
    Metric.objects.bulk_create(
        Metric(scope="region", unit_id=rid, month=0, metric_def_id=key, score=s, raw_value=s)
        for rid, vals in scores.items()
        for key, s in vals.items()
    )
    Metric.objects.create(
        scope="dong", unit_id=101, month=0, metric_def_id="quiet", score=95, raw_value=0.2
    )


def test_report_contract(client, annual):
    res = client.get("/api/reports/51820?year=2025&preset=quiet")
    assert res.status_code == 200
    body = res.json()
    assert set(body) == KEYS
    assert (body["id"], body["name"], body["province"]) == ("51820", "고성군", "강원특별자치도")
    assert body["year"] == 2025 and body["preset"] == "quiet"
    # 상위 3개월(100 + 79 + 67) ÷ 연간 합 694 = 35.4%, 강릉 33.4% → 평균 34
    assert body["peakConcentration"] == {
        "value": 35,
        "topMonths": [7, 8, 10],
        "average": 34,
        "rank": 1,
        "total": 2,
    }
    assert body["overnight"] == {"coef": 0.91, "score": 82, "grade": "S"}
    assert [m["month"] for m in body["months"]] == list(range(1, 13))
    assert body["months"][7] == {"month": 8, "crowd": 100}
    assert body["metricRanks"] == {
        "quiet": {"rank": 1, "score": 80},
        "crowd": {"rank": 1, "score": 90},
        "peer": {"rank": 2, "score": 40},
        "overnight": {"rank": 1, "score": 82},
        "vibe": {"rank": 2, "score": 30},
    }
    assert body["seasonType"] in {"summer_beach", "spring_autumn", "second_half_rise", "flat"}
    assert body["generatedAt"].endswith("Z")


def test_ranking_is_monthly_average(client, annual):
    """연간 순위 = 월별 프리셋 종합 점수의 12개월 평균 (10월 79 · 나머지 58 → 59.75 → 60)."""
    body = client.get("/api/reports/51820").json()  # year · preset 기본값
    assert body["ranking"] == {
        "rank": 1,
        "total": 2,
        "items": [
            {"id": "51820", "name": "고성군", "score": 60},
            {"id": "51150", "name": "강릉시", "score": 52},
        ],
    }
    lively = client.get("/api/reports/51150?preset=lively").json()
    assert lively["preset"] == "lively"
    # 활기 프리셋: 고성 (52 + 11 × 71) ÷ 12 = 69.4, 강릉 59
    assert [(i["id"], i["score"]) for i in lively["ranking"]["items"]] == [
        ("51820", 69),
        ("51150", 59),
    ]
    assert lively["ranking"]["rank"] == 2


def test_sentences(client, annual):
    body = client.get("/api/reports/51820").json()
    assert body["seasonNarrative"].endswith(
        "2~4월·12월은 연중 최대의 절반 이하로, 비수기 체류 지원에 적합합니다."
    )
    d = body["diagnosis"]
    assert d["strength"].startswith("동네 조용함 1위로 2곳 중")
    assert "숙박 계수 0.91로 방문객 대부분이 숙박하는 지역" in d["strength"]
    assert d["weakness"].startswith("또래 밀도가 2위로 가장 약하고")
    assert "성수기 집중도 35%는 2곳 평균 34%보다 높아 7·8·10월에 방문이 쏠립니다." in d["weakness"]
    assert d["suggestion"].startswith("2~4월·12월 비수기에 거진읍 등")


def test_report_errors(client, annual):
    assert client.get("/api/reports/99999").status_code == 404
    assert client.get("/api/reports/51820?preset=custom").status_code == 400
    assert client.get("/api/reports/51820?year=2024").status_code == 400
    assert client.get("/api/reports/99999/pdf").status_code == 404


def test_report_pdf(client, annual):
    res = client.get("/api/reports/51820/pdf?year=2025&preset=quiet")
    assert res.status_code == 200
    assert res["Content-Type"] == "application/pdf"
    assert "attachment" in res["Content-Disposition"]
    assert (
        "%EA%B3%A0%EC%84%B1%EA%B5%B0_" in res["Content-Disposition"]
    )  # 고성군_진단리포트_2025.pdf
    assert res.content.startswith(b"%PDF-") and b"/Count 1" in res.content  # A4 1장


def test_months_text():
    assert report_mod.months_text([2, 3, 4, 12]) == "2~4월·12월"
    assert report_mod.months_text([10, 7, 8]) == "7·8·10월"
    assert report_mod.months_text([8, 9, 10]) == "8~10월"
    assert report_mod.months_text([]) == ""


def test_wrap_breaks_long_words():
    report_pdf._fonts()
    lines = report_pdf.wrap("가나다라마바사아자차카타파하 가나다", report_pdf.REGULAR, 10, 60)
    assert (
        len(lines) > 2 and "".join(lines).replace(" ", "") == "가나다라마바사아자차카타파하가나다"
    )
