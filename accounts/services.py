"""로그인 사용자 관리. 토큰 검증이 끝난 claims 로 app_user 를 만들고, 계정 삭제 때 Supabase 사용자도 지운다."""

from __future__ import annotations

import urllib.error
import urllib.request

from django.conf import settings
from django.db import connection, transaction

from accounts.models import AppUser

SUPPORTED_PROVIDERS = ("kakao", "google")
NICKNAME_MIN = 2
NICKNAME_MAX = 12
DEFAULT_NICKNAME = "워케이션러"


class UnsupportedProvider(Exception):
    """kakao · google 이 아닌 로그인 (이메일 · Apple 등)."""


class AuthAdminError(Exception):
    """Supabase Admin API 호출 실패."""


def mask_email(email: str | None) -> str | None:
    """duiduck@kakao.com → du***@kakao.com. 로컬부가 2자 이하면 앞 1자만 남긴다."""
    if not email or "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    keep = 2 if len(local) > 2 else 1
    return f"{local[:keep]}***@{domain}"


def default_nickname(claims: dict) -> str:
    """제공자 프로필 이름. name → nickname → full_name 중 2자 이상인 첫 값을 12자로 자른다."""
    meta = claims.get("user_metadata") or {}
    for key in ("name", "nickname", "full_name"):
        value = (meta.get(key) or "").strip()
        if len(value) >= NICKNAME_MIN:
            return value[:NICKNAME_MAX]
    return DEFAULT_NICKNAME


def get_or_create_user(claims: dict) -> AppUser:
    """첫 GET /api/me 에서 부른다. 이미 있으면 건드리지 않는다."""
    provider = (claims.get("app_metadata") or {}).get("provider")
    if provider not in SUPPORTED_PROVIDERS:
        raise UnsupportedProvider(provider)
    meta = claims.get("user_metadata") or {}
    user_id = claims["sub"]
    provider_uid = str(meta.get("provider_id") or meta.get("sub") or user_id)[:100]
    avatar_url = meta.get("avatar_url") or meta.get("picture")
    with transaction.atomic(), connection.cursor() as cursor:
        # Supabase 에서만 지워진 옛 계정 행이 같은 소셜 계정을 쥐고 있으면 UNIQUE 충돌이 나므로 지운다
        cursor.execute(
            "DELETE FROM app_user WHERE provider = %s AND provider_uid = %s AND user_id <> %s",
            [provider, provider_uid, user_id],
        )
        # 같은 사용자의 첫 요청이 동시에 와도 한 행만 생긴다
        cursor.execute(
            "INSERT INTO app_user (user_id, provider, provider_uid, nickname, avatar_url, email_masked)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (user_id) DO NOTHING",
            [
                user_id,
                provider,
                provider_uid,
                default_nickname(claims),
                avatar_url,
                mask_email(claims.get("email")),
            ],
        )
    return AppUser.objects.get(user_id=user_id)


def delete_auth_user(user_id) -> None:
    """Supabase auth.users 에서 사용자를 지운다. 이미 없으면(404) 성공으로 본다."""
    url, key = settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY
    if not url or not key:
        raise AuthAdminError("SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY 가 설정되지 않았습니다.")
    req = urllib.request.Request(
        f"{url}/auth/v1/admin/users/{user_id}",
        method="DELETE",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except urllib.error.HTTPError as ex:
        if ex.code == 404:
            return
        raise AuthAdminError(f"Supabase Admin API {ex.code}") from ex
    except (urllib.error.URLError, TimeoutError) as ex:
        raise AuthAdminError("Supabase Admin API 연결 실패") from ex
