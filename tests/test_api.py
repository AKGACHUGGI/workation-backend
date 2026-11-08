"""데모용 화면 API (map/scores · tiers · regions · dongs · search/nl).
테스트 DB 는 db/schema.sql 만 실행해 pgvector 테이블(dong_feature · tag_vocab · dong_doc)이 없다.
그래서 비슷한 동네는 빈 목록이 되는지, 자연어 검색은 매칭 · 순위를 대신 넣어 응답 형식을 본다."""

import pytest

from metrics.models import Metric, MetricDef
from regions.models import Dong, DongHourly, Region
from search import engine

KEYS = ("quiet", "ease", "peer", "overnight", "evening", "weekend_gap", "local_life")


@pytest.fixture
def data(db):
    for key in KEYS:
        MetricDef.objects.create(metric_key=key, name_ko=key, higher_is="good")
    goseong = Region.objects.create(
        region_id=1,
        sido_name="강원특별자치도",
        sigungu_name="고성군",
        sigungu_code="51820",
        tags=["beach", "coast"],
    )
    gangneung = Region.objects.create(
        region_id=2,
        sido_name="강원특별자치도",
        sigungu_name="강릉시",
        sigungu_code="51150",
        tags=["cafe", "coast"],
    )
    Dong.objects.create(
        dong_id=101,
        region=goseong,
        dong_name="거진읍",
        adm_code="5182025300",
        tags=["coast"],
        coast_dist_km=0,
    )
    Dong.objects.create(dong_id=102, region=goseong, dong_name="수동면", is_excluded=True, tags=[])
    Dong.objects.create(
        dong_id=201,
        region=gangneung,
        dong_name="경포동",
        adm_code="5115060500",
        tags=["coast", "cafe"],
        coast_dist_km=0,
    )
    rows = []
    region_vals = {  # (score, grade, raw) for month 10
        1: {
            "quiet": (80, "S", 0.7),
            "ease": (90, "S", 67),
            "peer": (40, "B", 0.3),
            "evening": (30, "C", 0.1),
            "weekend_gap": (None, None, 1.32),
        },
        2: {
            "quiet": (20, "C", 0.9),
            "ease": (10, "C", 95),
            "peer": (70, "S", 0.5),
            "evening": (60, "A", 0.3),
            "weekend_gap": (None, None, 0.95),
        },
    }
    for rid, vals in region_vals.items():
        for key, (score, grade, raw) in vals.items():
            rows.append(
                Metric(
                    scope="region",
                    unit_id=rid,
                    month=10,
                    metric_def_id=key,
                    score=score,
                    grade=grade,
                    raw_value=raw,
                )
            )
        crowd = {
            1: [53, 41, 41, 45, 59, 60, 79, 100, 52, 67, 51, 46],
            2: [70, 60, 65, 55, 75, 80, 95, 100, 58, 95, 50, 66],
        }[rid]
        for m, c in enumerate(crowd, 1):
            if m != 10:
                rows.append(
                    Metric(
                        scope="region",
                        unit_id=rid,
                        month=m,
                        metric_def_id="ease",
                        score=50,
                        grade="B",
                        raw_value=c,
                    )
                )
    rows.append(
        Metric(
            scope="region",
            unit_id=1,
            month=0,
            metric_def_id="overnight",
            score=82,
            grade="S",
            raw_value=0.91,
        )
    )
    rows.append(
        Metric(
            scope="region",
            unit_id=2,
            month=0,
            metric_def_id="overnight",
            score=64,
            grade="A",
            raw_value=0.81,
        )
    )
    for dong_id, (q, p, e, ll) in {101: (95, 40, 10, 0.8), 201: (16, 98, 50, 0.5)}.items():
        for key, score in (("quiet", q), ("peer", p), ("evening", e)):
            grade = "S" if score >= 75 else "A" if score >= 50 else "B" if score >= 25 else "C"
            rows.append(
                Metric(
                    scope="dong",
                    unit_id=dong_id,
                    month=10,
                    metric_def_id=key,
                    score=score,
                    grade=grade,
                    raw_value=score / 100,
                )
            )
        rows.append(
            Metric(
                scope="dong", unit_id=dong_id, month=10, metric_def_id="local_life", raw_value=ll
            )
        )
    Metric.objects.bulk_create(rows)
    curve = [
        10,
        8,
        6,
        6,
        7,
        12,
        30,
        55,
        70,
        60,
        50,
        65,
        80,
        75,
        60,
        55,
        70,
        100,
        90,
        70,
        50,
        35,
        25,
        15,
    ]
    DongHourly.objects.bulk_create(
        [
            DongHourly(dong_id=101, month=10, day_group=g, hour=h, index_100=v)
            for g in ("weekday", "weekend")
            for h, v in enumerate(curve)
        ]
    )


