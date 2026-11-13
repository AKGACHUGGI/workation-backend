"""explore/queries.py 응답 모양 (순수) + 읽기 트랜잭션 흐름 (가짜 tx)."""

import pytest

from explore import queries
from explore.graphdb import GraphUnavailable
from tests.explore_samples import GANGNEUNG_RAW, JUMUNJIN_WEEKDAY, jumunjin_raw

PINS_REC = {  # PINS Cypher 결과 한 행
    "code": "51150",
    "dongs": [
        {
            "code": "5115037000", "name": "연곡면", "lat": 37.85, "lng": 128.8,
            "tags": [[6, "beach"], [0, "coast"], [2, "cafe"]],
            "top": [["장기체류형", 61]], "near": ["place:coast"], "life": ["관광지"],
            "grades": [["quiet", "A"], ["peer", "C"], ["evening", "B"]],
        },
        {
            "code": "5115025000", "name": "주문진읍", "lat": 37.88818, "lng": 128.75354,
            "tags": [[11, "market"], [0, "coast"], [5, "resort"], [2, "cafe"], [6, "beach"],
                     [7, "park"]],
            "top": [["장기체류형", 61]], "near": ["place:coast"], "life": ["생활권"],
            "grades": [["quiet", "B"], ["peer", "B"], ["evening", "A"]],
        },
        {
            "code": "5115099000", "name": "가상동", "lat": None, "lng": None,
            "tags": [], "top": [], "near": [], "life": [], "grades": [["peer", "S"]],
        },
    ],
}  # fmt: skip


def test_shape_pins():
    body = queries.shape_pins(PINS_REC)
    assert body["sigunguId"] == "51150"
    assert [d["name"] for d in body["dongs"]] == ["가상동", "연곡면", "주문진읍"]
    jumunjin = body["dongs"][2]
    assert jumunjin == {
        "id": "5115025000",
        "name": "주문진읍",
        "center": {"lat": 37.88818, "lng": 128.75354},
        "tags": ["coast", "cafe", "resort", "beach", "park", "market"],
        "topType": "장기체류형",
        "topScore": 61,
        "coastal": True,
        "life": "생활권",
        "grades": {"quiet": "B", "evening": "A"},
    }


def test_shape_pins_missing_values():
    empty = queries.shape_pins(PINS_REC)["dongs"][0]
    assert empty["tags"] == [] and empty["coastal"] is False
    assert (empty["topType"], empty["topScore"], empty["life"]) == (None, None, None)
    assert empty["grades"] == {"quiet": None, "evening": None}


def test_fact_list_labels_and_degrees():
    facts = queries.fact_list(jumunjin_raw()["facts"]["5115025000"])
    assert facts == {
        "grade:quiet:B": ("조용함 B", 50),
        "grade:peer:B": ("또래 밀도 B", 50),
        "grade:evening:A": ("저녁 활기 A", 51),
        "type:long_stay": ("1위 장기체류형", 58),
        "quiet:weekday:9": ("평일 9시 한산", 108),
        "quiet:weekend:3": ("주말 3시 한산", 102),
        "place:coast": ("바닷가", 97),
        "life:local": ("생활권", 107),
        "region:51150": ("같은 강릉시", 21),
    }


def test_common_facts_rare_first():
    raw = jumunjin_raw()
    mine = queries.fact_list(raw["facts"]["5115025000"])
    nohak = queries.fact_list(raw["facts"]["5121057000"])
    assert [label for _, label in queries.common_facts(mine, nohak)] == [
        "조용함 B", "1위 장기체류형", "바닷가", "주말 3시 한산", "생활권", "평일 9시 한산",
    ]  # fmt: skip


def test_score_diff_other_minus_mine_and_null():
    assert queries.score_diff({"quiet": 31, "peer": 39, "evening": 62}, {
        "quiet": 39, "peer": 52, "evening": 47,
    }) == {"quiet": 8, "peer": 13, "evening": -15}  # fmt: skip
    assert queries.score_diff({"quiet": 31}, {"quiet": 39, "peer": 52}) == {
        "quiet": 8, "peer": None, "evening": None,
    }  # fmt: skip


