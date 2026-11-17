"""accounts 서비스: app_user 생성 · 이메일 마스킹 · 기본 닉네임 · Supabase 사용자 삭제."""

import io
import urllib.error

import pytest

from accounts import services
from accounts.models import AppUser
from accounts.services import (
    AuthAdminError,
    UnsupportedProvider,
    default_nickname,
    delete_auth_user,
    get_or_create_user,
    mask_email,
)
from tests.supabase_tokens import USER_ID, make_claims, supabase  # noqa: F401


@pytest.mark.django_db
def test_app_user_has_auth_columns():
    AppUser.objects.create(
        user_id=USER_ID,
        provider="kakao",
        provider_uid="123",
        nickname="두이",
        email_masked="du***@kakao.com",
    )
    user = AppUser.objects.get(user_id=USER_ID)
    assert user.email_masked == "du***@kakao.com"
    assert user.agreed_at is None
    assert user.created_at is not None


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("duiduck@kakao.com", "du***@kakao.com"),
        ("ab@gmail.com", "a***@gmail.com"),
        ("a@x.io", "a***@x.io"),
        (None, None),
        ("", None),
        ("no-at-sign", None),
    ],
)
def test_mask_email(email, expected):
    assert mask_email(email) == expected


@pytest.mark.parametrize(
    ("meta", "expected"),
    [
        ({"name": "두이"}, "두이"),
        ({"name": "  두이  "}, "두이"),
        ({"name": "가나다라마바사아자차카타파"}, "가나다라마바사아자차카타"),
        ({"name": "두", "nickname": "두이두이"}, "두이두이"),
        ({"full_name": "Dui Kim"}, "Dui Kim"),
        ({"name": "두"}, "워케이션러"),
        ({}, "워케이션러"),
    ],
)
def test_default_nickname(meta, expected):
    assert default_nickname({"user_metadata": meta}) == expected


@pytest.mark.django_db
def test_create_user_from_claims():
    user = get_or_create_user(make_claims())
    assert str(user.user_id) == USER_ID
    assert (user.provider, user.provider_uid, user.nickname) == ("kakao", "123456789", "두이")
    assert user.avatar_url == "https://img.example/a.png"
    assert user.email_masked == "du***@kakao.com"
    assert user.agreed_at is None and user.created_at is not None


@pytest.mark.django_db
def test_second_call_keeps_existing_row():
    get_or_create_user(make_claims())
    AppUser.objects.filter(user_id=USER_ID).update(nickname="바뀐이름")
    user = get_or_create_user(make_claims())
    assert user.nickname == "바뀐이름"
    assert AppUser.objects.count() == 1


@pytest.mark.django_db
def test_provider_uid_falls_back_to_sub():
    user = get_or_create_user(make_claims(user_metadata={"name": "두이"}, email=None))
    assert user.provider_uid == USER_ID
    assert user.email_masked is None and user.avatar_url is None


@pytest.mark.django_db
def test_orphan_row_with_same_social_account_replaced():
    """Supabase 에서만 지워진 옛 계정 행이 같은 (provider, provider_uid) 를 쥐고 있으면 지우고 새로 만든다."""
    AppUser.objects.create(
        user_id="00000000-0000-4000-8000-000000000001",
        provider="kakao",
        provider_uid="123456789",
        nickname="옛계정",
    )
    user = get_or_create_user(make_claims())
    assert str(user.user_id) == USER_ID
    assert AppUser.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("provider", ["apple", "email", None])
def test_unsupported_provider(provider):
    with pytest.raises(UnsupportedProvider):
        get_or_create_user(make_claims(app_metadata={"provider": provider}))
    assert AppUser.objects.count() == 0


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_delete_auth_user_calls_admin_api(supabase, monkeypatch):  # noqa: F811
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["method"] = req.full_url, req.get_method()
        seen["auth"] = req.get_header("Authorization")
        seen["apikey"] = req.get_header("Apikey")
        return _Resp(b"{}")

    monkeypatch.setattr(services.urllib.request, "urlopen", fake_urlopen)
    delete_auth_user(USER_ID)
    assert seen == {
        "url": f"https://test.supabase.co/auth/v1/admin/users/{USER_ID}",
        "method": "DELETE",
        "auth": "Bearer service-role-test",
        "apikey": "service-role-test",
    }


def _raise(exc):
    def fake_urlopen(req, timeout):
        raise exc

    return fake_urlopen


def test_delete_auth_user_404_is_ok(supabase, monkeypatch):  # noqa: F811
    err = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    monkeypatch.setattr(services.urllib.request, "urlopen", _raise(err))
    delete_auth_user(USER_ID)


@pytest.mark.parametrize(
    "exc",
    [urllib.error.HTTPError("u", 500, "err", {}, None), urllib.error.URLError("down"), TimeoutError()],
    ids=["500", "network", "timeout"],
)
def test_delete_auth_user_failure(supabase, monkeypatch, exc):  # noqa: F811
    monkeypatch.setattr(services.urllib.request, "urlopen", _raise(exc))
    with pytest.raises(AuthAdminError):
        delete_auth_user(USER_ID)


def test_delete_auth_user_not_configured(settings):
    settings.SUPABASE_URL = ""
    with pytest.raises(AuthAdminError):
        delete_auth_user(USER_ID)
