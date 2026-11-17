"""accounts 서비스: app_user 생성 · 이메일 마스킹 · 기본 닉네임 · Supabase 사용자 삭제."""

import pytest

from accounts.models import AppUser

USER_ID = "3f0c1d2e-4b5a-4c6d-8e7f-9a0b1c2d3e4f"


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
