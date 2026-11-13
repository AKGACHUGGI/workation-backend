"""GET /api/explore/sigungu/{id}/pins · /api/explore/dongs/{id}.
queries · sources 함수를 바꿔 끼워 Neo4j · DB 없이 본다."""

import pytest
from django.core.cache import cache

from explore import queries, sources
from explore.graphdb import GraphUnavailable
from tests.explore_samples import jumunjin_raw
from tests.test_explore_queries import PINS_REC

PINS = queries.shape_pins(PINS_REC)
UNAVAILABLE = {
    "error": {"code": "GRAPH_UNAVAILABLE", "message": "탐색 데이터를 불러오지 못했어요."}
}


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def no_briefing(monkeypatch):
    monkeypatch.setattr(sources, "fetch_briefing", lambda code: [])


def down(*args):
    raise GraphUnavailable("connection refused")


def test_pins(client, monkeypatch):
    monkeypatch.setattr(queries, "fetch_pins", lambda code: PINS if code == "51150" else None)
    res = client.get("/api/explore/sigungu/51150/pins")
    assert res.status_code == 200
    jumunjin = next(d for d in res.json()["dongs"] if d["id"] == "5115025000")
    assert (jumunjin["topType"], jumunjin["topScore"], jumunjin["coastal"]) == (
        "장기체류형",
        61,
        True,
    )
    assert jumunjin["life"] == "생활권" and jumunjin["grades"] == {"quiet": "B", "evening": "A"}


def test_pins_not_found_is_not_cached(client, monkeypatch):
    calls = []
    monkeypatch.setattr(queries, "fetch_pins", lambda code: calls.append(code))
    res = client.get("/api/explore/sigungu/99999/pins")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "SIGUNGU_NOT_FOUND"
    client.get("/api/explore/sigungu/99999/pins")
    assert calls == ["99999", "99999"]


def test_dong_detail_with_briefing(client, monkeypatch):
    monkeypatch.setattr(queries, "fetch_dong", lambda code: jumunjin_raw())
    monkeypatch.setattr(sources, "fetch_briefing", lambda code: ["가", "나", "다"])
    body = client.get("/api/explore/dongs/5115025000").json()
    assert body["name"] == "주문진읍" and body["briefing"] == ["가", "나", "다"]
    assert body["types"][0] == {
        "name": "장기체류형",
        "score": 61,
        "reason": "숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달)",
    }
    assert body["months"]["quiet"] == [2, 4, 9]
    assert body["similar"][0]["commonTop"] == ["조용함 B", "1위 장기체류형", "바닷가"]


def test_dong_without_briefing_row(client, monkeypatch, no_briefing):
    monkeypatch.setattr(queries, "fetch_dong", lambda code: jumunjin_raw())
    assert client.get("/api/explore/dongs/5115025000").json()["briefing"] == []


def test_dong_not_found(client, monkeypatch, no_briefing):
    monkeypatch.setattr(queries, "fetch_dong", lambda code: None)
    res = client.get("/api/explore/dongs/0000000000")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DONG_NOT_FOUND"


def test_cache_hit_skips_neo4j(client, monkeypatch, no_briefing):
    calls = []
    monkeypatch.setattr(queries, "fetch_dong", lambda code: calls.append(code) or jumunjin_raw())
    client.get("/api/explore/dongs/5115025000")
    client.get("/api/explore/dongs/5115025000")
    assert calls == ["5115025000"]


def test_neo4j_down_serves_last_response(client, monkeypatch):
    monkeypatch.setattr(queries, "fetch_pins", lambda code: PINS)
    client.get("/api/explore/sigungu/51150/pins")
    cache.delete("explore:pins:51150")  # 1시간 캐시가 만료된 상황
    monkeypatch.setattr(queries, "fetch_pins", down)
    res = client.get("/api/explore/sigungu/51150/pins")
    assert res.status_code == 200 and res.json() == PINS


def test_neo4j_down_without_last_is_503(client, monkeypatch, no_briefing):
    monkeypatch.setattr(queries, "fetch_pins", down)
    monkeypatch.setattr(queries, "fetch_dong", down)
    for url in ("/api/explore/sigungu/51150/pins", "/api/explore/dongs/5115025000"):
        res = client.get(url)
        assert res.status_code == 503 and res.json() == UNAVAILABLE


def test_response_is_gzipped(client, monkeypatch, no_briefing):
    monkeypatch.setattr(queries, "fetch_dong", lambda code: jumunjin_raw())
    res = client.get("/api/explore/dongs/5115025000", HTTP_ACCEPT_ENCODING="gzip")
    assert res["Content-Encoding"] == "gzip"
