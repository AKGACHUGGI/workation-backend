"""로그인 사용자 표. DDL 원본은 db/schema.sql (managed = False)."""

from django.db import models
from django.db.models.functions import Now


class AppUser(models.Model):
    """소셜 로그인 사용자. user_id = Supabase auth.users.id (JWT 의 sub)."""

    user_id = models.UUIDField(primary_key=True)
    provider = models.CharField(max_length=10)
    provider_uid = models.CharField(max_length=100)
    nickname = models.CharField(max_length=20)
    avatar_url = models.TextField(null=True)
    email_masked = models.CharField(max_length=100, null=True)
    agreed_at = models.DateTimeField(null=True)
    age_group = models.CharField(max_length=3, null=True)
    created_at = models.DateTimeField(db_default=Now())

    class Meta:
        managed = False
        db_table = "app_user"

    def __str__(self):
        return f"{self.nickname} ({self.provider})"
