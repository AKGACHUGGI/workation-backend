"""이달의 사진 등록 (스펙 4-3) · 사진 신고 (4-4)."""

import pytest

from tests.photo_tools import make_image, r2, upload  # noqa: F401
from tests.supabase_tokens import make_token, supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GEOJIN, GOSEONG, OTHER_ID, add_user, auth, me, run  # noqa: F401
from tests.test_photo_quarantine import user_photo
from webapi import photo_quarantine, uploads

pytestmark = pytest.mark.django_db


def post_photos(client, kind, item_id, month="2025-10", n=1, **headers):
    payload = {"photos": [upload(make_image()) for _ in range(n)]}
    if month is not None:
        payload["month"] = month
    return client.post(f"/api/{kind}/{item_id}/photos", payload, **(headers or auth()))


def add_photo(status="approved", tip_id=None):
    return run(
        "INSERT INTO photo (scope, unit_id, month, url, source, status, tip_id)"
        " VALUES ('region', 1, 10, 'https://r2.example/p.webp', 'team', %s, %s)"
        " RETURNING photo_id",
        [status, tip_id],
    )[0][0]


# ───────────── 이달의 사진 ─────────────


def test_month_photos_contract(client, me, r2):  # noqa: F811
    r = post_photos(client, "regions", GOSEONG, n=2)
    assert r.status_code == 201
    items = r.json()["items"]
    assert [i | {"id": "", "url": ""} for i in items] == [
        {"id": "", "url": "", "month": "2025-10", "regionName": "고성군", "status": "approved"}
    ] * 2
    assert all(i["url"].startswith("https://pub.r2.example/uploads/photo/") for i in items)
    rows = run("SELECT scope, unit_id, month, source, user_id::text, tip_id FROM photo")
    assert rows == [("region", 1, 10, "user", me, None)] * 2
    mine = client.get("/api/me/photos", **auth()).json()["items"]
    assert sorted(i["id"] for i in mine) == sorted(i["id"] for i in items)


def test_month_photos_annual_and_dong(client, me, r2):  # noqa: F811
    r = post_photos(client, "dongs", GEOJIN, month="annual")
    assert r.status_code == 201 and r.json()["items"][0]["month"] == "annual"
    assert run("SELECT scope, unit_id, month FROM photo") == [("dong", 101, None)]


@pytest.mark.parametrize(("month", "n"), [(None, 1), ("2025-13", 1), ("10", 1), ("2025-10", 0)])
def test_month_photos_bad_400(client, me, r2, month, n):  # noqa: F811
    r = post_photos(client, "regions", GOSEONG, month=month, n=n)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PHOTO"
    assert r2.puts == 0


def test_month_photos_404_and_401(client, me, r2):  # noqa: F811
    assert post_photos(client, "regions", "99999").status_code == 404
    assert post_photos(client, "dongs", "0000000000").status_code == 404
    r = client.post(f"/api/regions/{GOSEONG}/photos", {"month": "2025-10"})
    assert r.status_code == 401


def test_month_photos_hourly_limit(client, me, r2, monkeypatch):  # noqa: F811
    monkeypatch.setattr(uploads, "PHOTO_HOURLY_LIMIT", 3)
    assert post_photos(client, "regions", GOSEONG, n=2).status_code == 201
    r = post_photos(client, "regions", GOSEONG, n=2)
    assert r.status_code == 429 and r.json()["error"]["code"] == "PHOTO_RATE_LIMIT"
    assert r2.puts == 2
    run("UPDATE photo SET created_at = now() - interval '61 minutes'")
    assert post_photos(client, "regions", GOSEONG, n=2).status_code == 201


def test_month_photos_upload_failure(client, me, r2):  # noqa: F811
    r2.fail_after = 0
    r = post_photos(client, "regions", GOSEONG)
    assert r.status_code == 502 and r.json()["error"]["code"] == "UPLOAD_FAILED"
    assert run("SELECT count(*) FROM photo") == [(0,)]


# ───────────── 신고 ─────────────


def report(client, photo_id, reason="부적절해요", token=None):
    return client.post(
        f"/api/photos/{photo_id}/report",
        {"reason": reason},
        content_type="application/json",
        **auth(token),
    )


def reporters(client, n):
    """신고할 사용자 n 명의 토큰. 첫 GET /api/me 로 app_user 를 만든다."""
    tokens = []
    for i in range(n):
        sub = f"00000000-0000-4000-8000-00000000000{i}"
        token = make_token(sub=sub, user_metadata={"name": f"신고{i}", "provider_id": sub})
        assert client.get("/api/me", **auth(token)).status_code == 200
        tokens.append(token)
    return tokens


def status_of(photo_id):
    return run("SELECT status FROM photo WHERE photo_id = %s", [photo_id])[0][0]


def test_report_hides_at_threshold(client, me, settings):  # noqa: F811
    settings.PHOTO_REPORT_THRESHOLD = 3
    photo = add_photo()
    t1, t2, t3 = reporters(client, 3)
    assert report(client, photo, token=t1).status_code == 204
    assert report(client, photo, token=t1).status_code == 204  # 같은 사람은 한 번만 센다
    assert report(client, photo, token=t2).status_code == 204
    assert status_of(photo) == "approved"
    assert report(client, photo, token=t3).status_code == 204
    assert status_of(photo) == "pending"
    assert run("SELECT count(*) FROM report WHERE target_type = 'photo'") == [(3,)]
    # 가려진 사진은 갤러리에서 빠지고 다시 신고할 수 없다
    body = client.get("/api/v1/regions/51820?month=10").json()
    assert body["photos"]["totalCount"] == 0
    assert report(client, photo).status_code == 404
    # 팀 사진(r2.example)은 버킷 주인 파일이라 옮기지 않는다
    assert run("SELECT url FROM photo WHERE photo_id = %s", [photo]) == [
        ("https://r2.example/p.webp",)
    ]


def test_report_quarantines_user_photo(client, me, r2, settings):  # noqa: F811
    settings.PHOTO_REPORT_THRESHOLD = 1
    photo, key = user_photo(r2)
    assert report(client, photo).status_code == 204
    url = run("SELECT url FROM photo WHERE photo_id = %s", [photo])[0][0]
    assert photo_quarantine.is_quarantined(url) and key not in r2.objects


def test_report_quarantine_failure_still_204(client, me, r2, settings):  # noqa: F811
    settings.PHOTO_REPORT_THRESHOLD = 1
    photo, key = user_photo(r2)
    r2.fail_copy = True
    assert report(client, photo).status_code == 204
    assert status_of(photo) == "pending"  # 가리기는 됐고, 파일은 photos hide 로 다시 격리
    assert key in r2.objects


@pytest.mark.parametrize("reason", ["", "   ", "가" * 31, None, 3])
def test_report_bad_reason(client, me, reason):  # noqa: F811
    r = report(client, add_photo(), reason=reason)
    assert r.status_code == 400


def test_report_404_and_401(client, me):  # noqa: F811
    assert report(client, 999999).status_code == 404
    assert report(client, "abc").status_code == 404
    assert report(client, add_photo("hidden")).status_code == 404
    r = client.post(
        f"/api/photos/{add_photo()}/report", {"reason": "x"}, content_type="application/json"
    )
    assert r.status_code == 401