# ───────────── 지도 · 티어 ─────────────


def test_map_scores_region_value(client, data):
    res = client.get("/api/v1/map/scores?level=region&metric=value&month=10&preset=quiet")
    assert res.status_code == 200
    body = res.json()
    items = {i["id"]: i for i in body["items"]}
    # 고성 (3*80 + 3*90 + 40 + 82) / 8 = 79, 강릉 (3*20 + 3*10 + 70 + 64) / 8 = 28
    assert items["51820"]["score"] == 79 and items["51150"]["score"] == 28
    assert items["51820"]["grade"] == "S" and items["51150"]["grade"] == "B"


def test_map_scores_dong_skips_excluded(client, data):
    res = client.get("/api/v1/map/scores?level=dong&metric=quiet&month=10")
    ids = [i["id"] for i in res.json()["items"]]
    assert ids == ["5182025300", "5115060500"]


def test_map_scores_bad_metric(client, data):
    res = client.get("/api/v1/map/scores?level=region&metric=crowd")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "BAD_REQUEST"


def test_tiers_custom_metrics(client, data):
    res = client.get("/api/v1/tiers?metric=value&metrics=quiet,ease,overnight&month=10")
    body = res.json()
    assert body["metrics"] == ["quiet", "ease", "overnight"]
    s_items = next(t for t in body["tiers"] if t["grade"] == "S")["items"]
    assert [i["name"] for i in s_items] == ["고성군"]
    assert s_items[0]["grades"]["overnight"] == "S"
    assert s_items[0]["parentName"] == "강원특별자치도"


def test_tiers_dong_region_only_metric_rejected(client, data):
    assert client.get("/api/v1/tiers?metric=ease&scope=dong").status_code == 400


# ───────────── 지역 상세 · 월별 ─────────────


def test_region_detail(client, data):
    res = client.get("/api/v1/regions/51820?month=10&preset=quiet")
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "고성군"
    assert body["score"] == 79
    assert body["presetRank"] == {"rank": 1, "total": 2}
    assert body["metrics"]["overnight"] == {"score": 82, "grade": "S"}
    assert body["stats"] == {"crowdIndex": 67, "quietRank": 1, "overnightCoef": 0.91}
    assert body["weekendGap"]["message"] == "주말에 낮 체류인구가 32% 늘어납니다."
    assert body["photos"] == {"featured": [], "totalCount": 0}


def test_region_not_found(client, data):
    assert client.get("/api/v1/regions/99999").status_code == 404


def test_region_months(client, data):
    body = client.get("/api/v1/regions/51820/months").json()
    assert len(body["items"]) == 12
    assert body["bestMonths"] == [2, 3, 4]
    assert body["avoidMonths"] == [7, 8, 10]
    assert body["stayMonth"]["month"] in (2, 3)


# ───────────── 동네 프로필 ─────────────


def test_dong_detail(client, data):
    res = client.get("/api/v1/dongs/5182025300?month=10")
    assert res.status_code == 200
    body = res.json()
    assert body["regionName"] == "고성군"
    assert body["metrics"]["quiet"] == {"score": 95, "grade": "S"}
    assert body["metrics"]["local_life"] == 0.8
    assert body["lifeType"] == "local"
    assert len(body["curves"]["weekday"]) == 24
    assert body["peaks"]["weekdayPeak"] == {"hour": 17, "index": 100}
    assert body["peaks"]["quietest"] == {"hour": 10, "index": 50}
    assert body["stats"]["quietTopPercent"] == 5
    assert body["rankInRegion"] == 1
    assert body["similar"] == []  # 테스트 DB 에는 dong_feature 가 없다
    assert body["location"]["note"] == "해안선까지 0.0 km"


def test_dong_excluded_is_hidden(client, data):
    # 수동면은 adm_code 가 없어 코드로 찾을 수 없다
    assert client.get("/api/v1/dongs/0000000000").status_code == 404


# ───────────── 자연어 검색 ─────────────


def test_parse_rule():
    parsed = engine.parse_rule("가을에 바닷가, 카페 많고 조용한 곳 추천해줘")
    assert parsed["month"] == 10
    assert "바닷가" in parsed["pieces"]


