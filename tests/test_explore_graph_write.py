"""explore/graph_write.py · sync_graph 명령. 실제 Neo4j 대신 쿼리를 기록하는 가짜 드라이버."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from neo4j.exceptions import ServiceUnavailable

from explore import graph, graph_write, graphdb, sources
from tests.explore_samples import sample_graph_input


class FakeResult(list):
    def consume(self):
        return None


class FakeTx:
    def __init__(self, driver):
        self.driver = driver

    def run(self, query, **params):
        self.driver.log.append((query, params))
        if query in (graph_write.COUNT_NODES, graph_write.COUNT_RELS):
            return FakeResult(self.driver.answers.pop(0))
        return FakeResult()


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.driver.schema.append(query)
        return FakeResult()

    def execute_write(self, work):
        if self.driver.fail:
            raise self.driver.fail
        return work(FakeTx(self.driver))

    def execute_read(self, work, **params):
        return work(FakeTx(self.driver), **params)


class FakeDriver:
    """개수 쿼리(COUNT_NODES · COUNT_RELS)는 answers 를 앞에서부터 하나씩 돌려준다."""

    def __init__(self, answers=None, fail=None):
        self.log, self.schema, self.answers, self.fail = [], [], list(answers or []), fail

    def session(self):
        return FakeSession(self)


def counts_as_rows(counts):
    return [
        [{"key": k, "cnt": n} for k, n in counts["nodes"].items()],
        [{"key": k, "cnt": n} for k, n in counts["rels"].items()],
    ]


def test_write_graph_query_order():
    g = graph.build_graph(sample_graph_input())
    driver = FakeDriver()
    graph_write.write_graph(driver, g, "sid1", "2026-11-13T10:00:00+09:00")
    queries = [q for q, _ in driver.log]
    first_rel = next(i for i, q in enumerate(queries) if "MERGE (a)-" in q)
    assert all("MERGE (n:" in q for q in queries[:first_rel])
    assert queries[-3:] == [
        graph_write.DELETE_OLD_RELS,
        graph_write.DELETE_OLD_NODES,
        graph_write.MERGE_META,
    ]
    assert all(p["sid"] == "sid1" for _, p in driver.log)
    meta = driver.log[-1][1]
    assert meta["synced_at"] == "2026-11-13T10:00:00+09:00"
    assert (meta["dong_count"], meta["data_year"], meta["region_count"]) == (4, 2025, 3)


def test_write_graph_rows_and_keys():
    g = graph.build_graph(sample_graph_input())
    driver = FakeDriver()
    graph_write.write_graph(driver, g, "sid1", "t")
    dong_q = graph_write.node_query("Dong")
    rows = next(p["rows"] for q, p in driver.log if q == dong_q)
    assert {r["id"] for r in rows} == {
        "dong:5115025000", "dong:5115037000", "dong:5121057000", "dong:5211357500",
    }  # fmt: skip
    quiet_q = graph_write.rel_query("QUIET_AT", "Dong", "Hour", ("day",))
    assert "MERGE (a)-[r:QUIET_AT {day: row.props.day}]->(b)" in quiet_q
    quiet_rows = next(p["rows"] for q, p in driver.log if q == quiet_q)
    assert {"src": "dong:5115025000", "dst": "hour:3", "props": {"day": "weekend"}} in quiet_rows


def test_write_graph_batches_by_1000(monkeypatch):
    monkeypatch.setattr(graph_write, "BATCH", 5)
    g = graph.build_graph(sample_graph_input())
    driver = FakeDriver()
    graph_write.write_graph(driver, g, "sid1", "t")
    hour_q = graph_write.node_query("Hour")
    assert [len(p["rows"]) for q, p in driver.log if q == hour_q] == [5, 5, 5, 5, 4]


def test_ensure_constraints_one_per_label():
    driver = FakeDriver()
    graph_write.ensure_constraints(driver)
    assert len(driver.schema) == len(graph.LABELS)
    assert (
        "CREATE CONSTRAINT dong_id IF NOT EXISTS FOR (n:Dong) REQUIRE n.id IS UNIQUE"
        in driver.schema
    )


def test_compare_counts_lists_differences():
    expected = {"nodes": {"Dong": 203, "Meta": 1}, "rels": {"IN": 215}}
    actual = {"nodes": {"Dong": 202, "Meta": 1}, "rels": {"IN": 215, "OLD": 3}}
    assert graph_write.compare_counts(expected, actual) == [
        "노드 Dong: 기대 203, 실제 202",
        "관계 OLD: 기대 0, 실제 3",
    ]


def test_sync_graph_command_ok(monkeypatch):
    g = graph.build_graph(sample_graph_input())
    driver = FakeDriver(answers=counts_as_rows(graph.expected_counts(g)))
    monkeypatch.setattr(sources, "load_graph_input", sample_graph_input)
    monkeypatch.setattr(graphdb, "get_driver", lambda: driver)
    out = StringIO()
    call_command("sync_graph", stdout=out)
    text = out.getvalue()
    assert "Dong 4" in text and "SIMILAR 6" in text and "개수 일치" in text
    assert driver.schema  # 제약 먼저


def test_sync_graph_command_count_mismatch_fails(monkeypatch):
    g = graph.build_graph(sample_graph_input())
    counts = graph.expected_counts(g)
    counts["nodes"]["Dong"] = 3
    driver = FakeDriver(answers=counts_as_rows(counts))
    monkeypatch.setattr(sources, "load_graph_input", sample_graph_input)
    monkeypatch.setattr(graphdb, "get_driver", lambda: driver)
    with pytest.raises(CommandError, match="노드 Dong: 기대 4, 실제 3"):
        call_command("sync_graph", stdout=StringIO())


def test_sync_graph_command_neo4j_down(monkeypatch):
    driver = FakeDriver(fail=ServiceUnavailable("refused"))
    monkeypatch.setattr(sources, "load_graph_input", sample_graph_input)
    monkeypatch.setattr(graphdb, "get_driver", lambda: driver)
    with pytest.raises(CommandError, match="Neo4j 쓰기 실패"):
        call_command("sync_graph", stdout=StringIO())


def test_get_driver_without_uri(settings):
    settings.NEO4J_URI = ""
    with pytest.raises(graphdb.GraphUnavailable, match="NEO4J_URI"):
        graphdb.get_driver()


def test_read_wraps_driver_errors(monkeypatch):
    class DownSession(FakeSession):
        def execute_read(self, work, **params):
            raise ServiceUnavailable("refused")

    class DownDriver(FakeDriver):
        def session(self):
            return DownSession(self)

    monkeypatch.setattr(graphdb, "get_driver", lambda: DownDriver())
    with pytest.raises(graphdb.GraphUnavailable, match="refused"):
        graphdb.read(lambda tx: None)
