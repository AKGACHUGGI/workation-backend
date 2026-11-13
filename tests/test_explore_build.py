"""manage.py build_explore · explore/sources.py (테스트 DB).
테스트 DB 에는 PostGIS 가 없어 동 중심 좌표(load_centers)만 바꿔 끼운다."""

import math
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import DatabaseError, IntegrityError, connection, transaction

from explore import derive, graph, sources
from metrics.models import Metric, MetricDef
from regions.models import Dong, DongHourly, Region
from tests.explore_samples import (
    GANGNEUNG_RAW,
    JEONJU_RAW,
    JUMUNJIN_WEEKDAY,
    JUMUNJIN_WEEKEND,
    curve,
)

KEYS = ("quiet", "peer", "evening", "local_life", "overnight", "ease")
# dong_id: (region_id, 이름, adm_code, 해안 km, 태그, (quiet, peer, evening) 각 (점수, 등급, 원값),
#           local_life 원값, 평일 9~11시 보행 지수 index_100)
DONGS = {
    116: (1, "주문진읍", "5115025000", 0, ["beach", "cafe", "food", "coast"],
          ((31, "B", 0.8721), (39, "B", 0.3735), (62, "A", 0.278)), 0.7365, 40),
    112: (1, "연곡면", "5115037000", 0, ["beach", "coast"],
          ((55, "A", 0.80), (20, "C", 0.30), (40, "B", 0.20)), 0.65, 80),
    901: (9, "금암동", "5211357500", None, [],
          ((21, "C", 0.95), (97, "S", 0.60), (94, "S", 0.45)), 0.7218, 60),
    902: (9, "노송동", "5211160500", None, ["market"],
          ((6, "C", 0.99), (92, "S", 0.58), (95, "S", 0.46)), 0.70, 20),
}  # fmt: skip
HOURS = {  # dong_id: (평일, 주말) 차량 이동 인구 24칸
    116: (JUMUNJIN_WEEKDAY, JUMUNJIN_WEEKEND),
    112: (curve(13, 9), curve(15, 3)),
    901: (curve(17, 10), curve(13, 5)),
    902: (curve(18, 11), curve(12, 6)),
}
# ease 점수: 점수 기준으로 고르면 한산한 달이 1·4·10월 (db/05_dong_doc.sql 버그와 같은 상황)
EASE_SCORE = {4: 95, 10: 90, 1: 85}


def time_band(h):
    return "night" if h < 6 else "morning" if h < 12 else "day" if h < 18 else "evening"


