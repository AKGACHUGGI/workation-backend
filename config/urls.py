from django.urls import include, path

urlpatterns = [
    path("api/", include("core.urls")),
    # 화면 API. 명세: workation-docs 명세/api/openapi.yaml (경로 접두사 /api/v1)
    path("api/v1/", include("metrics.urls")),
    path("api/v1/", include("regions.urls")),
    path("api/v1/", include("search.urls")),
]
