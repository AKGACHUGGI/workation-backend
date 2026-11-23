"""GET · PATCH /api/me 의 ageGroup (연령대). 설계 5-4절"""

import pytest

from accounts.models import AppUser
from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_accounts_api import get_me, patch_me
from tests.test_me_api import auth

pytestmark = pytest.mark.django_db

T = "2026-11-23T12:00:00+09:00"


def test_me_age_group_null_for_new_user(client, supabase):  # noqa: F811
    r = get_me(client, **auth())
    assert r.json()["ageGroup"] is None


def test_patch_age_group_saves(client, supabase):  # noqa: F811
    get_me(client, **auth())
    r = patch_me(client, {"ageGroup": "30s"}, **auth())
    assert r.status_code == 200
    assert r.json()["ageGroup"] == "30s"
    assert AppUser.objects.get().age_group == "30s"


def test_patch_age_group_rejects_unknown(client, supabase):  # noqa: F811
    get_me(client, **auth())
    for body in ({"ageGroup": "100s"}, {"ageGroup": 30}):
        r = patch_me(client, body, **auth())
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "INVALID_AGE_GROUP"
        assert AppUser.objects.get().age_group is None


def test_onboarding_requires_age_group(client, supabase):  # noqa: F811
    get_me(client, **auth())
    before = AppUser.objects.get().nickname
    r = patch_me(client, {"nickname": "두이두이", "agreedAt": T}, **auth())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "AGE_GROUP_REQUIRED"
    user = AppUser.objects.get()
    assert user.agreed_at is None
    assert user.nickname == before


def test_invalid_nickname_reported_first(client, supabase):  # noqa: F811
    get_me(client, **auth())
    r = patch_me(client, {"nickname": "x", "ageGroup": "100s", "agreedAt": T}, **auth())
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_NICKNAME"


def test_onboarding_with_age_group_ok(client, supabase):  # noqa: F811
    get_me(client, **auth())
    r = patch_me(client, {"nickname": "두이두이", "agreedAt": T, "ageGroup": "20s"}, **auth())
    assert r.status_code == 200
    body = r.json()
    assert body["needsOnboarding"] is False
    assert body["ageGroup"] == "20s"


def test_agreed_user_with_age_may_resend_agreed_at(client, supabase):  # noqa: F811
    get_me(client, **auth())
    patch_me(client, {"nickname": "두이두이", "agreedAt": T, "ageGroup": "20s"}, **auth())
    r = patch_me(client, {"agreedAt": T}, **auth())
    assert r.status_code == 200
