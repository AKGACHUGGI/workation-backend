"""Neo4j 읽기와 API 응답 모양 (스펙 §6). 읽기는 graphdb.read 한 트랜잭션에서 Cypher 몇 개.

fetch_* 는 Neo4j 레코드를 dict 로 돌려주고, shape_* 는 그 dict 를 웹 계약 모양으로 바꾼다(순수).
"""

from __future__ import annotations

from explore.graph import METRIC_KO, METRICS
from explore.graphdb import GraphUnavailable, read

EMPTY = "Neo4j 에 탐색 그래프가 없음: uv run python manage.py sync_graph 실행 필요"

META = (
    "MATCH (m:Meta) RETURN m.synced_at AS synced_at,"
    " m.dong_count AS dong_count, m.region_count AS region_count"
)

PINS = """
MATCH (r:Region {id: 'region:' + $code})
OPTIONAL MATCH (d:Dong)-[:IN]->(r)
RETURN r.code AS code, collect(d {
  .code, .name, .lat, .lng,
  tags: [(d)-[:HAS_TAG]->(t:PoiTag) | [t.order, t.code]],
  top: [(d)-[f:FITS {rank: 1}]->(ty:Type) | [ty.name, f.score]],
  near: [(d)-[:NEAR]->(p:Place) | p.id],
  life: [(d)-[:LIFE]->(t:Tag) | t.name],
  grades: [(d)-[:HAS_GRADE]->(g:Grade) | [g.metric, g.grade]]
}) AS dongs
"""

DONG = """
MATCH (d:Dong {id: 'dong:' + $code})-[:IN]->(r:Region)-[:IN]->(s:Sido)
RETURN d {.code, .name, .coast_km, .driv_weekday, .driv_weekend} AS dong,
  r {.code, .name} AS region, s.name AS sido,
  [(d)-[f:FITS]->(t:Type) | [f.rank, t.name, f.score, f.reason]] AS types,
  [(d)-[g:HAS_GRADE]->(x:Grade) |
    [x.metric, x.grade, g.score, g.rank, g.of, g.rank_sgg, g.of_sgg]] AS grades,
  [(d)-[h:QUIET_AT|PEAK_AT]->(x:Hour) | [type(h), h.day, x.h]] AS hours,
  [(d)-[n:NEAR]->(:Place) | n.km] AS near,
  [(r)-[e:EASE]->(m:Month) | [m.m, e.raw, e.quiet]] AS months,
  [(d)-[s2:SIMILAR]->(o:Dong)-[:IN]->(o_r:Region) | [s2.rank, o.code, o.name, o_r.name]]
    AS similar
"""

# 공통점이 되는 사실 노드 (스펙 §6-3): 등급 · 1위 유형 · 한산 시각(day 별) · 바닷가 · 생활 · 시군구
FACTS = """
UNWIND $codes AS c
MATCH (d:Dong {id: 'dong:' + c})-[:IN]->(r:Region)
RETURN d.code AS code,
  [(d)-[g:HAS_GRADE]->(x:Grade) |
    {metric: x.metric, grade: x.grade, score: g.score, degree: x.degree}] AS grades,
  [(d)-[:FITS {rank: 1}]->(t:Type) | {id: t.id, name: t.name, degree: t.degree}] AS top,
  [(d)-[q:QUIET_AT]->(h:Hour) | {day: q.day, h: h.h,
    degree: CASE q.day WHEN 'weekday' THEN h.degree_weekday ELSE h.degree_weekend END}] AS quiet,
  [(d)-[:NEAR]->(p:Place) | {id: p.id, kind: p.kind, degree: p.degree}] AS near,
  [(d)-[l:LIFE]->(t:Tag) | {id: t.id, name: t.name, degree: t.degree, value: l.value}] AS life,
  r {.id, .name, .degree} AS region
"""

DAY_KO = {"weekday": "평일", "weekend": "주말"}


def _meta(tx) -> dict:
    meta = tx.run(META).single()
    if meta is None:
        raise GraphUnavailable(EMPTY)
    return meta.data()


def _pins_work(tx, code):
    _meta(tx)
    rec = tx.run(PINS, code=code).single()
    return None if rec is None else rec.data()


def _dong_work(tx, code):
    meta = _meta(tx)
    rec = tx.run(DONG, code=code).single()
    if rec is None:
        return None
    rec = rec.data()
    codes = [code] + [row[1] for row in rec["similar"]]
    facts = {r["code"]: r.data() for r in tx.run(FACTS, codes=codes)}
    return {"rec": rec, "facts": facts, **meta}


def fetch_pins(code: str) -> dict | None:
    """시군구 핀 응답. 없는 시군구면 None. Neo4j 문제는 GraphUnavailable."""
    rec = read(_pins_work, code=code)
    return None if rec is None else shape_pins(rec)


