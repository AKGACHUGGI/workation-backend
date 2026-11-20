"""웹 화면 계약 API (접두사 /api). 계약: workation-web src/features/*/types.ts"""

from django.urls import path, re_path

from webapi import me, poll, uploads, views


class RegionNoteCreateView(me.NoteCreateView):
    kind = "regions"


class DongNoteCreateView(me.NoteCreateView):
    kind = "dongs"


class RegionMonthPhotoView(uploads.MonthPhotoView):
    kind = "regions"


class DongMonthPhotoView(uploads.MonthPhotoView):
    kind = "dongs"


class RegionPollView(poll.PollView):
    kind = "regions"


class DongPollView(poll.PollView):
    kind = "dongs"


class RegionPollVoteView(poll.PollVoteView):
    kind = "regions"


class DongPollVoteView(poll.PollVoteView):
    kind = "dongs"


def read_or_write(read, write):
    """같은 경로에서 POST 는 로그인이 필요한 쓰기 뷰로, 나머지는 읽기 뷰로 보낸다."""

    def view(request, **kwargs):
        return (write if request.method == "POST" else read)(request, **kwargs)

    return view


urlpatterns = [
    path("tier", views.tier, name="web-tier"),
    path("regions/popular", views.popular, name="web-popular"),
    path(
        "regions/<str:sgg_id>/notes",
        read_or_write(views.region_notes, RegionNoteCreateView.as_view()),
        name="web-region-notes",
    ),
    path("regions/<str:sgg_id>/photos", RegionMonthPhotoView.as_view(), name="web-region-photos"),
    path("regions/<str:sgg_id>/poll/vote", RegionPollVoteView.as_view(), name="web-region-vote"),
    path("regions/<str:sgg_id>/poll", RegionPollView.as_view(), name="web-region-poll"),
    path("regions/<str:sgg_id>/view", views.record_view, name="web-region-view"),
    path("regions/<str:sgg_id>", views.region_detail, name="web-region"),
    path(
        "dongs/<str:dong_id>/notes",
        read_or_write(views.dong_notes, DongNoteCreateView.as_view()),
        name="web-dong-notes",
    ),
    path("dongs/<str:dong_id>/photos", DongMonthPhotoView.as_view(), name="web-dong-photos"),
    path("dongs/<str:dong_id>/poll/vote", DongPollVoteView.as_view(), name="web-dong-vote"),
    path("dongs/<str:dong_id>/poll", DongPollView.as_view(), name="web-dong-poll"),
    path("dongs/<str:dong_id>", views.dong_detail, name="web-dong"),
    path(
        "photos/<str:photo_id>/report", uploads.PhotoReportView.as_view(), name="web-photo-report"
    ),
    path("search/parse", views.search_parse, name="web-search-parse"),
    path("events", views.events, name="web-events"),
    # 마이페이지 · 즐겨찾기 (로그인 필요).
    # /api/me 와 아직 없는 /api/me/settings 는 accounts 가 받는다
    path("me/favorites/ids", me.FavoriteIdsView.as_view(), name="me-favorite-ids"),
    path("me/favorites", me.FavoritesView.as_view(), name="me-favorites"),
    path(
        "me/favorites/<str:kind>/<str:item_id>",
        me.FavoriteItemView.as_view(),
        name="me-favorite-item",
    ),
    path("me/summary", me.SummaryView.as_view(), name="me-summary"),
    path("me/tips", me.MyTipsView.as_view(), name="me-tips"),
    path("me/tips/<str:tip_id>", me.MyTipView.as_view(), name="me-tip"),
    path("me/photos", me.MyPhotosView.as_view(), name="me-photos"),
    path("me/photos/<str:photo_id>", uploads.MyPhotoView.as_view(), name="me-photo"),
    # 예전 쿠키 로그인 경로
    re_path(r"^auth(/.*)?$", views.unauthorized, name="web-auth"),
]
