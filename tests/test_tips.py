"""노트 mine · 수정 · 도움돼요 · 답글."""

import pytest

from tests.supabase_tokens import make_token, supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GOSEONG, OTHER_ID, add_tip, auth, me, run  # noqa: F401

pytestmark = pytest.mark.django_db

OTHER = make_token(sub=OTHER_ID)


def notes(client, **headers):
    return client.get(f"/api/regions/{GOSEONG}/notes?sort=recent", **headers).json()["items"]


# ───────────────────────── mine ─────────────────────────


def test_mine_only_for_my_tips(client, me):  # noqa: F811
    my = add_tip(me, 1, body="내 팁", minutes_ago=1)
    add_tip(OTHER_ID, 1, body="남의 팁")
    by_me = {n["body"]: n for n in notes(client, **auth())}
    assert by_me["내 팁"]["mine"] is True and "mine" not in by_me["남의 팁"]
    # 비로그인 · 틀린 토큰도 읽기는 된다 (mine 없음)
    for headers in ({}, {"HTTP_AUTHORIZATION": "Bearer broken"}):
        assert all("mine" not in n for n in notes(client, **headers))
    assert client.get("/api/me/tips", **auth()).json()["items"][0]["id"] == str(my)


# ───────────────────────── 수정 ─────────────────────────


def patch(client, tip_id, payload, token=None):
    return client.patch(
        f"/api/me/tips/{tip_id}", payload, content_type="application/json", **auth(token)
    )


def test_patch_my_tip_body(client, me):  # noqa: F811
    tip = add_tip(me, 1, body="처음", visit_month=10)
    r = patch(client, tip, {"body": "  고친 내용  "})
    assert r.status_code == 200
    note = r.json()
    assert note["id"] == str(tip) and note["body"] == "고친 내용" and note["mine"] is True
    assert note["visitMonth"] == "2025-10"  # 방문 월은 그대로
    assert run("SELECT body, visit_month FROM tip") == [("고친 내용", 10)]


@pytest.mark.parametrize("payload", [{}, {"body": ""}, {"body": "  "}, {"body": "가" * 301}])
def test_patch_bad_body(client, me, payload):  # noqa: F811
    tip = add_tip(me, 1, body="처음")
    assert patch(client, tip, payload).status_code == 400
    assert run("SELECT body FROM tip") == [("처음",)]


def test_patch_others_or_hidden_tip_404(client, me):  # noqa: F811
    others = add_tip(OTHER_ID, 1, body="남의 팁")
    hidden = add_tip(me, 1, body="가려진 팁", status="hidden")
    for tip_id in (others, hidden, "abc", "9999999999"):
        assert patch(client, tip_id, {"body": "고침"}).status_code == 404
    assert run("SELECT body FROM tip ORDER BY tip_id") == [("남의 팁",), ("가려진 팁",)]


def test_patch_needs_login(client, me):  # noqa: F811
    tip = add_tip(me, 1)
    r = client.patch(f"/api/me/tips/{tip}", {"body": "x"}, content_type="application/json")
    assert r.status_code == 401


# ───────────────────────── 도움돼요 ─────────────────────────


def test_helpful_toggle(client, me):  # noqa: F811
    tip = add_tip(OTHER_ID, 1, body="남의 팁")
    url = f"/api/tips/{tip}/helpful"
    for _ in range(2):  # 여러 번 눌러도 1
        r = client.put(url, **auth())
        assert r.status_code == 200 and r.json() == {"helpful": 1, "myHelpful": True}
    note = notes(client, **auth())[0]
    assert note["helpful"] == 1 and note["myHelpful"] is True
    assert "myHelpful" not in notes(client)[0]
    for _ in range(2):
        r = client.delete(url, **auth())
        assert r.status_code == 200 and r.json() == {"helpful": 0, "myHelpful": False}
    assert run("SELECT count(*) FROM tip_reaction") == [(0,)]


