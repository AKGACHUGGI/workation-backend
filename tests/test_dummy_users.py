"""manage.py dummy_users create · delete (네트워크 없이). Admin API 함수는 가짜로 바꾼다.
설계: docs/superpowers/specs/2026-11-23-peer-personalization-design.md 5-5절"""

import uuid

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import AppUser
from accounts.services import AuthAdminError

pytestmark = pytest.mark.django_db

MODULE = "accounts.management.commands.dummy_users"


def fixed_uid(email: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, email))


def fake_create(email, password):
    return fixed_uid(email)


def dummies():
    return AppUser.objects.filter(provider="dummy")


@pytest.fixture
def env(settings, monkeypatch):
    settings.SUPABASE_URL = "https://example.supabase.co"
    settings.SUPABASE_SERVICE_ROLE_KEY = "service"
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("DUMMY_USER_PASSWORD", "pw")


def test_dummy_users_requires_service_key(env, settings):
    settings.SUPABASE_SERVICE_ROLE_KEY = ""
    with pytest.raises(CommandError, match="SUPABASE_SERVICE_ROLE_KEY"):
        call_command("dummy_users", "create")


def test_dummy_users_create_is_idempotent(env, monkeypatch):
    monkeypatch.setattr(f"{MODULE}.admin_create_or_get", fake_create)
    call_command("dummy_users", "create")
    call_command("dummy_users", "create")
    assert dummies().count() == 10
    assert dummies().get(provider_uid="dummy-30s@workcation.test").age_group == "30s"
    assert dummies().get(provider_uid="dummy-none@workcation.test").age_group is None
    assert all(u.agreed_at is not None for u in dummies())


def test_dummy_users_create_replaces_stale_id(env, monkeypatch):
    monkeypatch.setattr(f"{MODULE}.admin_create_or_get", fake_create)
    call_command("dummy_users", "create")
    new_uid = uuid.uuid4()

    def recreated(email, password):
        return str(new_uid) if email == "dummy-30s@workcation.test" else fixed_uid(email)

    monkeypatch.setattr(f"{MODULE}.admin_create_or_get", recreated)
    call_command("dummy_users", "create")
    assert dummies().count() == 10
    assert dummies().get(provider_uid="dummy-30s@workcation.test").user_id == new_uid


def test_dummy_users_delete_auth_first(env, monkeypatch):
    monkeypatch.setattr(f"{MODULE}.admin_create_or_get", fake_create)
    call_command("dummy_users", "create")
    deleted = []
    failing = fixed_uid("dummy-50s@workcation.test")

    def fake_delete(user_id):
        deleted.append(str(user_id))
        if str(user_id) == failing:
            raise AuthAdminError("boom")

    monkeypatch.setattr(f"{MODULE}.admin_find", fixed_uid)
    monkeypatch.setattr(f"{MODULE}.delete_auth_user", fake_delete)
    with pytest.raises(CommandError):
        call_command("dummy_users", "delete")
    assert [u.provider_uid for u in dummies()] == ["dummy-50s@workcation.test"]
    assert len(deleted) == 10


def test_dummy_users_delete_orphan_row(env, monkeypatch):
    monkeypatch.setattr(f"{MODULE}.admin_create_or_get", fake_create)
    call_command("dummy_users", "create")
    deleted = []

    def find(email):
        return None if email == "dummy-10s@workcation.test" else fixed_uid(email)

    monkeypatch.setattr(f"{MODULE}.admin_find", find)
    monkeypatch.setattr(f"{MODULE}.delete_auth_user", lambda user_id: deleted.append(user_id))
    call_command("dummy_users", "delete")
    assert dummies().count() == 0
    assert len(deleted) == 9
