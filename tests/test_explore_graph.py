"""explore/graph.py build_graph (순수, Neo4j 없이)."""

from explore import graph
from tests.explore_samples import sample_graph_input, weak_yeongok_input


def build():
    return graph.build_graph(sample_graph_input())


def nodes_by_id(g):
    return {n.id: n for n in g.nodes}


def rels_of(g, type_, src=None):
    return [r for r in g.rels if r.type == type_ and (src is None or r.src == src)]


def test_label_counts():
    assert graph.expected_counts(build()) == {
        "nodes": {
            "Sido": 2, "Region": 3, "Dong": 4, "Grade": 12, "Type": 3, "Hour": 24,
            "Month": 12, "Place": 1, "Tag": 2, "PoiTag": 12, "Meta": 1,
        },
        "rels": {
            "IN": 7, "EASE": 36, "HAS_GRADE": 12, "FITS": 12, "QUIET_AT": 8, "PEAK_AT": 8,
            "NEAR": 3, "LIFE": 4, "HAS_TAG": 13, "SIMILAR": 6,
        },
    }  # fmt: skip


def test_label_of():
    assert graph.label_of("dong:5115025000") == "Dong"
    assert graph.label_of("life:local") == "Tag"
    assert graph.label_of("poi:cafe") == "PoiTag"
    assert graph.label_of("grade:evening:A") == "Grade"


def test_dong_props():
    n = nodes_by_id(build())["dong:5115025000"]
    assert n.label == "Dong"
    assert n.props["code"] == "5115025000" and n.props["name"] == "주문진읍"
    assert (n.props["lat"], n.props["lng"]) == (37.88818, 128.75354)
    assert n.props["coast_km"] == 0.0 and n.props["pg_id"] == 116
    assert n.props["driv_weekday"][12] == 2064 and len(n.props["driv_weekend"]) == 24
    # 값 없는 시각은 -1 (Neo4j 리스트에 null 불가)
    assert nodes_by_id(build())["dong:5211357500"].props["driv_weekday"][0] == -1


def test_fits_three_with_rank():
    fits = rels_of(build(), "FITS", "dong:5115025000")
    assert [(r.dst, r.props["score"], r.props["rank"]) for r in fits] == [
        ("type:local", 60, 1),
        ("type:networking", 50, 2),
        ("type:immersion", 36, 3),
    ]
    assert fits[0].props["reason"] == "생활권 지수 0.74"


def test_near_only_coastal_dongs():
    near = rels_of(build(), "NEAR")
    assert {r.src for r in near} == {"dong:5115025000", "dong:5115037000", "dong:5121057000"}
    assert next(r for r in near if r.src == "dong:5121057000").props == {"km": 0.887}


def test_poi_tags_skip_food_and_keep_order():
    g = build()
    tags = [r.dst for r in rels_of(g, "HAS_TAG", "dong:5115025000")]
    assert tags == ["poi:coast", "poi:cafe", "poi:resort", "poi:beach", "poi:park", "poi:market"]
    assert "poi:food" not in nodes_by_id(g)
    cafe = nodes_by_id(g)["poi:cafe"].props
    assert cafe == {"code": "cafe", "name": "카페 많음", "order": 2}


def test_grade_rel_ranks_use_raw_values():
    g = build()
    jumunjin = {r.dst: r.props for r in rels_of(g, "HAS_GRADE", "dong:5115025000")}
    # 저녁 활기 원값 0.278: 전국 4동 중 2위(금암동 0.45 다음), 강릉 2동 중 1위
    assert jumunjin["grade:evening:A"] == {
        "score": 62, "rank": 2, "of": 4, "rank_sgg": 1, "of_sgg": 2,
    }  # fmt: skip
    # 조용함은 원값이 낮을수록 1위: 0.80(연곡) 0.85(노학) 0.8721(주문진) 0.95(금암)
    assert jumunjin["grade:quiet:B"]["rank"] == 3
    assert jumunjin["grade:quiet:B"]["rank_sgg"] == 2


def test_hour_rels_keyed_by_day():
    g = build()
    quiet = rels_of(g, "QUIET_AT", "dong:5115025000")
    assert [(r.dst, r.props, r.key) for r in quiet] == [
        ("hour:9", {"day": "weekday"}, ("day",)),
        ("hour:3", {"day": "weekend"}, ("day",)),
    ]
    peak = rels_of(g, "PEAK_AT", "dong:5115025000")
    assert [(r.dst, r.props["day"]) for r in peak] == [
        ("hour:12", "weekday"),
        ("hour:14", "weekend"),
    ]


def test_degree_counts_connected_dongs():
    n = nodes_by_id(build())
    assert n["grade:quiet:B"].props["degree"] == 2  # 주문진읍·노학동
    assert n["type:local"].props["degree"] == 2  # 주문진읍·노학동. FITS rank 1 만 센다
    assert n["type:immersion"].props["degree"] == 1  # 연곡면
    assert n["place:coast"].props["degree"] == 3
    assert n["life:local"].props["degree"] == 3 and n["life:tourist"].props["degree"] == 1
    assert n["region:51150"].props["degree"] == 2
    assert n["hour:9"].props == {"h": 9, "degree_weekday": 3, "degree_weekend": 0}
    assert n["hour:3"].props["degree_weekend"] == 3


def test_ease_quiet_months_are_raw_bottom_three():
    ease = {int(r.dst.split(":")[1]): r.props for r in rels_of(build(), "EASE", "region:51150")}
    assert sorted(m for m, p in ease.items() if p["quiet"]) == [2, 4, 9]
    assert ease[8]["raw"] == 100.0 and ease[1]["raw"] == 80.3


def test_similar_rank_and_region_links():
    g = build()
    sim = rels_of(g, "SIMILAR", "dong:5115025000")
    assert [(r.dst, r.props) for r in sim] == [
        ("dong:5121057000", {"rank": 1, "l2": 0.2141}),
        ("dong:5115037000", {"rank": 2, "l2": 0.3}),
    ]
    ins = {(r.src, r.dst) for r in rels_of(g, "IN")}
    assert ("region:51150", "sido:51") in ins and ("dong:5211357500", "region:52110") in ins
    assert nodes_by_id(g)["sido:52"].props == {"name": "전북특별자치도"}


def test_meta():
    assert build().meta == {"data_year": 2025, "dong_count": 4, "region_count": 3}


def test_type_degree_counts_only_top_at_or_above_min():
    g = graph.build_graph(weak_yeongok_input())
    n = nodes_by_id(g)
    assert n["type:immersion"].props["degree"] == 0  # 연곡면 1위 38 < 40
    assert n["type:local"].props["degree"] == 2
    # 막대(동 상세 types)용 FITS 는 기준과 관계없이 3개
    assert len(rels_of(g, "FITS", "dong:5115037000")) == 3
