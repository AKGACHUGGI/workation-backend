from django.urls import include, path

urlpatterns = [
    path("api/", include("core.urls")),
    # 화면 API. 명세: workation-docs 명세/api/openapi.yaml (경로 접두사 /api/v1)
    path("api/v1/", include("metrics.urls")),
    path("api/v1/", include("regions.urls")),
    path("api/v1/", include("search.urls")),
    # 탐색 v6 (Neo4j 동네 근거 그래프). webapi 의 api/ 보다 먼저 둔다
    path("api/explore/", include("explore.urls")),
    # 웹 화면 계약 (workation-web src/features/*/types.ts). /api/v1 과 같은 데이터, 모양만 웹에 맞춤
    # 로그인 사용자 (Supabase JWT). /api/me · /api/me/* 를 받는다
    path("api/", include("accounts.urls")),
    path("api/", include("webapi.urls")),
]
