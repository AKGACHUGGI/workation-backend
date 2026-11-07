"""시군구·행정동과 24시간 붐빔 곡선.
테이블은 db/schema.sql 이 기준이고 Django 는 만들거나 바꾸지 않는다.
dong.geom(PostGIS)은 지도 API 작업 때 GeoDjango 로 붙인다."""

from django.contrib.postgres.fields import ArrayField
from django.db import models


class Region(models.Model):
    """시군구 12곳. region_id 1~12 는 적재 스크립트가 부여한 대리키."""

    region_id = models.SmallIntegerField(primary_key=True)
    sido_name = models.CharField(max_length=20)
    sigungu_name = models.CharField(max_length=20)
    sigungu_code = models.CharField(max_length=5, null=True)
    area_km2 = models.DecimalField(max_digits=8, decimal_places=2, null=True)
    tags = ArrayField(models.TextField(), null=True)

    class Meta:
        managed = False
        db_table = "region"

    def __str__(self):
        return f"{self.sido_name} {self.sigungu_name}"


class Dong(models.Model):
    """행정동 204곳 (is_excluded 인 수동면 제외 시 203곳). dong_id = region_id*100 + 순번."""

    dong_id = models.SmallIntegerField(primary_key=True)
    region = models.ForeignKey(
        Region, on_delete=models.DO_NOTHING, db_column="region_id", related_name="dongs"
    )
    dong_name = models.CharField(max_length=30)
    adm_code = models.CharField(max_length=10, null=True)
    area_km2 = models.DecimalField(max_digits=8, decimal_places=3, null=True)
    is_excluded = models.BooleanField(default=False)
    excluded_reason = models.CharField(max_length=100, null=True)
    tags = ArrayField(models.TextField(), default=list)
    coast_dist_km = models.DecimalField(max_digits=7, decimal_places=3, null=True)

    class Meta:
        managed = False
        db_table = "dong"

    def __str__(self):
        return self.dong_name


class DayGroup(models.TextChoices):
    WEEKDAY = "weekday", "주중"
    WEEKEND = "weekend", "주말"


class DongHourly(models.Model):
    """행정동 24시간 붐빔 곡선. 같은 dong·month·day_group 안에서 최대 시간대 = 100."""

    pk = models.CompositePrimaryKey("dong_id", "month", "day_group", "hour")
    dong = models.ForeignKey(
        Dong, on_delete=models.DO_NOTHING, db_column="dong_id", related_name="hourly"
    )
    month = models.SmallIntegerField()
    day_group = models.CharField(max_length=7, choices=DayGroup.choices)
    hour = models.SmallIntegerField()
    index_100 = models.SmallIntegerField(null=True)

    class Meta:
        managed = False
        db_table = "dong_hourly"

    def __str__(self):
        return f"{self.dong_id} {self.month}월 {self.day_group} {self.hour}시"
