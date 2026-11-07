import pytest
from django.db import IntegrityError

from metrics.models import Grade, Metric, MetricDef, Scope


@pytest.fixture
def quiet(db):
    return MetricDef.objects.create(
        metric_key="quiet", name_ko="동네 조용함", higher_is="good", is_core=True
    )


def test_metric_def_reads_back(quiet):
    metric_def = MetricDef.objects.get(metric_key="quiet")

    assert metric_def.name_ko == "동네 조용함"
    assert metric_def.is_core is True


def test_metric_composite_key(quiet):
    Metric.objects.create(
        scope=Scope.REGION,
        unit_id=1,
        month=10,
        metric_def=quiet,
        raw_value=0.79,
        score=82,
        grade=Grade.S,
    )

    metric = Metric.objects.select_related("metric_def").get(
        scope="region", unit_id=1, month=10, metric_def_id="quiet"
    )

    assert metric.pk == ("region", 1, 10, "quiet")
    assert metric.score == 82
    assert metric.grade == "S"
    assert metric.metric_def.name_ko == "동네 조용함"
    assert list(quiet.values.values_list("unit_id", flat=True)) == [1]


def test_metric_filter_by_month(quiet):
    for month, score in [(0, 60), (10, 82)]:
        Metric.objects.create(
            scope=Scope.REGION, unit_id=1, month=month, metric_def=quiet, score=score
        )

    assert list(
        Metric.objects.filter(scope="region", month=10).values_list("score", flat=True)
    ) == [82]


def test_metric_check_constraint_from_schema(quiet):
    with pytest.raises(IntegrityError):
        Metric.objects.create(scope=Scope.REGION, unit_id=1, month=10, metric_def=quiet, grade="X")
