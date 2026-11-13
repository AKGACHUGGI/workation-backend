"""실제 Neo4j 에 그래프를 쓰고 Cypher 로 읽어 본다. NEO4J_TEST_URI 가 없으면 건너뛴다.

docker compose up -d --wait neo4j
NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest -m neo4j -v

주의: 그 Neo4j 의 그래프를 테스트 그래프로 바꾼다.
개발 그래프가 필요하면 끝난 뒤 sync_graph 를 다시 실행한다.
"""

import dataclasses
import os

import pytest

from explore import graph, graph_write, graphdb, queries
from tests.explore_samples import sample_graph_input

URI = os.environ.get("NEO4J_TEST_URI", "")
pytestmark = [pytest.mark.neo4j, pytest.mark.skipif(not URI, reason="NEO4J_TEST_URI 없음")]


@pytest.fixture
def driver(settings):
    settings.NEO4J_URI = URI
    settings.NEO4J_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
    settings.NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "localpassword")
    d = graphdb.get_driver()
    graph_write.ensure_constraints(d)
    return d


def sync(driver, inp, sync_id):
    g = graph.build_graph(inp)
    graph_write.write_graph(driver, g, sync_id, "2026-11-13T10:00:00+09:00")
    return g


def test_sync_twice_same_counts(driver):
    g = sync(driver, sample_graph_input(), "first")
    first = graph_write.count_graph(driver)
    assert graph_write.compare_counts(graph.expected_counts(g), first) == []
    sync(driver, sample_graph_input(), "second")
    assert graph_write.count_graph(driver) == first


def test_removed_dong_disappears(driver):
    inp = sample_graph_input()
    sync(driver, inp, "first")
    fewer = dataclasses.replace(inp, dongs=[d for d in inp.dongs if d.dong_id != 112])
    g = sync(driver, fewer, "second")
    counts = graph_write.count_graph(driver)
    assert counts["nodes"]["Dong"] == 3
    assert graph_write.compare_counts(graph.expected_counts(g), counts) == []
    assert queries.fetch_dong("5115037000") is None


def test_pins_with_real_cypher(driver):
    sync(driver, sample_graph_input(), "first")
    pins = queries.fetch_pins("51150")
    assert [d["name"] for d in pins["dongs"]] == ["연곡면", "주문진읍"]
    jumunjin = pins["dongs"][1]
    assert jumunjin["tags"] == ["coast", "cafe", "resort", "beach", "park", "market"]
    assert (jumunjin["topType"], jumunjin["topScore"]) == ("로컬살이형", 60)
    assert (jumunjin["coastal"], jumunjin["life"]) == (True, "생활권")
    assert jumunjin["grades"] == {"quiet": "B", "evening": "A"}
    assert jumunjin["center"] == {"lat": 37.88818, "lng": 128.75354}
    assert queries.fetch_pins("99999") is None


def test_dong_with_real_cypher(driver):
    sync(driver, sample_graph_input(), "first")
    raw = queries.fetch_dong("5115025000")
    assert raw["facts"]["5115025000"]["life"][0]["value"] == 0.7365
    assert (raw["dong_count"], raw["region_count"]) == (4, 3)
    body = queries.shape_dong(raw, [])
    assert body["sido"] == "강원특별자치도" and body["coastKm"] == 0.0
    assert [t["name"] for t in body["types"]] == ["로컬살이형", "네트워킹형", "몰입형"]
    assert body["grades"]["evening"] == {"grade": "A", "score": 62}
    assert body["hourly"]["weekday"]["peakHour"] == 12
    assert body["hourly"]["weekend"]["quietHour"] == 3
    assert body["months"]["quiet"] == [2, 4, 9] and body["months"]["raw"][7] == 100.0
    nohak, yeongok = body["similar"]
    # 노학동: 조용함 B · 1위 로컬살이형 · 평일 9시 · 주말 3시 · 바닷가 · 생활권
    assert (nohak["name"], nohak["common"]) == ("노학동", 6)
    assert nohak["commonTop"] == ["조용함 B", "1위 로컬살이형", "생활권"]  # degree 2 → 3(key 순)
    assert nohak["diff"] == {"quiet": 8, "peer": 13, "evening": -15}
    # 연곡면: 평일 9시 · 주말 3시 · 바닷가 · 같은 강릉시 (1위 유형이 다름)
    assert (yeongok["name"], yeongok["common"]) == ("연곡면", 4)
    assert queries.fetch_dong("0000000000") is None
