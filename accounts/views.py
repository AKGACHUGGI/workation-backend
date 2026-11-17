"""GET · PATCH · DELETE /api/me. 계약: workation-web src/features/auth/types.ts Me"""

import logging

from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed, NotAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import SupabaseJWTAuthentication
from accounts.models import AppUser
from accounts.services import (
    NICKNAME_MAX,
    NICKNAME_MIN,
    AuthAdminError,
    UnsupportedProvider,
    delete_auth_user,
    get_or_create_user,
)
from core.errors import error

logger = logging.getLogger(__name__)


def unauthorized() -> Response:
    return error(401, "UNAUTHORIZED", "로그인이 필요합니다.")


def serialize_me(user: AppUser) -> dict:
    return {
        "id": str(user.user_id),
        "nickname": user.nickname,
        "initial": user.nickname[:1],
        "provider": user.provider,
        "emailMasked": user.email_masked or "",
        "needsOnboarding": user.agreed_at is None,
        "createdAt": timezone.localtime(user.created_at).isoformat(),
    }


class MeView(APIView):
    authentication_classes = [SupabaseJWTAuthentication]
    permission_classes = []

    def handle_exception(self, exc):
        # 인증 실패도 다른 API 와 같은 에러 형식으로
        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            return unauthorized()
        return super().handle_exception(exc)

    def get(self, request):
        if request.auth is None:
            return unauthorized()
        user = request.user
        if user is None:
            try:
                user = get_or_create_user(request.auth)
            except UnsupportedProvider:
                return unauthorized()
        return Response(serialize_me(user))

    def patch(self, request):
        user = request.user
        if user is None:
            return unauthorized()
        body = request.data if isinstance(request.data, dict) else {}
        fields = []
        if "nickname" in body:
            nickname = body["nickname"]
            if not isinstance(nickname, str) or not (
                NICKNAME_MIN <= len(nickname.strip()) <= NICKNAME_MAX
            ):
                return error(
                    400,
                    "INVALID_NICKNAME",
                    f"닉네임은 {NICKNAME_MIN}~{NICKNAME_MAX}자로 입력해 주세요",
                )
            user.nickname = nickname.strip()
            fields.append("nickname")
        if body.get("agreedAt"):
            # 동의 시각은 서버 시각으로 남긴다 (클라이언트 값은 신호로만 쓴다)
            user.agreed_at = timezone.now()
            fields.append("agreed_at")
        if fields:
            user.save(update_fields=fields)
        return Response(serialize_me(user))

    def delete(self, request):
        user = request.user
        if user is None:
            return unauthorized()
        # Supabase 사용자부터 지운다. 실패하면 아무것도 지우지 않는다 (다시 시도 가능)
        try:
            delete_auth_user(user.user_id)
        except AuthAdminError:
            logger.exception("Supabase 사용자 삭제 실패 user_id=%s", user.user_id)
            return error(
                502,
                "AUTH_PROVIDER_ERROR",
                "계정을 삭제하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            )
        # 즐겨찾기 · 팁 등은 DB 의 ON DELETE CASCADE / SET NULL 이 정리한다
        AppUser.objects.filter(user_id=user.user_id).delete()
        return Response(status=204)
