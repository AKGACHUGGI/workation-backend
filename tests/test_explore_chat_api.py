"""POST /api/explore/chat (스펙 §8).
읽기 · 의도 · 생성을 바꿔 끼워 Redis · Neo4j · HCX 없이 본다."""

import pytest

from explore import ask_match, views
from explore.chat import arrange, flow, llm
from explore.chat import data as chat_data
from explore.graphdb import GraphUnavailable
from tests.explore_chat_samples import HYOJA, NAMES, RAWS, YEOUI

URL = "/api/explore/chat"
INTENTS = {"여의동이랑 비교하면?": ["compare_summary"], "주말엔?": ["weekend_hours"]}


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
        ask_match, "find_intents", lambda q: (INTENTS.get(q, ["unknown_other"]), 0.9)
    )
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
