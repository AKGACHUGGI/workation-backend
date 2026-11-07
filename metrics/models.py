"""지표 정의와 산출값. 값은 db/03_metrics.sql 이 계산해 넣는다.
종합 점수(value)는 프리셋 가중 평균이라 저장하지 않고 API 가 요청 시 계산한다."""

from django.db import models


class HigherIs(models.TextChoices):
    GOOD = "good", "높을수록 좋음"
    BAD = "bad", "낮을수록 좋음"


class MetricDef(models.Model):
    """핵심 ease·quiet·peer·overnight·evening + 보조 weekend_gap·peak_share·local_life."""

    metric_key = models.CharField(max_length=20, primary_key=True)
    name_ko = models.CharField(max_length=30)
    description = models.TextField(null=True)
    higher_is = models.CharField(max_length=4, choices=HigherIs.choices)
    is_core = models.BooleanField(default=True)

    class Meta:
        managed = False
        db_table = "metric_def"

    def __str__(self):
        return self.name_ko


class Scope(models.TextChoices):
    REGION = "region", "시군구"
    DONG = "dong", "행정동"


class Grade(models.TextChoices):
    S = "S", "상위 25%"
    A = "A", "25~50%"
    B = "B", "50~75%"
    C = "C", "하위 25%"


class Metric(models.Model):
    """시군구·행정동 × 월(0 = 연간) × 지표. unit_id 는 scope 에 따라 region_id 또는 dong_id 를
    가리키는 논리 참조라 DB 에 FK 가 없다. 이름이 필요하면 Region·Dong 을 따로 조회해 합친다."""

    pk = models.CompositePrimaryKey("scope", "unit_id", "month", "metric_def_id")
    scope = models.CharField(max_length=6, choices=Scope.choices)
    unit_id = models.SmallIntegerField()
    month = models.SmallIntegerField()
    metric_def = models.ForeignKey(
        MetricDef, on_delete=models.DO_NOTHING, db_column="metric_key", related_name="values"
    )
    raw_value = models.FloatField(null=True)
    score = models.SmallIntegerField(null=True)
    grade = models.CharField(max_length=1, choices=Grade.choices, null=True)

    class Meta:
        managed = False
        db_table = "metric"

    def __str__(self):
        return f"{self.scope}:{self.unit_id} {self.month}월 {self.metric_def_id}"
