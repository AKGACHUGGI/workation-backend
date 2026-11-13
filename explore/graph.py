"""탐색 v6 그래프 모양 (순수). Postgres 에서 읽은 GraphInput → Neo4j 노드·관계 목록 (스펙 §3).

Neo4j 를 모른다. 쓰기는 explore/graph_write.py, 읽기는 explore/queries.py.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from explore import derive
from webapi.data import TAG_KO

# 핀 아이콘 태그 순서 (v5 §3). food 는 넣지 않는다
POI_TAGS = (
    "coast", "city", "cafe", "library", "long_stay", "resort",
    "beach", "park", "mountain", "island", "surf", "market",
)  # fmt: skip
METRICS = ("quiet", "peer", "evening")
METRIC_KO = {"quiet": "조용함", "peer": "또래 밀도", "evening": "저녁 활기"}
GRADES = ("S", "A", "B", "C")
DAYS = ("weekday", "weekend")
LIFE_KO = {"local": "생활권", "tourist": "관광지"}
# 노드 id 앞부분 → 라벨
PREFIX_LABEL = {
    "sido": "Sido",
    "region": "Region",
    "dong": "Dong",
    "grade": "Grade",
    "type": "Type",
    "hour": "Hour",
    "month": "Month",
    "place": "Place",
    "life": "Tag",
    "poi": "PoiTag",
    "meta": "Meta",
}
LABELS = tuple(PREFIX_LABEL.values())


def label_of(node_id: str) -> str:
    return PREFIX_LABEL[node_id.split(":", 1)[0]]


@dataclass(frozen=True)
class Node:
    label: str
    id: str
    props: dict


@dataclass(frozen=True)
class Rel:
    type: str
    src: str
    dst: str
    props: dict = field(default_factory=dict)
    key: tuple[str, ...] = ()  # MERGE 기준 속성. 같은 두 노드 사이에 day 별로 따로 둘 때


@dataclass(frozen=True)
class Graph:
    nodes: list[Node]
    rels: list[Rel]
    meta: dict  # Meta 노드 속성 (synced_at 은 쓰기 때 붙인다)


@dataclass(frozen=True)
class RegionRow:
    region_id: int
    code: str  # sigungu_code 5자리
    name: str
    sido: str


@dataclass(frozen=True)
class DongRow:
    dong_id: int
    code: str  # adm_code 10자리
    name: str
    region_id: int
    lat: float | None
    lng: float | None
    coast_km: float | None
    tags: list[str]
    local_life: float


@dataclass(frozen=True)
class GraphInput:
    regions: list[RegionRow]
    dongs: list[DongRow]
    grades: dict[int, dict[str, tuple[str, int, float]]]  # 동 → 지표 → (등급, 점수, 원값)
    types: dict[int, list[derive.TypeScore]]  # dong_type_score, rank 순
    hours: dict[
        int, dict[str, tuple[list, int | None, int | None]]
    ]  # 동 → day → (driv, peak, quiet)
    similar: dict[int, list[tuple[int, float]]]  # dong_similar, rank 순 [(other_id, l2)]
    ease: dict[int, dict[int, float]]  # 시군구 → 1~12월 → ease 원값
    data_year: int


def _driv_prop(driv: list | None) -> list[int]:
    """Neo4j 리스트에는 null 을 못 넣는다 → 반올림 정수, 값 없음 = -1."""
    if not driv:
        return [-1] * 24
    return [-1 if v is None else round(v) for v in driv]


def _grade_ranks(inp: GraphInput, dongs: list[DongRow]) -> dict[tuple[int, str], dict]:
    """원값 기준 전국·시군구 안 순위. quiet 는 낮을수록 1위."""
    region_of = {d.dong_id: d.region_id for d in dongs}
    out = {}
    for m in METRICS:
        raws = {d: g[m][2] for d, g in inp.grades.items() if m in g and d in region_of}
        desc = m != "quiet"
        for d, v in raws.items():
            same = [x for o, x in raws.items() if region_of[o] == region_of[d]]
            out[(d, m)] = {
                "rank": derive.rank_of(list(raws.values()), v, desc),
                "of": len(raws),
                "rank_sgg": derive.rank_of(same, v, desc),
                "of_sgg": len(same),
            }
    return out


def build_graph(inp: GraphInput) -> Graph:
    regions = {r.region_id: r for r in inp.regions}
    dongs = sorted((d for d in inp.dongs if d.region_id in regions), key=lambda d: d.dong_id)
    dong_key = {d.dong_id: f"dong:{d.code}" for d in dongs}
    ranks = _grade_ranks(inp, dongs)
    nodes: list[Node] = []
    rels: list[Rel] = []

    for code, name in sorted({(r.code[:2], r.sido) for r in regions.values()}):
        nodes.append(Node("Sido", f"sido:{code}", {"name": name}))
    for r in sorted(regions.values(), key=lambda r: r.code):
        rels.append(Rel("IN", f"region:{r.code}", f"sido:{r.code[:2]}"))
        quiet = set(derive.quiet_months(inp.ease.get(r.region_id, {})))
        for m, raw in sorted(inp.ease.get(r.region_id, {}).items()):
            rels.append(
                Rel(
                    "EASE",
                    f"region:{r.code}",
                    f"month:{m}",
                    {"raw": round(raw, 1), "quiet": m in quiet},
                )
            )

    for d in dongs:
        me = dong_key[d.dong_id]
        hours = inp.hours.get(d.dong_id, {})
        nodes.append(
            Node(
                "Dong",
                me,
                {
                    "code": d.code,
                    "name": d.name,
                    "lat": d.lat,
                    "lng": d.lng,
                    "coast_km": d.coast_km,
                    "driv_weekday": _driv_prop(hours.get("weekday", (None,))[0]),
                    "driv_weekend": _driv_prop(hours.get("weekend", (None,))[0]),
                    "local_life": round(d.local_life, 4),
                    "pg_id": d.dong_id,
                },
            )
        )
        rels.append(Rel("IN", me, f"region:{regions[d.region_id].code}"))
        for m in METRICS:
            g = inp.grades.get(d.dong_id, {}).get(m)
            if g is not None:
                props = {"score": g[1], **ranks[(d.dong_id, m)]}
                rels.append(Rel("HAS_GRADE", me, f"grade:{m}:{g[0]}", props))
        for t in inp.types.get(d.dong_id, []):
            props = {"score": t.score, "rank": t.rank, "reason": t.reason}
            rels.append(Rel("FITS", me, f"type:{t.type}", props))
        for day in DAYS:
            _, peak, quiet = hours.get(day, (None, None, None))
            if quiet is not None:
                rels.append(Rel("QUIET_AT", me, f"hour:{quiet}", {"day": day}, ("day",)))
            if peak is not None:
                rels.append(Rel("PEAK_AT", me, f"hour:{peak}", {"day": day}, ("day",)))
        if derive.is_coastal(d.coast_km):
            rels.append(Rel("NEAR", me, "place:coast", {"km": d.coast_km}))
        life = derive.life_tag(d.local_life)
        rels.append(Rel("LIFE", me, f"life:{life}", {"value": round(d.local_life, 4)}))
        for t in POI_TAGS:
            if t in d.tags:
                rels.append(Rel("HAS_TAG", me, f"poi:{t}"))
        for rank, (other, l2) in enumerate(inp.similar.get(d.dong_id, []), start=1):
            if other in dong_key:
                props = {"rank": rank, "l2": round(l2, 4)}
                rels.append(Rel("SIMILAR", me, dong_key[other], props))

    # 사실 노드의 degree = 이어진 동 수. 공통점 정렬(스펙 §6-3)과 브리핑 흔함에 쓴다
    degree: Counter[str] = Counter()
    for r in rels:
        if not r.src.startswith("dong:"):
            continue
        if r.type in ("IN", "HAS_GRADE", "NEAR", "LIFE") or (
            r.type == "FITS" and r.props["rank"] == 1
        ):
            degree[r.dst] += 1
        elif r.type == "QUIET_AT":
            degree[f"{r.dst}:{r.props['day']}"] += 1

    for r in sorted(regions.values(), key=lambda r: r.code):
        rid = f"region:{r.code}"
        nodes.append(Node("Region", rid, {"code": r.code, "name": r.name, "degree": degree[rid]}))
    for m in METRICS:
        for g in GRADES:
            gid = f"grade:{m}:{g}"
            props = {"metric": m, "grade": g, "label": f"{METRIC_KO[m]} {g}", "degree": degree[gid]}
            nodes.append(Node("Grade", gid, props))
    for t in derive.TYPES:
        tid = f"type:{t}"
        nodes.append(Node("Type", tid, {"name": derive.TYPE_KO[t], "degree": degree[tid]}))
    for h in range(24):
        hid = f"hour:{h}"
        props = {f"degree_{day}": degree[f"{hid}:{day}"] for day in DAYS}
        nodes.append(Node("Hour", hid, {"h": h, **props}))
    for m in range(1, 13):
        nodes.append(Node("Month", f"month:{m}", {"m": m}))
    nodes.append(Node("Place", "place:coast", {"kind": "바닷가", "degree": degree["place:coast"]}))
    for key, name in LIFE_KO.items():
        lid = f"life:{key}"
        nodes.append(Node("Tag", lid, {"name": name, "degree": degree[lid]}))
    for order, t in enumerate(POI_TAGS):
        nodes.append(Node("PoiTag", f"poi:{t}", {"code": t, "name": TAG_KO[t], "order": order}))

    meta = {"data_year": inp.data_year, "dong_count": len(dongs), "region_count": len(regions)}
    return Graph(nodes, rels, meta)


def expected_counts(graph: Graph) -> dict[str, dict[str, int]]:
    """sync_graph 가 Neo4j 개수와 비교할 기대값. Meta 1개를 더한다."""
    nodes = Counter(n.label for n in graph.nodes)
    nodes["Meta"] = 1
    return {"nodes": dict(nodes), "rels": dict(Counter(r.type for r in graph.rels))}
