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
from metrics.scoring import AGE_GROUPS

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
        "ageGroup": user.age_group,
        "createdAt": timezone.localtime(user.created_at).isoformat(),
    }


class LoginAPIView(APIView):
    """Supabase JWT 로 인증하는 뷰. request.user 는 AppUser 또는 None."""

    authentication_classes = [SupabaseJWTAuthentication]
    permission_classes = []

    def handle_exception(self, exc):
        # 인증 실패도 다른 API 와 같은 에러 형식으로
        if isinstance(exc, (AuthenticationFailed, NotAuthenticated)):
            return unauthorized()
        return super().handle_exception(exc)


class MeView(LoginAPIView):
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
        # 검사를 모두 끝낸 뒤 한 번에 저장한다. 첫 실패 하나만 돌려주고 어떤 칸도 바꾸지 않는다
        # (설계 2026-11-23-peer-personalization-design.md 5-4절: ① 닉네임 → ② 연령대 형식 → ③ 필수)
        updates = {}
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
            updates["nickname"] = nickname.strip()
        if "ageGroup" in body:
            age_group = body["ageGroup"]
            if not isinstance(age_group, str) or age_group not in AGE_GROUPS:
                return error(400, "INVALID_AGE_GROUP", "연령대를 선택해 주세요")
            updates["age_group"] = age_group
        if body.get("agreedAt"):
            # 온보딩(약관 동의)에는 연령대가 필수다.
            # 이미 연령대가 있으면 동의 시각만 다시 보내도 된다
            if not updates.get("age_group") and not user.age_group:
                return error(400, "AGE_GROUP_REQUIRED", "연령대를 선택해 주세요")
            # 동의 시각은 서버 시각으로 남긴다 (클라이언트 값은 신호로만 쓴다)
            updates["agreed_at"] = timezone.now()
        for field, value in updates.items():
            setattr(user, field, value)
        if updates:
            user.save(update_fields=list(updates))
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


class MeNotReadyView(LoginAPIView):
    """아직 없는 /api/me/settings (알림 설정, 저장할 칸이 DB 에 없음).

    로그인 상태면 501 이라 웹이 세션 만료(401)로 보고 로그아웃하지 않는다.
    """

    def handle(self, request, *args, **kwargs):
        if request.user is None:
            return unauthorized()
        return error(501, "NOT_IMPLEMENTED", "아직 준비 중인 기능입니다.")

    get = post = put = patch = delete = handle
