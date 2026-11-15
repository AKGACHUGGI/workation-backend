"""웹 API 가 쓰는 표. DDL 원본은 db/schema.sql (managed = False)."""

from django.db import models


class UserEvent(models.Model):
    """개인화 랭킹 행동 기록. 계산은 웹이 하고 서버는 기록과 viewCount 집계만 한다."""

    id = models.BigAutoField(primary_key=True)
    anon_id = models.UUIDField()
    # app_user 참조지만 모델 의존을 만들지 않으려고 값만 둔다. 로그인 작업에서 채운다
    user_id = models.UUIDField(null=True)
    kind = models.CharField(max_length=16)
    target = models.CharField(max_length=10, null=True)
    payload = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = "user_event"

    def __str__(self):
        return f"{self.kind} {self.target or '-'}"
