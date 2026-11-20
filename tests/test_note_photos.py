"""노트 사진 첨부 — POST /api/{regions|dongs}/{id}/notes 를 multipart 로 (스펙 4-2)."""

import pytest
from django.db import DatabaseError

from tests.photo_tools import make_image, r2, upload  # noqa: F401
from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GEOJIN, GOSEONG, OTHER_ID, add_tip, auth, me, run  # noqa: F401
from webapi import community

pytestmark = pytest.mark.django_db


def post_note(client, kind, item_id, body="사진 팁", photos=(), **fields):
    payload = {"body": body, **fields}
    if photos:
        payload["photos"] = [upload(p, f"{i}.jpg") for i, p in enumerate(photos)]
    return client.post(f"/api/{kind}/{item_id}/notes", payload, **auth())


def test_json_note_has_empty_photos(client, me, r2):  # noqa: F811
    r = client.post(
        f"/api/regions/{GOSEONG}/notes", {"body": "팁"}, content_type="application/json", **auth()
    )
    assert r.status_code == 201 and r.json()["photos"] == []
    assert r2.puts == 0


def test_multipart_note_with_photos(client, me, r2):  # noqa: F811
    imgs = [make_image(size=(100 + i, 100)) for i in range(3)]
    r = post_note(client, "dongs", GEOJIN, photos=imgs, visitMonth="2025-10")
    assert r.status_code == 201
    note = r.json()
    assert note["body"] == "사진 팁" and note["visitMonth"] == "2025-10"
    assert len(note["photos"]) == 3
    keys = [p["url"].removeprefix("https://pub.r2.example/") for p in note["photos"]]
    assert all(k.startswith("uploads/tip/") for k in keys) and set(keys) == set(r2.objects)
    rows = run(
        "SELECT user_photo_id::text, scope, unit_id, month, user_id::text, tip_id::text,"
        " position, status FROM user_photo ORDER BY position"
    )
    assert [r[0] for r in rows] == [p["id"] for p in note["photos"]]
    assert {r[1:] for r in rows} == {
        ("dong", 101, 10, me, note["id"], pos, "approved") for pos in range(3)
    }
    # 노트 목록 · 내 팁에도 같은 순서로 보인다
    listed = client.get(f"/api/dongs/{GEOJIN}/notes").json()["items"][0]
    mine = client.get("/api/me/tips", **auth()).json()["items"][0]
    assert listed["photos"] == mine["photos"] == note["photos"]


def test_multipart_note_without_photos(client, me, r2):  # noqa: F811
    r = post_note(client, "regions", GOSEONG)
    assert r.status_code == 201 and r.json()["photos"] == []


def test_hidden_photo_not_in_note(client, me, r2):  # noqa: F811
    note = post_note(client, "regions", GOSEONG, photos=[make_image(), make_image()]).json()
    run(
        "UPDATE user_photo SET status = 'pending' WHERE user_photo_id = %s",
        [note["photos"][0]["id"]],
    )
    listed = client.get(f"/api/regions/{GOSEONG}/notes").json()["items"][0]
    assert listed["photos"] == note["photos"][1:]


@pytest.mark.parametrize(
    "photos", [[b"not an image"], [make_image()] * 4], ids=["not-image", "four"]
)
def test_bad_photo_creates_nothing(client, me, r2, photos):  # noqa: F811
    r = post_note(client, "regions", GOSEONG, photos=photos)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PHOTO"
    assert run("SELECT count(*) FROM tip") == [(0,)] and r2.puts == 0


def test_bad_body_does_not_touch_photos(client, me, r2):  # noqa: F811
    r = post_note(client, "regions", GOSEONG, body="  ", photos=[b"not an image"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_REQUEST"


def test_rate_limit_before_upload(client, me, r2):  # noqa: F811
    for i in range(community.TIP_BURST_LIMIT):
        add_tip(me, 1, None, f"팁 {i}")
    r = post_note(client, "regions", GOSEONG, photos=[make_image()])
    assert r.status_code == 429 and r.json()["error"]["code"] == "TIP_RATE_LIMIT"
    assert r2.puts == 0


def test_upload_failure_502_and_no_tip(client, me, r2):  # noqa: F811
    r2.fail_after = 1
    r = post_note(client, "regions", GOSEONG, photos=[make_image(), make_image()])
    assert r.status_code == 502 and r.json()["error"]["code"] == "UPLOAD_FAILED"
    assert run("SELECT count(*) FROM tip") == [(0,)]
    assert r2.objects == {}


def test_db_failure_removes_uploaded(client, me, r2, monkeypatch):  # noqa: F811
    def boom(*a, **kw):
        raise DatabaseError("가짜 DB 실패")

    monkeypatch.setattr(community, "_insert_tip_photos", boom)
    with pytest.raises(DatabaseError):
        post_note(client, "regions", GOSEONG, photos=[make_image(), make_image()])
    assert r2.puts == 2 and r2.objects == {} and len(r2.deleted) == 2
    assert run("SELECT count(*) FROM tip") == [(0,)]