def fetch_dong(code: str) -> dict | None:
    """동 상세 원재료 {"rec", "facts": {code: row}, "synced_at", "dong_count", "region_count"}.

    없는 동이면 None. 응답 모양은 shape_dong, 브리핑 사실은 brief.collect_facts 가 만든다."""
    return read(_dong_work, code=code)


def shape_pins(rec: dict) -> dict:
    dongs = []
    for d in sorted(rec["dongs"], key=lambda d: d["name"]):
        grades = dict(d["grades"])
        top = d["top"][0] if d["top"] else [None, None]
        dongs.append(
            {
                "id": d["code"],
                "name": d["name"],
                "center": {"lat": d["lat"], "lng": d["lng"]},
                "tags": [code for _, code in sorted(d["tags"])],
                "topType": top[0],
                "topScore": top[1],
                "coastal": bool(d["near"]),
                "life": d["life"][0] if d["life"] else None,
                "grades": {"quiet": grades.get("quiet"), "evening": grades.get("evening")},
            }
        )
    return {"sigunguId": rec["code"], "dongs": dongs}


def fact_list(row: dict) -> dict[str, tuple[str, int]]:
    """FACTS 한 행 → {사실 key: (문구, 이어진 동 수)}. 문구는 웹 commonTop 에 그대로 나간다."""
    out = {}
    for g in row["grades"]:
        out[f"grade:{g['metric']}:{g['grade']}"] = (
            f"{METRIC_KO[g['metric']]} {g['grade']}",
            g["degree"],
        )
    for t in row["top"]:
        out[t["id"]] = (f"1위 {t['name']}", t["degree"])
    for q in row["quiet"]:
        out[f"quiet:{q['day']}:{q['h']}"] = (f"{DAY_KO[q['day']]} {q['h']}시 한산", q["degree"])
    for p in row["near"]:
        out[p["id"]] = (p["kind"], p["degree"])
    for t in row["life"]:
        out[t["id"]] = (t["name"], t["degree"])
    r = row["region"]
    out[r["id"]] = (f"같은 {r['name']}", r["degree"])
    return out


def common_facts(mine: dict, other: dict) -> list[tuple[str, str]]:
    """두 동이 함께 가진 사실 [(key, 문구)]. 이어진 동이 적은(드문) 사실부터, 같으면 key 순."""
    keys = sorted(set(mine) & set(other), key=lambda k: (mine[k][1], k))
    return [(k, mine[k][0]) for k in keys]


def scores_of(row: dict) -> dict[str, int]:
    return {g["metric"]: g["score"] for g in row["grades"]}


def score_diff(mine: dict[str, int], other: dict[str, int]) -> dict[str, int | None]:
    """상대 − 이 동. 한쪽이라도 없으면 null."""
    return {m: (other[m] - mine[m]) if m in mine and m in other else None for m in METRICS}


def _driv(values: list | None) -> list[int | None]:
    if not values:
        return [None] * 24
    return [None if v < 0 else v for v in values]


def shape_dong(raw: dict, briefing: list[str]) -> dict:
    rec = raw["rec"]
    d, region = rec["dong"], rec["region"]
    me = raw["facts"][d["code"]]
    mine = fact_list(me)
    hours = {(rel, day): h for rel, day, h in rec["hours"]}
    grades = {g[0]: {"grade": g[1], "score": g[2]} for g in rec["grades"]}
    month_raw = {m: v for m, v, _ in rec["months"]}
    similar = []
    for _, code, name, sigungu in sorted(rec["similar"]):
        other = raw["facts"].get(code)
        if other is None:
            continue
        common = common_facts(mine, fact_list(other))
        similar.append(
            {
                "id": code,
                "name": name,
                "sigungu": sigungu,
                "common": len(common),
                "commonTop": [label for _, label in common[:3]],
                "diff": score_diff(scores_of(me), scores_of(other)),
            }
        )
    return {
        "id": d["code"],
        "name": d["name"],
        "sigungu": {"id": region["code"], "name": region["name"]},
        "sido": rec["sido"],
        "coastKm": d["coast_km"],
        "types": [
            {"name": name, "score": score, "reason": reason}
            for _, name, score, reason in sorted(rec["types"])
        ],
        "grades": {m: grades.get(m) for m in METRICS},
        "hourly": {
            day: {
                "driv": _driv(d[f"driv_{day}"]),
                "peakHour": hours.get(("PEAK_AT", day)),
                "quietHour": hours.get(("QUIET_AT", day)),
            }
            for day in ("weekday", "weekend")
        },
        "months": {
            "raw": [month_raw.get(m) for m in range(1, 13)],
            "quiet": sorted(m for m, _, quiet in rec["months"] if quiet),
            "basis": region["name"],
        },
        "briefing": briefing,
        "similar": similar,
        "syncedAt": raw["synced_at"],
    }
