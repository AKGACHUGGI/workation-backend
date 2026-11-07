from decimal import Decimal

import pytest
from django.db import IntegrityError

from regions.models import DayGroup, Dong, DongHourly, Region


@pytest.fixture
def gangneung(db):
    return Region.objects.create(
        region_id=1,
        sido_name="강원특별자치도",
        sigungu_name="강릉시",
        sigungu_code="51150",
        area_km2=Decimal("1040.07"),
        tags=["coast", "beach"],
    )


@pytest.fixture
def gyeongpo(gangneung):
    return Dong.objects.create(
        dong_id=101,
        region=gangneung,
        dong_name="경포동",
        tags=["coast", "beach"],
        coast_dist_km=Decimal("0.000"),
    )


def test_region_reads_back(gangneung):
    region = Region.objects.get(region_id=1)

    assert region.sigungu_name == "강릉시"
    assert region.tags == ["coast", "beach"]
    assert region.area_km2 == Decimal("1040.07")


def test_dong_belongs_to_region(gyeongpo, gangneung):
    dong = Dong.objects.select_related("region").get(dong_id=101)

    assert dong.region.sigungu_name == "강릉시"
    assert list(gangneung.dongs.values_list("dong_name", flat=True)) == ["경포동"]
    assert dong.is_excluded is False
    assert dong.excluded_reason is None


def test_dong_name_unique_within_region(gyeongpo, gangneung):
    with pytest.raises(IntegrityError):
        Dong.objects.create(dong_id=102, region=gangneung, dong_name="경포동")


def test_dong_hourly_composite_key(gyeongpo):
    DongHourly.objects.create(
        dong=gyeongpo, month=10, day_group=DayGroup.WEEKDAY, hour=9, index_100=72
    )

    row = DongHourly.objects.get(dong_id=101, month=10, day_group="weekday", hour=9)

    assert row.pk == (101, 10, "weekday", 9)
    assert row.index_100 == 72
    assert list(gyeongpo.hourly.values_list("hour", flat=True)) == [9]


def test_dong_hourly_check_constraint_from_schema(gyeongpo):
    with pytest.raises(IntegrityError):
        DongHourly.objects.create(dong=gyeongpo, month=10, day_group="holiday", hour=9)
