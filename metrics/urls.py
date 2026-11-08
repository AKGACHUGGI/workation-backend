from django.urls import path

from metrics import views

urlpatterns = [
    path("map/scores", views.map_scores, name="map-scores"),
    path("tiers", views.tiers, name="tiers"),
]