def test_understand_applies_threshold(monkeypatch):
    monkeypatch.setattr(
        engine, "parse", lambda q: ({"month": 10, "pieces": ["a", "b", "c"]}, "llm")
    )
    monkeypatch.setattr(
        engine,
        "match",
        lambda pieces: [
            {
                "piece": "a",
                "kind": "tag",
                "key": "coast",
                "direction": 1,
                "phrase": "바닷가",
                "sim": 0.94,
            },
            {
                "piece": "b",
                "kind": "metric",
                "key": "quiet",
                "direction": 1,
                "phrase": "조용한",
                "sim": 0.9,
            },
            {
                "piece": "c",
                "kind": "tag",
                "key": "library",
                "direction": 1,
                "phrase": "도서관",
                "sim": 0.6,
            },
        ],
    )
    cond = engine.understand("질문")
    assert cond.tags == ["coast"]
    assert cond.metrics == [("quiet", 1)]
    assert cond.unmatched == [{"text": "c", "similarity": 0.6}]
    assert engine.rank_query(cond) == "month=10&w_quiet=1&tags=coast"
    assert [c["label"] for c in engine.chips(cond)] == ["10월", "바닷가", "동네 조용함 높은 곳"]


def test_search_nl_response(client, monkeypatch):
    cond = engine.Conditions(month=10, tags=["coast"], metrics=[("quiet", 1)])
    monkeypatch.setattr(engine, "understand", lambda q: cond)
    monkeypatch.setattr(
        engine,
        "rank",
        lambda c, q: [
            {
                "rank": 1,
                "dongId": "5011067000",
                "name": "외도동",
                "regionId": "50110",
                "regionName": "제주시",
                "score": 99.0,
                "reason": "10월 동네 조용함 98점",
            }
        ],
    )
    res = client.post(
        "/api/v1/search/nl", {"query": "가을 바닷가"}, content_type="application/json"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["conditions"]["metrics"] == [{"key": "quiet", "direction": 1}]
    assert body["results"][0]["name"] == "외도동"


def test_search_nl_no_conditions(client, monkeypatch):
    monkeypatch.setattr(engine, "understand", lambda q: engine.Conditions())
    body = client.post(
        "/api/v1/search/nl", {"query": "오늘 저녁 뭐 먹지"}, content_type="application/json"
    ).json()
    assert body["results"] == [] and body["chips"] == [] and body["conditions"] == {"tags": []}


def test_search_nl_bad_query(client):
    res = client.post("/api/v1/search/nl", {"query": ""}, content_type="application/json")
    assert res.status_code == 400


def test_weighted_rounds_half_up():
    from metrics.scoring import weighted

    scores = {"quiet": {"score": 60}, "ease": {"score": 61}}
    assert weighted(scores, {"quiet": 1, "ease": 1}) == 61  # 60.5 → 61 (SQL round 와 같게)


# ───────────── 사진 ─────────────


@pytest.fixture
def photos(data):
    from django.db import connection

    rows = [
        ("region", 1, 10, "https://r2.example/a.webp", "한국관광공사 포토코리아 / 갑"),
        ("region", 1, 7, "https://r2.example/b.webp", "한국관광공사 포토코리아 / 을"),
        ("region", 1, None, "https://r2.example/c.webp", "한국관광공사 포토코리아 / 병"),
        ("dong", 101, 7, "https://r2.example/b.webp", "한국관광공사 포토코리아 / 을"),
        ("region", 2, 10, "https://r2.example/d.webp", "한국관광공사 포토코리아 / 정"),
    ]
    with connection.cursor() as cur:
        for scope, unit, month, url, credit in rows:
            cur.execute(
                "INSERT INTO photo (scope, unit_id, month, url, credit, source, status)"
                " VALUES (%s, %s, %s, %s, %s, 'tourapi', 'approved')",
                [scope, unit, month, url, credit],
            )


def test_region_photos_same_month_first(client, photos):
    body = client.get("/api/v1/regions/51820?month=10").json()
    urls = [p["url"] for p in body["photos"]["featured"]]
    assert urls[0] == "https://r2.example/a.webp"
    assert body["photos"]["totalCount"] == 3
    assert body["photos"]["featured"][0]["credit"] == "한국관광공사 포토코리아 / 갑"


def test_dong_photos_prefer_dong_and_dedupe(client, photos):
    body = client.get("/api/v1/dongs/5182025300?month=10").json()
    urls = [p["url"] for p in body["photos"]["featured"]]
    assert urls[0] == "https://r2.example/b.webp"  # 그 동 사진이 먼저 (달이 달라도)
    assert sorted(urls) == sorted(set(urls))  # 같은 사진이 시군구 · 동으로 두 번 나오지 않음
    assert body["photos"]["totalCount"] == 3


def test_tiers_item_photo(client, photos):
    body = client.get("/api/v1/tiers?metric=quiet&month=10").json()
    items = {i["name"]: i for t in body["tiers"] for i in t["items"]}
    assert items["강릉시"]["photo"]["url"] == "https://r2.example/d.webp"
