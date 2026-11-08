"""웹 화면 계약 API (접두사 /api). 계약: workation-web src/features/*/types.ts"""

from django.urls import path, re_path

from webapi import views

urlpatterns = [
    path("tier", views.tier, name="web-tier"),
    path("regions/popular", views.popular, name="web-popular"),
    path("regions/<str:sgg_id>/notes", views.empty_notes, name="web-region-notes"),
    path("regions/<str:sgg_id>/poll", views.empty_poll, name="web-region-poll"),
    path("regions/<str:sgg_id>/view", views.record_view, name="web-region-view"),
    path("regions/<str:sgg_id>", views.region_detail, name="web-region"),
    path("dongs/<str:dong_id>/notes", views.empty_notes, name="web-dong-notes"),
    path("dongs/<str:dong_id>/poll", views.empty_poll, name="web-dong-poll"),
    path("dongs/<str:dong_id>", views.dong_detail, name="web-dong"),
    path("search/parse", views.search_parse, name="web-search-parse"),
    re_path(r"^(auth|me)(/.*)?$", views.unauthorized, name="web-auth"),
]
