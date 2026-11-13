"""탐색 v6 API (접두사 /api/explore/). 계약: 백엔드·웹 v6 스펙 §6, v6.1 §5"""

from django.urls import path

from explore import views

urlpatterns = [
    path("sigungu/<str:sigungu_id>/pins", views.sigungu_pins, name="explore-pins"),
    path("dongs/<str:dong_id>", views.dong_detail, name="explore-dong"),
    path("dongs/<str:dong_id>/ask", views.DongAsk.as_view(), name="explore-ask"),
]
