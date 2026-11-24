"""채팅 읽기: Postgres 동 이름 사전 · metric_vec, 캐시."""

import pytest
from django.db import connection

from explore import queries, sources
from explore.chat import data
from explore.chat.resolve import DongName


@pytest.fixture
def rows(db):
    with connection.cursor() as c:
        c.execute(
            "INSERT INTO region (region_id, sido_name, sigungu_name, sigungu_code)"
            " VALUES (9, '전북특별자치도', '전주시', '45111')"
        )
        c.execute(
            """INSERT INTO dong
                   (dong_id, region_id, dong_name, adm_code, coast_dist_km, is_excluded)
               VALUES (901, 9, '효자2동', '4511164500', 12.5, false),
                      (902, 9, '여의동', '4511163500', 0.4, false),
                      (903, 9, '코드없음동', NULL, NULL, false),
                      (904, 9, '제외동', '4511100000', 3, true)"""
        )
        c.execute(
            """INSERT INTO metric_def (metric_key, name_ko, higher_is)
               VALUES ('quiet', 'q', 'good'), ('peer', 'p', 'good'),
                      ('evening', 'e', 'good'), ('local_life', 'l', 'good')"""
        )
        for d, (q, p, e, life) in {901: (73, 56, 79, 0.7), 902: (57, 79, 96, 0.6)}.items():
            for key, score in (("quiet", q), ("peer", p), ("evening", e)):
                c.execute(
                    "INSERT INTO metric (scope, unit_id, month, metric_key, score)"
                    " VALUES ('dong', %s, 0, %s, %s)",
                    [d, key, score],
                )
            c.execute(
                "INSERT INTO metric (scope, unit_id, month, metric_key, raw_value)"
                " VALUES ('dong', %s, 0, 'local_life', %s)",
                [d, life],
            )
        c.execute(
            """INSERT INTO dong_type_score (dong_id, type, score, rank, reason)
               VALUES (901, 'local', 60, 1, 'x'), (902, 'networking', 70, 1, 'x')"""
        )


def test_load_dong_names(rows):
    assert sources.load_dong_names() == [
        ("4511164500", "효자2동", "45111", "전주시"),
        ("4511163500", "여의동", "45111", "전주시"),
    ]


def test_load_metric_vecs(rows):
    got = sources.load_metric_vecs()
    assert set(got) == {"4511164500", "4511163500"}
    assert got["4511164500"][0] == pytest.approx((0.73, 0.56, 0.79, 0.7))
    assert got["4511164500"][1] is False
    assert got["4511163500"][1] is True


def test_names_and_pairs_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sources,
        "load_dong_names",
        lambda: calls.append("names") or [("1", "가동", "11", "가시")],
    )
    monkeypatch.setattr(
        sources,
        "load_metric_vecs",
        lambda: calls.append("vecs") or {"1": ((0, 0, 0, 0), False), "2": ((1, 0, 0, 0), False)},
    )
    assert data.dong_names() == [DongName("1", "가동", "11", "가시")]
    assert data.dong_names() == [DongName("1", "가동", "11", "가시")]
    assert data.pairs().same == [1.0]
    assert data.pairs().same == [1.0]
    assert calls == ["names", "vecs"]


def test_dong_raw_cache_skips_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(queries, "fetch_dong", lambda code: calls.append(code) or None)
    assert data.dong_raw("x") is None
    assert data.dong_raw("x") is None
    assert calls == ["x", "x"]  # 없는 동은 캐시하지 않는다


def test_region_raw_caches_found_only(monkeypatch):
    from django.core.cache import cache

    cache.clear()
    calls = []

    def fetch(code):
        calls.append(code)
        return {"region": {"code": code}} if code == "45111" else None

    monkeypatch.setattr(queries, "fetch_region", fetch)
    assert data.region_raw("45111") == {"region": {"code": "45111"}}
    assert data.region_raw("45111") == {"region": {"code": "45111"}}
    assert data.region_raw("99999") is None
    assert data.region_raw("99999") is None
    assert calls == ["45111", "99999", "99999"]