def test_helpful_own_tip_400(client, me):  # noqa: F811
    tip = add_tip(me, 1)
    r = client.put(f"/api/tips/{tip}/helpful", **auth())
    assert r.status_code == 400 and r.json()["error"]["code"] == "OWN_TIP"


def test_helpful_hidden_or_missing_404(client, me):  # noqa: F811
    hidden = add_tip(OTHER_ID, 1, status="hidden")
    for tip_id in (hidden, 999, "x"):
        assert client.put(f"/api/tips/{tip_id}/helpful", **auth()).status_code == 404
        assert client.delete(f"/api/tips/{tip_id}/helpful", **auth()).status_code == 404


def test_helpful_needs_login(client, me):  # noqa: F811
    tip = add_tip(OTHER_ID, 1)
    assert client.put(f"/api/tips/{tip}/helpful").status_code == 401


# ───────────────────────── 답글 ─────────────────────────


def post_reply(client, tip_id, body, token=None):
    return client.post(
        f"/api/tips/{tip_id}/replies",
        {"body": body},
        content_type="application/json",
        **auth(token),
    )


def test_reply_create_list_delete(client, me):  # noqa: F811
    tip = add_tip(OTHER_ID, 1, body="남의 팁")
    r = post_reply(client, tip, "  고마워요  ")
    assert r.status_code == 201
    reply = r.json()
    assert reply == {
        "id": reply["id"],
        "author": {"nickname": "두이", "initial": "두"},
        "createdAt": reply["createdAt"],
        "body": "고마워요",
        "mine": True,
    }
    assert post_reply(client, tip, "저도요", OTHER).status_code == 201
    run(
        "INSERT INTO tip_reply (tip_id, user_id, body, status) VALUES (%s, %s, '숨김', 'hidden')",
        [tip, OTHER_ID],
    )

    listed = client.get(f"/api/tips/{tip}/replies", **auth()).json()["items"]
    assert [(x["body"], x.get("mine")) for x in listed] == [("고마워요", True), ("저도요", None)]
    anon = client.get(f"/api/tips/{tip}/replies").json()["items"]
    assert all("mine" not in x for x in anon)
    assert notes(client)[0]["replies"] == 2  # 숨긴 답글은 세지 않는다

    others_reply = listed[1]["id"]
    assert client.delete(f"/api/me/replies/{others_reply}", **auth()).status_code == 404
    assert client.delete(f"/api/me/replies/{reply['id']}", **auth()).status_code == 204
    assert client.delete(f"/api/me/replies/{reply['id']}", **auth()).status_code == 404
    assert notes(client)[0]["replies"] == 1


@pytest.mark.parametrize("body", ["", "   ", "가" * 301, None])
def test_reply_bad_body(client, me, body):  # noqa: F811
    tip = add_tip(OTHER_ID, 1)
    assert post_reply(client, tip, body).status_code == 400
    assert run("SELECT count(*) FROM tip_reply") == [(0,)]


def test_reply_hidden_tip_404(client, me):  # noqa: F811
    hidden = add_tip(OTHER_ID, 1, status="hidden")
    assert post_reply(client, hidden, "답글").status_code == 404
    assert client.get(f"/api/tips/{hidden}/replies").status_code == 404


def test_reply_rate_limit(client, me):  # noqa: F811
    tip = add_tip(OTHER_ID, 1)
    for i in range(5):
        assert post_reply(client, tip, f"답글 {i}").status_code == 201
    r = post_reply(client, tip, "하나 더")
    assert r.status_code == 429 and r.json()["error"]["code"] == "REPLY_RATE_LIMIT"


def test_reply_needs_login(client, me):  # noqa: F811
    tip = add_tip(OTHER_ID, 1)
    r = client.post(f"/api/tips/{tip}/replies", {"body": "x"}, content_type="application/json")
    assert r.status_code == 401
