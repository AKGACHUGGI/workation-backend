from django.urls import path

from regions import views

urlpatterns = [
    path("regions/<str:region_id>", views.region_detail, name="region-detail"),
    path("regions/<str:region_id>/months", views.region_months, name="region-months"),
    path("dongs/<str:dong_id>", views.dong_detail, name="dong-detail"),
]
