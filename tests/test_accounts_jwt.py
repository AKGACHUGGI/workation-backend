"""Supabase JWT 검증 accounts.jwt.verify."""

import time

import jwt
import pytest

from accounts.jwt import InvalidToken, verify
from tests.supabase_tokens import (  # noqa: F401
    OTHER_KEY,
    PRIVATE_KEY,
    USER_ID,
    make_claims,
    make_token,
    supabase,
)


def test_valid_token(supabase):  # noqa: F811
    claims = verify(make_token())
    assert claims["sub"] == USER_ID
    assert claims["app_metadata"]["provider"] == "kakao"


@pytest.mark.parametrize(
    "over",
    [
        {"exp": int(time.time()) - 10},
        {"aud": "anon"},
        {"iss": "https://evil.supabase.co/auth/v1"},
    ],
    ids=["expired", "wrong-aud", "wrong-iss"],
)
def test_rejects_bad_claims(supabase, over):  # noqa: F811
    with pytest.raises(InvalidToken):
        verify(make_token(**over))


def test_rejects_wrong_signature(supabase):  # noqa: F811
    with pytest.raises(InvalidToken):
        verify(make_token(key=OTHER_KEY))


def test_rejects_garbage(supabase):  # noqa: F811
    with pytest.raises(InvalidToken):
        verify("not-a-jwt")


def test_missing_exp_rejected(supabase):  # noqa: F811
    claims = make_claims()
    del claims["exp"]
    token = jwt.encode(claims, PRIVATE_KEY, algorithm="ES256")
    with pytest.raises(InvalidToken):
        verify(token)


def test_hs256_secret_mode(supabase, settings):  # noqa: F811
    settings.SUPABASE_JWT_SECRET = "legacy-secret-at-least-32-bytes-long!!"
    token = jwt.encode(make_claims(), settings.SUPABASE_JWT_SECRET, algorithm="HS256")
    assert verify(token)["sub"] == USER_ID
    # 비밀 모드에서는 ES256 토큰을 받지 않는다
    with pytest.raises(InvalidToken):
        verify(make_token())


def test_not_configured(settings):
    settings.SUPABASE_URL = ""
    with pytest.raises(InvalidToken):
        verify(make_token())
