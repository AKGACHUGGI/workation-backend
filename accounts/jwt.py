"""Supabase Auth 가 발급한 access token(JWT) 검증.

SUPABASE_JWT_SECRET 이 있으면 레거시 HS256, 없으면 프로젝트 JWKS(공개키, ES256/RS256)로 검증한다.
aud · iss · exp 는 항상 검사한다. 실패는 모두 InvalidToken 하나로 바꾼다.
"""

from __future__ import annotations

import jwt
from django.conf import settings

AUDIENCE = "authenticated"
ASYMMETRIC_ALGORITHMS = ["ES256", "RS256"]
# 서버 간 시계 오차 허용. 이 서버가 Supabase 보다 느리면 갓 발급된 토큰의 iat 가 미래로 보인다.
# 같은 값이 exp 에도 적용돼 토큰이 30초 늦게 만료된다
LEEWAY_SECONDS = 30

_jwks_client: jwt.PyJWKClient | None = None


class InvalidToken(Exception):
    """서명 · 만료 · aud · iss 중 하나라도 맞지 않는 토큰."""


def _jwks() -> jwt.PyJWKClient:
    """JWKS 클라이언트. 공개키는 1시간 캐시한다 (Supabase 키 교체 주기보다 짧게)."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(
            f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json",
            cache_keys=True,
            lifespan=3600,
            timeout=5,
        )
    return _jwks_client


def verify(token: str) -> dict:
    if not settings.SUPABASE_URL:
        raise InvalidToken("SUPABASE_URL 이 설정되지 않았습니다.")
    try:
        if settings.SUPABASE_JWT_SECRET:
            key, algorithms = settings.SUPABASE_JWT_SECRET, ["HS256"]
        else:
            key, algorithms = _jwks().get_signing_key_from_jwt(token).key, ASYMMETRIC_ALGORITHMS
        return jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=AUDIENCE,
            leeway=LEEWAY_SECONDS,
            issuer=f"{settings.SUPABASE_URL}/auth/v1",
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as ex:
        raise InvalidToken(str(ex)) from ex
