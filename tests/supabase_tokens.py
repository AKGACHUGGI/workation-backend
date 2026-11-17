"""Supabase JWT 테스트 도구. 테스트용 ES256 키쌍으로 서명하고 JWKS 조회는 가짜로 바꾼다.

쓰는 쪽: from tests.supabase_tokens import supabase  # noqa: F401
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

SUPABASE_URL = "https://test.supabase.co"
USER_ID = "3f0c1d2e-4b5a-4c6d-8e7f-9a0b1c2d3e4f"
PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def make_claims(**over) -> dict:
    now = int(time.time())
    claims = {
        "sub": USER_ID,
        "aud": "authenticated",
        "iss": f"{SUPABASE_URL}/auth/v1",
        "iat": now,
        "exp": now + 3600,
        "role": "authenticated",
        "email": "duiduck@kakao.com",
        "app_metadata": {"provider": "kakao"},
        "user_metadata": {
            "name": "두이",
            "provider_id": "123456789",
            "avatar_url": "https://img.example/a.png",
        },
    }
    claims.update(over)
    return claims


def make_token(key=PRIVATE_KEY, **over) -> str:
    return jwt.encode(make_claims(**over), key, algorithm="ES256", headers={"kid": "test"})


class _FakeJWKS:
    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=PRIVATE_KEY.public_key())


@pytest.fixture
def supabase(settings, monkeypatch):
    """비대칭 키(JWKS) 모드의 Supabase. Admin API 키도 채운다."""
    settings.SUPABASE_URL = SUPABASE_URL
    settings.SUPABASE_JWT_SECRET = ""
    settings.SUPABASE_SERVICE_ROLE_KEY = "service-role-test"
    monkeypatch.setattr("accounts.jwt._jwks", lambda: _FakeJWKS())
