"""로그인 사용자 API (접두사 /api).

스펙: docs/superpowers/specs/2026-11-17-login-supabase-design.md
"""

from django.urls import path

from accounts import views

urlpatterns = [
    path("me", views.MeView.as_view(), name="me"),
    # 즐겨찾기 · 요약 · 내 팁 · 내 사진(/api/me/...)은 webapi/me.py.
    # 알림 설정은 저장할 칸이 DB 에 아직 없어 501 로 둔다
    path("me/settings", views.MeNotReadyView.as_view(), name="me-not-ready"),
]
