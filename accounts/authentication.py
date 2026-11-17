"""DRF 인증: Authorization: Bearer <Supabase access token>.

헤더가 없으면 비로그인(None), 있는데 틀리면 401. 성공하면 (AppUser 또는 None, claims).
AppUser 가 None 이면 첫 로그인 — GET /api/me 가 만든다.
"""

import uuid

from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from accounts.jwt import InvalidToken, verify
from accounts.models import AppUser


class SupabaseJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != b"bearer":
            return None
        if len(parts) != 2:
            raise AuthenticationFailed("Authorization 헤더 형식이 잘못됐습니다.")
        try:
            claims = verify(parts[1].decode())
            user_id = uuid.UUID(claims["sub"])
        except (InvalidToken, UnicodeDecodeError, ValueError) as ex:
            raise AuthenticationFailed("토큰이 유효하지 않습니다.") from ex
        return AppUser.objects.filter(user_id=user_id).first(), claims

    def authenticate_header(self, request):
        # 있어야 DRF 가 인증 실패를 403 이 아니라 401 로 돌려준다
        return 'Bearer realm="api"'
