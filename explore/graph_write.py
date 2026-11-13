"""Neo4j 쓰기 (manage.py sync_graph 전용). 서비스 코드에서 import 하지 않는다.

한 쓰기 트랜잭션에서 MERGE → 이번 sync_id 가 아닌 관계·노드 삭제 → Meta 갱신.
중간에 실패하면 트랜잭션이 되돌아가 이전 그래프가 그대로 남는다 (스펙 §5-3).
"""

from __future__ import annotations

from collections import defaultdict

from explore.graph import LABELS, Graph, label_of

BATCH = 1000

COUNT_NODES = "MATCH (n) RETURN labels(n)[0] AS key, count(*) AS cnt"
COUNT_RELS = "MATCH ()-[r]->() RETURN type(r) AS key, count(*) AS cnt"
DELETE_OLD_RELS = "MATCH ()-[r]->() WHERE coalesce(r.sync_id, '') <> $sid DELETE r"
DELETE_OLD_NODES = "MATCH (n) WHERE NOT n:Meta AND coalesce(n.sync_id, '') <> $sid DETACH DELETE n"
MERGE_META = (
    "MERGE (m:Meta {id: 'meta'})"
    " SET m.synced_at = $synced_at, m.data_year = $data_year,"
    " m.dong_count = $dong_count, m.region_count = $region_count, m.sync_id = $sid"
)


def _chunks(rows: list):
    for i in range(0, len(rows), BATCH):
        yield rows[i : i + BATCH]


def node_query(label: str) -> str:
    return f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n = row, n.sync_id = $sid"


def rel_query(rel_type: str, src_label: str, dst_label: str, key: tuple[str, ...]) -> str:
    match_props = ", ".join(f"{k}: row.props.{k}" for k in key)
    rel = f"[r:{rel_type} {{{match_props}}}]" if key else f"[r:{rel_type}]"
    return (
        "UNWIND $rows AS row"
        f" MATCH (a:{src_label} {{id: row.src}})"
        f" MATCH (b:{dst_label} {{id: row.dst}})"
        f" MERGE (a)-{rel}->(b)"
        " SET r = row.props, r.sync_id = $sid"
    )


def ensure_constraints(driver) -> None:
    """라벨마다 id 유일 제약. 스키마 변경이라 데이터 쓰기 트랜잭션과 따로 실행한다."""
    with driver.session() as session:
        for label in LABELS:
            session.run(
                f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS"
                f" FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            ).consume()


def write_graph(driver, graph: Graph, sync_id: str, synced_at: str) -> None:
    nodes: dict[str, list[dict]] = defaultdict(list)
    for n in graph.nodes:
        nodes[n.label].append({"id": n.id, **n.props})
    rels: dict[tuple, list[dict]] = defaultdict(list)
    for r in graph.rels:
        group = (r.type, label_of(r.src), label_of(r.dst), r.key)
        rels[group].append({"src": r.src, "dst": r.dst, "props": r.props})

    def work(tx):
        for label, rows in nodes.items():
            for chunk in _chunks(rows):
                tx.run(node_query(label), rows=chunk, sid=sync_id).consume()
        for group, rows in rels.items():
            for chunk in _chunks(rows):
                tx.run(rel_query(*group), rows=chunk, sid=sync_id).consume()
        tx.run(DELETE_OLD_RELS, sid=sync_id).consume()
        tx.run(DELETE_OLD_NODES, sid=sync_id).consume()
        tx.run(MERGE_META, sid=sync_id, synced_at=synced_at, **graph.meta).consume()

    with driver.session() as session:
        session.execute_write(work)


def count_graph(driver) -> dict[str, dict[str, int]]:
    def work(tx):
        nodes = {r["key"]: r["cnt"] for r in tx.run(COUNT_NODES)}
        rels = {r["key"]: r["cnt"] for r in tx.run(COUNT_RELS)}
        return {"nodes": nodes, "rels": rels}

    with driver.session() as session:
        return session.execute_read(work)


def compare_counts(expected: dict, actual: dict) -> list[str]:
    """다른 항목만 '노드 Dong: 기대 203, 실제 202' 형식으로."""
    out = []
    for kind, ko in (("nodes", "노드"), ("rels", "관계")):
        for key in sorted(set(expected[kind]) | set(actual[kind])):
            exp, act = expected[kind].get(key, 0), actual[kind].get(key, 0)
            if exp != act:
                out.append(f"{ko} {key}: 기대 {exp}, 실제 {act}")
    return out