def insert_hourly():
    rows = []
    for dong_id, (weekday, weekend) in HOURS.items():
        for h in range(24):
            # 평일은 두 달(1·2월) 값의 평균이 weekday[h] 가 되도록 ±1
            rows.append((dong_id, 2025, 1, 1, h, "weekday", time_band(h), 4, weekday[h] - 1))
            rows.append((dong_id, 2025, 2, 1, h, "weekday", time_band(h), 4, weekday[h] + 1))
            rows.append((dong_id, 2025, 1, 6, h, "weekend", time_band(h), 4, weekend[h]))
    rows.append((116, 2024, 1, 1, 0, "weekday", "night", 4, 99999.0))  # 데이터 연도가 아님
    with connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO population_hourly (dong_id, year, month, dow, hour, day_group,"
            " time_band, weekday_count, driv_avg) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )


@pytest.fixture
def explore_data(db):
    for key in KEYS:
        MetricDef.objects.create(metric_key=key, name_ko=key, higher_is="good")
    Region.objects.create(
        region_id=1, sido_name="강원특별자치도", sigungu_name="강릉시", sigungu_code="51150"
    )
    Region.objects.create(
        region_id=9, sido_name="전북특별자치도", sigungu_name="전주시", sigungu_code="52110"
    )
    Dong.objects.create(dong_id=199, region_id=1, dong_name="수동면", is_excluded=True, tags=[])
    metrics, curves = [], []
    for dong_id, (region_id, name, code, km, tags, grades, life, am) in DONGS.items():
        Dong.objects.create(
            dong_id=dong_id,
            region_id=region_id,
            dong_name=name,
            adm_code=code,
            coast_dist_km=km,
            tags=tags,
        )
        for key, (score, grade, raw) in zip(("quiet", "peer", "evening"), grades, strict=True):
            metrics.append(
                Metric(scope="dong", unit_id=dong_id, month=0, metric_def_id=key,
                       score=score, grade=grade, raw_value=raw)
            )  # fmt: skip
        metrics.append(
            Metric(
                scope="dong", unit_id=dong_id, month=0, metric_def_id="local_life", raw_value=life
            )
        )
        curves += [
            DongHourly(dong_id=dong_id, month=0, day_group="weekday", hour=h, index_100=am)
            for h in (9, 10, 11)
        ]
    for region_id, overnight, raws in ((1, 64, GANGNEUNG_RAW), (9, 27, JEONJU_RAW)):
        metrics.append(
            Metric(scope="region", unit_id=region_id, month=0, metric_def_id="overnight",
                   score=overnight, grade="A", raw_value=0.8)
        )  # fmt: skip
        for m, raw in raws.items():
            metrics.append(
                Metric(scope="region", unit_id=region_id, month=m, metric_def_id="ease",
                       score=EASE_SCORE.get(m, 10), raw_value=raw)
            )  # fmt: skip
    Metric.objects.bulk_create(metrics)
    DongHourly.objects.bulk_create(curves)
    insert_hourly()


def build():
    out = StringIO()
    call_command("build_explore", stdout=out)
    return out.getvalue()


def fetch(sql):
    with connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def test_build_explore_counts(explore_data):
    assert "2025년 · 동 4 → dong_type_score 12 · dong_hour_stat 8 · dong_similar 4" in build()


def test_type_scores_written(explore_data):
    build()
    rows = fetch(
        "SELECT type, score, rank, reason FROM dong_type_score WHERE dong_id = 116 ORDER BY rank"
    )
    assert rows == [
        ("local", 100, 1, "생활권 지수 0.74"),
        ("networking", 50, 2, "또래 밀도 39 · 저녁 활기 62"),
        ("immersion", 39, 3, "조용함 31 · 오전 한산 50"),
    ]
    assert fetch("SELECT DISTINCT type FROM dong_type_score ORDER BY type") == [
        ("immersion",), ("local",), ("networking",),
    ]  # fmt: skip


def test_dongs_scored_without_region_overnight(explore_data):
    # 장기체류형이 쓰던 시군구 숙박 성향이 없어도 동을 빼지 않는다
    Metric.objects.filter(scope="region", metric_def_id="overnight").delete()
    assert "동 4 → dong_type_score 12" in build()


def test_hour_stat_written(explore_data):
    build()
    rows = {
        (d, g): (driv, peak, quiet)
        for d, g, driv, peak, quiet in fetch(
            "SELECT dong_id, day_group, driv, peak_hour, quiet_hour FROM dong_hour_stat"
        )
    }
    driv, peak, quiet = rows[(116, "weekday")]
    assert (peak, quiet) == (12, 9)
    assert driv[12] == 2064 and driv[0] == 637  # 두 달 평균, 2024년 행 제외
    assert rows[(116, "weekend")][1:] == (14, 3)
    assert rows[(902, "weekday")][1:] == (18, 11)


def test_similar_written_same_coast_only(explore_data):
    build()
    rows = {d: (other, l2) for d, rank, other, l2 in fetch("SELECT * FROM dong_similar")}
    assert {d: other for d, (other, _) in rows.items()} == {116: 112, 112: 116, 901: 902, 902: 901}
    expected = math.dist((0.31, 0.39, 0.62, 0.7365), (0.55, 0.20, 0.40, 0.65))
    assert rows[116][1] == pytest.approx(expected, rel=1e-6)


def test_build_explore_rerun_replaces_rows(explore_data):
    build()
    build()
    assert fetch("SELECT count(*) FROM dong_type_score") == [(12,)]
    assert fetch("SELECT count(*) FROM dong_similar") == [(4,)]


def test_ease_quiet_months_use_raw_not_score(explore_data, monkeypatch):
    build()
    monkeypatch.setattr(sources, "load_centers", lambda: {})
    ease = sources.load_graph_input().ease[1]
    assert derive.quiet_months(ease) == [2, 4, 9]  # 점수 기준이면 1·4·10월


def test_load_graph_input(explore_data, monkeypatch):
    build()
    monkeypatch.setattr(sources, "load_centers", lambda: {116: (37.88818, 128.75354)})
    inp = sources.load_graph_input()
    assert [r.code for r in inp.regions] == ["51150", "52110"]
    assert [d.dong_id for d in inp.dongs] == [112, 116, 901, 902]  # 집계 제외 수동면 없음
    jumunjin = next(d for d in inp.dongs if d.dong_id == 116)
    assert (jumunjin.lat, jumunjin.lng, jumunjin.coast_km) == (37.88818, 128.75354, 0.0)
    assert jumunjin.tags == ["beach", "cafe", "food", "coast"] and jumunjin.local_life == 0.7365
    assert next(d for d in inp.dongs if d.dong_id == 901).coast_km is None
    assert inp.grades[116]["evening"] == ("A", 62, 0.278)
    assert inp.types[116][0] == derive.TypeScore("local", 100, 1, "생활권 지수 0.74")
    assert inp.hours[116]["weekday"][1:] == (12, 9)
    assert [o for o, _ in inp.similar[116]] == [112]
    assert inp.ease[1][2] == 69.8 and inp.data_year == 2025
    counts = graph.expected_counts(graph.build_graph(inp))
    assert counts["nodes"]["Dong"] == 4 and counts["rels"]["SIMILAR"] == 4


def test_brief_rows(explore_data):
    build()
    assert sources.load_brief_targets() == [
        (112, "5115037000"), (116, "5115025000"), (901, "5211357500"), (902, "5211160500"),
    ]  # fmt: skip
    sources.save_brief(116, ["가", "나", "다"], "mixed", "HCX-005", "a" * 64)
    sources.save_brief(116, ["라", "마", "바"], "rule", None, "b" * 64)  # upsert
    assert sources.load_brief_hashes() == {116: "b" * 64}
    assert sources.fetch_briefing("5115025000") == ["라", "마", "바"]
    assert sources.fetch_briefing("5115037000") == []


def test_fetch_briefing_db_error_is_empty(monkeypatch):
    class Broken:
        def cursor(self):
            raise DatabaseError('relation "dong_brief" does not exist')

    monkeypatch.setattr(sources, "connection", Broken())
    assert sources.fetch_briefing("5115025000") == []


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE dong_type_score SET type = 'long_stay' WHERE dong_id = 116 AND rank = 3",
        "UPDATE dong_type_score SET rank = 4 WHERE dong_id = 116 AND rank = 3",
    ],
)
def test_type_check_allows_three_types_and_ranks(explore_data, sql):
    build()
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cur:
        cur.execute(sql)
