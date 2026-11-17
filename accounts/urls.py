"""로그인 사용자 API (접두사 /api).

스펙: docs/superpowers/specs/2026-11-17-login-supabase-design.md
"""

from django.urls import path, re_path

from accounts import views

urlpatterns = [
    path("me", views.MeView.as_view(), name="me"),
    re_path(r"^me/.+$", views.MeNotReadyView.as_view(), name="me-not-ready"),
]
