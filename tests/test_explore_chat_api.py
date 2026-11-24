"""POST /api/explore/chat (스펙 §8).
읽기 · 의도 · 생성을 바꿔 끼워 Redis · Neo4j · HCX 없이 본다."""

import pytest

from explore import ask_match, views
from explore.chat import arrange, flow, llm
from explore.chat import data as chat_data
from explore.graphdb import GraphUnavailable
from tests.explore_chat_samples import GYO, HYOJA, NAMES, RAWS, REGION_RAW, YEOUI
from webapi import data as webdata

URL = "/api/explore/chat"
INTENTS = {"여의동이랑 비교하면?": ["compare_summary"], "주말엔?": ["weekend_hours"]}
REGION_FOUND = {"조용한 동네 어디야?": ["find_quiet"]}
WALK = {
    "weekday": {"peakHour": 11, "lowHour": 18, "lowValue": 90},
    "weekend": {"peakHour": 12, "lowHour": 9, "lowValue": 88},
}


@pytest.fixture(autouse=True)
def world(settings, monkeypatch):
    settings.REDIS_URL = ""
    monkeypatch.setattr(flow, "_saver", None)
    monkeypatch.setattr(chat_data, "dong_names", lambda: NAMES)
    monkeypatch.setattr(chat_data, "dong_raw", RAWS.get)
    vecs = {HYOJA: ((0.73, 0.56, 0.79, 0.7), False), YEOUI: ((0.57, 0.79, 0.96, 0.6), False)}
    monkeypatch.setattr(chat_data, "metric_vecs", lambda: vecs)
    monkeypatch.setattr(chat_data, "pairs", lambda: arrange.pair_distances(vecs))
    monkeypatch.setattr(
        ask_match,
        "find_intents",
        lambda q, scope="dong": (
            (REGION_FOUND if scope == "region" else INTENTS).get(q, ["unknown_other"]),
            0.9,
        ),
    )
    monkeypatch.setattr(chat_data, "region_raw", {"45111": REGION_RAW}.get)
    monkeypatch.setattr(webdata, "walk_peaks", lambda code, month: WALK)
    monkeypatch.setattr(webdata, "region_best_months", lambda sgg: [2, 3])
    monkeypatch.setattr(views, "answer_one", lambda *a: (["한 동 답이에요."], "llm"))
    monkeypatch.setattr(llm, "enabled", lambda: False)


def post(client, question="여의동이랑 비교하면?", **body):
    payload = {"panel": HYOJA, "question": question, **body}
    return client.post(URL, payload, content_type="application/json")


def test_compare_answer(client):
    res = post(client, criteria={"chips": ["quiet"]})
    assert res.status_code == 200
    body = res.json()
    assert len(body["conversationId"]) == 32
    assert body["dongs"] == [{"id": HYOJA, "name": "효자2동"}, {"id": YEOUI, "name": "여의동"}]
    assert body["intent"] == "compare_summary"
    assert body["sentences"][0] == {
        "text": "조용함을 원한다면 효자2동이 맞아요.",
        "evidence": "조용함: 효자2동 S(73점) · 여의동 A(57점)",
        "source": "rule",
    }
    assert body["guide"] is None
    assert len(body["examples"]) == 3


def test_conversation_continues(client):
    cid = post(client).json()["conversationId"]
    body = post(client, "주말엔?", conversationId=cid).json()
    assert body["conversationId"] == cid
    assert [d["id"] for d in body["dongs"]] == [HYOJA, YEOUI]
    assert body["intent"] == "weekend_hours"


def test_unknown_question_guides(client):
    body = post(client, "이 동네 어때?").json()
    assert body["dongs"] == [{"id": HYOJA, "name": "효자2동"}]
    assert body["intent"] == "unknown_other"
    assert body["guide"] is not None


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"panel": HYOJA, "question": ""}, "QUESTION_INVALID"),
        ({"panel": HYOJA, "question": "가" * 101}, "QUESTION_INVALID"),
        ({"question": "주말엔?"}, "PANEL_INVALID"),
        (
            {"panel": HYOJA, "question": "주말엔?", "criteria": {"chips": ["cheap"]}},
            "CRITERIA_INVALID",
        ),
    ],
)
def test_bad_request(client, payload, code):
    res = client.post(URL, payload, content_type="application/json")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == code


def test_unknown_panel(client):
    res = post(client, panel="0000000000")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DONG_NOT_FOUND"


def test_graph_unavailable(client, monkeypatch):
    def down(code):
        raise GraphUnavailable("neo4j")

    monkeypatch.setattr(chat_data, "dong_raw", down)
    res = post(client)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "GRAPH_UNAVAILABLE"


def test_burst_throttle(client):
    for _ in range(10):
        assert post(client).status_code == 200
    res = post(client)
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert int(res["Retry-After"]) > 0


def test_old_ask_endpoint_is_gone(client):
    # PR #20 대로 동네에 물어보기 API 는 없다. 채팅이 대신한다
    res = client.post(
        f"/api/explore/dongs/{HYOJA}/ask", {"question": "조용해?"}, "application/json"
    )
    assert res.status_code == 404


def post_region(client, question="조용한 동네 어디야?", **body):
    payload = {"surface": "region", "sigungu": "45111", "question": question, **body}
    return client.post(URL, payload, content_type="application/json")


def test_region_find_answer(client):
    res = post_region(client, month="2025-10")
    assert res.status_code == 200
    body = res.json()
    assert body["scope"] == "region"
    assert [d["id"] for d in body["dongs"]] == [HYOJA, YEOUI, GYO]
    assert body["sentences"][0] == {
        "text": "조용함을 원한다면 효자2동이 맞아요.",
        "evidence": (
            "조용함: 효자2동 S(전주시 1위/4) · 여의동 A(전주시 2위/4) · 교1동 A(전주시 3위/4)"
        ),
        "source": "rule",
    }
    assert body["examples"][0] == "효자2동은 저녁에 어때?"


def test_dong_hours_say_both_bases(client):
    body = post(client, "주말엔?", month="2025-10", surface="dong").json()
    assert body["scope"] == "dong"
    assert body["sentences"][0]["text"] == (
        "효자2동은 주말 차량이 14시에 가장 많고, "
        "걸어 다니는 사람은 10월 기준 12시에 가장 붐비고 9시에 가장 한산해요."
    )
    # 달이 없거나 틀리면 차량 기준만 (오류 아님)
    body = post(client, "주말엔?", month="2025-13").json()
    assert body["sentences"][0]["text"] == "효자2동은 주말 차량이 14시에 가장 많아요."


@pytest.mark.parametrize(
    ("payload", "status", "code"),
    [
        ({"surface": "explore", "panel": HYOJA, "question": "주말엔?"}, 400, "SURFACE_INVALID"),
        ({"surface": "region", "question": "조용한 동네 어디야?"}, 400, "SIGUNGU_INVALID"),
        (
            {"surface": "region", "sigungu": "4511", "question": "조용한 동네 어디야?"},
            400,
            "SIGUNGU_INVALID",
        ),
        (
            {"surface": "region", "sigungu": "99999", "question": "조용한 동네 어디야?"},
            404,
            "REGION_NOT_FOUND",
        ),
    ],
)
def test_region_request_errors(client, payload, status, code):
    res = client.post(URL, payload, content_type="application/json")
    assert res.status_code == status
    assert res.json()["error"]["code"] == code
