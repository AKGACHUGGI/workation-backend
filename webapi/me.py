"""마이페이지 · 즐겨찾기 (/api/me/*). 웹 W-08 · 모바일 M-05 · M-07.

계약: workation-web src/features/me/types.ts · api.ts. 모바일도 같은 경로를 쓴다.
GET · PATCH · DELETE /api/me 자체는 accounts/views.py 에 있다.
"""

from rest_framework.exceptions import NotAuthenticated
from rest_framework.response import Response

from accounts.views import LoginAPIView
from core.errors import bad_request, error, not_found
from webapi import community, data


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
        regions, dongs = community.favorites(request.user.user_id, month)
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
                "user", request.user.user_id, "recent", request.query_params.get("cursor")
            )
        except community.BadCursor as ex:
            return bad_request(str(ex))
        body = {"items": page["items"], "helpfulSum": page["helpfulSum"]}
        if page["nextCursor"]:
            body["nextCursor"] = page["nextCursor"]
        return Response(body)


class MyTipView(MeAPIView):
    def delete(self, request, tip_id: str):
        # tip_id 는 INTEGER 라 그 범위의 숫자만 본다
        if not (tip_id.isascii() and tip_id.isdigit() and len(tip_id) <= 9) or not (
            community.delete_tip(request.user.user_id, int(tip_id))
        ):
            return not_found("내 팁을 찾을 수 없습니다.")
        return Response(status=204)


class MyPhotosView(MeAPIView):
    def get(self, request):
        return Response({"items": community.my_photos(request.user.user_id)})