def test_shape_dong_jumunjin():
    body = queries.shape_dong(jumunjin_raw(), ["문장 1", "문장 2", "문장 3"])
    assert body["id"] == "5115025000" and body["name"] == "주문진읍"
    assert body["sigungu"] == {"id": "51150", "name": "강릉시"}
    assert body["sido"] == "강원특별자치도" and body["coastKm"] == 0.0
    assert [(t["name"], t["score"]) for t in body["types"]] == [
        ("장기체류형", 61), ("로컬살이형", 60), ("네트워킹형", 50), ("몰입형", 36),
    ]  # fmt: skip
    assert body["types"][0]["reason"] == "숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달)"
    assert body["grades"] == {
        "quiet": {"grade": "B", "score": 31},
        "peer": {"grade": "B", "score": 39},
        "evening": {"grade": "A", "score": 62},
    }
    assert body["hourly"]["weekday"] == {"driv": JUMUNJIN_WEEKDAY, "peakHour": 12, "quietHour": 9}
    assert (body["hourly"]["weekend"]["peakHour"], body["hourly"]["weekend"]["quietHour"]) == (
        14,
        3,
    )
    assert body["months"] == {
        "raw": [GANGNEUNG_RAW[m] for m in range(1, 13)],
        "quiet": [2, 4, 9],
        "basis": "강릉시",
    }
    assert body["briefing"] == ["문장 1", "문장 2", "문장 3"]
    assert body["syncedAt"] == "2026-11-13T10:00:00+09:00"


def test_shape_dong_similar():
    similar = queries.shape_dong(jumunjin_raw(), [])["similar"]
    assert [(s["name"], s["sigungu"], s["common"]) for s in similar] == [
        ("노학동", "속초시", 6), ("대정읍", "서귀포시", 4), ("영천동", "서귀포시", 5),
    ]  # fmt: skip
    assert similar[0]["id"] == "5121057000"
    assert similar[0]["commonTop"] == ["조용함 B", "1위 장기체류형", "바닷가"]
    assert similar[0]["diff"] == {"quiet": 8, "peer": 13, "evening": -15}
    assert similar[1]["commonTop"] == ["저녁 활기 A", "바닷가", "주말 3시 한산"]
    assert similar[1]["diff"] == {"quiet": 19, "peer": 11, "evening": -3}
    assert similar[2]["commonTop"] == ["조용함 B", "저녁 활기 A", "바닷가"]
    assert similar[2]["diff"] == {"quiet": 10, "peer": 20, "evening": 5}


def test_shape_dong_missing_values():
    raw = jumunjin_raw()
    raw["rec"]["grades"] = [g for g in raw["rec"]["grades"] if g[0] != "peer"]
    raw["rec"]["months"] = [row for row in raw["rec"]["months"] if row[0] != 5]
    raw["rec"]["dong"]["driv_weekend"] = [-1] * 23 + [788]
    raw["rec"]["similar"] = []
    body = queries.shape_dong(raw, [])
    assert body["grades"]["peer"] is None
    assert body["months"]["raw"][4] is None
    assert body["hourly"]["weekend"]["driv"] == [None] * 23 + [788]
    assert body["similar"] == [] and body["briefing"] == []


class FakeRecord(dict):
    def data(self):
        return dict(self)


class FakeResult(list):
    def single(self):
        return self[0] if self else None


class FakeTx:
    """Cypher 문자열 → 돌려줄 레코드 목록."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def run(self, query, **params):
        self.calls.append((query, params))
        return FakeResult(FakeRecord(r) for r in self.answers.get(query, []))


META_ROW = {"synced_at": "2026-11-13T10:00:00+09:00", "dong_count": 203, "region_count": 12}


def fake_read(tx):
    return lambda work, **params: work(tx, **params)


def test_fetch_dong_reads_self_and_similar_facts(monkeypatch):
    raw = jumunjin_raw()
    tx = FakeTx(
        {
            queries.META: [META_ROW],
            queries.DONG: [raw["rec"]],
            queries.FACTS: list(raw["facts"].values()),
        }
    )
    monkeypatch.setattr(queries, "read", fake_read(tx))
    assert queries.fetch_dong("5115025000") == raw
    facts_call = next(p for q, p in tx.calls if q == queries.FACTS)
    assert facts_call["codes"] == ["5115025000", "5121057000", "5013025000", "5013056000"]


def test_fetch_dong_unknown_is_none(monkeypatch):
    monkeypatch.setattr(queries, "read", fake_read(FakeTx({queries.META: [META_ROW]})))
    assert queries.fetch_dong("0000000000") is None


def test_fetch_pins(monkeypatch):
    tx = FakeTx({queries.META: [META_ROW], queries.PINS: [PINS_REC]})
    monkeypatch.setattr(queries, "read", fake_read(tx))
    assert queries.fetch_pins("51150")["dongs"][2]["id"] == "5115025000"
    monkeypatch.setattr(queries, "read", fake_read(FakeTx({queries.META: [META_ROW]})))
    assert queries.fetch_pins("99999") is None


def test_no_meta_is_unavailable(monkeypatch):
    monkeypatch.setattr(queries, "read", fake_read(FakeTx({})))
    with pytest.raises(GraphUnavailable, match="sync_graph"):
        queries.fetch_dong("5115025000")
    with pytest.raises(GraphUnavailable):
        queries.fetch_pins("51150")
