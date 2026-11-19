"""GET · PATCH · DELETE /api/me (Supabase JWT). 계약: workation-web src/features/auth/types.ts Me"""

import pytest

from accounts import views
from accounts.models import AppUser
from accounts.services import AuthAdminError
from tests.supabase_tokens import OTHER_KEY, USER_ID, make_token, supabase  # noqa: F401

pytestmark = pytest.mark.django_db


def auth(token=None):
    return {"HTTP_AUTHORIZATION": f"Bearer {token or make_token()}"}


def get_me(client, **headers):
    return client.get("/api/me", **headers)


def patch_me(client, body, **headers):
    return client.patch("/api/me", body, content_type="application/json", **headers)


def assert_unauthorized(r):
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_no_header_401(client, supabase):  # noqa: F811
    assert_unauthorized(get_me(client))


def test_bad_token_401(client, supabase):  # noqa: F811
    assert_unauthorized(get_me(client, **auth(make_token(key=OTHER_KEY))))
    assert_unauthorized(get_me(client, HTTP_AUTHORIZATION="Bearer"))
    assert AppUser.objects.count() == 0


def test_non_uuid_sub_401(client, supabase):  # noqa: F811
    assert_unauthorized(get_me(client, **auth(make_token(sub="not-a-uuid"))))


def test_first_get_creates_user(client, supabase):  # noqa: F811
    r = get_me(client, **auth())
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == USER_ID
    assert body["nickname"] == "두이" and body["initial"] == "두"
    assert body["provider"] == "kakao"
    assert body["emailMasked"] == "du***@kakao.com"
    assert body["needsOnboarding"] is True
    assert body["createdAt"].endswith("+09:00")
    assert AppUser.objects.count() == 1


def test_second_get_does_not_create(client, supabase):  # noqa: F811
    get_me(client, **auth())
    get_me(client, **auth())
    assert AppUser.objects.count() == 1


def test_no_email_gives_empty_string(client, supabase):  # noqa: F811
    r = get_me(client, **auth(make_token(email=None)))
    assert r.json()["emailMasked"] == ""


def test_unsupported_provider_401(client, supabase):  # noqa: F811
    assert_unauthorized(get_me(client, **auth(make_token(app_metadata={"provider": "email"}))))


@pytest.mark.parametrize("nickname", ["두", " 두 ", "가나다라마바사아자차카타파", 3, None])
def test_patch_invalid_nickname_400(client, supabase, nickname):  # noqa: F811
    get_me(client, **auth())
    r = patch_me(client, {"nickname": nickname}, **auth())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_NICKNAME"
    assert AppUser.objects.get().nickname == "두이"


def test_patch_nickname_and_agree(client, supabase):  # noqa: F811
    get_me(client, **auth())
    r = patch_me(
        client, {"nickname": "  워케이션  ", "agreedAt": "2026-11-17T12:00:00+09:00"}, **auth()
    )
    assert r.status_code == 200
    body = r.json()
    assert body["nickname"] == "워케이션" and body["initial"] == "워"
    assert body["needsOnboarding"] is False
    assert AppUser.objects.get().agreed_at is not None


def test_patch_before_get_401(client, supabase):  # noqa: F811
    assert_unauthorized(patch_me(client, {"nickname": "두이"}, **auth()))


def test_delete_removes_user(client, supabase, monkeypatch):  # noqa: F811
    called = []
    monkeypatch.setattr(views, "delete_auth_user", lambda uid: called.append(str(uid)))
    get_me(client, **auth())
    r = client.delete("/api/me", **auth())
    assert r.status_code == 204
    assert called == [USER_ID]
    assert AppUser.objects.count() == 0


def test_delete_admin_failure_502_keeps_row(client, supabase, monkeypatch):  # noqa: F811
    def boom(uid):
        raise AuthAdminError("down")

    monkeypatch.setattr(views, "delete_auth_user", boom)
    get_me(client, **auth())
    r = client.delete("/api/me", **auth())
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "AUTH_PROVIDER_ERROR"
    assert AppUser.objects.count() == 1


def test_me_subpaths_401_without_login(client, supabase):  # noqa: F811
    """/api/me/* 는 토큰이 없거나 틀리면 401. 예전 /api/auth/* 도 401."""
    assert_unauthorized(client.get("/api/me/summary"))
    assert_unauthorized(client.get("/api/me/summary", **auth(make_token(key=OTHER_KEY))))
    assert_unauthorized(client.get("/api/auth/me"))


def test_me_settings_501_when_logged_in(client, supabase):  # noqa: F811
    """알림 설정 /api/me/settings 는 아직 없다 (저장할 칸이 DB 에 없음).
    로그인 상태면 501 이라 웹이 로그아웃하지 않는다. 나머지 /api/me/* 는 tests/test_me_api.py"""
    assert_unauthorized(client.get("/api/me/settings"))
    get_me(client, **auth())
    for method in (client.get, client.patch):
        r = method("/api/me/settings", **auth())
        assert r.status_code == 501
        assert r.json()["error"]["code"] == "NOT_IMPLEMENTED"
    assert_unauthorized(client.get("/api/auth/me", **auth()))
