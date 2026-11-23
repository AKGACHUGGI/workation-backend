"""또래 밀도 연령대 개인화: scoring.peer_basis · unit_scores(scope, month, basis).
설계: docs/superpowers/specs/2026-11-23-peer-personalization-design.md 4-1, 5-1절"""

import pytest

from metrics import scoring
from metrics.models import Metric, MetricDef
from tests.test_api import data  # noqa: F401

pytestmark = pytest.mark.django_db

BUCKET_KEYS = ("peer_under20", "peer_20s", "peer_30s", "peer_40s", "peer_50s", "peer_60plus")


@pytest.fixture
def peer_data(data):  # noqa: F811
    """test_api.data 위에 구간 값. 30s·60plus 만 넣는다 (다른 구간은 '자료 없음' 경로 확인용).
    시군구 1=고성(51820) 2=강릉(51150), 동 101=5182025300 201=5115060500, 월 10."""
    for key in BUCKET_KEYS:
        MetricDef.objects.create(metric_key=key, name_ko=key, higher_is="good")
    rows = [
        ("region", 1, "peer_30s", 10, "C", 0.05),
        ("region", 2, "peer_30s", 90, "S", 0.2),
        ("region", 1, "peer_60plus", 95, "S", 0.4),
        ("region", 2, "peer_60plus", 5, "C", 0.1),
        ("dong", 101, "peer_30s", 15, "C", 0.05),
        ("dong", 201, "peer_30s", 85, "S", 0.25),
    ]
    Metric.objects.bulk_create(
        Metric(scope=s, unit_id=u, month=10, metric_def_id=k, score=sc, grade=g, raw_value=r)
        for s, u, k, sc, g, r in rows
    )
    return data


def _no_bucket_keys(scores):
    return all(not k.startswith("peer_") for row in scores.values() for k in row)


def test_peer_basis_maps_age_groups():
    assert scoring.peer_basis("10s") == "under20"
    assert scoring.peer_basis("30s") == "30s"
    assert scoring.peer_basis("60s") == "60plus"
    assert scoring.peer_basis("90s") == "60plus"
    assert scoring.peer_basis(None) is None
    assert scoring.peer_basis("99s") is None


def test_unit_scores_default_keeps_peer_and_hides_buckets(peer_data):
    s = scoring.unit_scores("region", 10)
    assert s[1]["peer"] == {"score": 40, "grade": "B", "raw": 0.3}
    assert _no_bucket_keys(s)


def test_unit_scores_basis_swaps_peer(peer_data):
    s = scoring.unit_scores("region", 10, "30s")
    assert s[1]["peer"] == {"score": 10, "grade": "C", "raw": 0.05}
    assert s[2]["peer"]["score"] == 90
    assert s[1]["quiet"]["score"] == 80
    assert _no_bucket_keys(s)


def test_unit_scores_missing_bucket_drops_peer(peer_data):
    assert "peer" not in scoring.unit_scores("region", 10, "20s")[1]


def test_unit_scores_does_not_pollute_cache(peer_data):
    s = scoring.unit_scores("region", 10, "30s")
    s[1]["peer"] = {"score": -1}
    assert scoring.unit_scores("region", 10)[1]["peer"]["score"] == 40
    assert scoring.unit_scores("region", 10, "30s")[1]["peer"]["score"] == 10


def test_unit_scores_rejects_unknown_basis(peer_data):
    with pytest.raises(ValueError):
        scoring.unit_scores("region", 10, "70s")


def test_unit_scores_dong_scope(peer_data):
    assert scoring.unit_scores("dong", 10, "30s")[101]["peer"]["score"] == 15
    assert scoring.unit_scores("dong", 10, "30s")[201]["peer"]["score"] == 85
    assert scoring.unit_scores("dong", 10)[101]["peer"]["score"] == 40
