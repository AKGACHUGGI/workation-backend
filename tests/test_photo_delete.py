"""올린 사람의 사진 삭제 · 노트 삭제 시 첨부 사진 (스펙 4-6)."""

import pytest

from tests.photo_tools import PUBLIC, make_image, r2, upload  # noqa: F401
from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GOSEONG, OTHER_ID, auth, me, run  # noqa: F401
from tests.test_photo_quarantine import user_photo
from webapi import photo_quarantine

pytestmark = pytest.mark.django_db


def delete_photo(client, photo_id, **headers):
    return client.delete(f"/api/me/photos/{photo_id}", **(headers or auth()))


def test_delete_my_photo(client, me, r2):  # noqa: F811
    pid, key = user_photo(r2)
    assert delete_photo(client, pid).status_code == 204
    assert key not in r2.objects and run("SELECT count(*) FROM photo") == [(0,)]
    assert client.get("/api/me/photos", **auth()).json()["items"] == []


def test_delete_my_photo_r2_failure(client, me, r2):  # noqa: F811
    pid, key = user_photo(r2)
    r2.fail_delete = True
    r = delete_photo(client, pid)
    assert r.status_code == 502 and r.json()["error"]["code"] == "DELETE_FAILED"
    assert run("SELECT count(*) FROM photo") == [(1,)] and key in r2.objects


def test_delete_reported_photo_keeps_evidence(client, me, r2):  # noqa: F811
    pid, _ = user_photo(r2, "pending")
    assert delete_photo(client, pid).status_code == 204
    assert client.get("/api/me/photos", **auth()).json()["items"] == []
    assert run("SELECT status, deleted_by_user FROM photo") == [("hidden", True)]
    assert photo_quarantine.is_quarantined(run("SELECT url FROM photo")[0][0])


def test_delete_photo_404_401(client, me, r2):  # noqa: F811
    others, _ = user_photo(r2, user_id=OTHER_ID)
    for photo_id in (others, 999999, "abc"):
        r = delete_photo(client, photo_id)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert client.delete(f"/api/me/photos/{others}").status_code == 401


def post_note(client, n):
    payload = {"body": "사진 팁", "photos": [upload(make_image()) for _ in range(n)]}
    return client.post(f"/api/regions/{GOSEONG}/notes", payload, **auth()).json()


def delete_note(client, note):
    return client.delete(f"/api/me/tips/{note['id']}", **auth())


def test_delete_note_deletes_photo_files(client, me, r2):  # noqa: F811
    note = post_note(client, 2)
    keys = [p["url"].removeprefix(f"{PUBLIC}/") for p in note["photos"]]
    assert delete_note(client, note).status_code == 204
    assert not set(keys) & set(r2.objects)
    assert run("SELECT count(*) FROM photo") == [(0,)]
    assert run("SELECT count(*) FROM tip") == [(0,)]


def test_delete_note_keeps_reported_photo(client, me, r2):  # noqa: F811
    note = post_note(client, 2)
    reported = note["photos"][0]["id"]
    run("UPDATE photo SET status = 'pending' WHERE photo_id = %s", [reported])
    photo_quarantine.quarantine(int(reported))
    assert delete_note(client, note).status_code == 204
    rows = run("SELECT photo_id::text, tip_id, status, deleted_by_user, url FROM photo")
    assert [r[:4] for r in rows] == [(reported, None, "hidden", True)]
    assert photo_quarantine.is_quarantined(rows[0][4])
    assert run("SELECT count(*) FROM tip") == [(0,)]


def test_delete_note_r2_failure_keeps_note(client, me, r2):  # noqa: F811
    note = post_note(client, 1)
    r2.fail_delete = True
    r = delete_note(client, note)
    assert r.status_code == 502 and r.json()["error"]["code"] == "DELETE_FAILED"
    assert run("SELECT count(*) FROM tip") == [(1,)]
    assert run("SELECT count(*) FROM photo") == [(1,)]
