"""마이페이지 · 즐겨찾기 (/api/me/*). 웹 W-08 · 모바일 M-05 · M-07.

계약: workation-web src/features/me/types.ts · api.ts. 모바일도 같은 경로를 쓴다.
GET · PATCH · DELETE /api/me 자체는 accounts/views.py 에 있다.
"""

from rest_framework.exceptions import NotAuthenticated
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from accounts.views import LoginAPIView
from core.errors import bad_request, error, not_found
from metrics import scoring
from webapi import community, data, photo_quarantine, photo_upload


class MeAPIView(LoginAPIView):
    """로그인해야 쓰는 뷰. 토큰이 없거나 app_user 가 아직 없으면(첫 GET /api/me 전) 401."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if request.user is None:
            raise NotAuthenticated()


# ───────────────────────── 즐겨찾기 ─────────────────────────


class FavoriteIdsView(MeAPIView):
    def get(self, request):
        return Response(community.favorite_ids(request.user.user_id))


class FavoritesView(MeAPIView):
    def get(self, request):
        try:
            month, month_str = data.parse_month(request.query_params.get("month"))
        except data.BadMonth as ex:
            return bad_request(str(ex))
        regions, dongs = community.favorites(
            request.user.user_id, month, scoring.peer_basis(request.user.age_group)
        )
        return Response({"month": month_str, "regions": regions, "dongs": dongs})


class FavoriteItemView(MeAPIView):
    """PUT · DELETE /api/me/favorites/{regions|dongs}/{id}. 둘 다 여러 번 불러도 결과가 같다."""

    def put(self, request, kind: str, item_id: str):
        scope = community.KIND_SCOPE.get(kind)
        unit = community.unit_id(scope, item_id) if scope else None
        if unit is None:
            return not_found("즐겨찾기할 지역 · 동네를 찾을 수 없습니다.")
        if not community.put_favorite(request.user.user_id, scope, unit):
            return error(
                409,
                "FAVORITE_LIMIT",
                f"즐겨찾기는 지역 · 동네별로 {community.FAVORITE_LIMIT}개까지 저장할 수 있어요.",
            )
        return Response(status=204)

    def delete(self, request, kind: str, item_id: str):
        scope = community.KIND_SCOPE.get(kind)
        if scope is None:
            return not_found("즐겨찾기 종류는 regions 또는 dongs 입니다.")
        unit = community.unit_id(scope, item_id)
        if unit is not None:  # 없는 id 는 지울 것이 없다
            community.delete_favorite(request.user.user_id, scope, unit)
        return Response(status=204)


# ───────────────────────── 팁 쓰기 ─────────────────────────


class NoteCreateView(MeAPIView):
    """POST /api/{regions|dongs}/{id}/notes. 201 + 만든 팁(Note). 같은 경로의 GET 은 views.py.

    사진이 없으면 JSON {body, visitMonth?}, 있으면 multipart (body · visitMonth · photos 0~3장).
    kind(regions · dongs)는 urls.py 의 하위 클래스가 정한다.
    """

    kind = ""
    parser_classes = [JSONParser, MultiPartParser]

    def post(self, request, sgg_id: str | None = None, dong_id: str | None = None):
        region_id, dong = None, None
        if self.kind == "regions":
            region = community.region_by_code(sgg_id)
            region_id = region.region_id if region else None
        else:
            dong = community.dong_by_code(dong_id)
            if dong is not None and dong.is_excluded:  # 집계 제외 동은 팁 대상이 아니다
                dong = None
            region_id = dong.region_id if dong else None
        if region_id is None:
            return not_found("팁을 쓸 지역 · 동네를 찾을 수 없습니다.")
        payload = request.data
        if (request.content_type or "").startswith("multipart/"):
            payload = {k: payload[k] for k in ("body", "visitMonth") if k in payload}
        try:
            body, visit_month = community.parse_tip(payload)
        except community.BadTip as ex:
            return bad_request(str(ex))
        try:
            photos = photo_upload.process(request.FILES.getlist("photos"), min_count=0)
        except photo_upload.BadPhoto as ex:
            return error(400, "INVALID_PHOTO", str(ex))
        try:
            note = community.create_tip(request.user, region_id, dong, body, visit_month, photos)
        except photo_upload.UploadFailed:
            return error(
                502, "UPLOAD_FAILED", "사진을 올리지 못했어요. 잠시 후 다시 시도해 주세요."
            )
        if note is None:
            return error(
                429,
                "TIP_RATE_LIMIT",
                "팁을 너무 빠르게 올리고 있어요. 잠시 후 다시 시도해 주세요.",
            )
        return Response(note, status=201)


# ───────────────────────── MY ─────────────────────────


class SummaryView(MeAPIView):
    def get(self, request):
        user = request.user
        return Response(
            community.summary(user.user_id) | {"createdAt": community.iso(user.created_at)}
        )


class MyTipsView(MeAPIView):
    def get(self, request):
        try:
            page = community.tip_page(
                "user",
                request.user.user_id,
                "recent",
                request.query_params.get("cursor"),
                viewer=request.user.user_id,
            )
        except community.BadCursor as ex:
            return bad_request(str(ex))
        body = {"items": page["items"], "helpfulSum": page["helpfulSum"]}
        if page["nextCursor"]:
            body["nextCursor"] = page["nextCursor"]
        return Response(body)


def int_id(raw: str) -> int | None:
    """INTEGER 키(tip_id · reply_id) 범위의 숫자만 int 로, 아니면 None."""
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 9 else None


class MyTipView(MeAPIView):
    def patch(self, request, tip_id: str):
        """본문만 고친다 {body}. 200 + 고친 팁(Note)."""
        if int_id(tip_id) is None:
            return not_found("내 팁을 찾을 수 없습니다.")
        payload = request.data if isinstance(request.data, dict) else None
        try:
            body, _ = community.parse_tip({"body": payload.get("body")} if payload else None)
        except community.BadTip as ex:
            return bad_request(str(ex))
        note = community.update_tip(request.user.user_id, int(tip_id), body)
        if note is None:
            return not_found("내 팁을 찾을 수 없습니다.")
        return Response(note)

    def delete(self, request, tip_id: str):
        if int_id(tip_id) is None:
            return not_found("내 팁을 찾을 수 없습니다.")
        try:
            deleted = community.delete_tip(request.user.user_id, int(tip_id))
        except photo_quarantine.DeleteFailed:
            return error(
                502, "DELETE_FAILED", "사진을 지우지 못했어요. 잠시 후 다시 시도해 주세요."
            )
        if not deleted:
            return not_found("내 팁을 찾을 수 없습니다.")
        return Response(status=204)


class MyPhotosView(MeAPIView):
    def get(self, request):
        return Response({"items": community.my_photos(request.user.user_id)})
