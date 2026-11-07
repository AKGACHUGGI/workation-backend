# Django 초기 세팅 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Supabase 기존 테이블을 ORM 모델(`managed = False`)로 읽는 Django 프로젝트를 만들고, 헬스체크 API·pytest·ruff·CI·Docker를 갖춘다.

**Architecture:** `config/`(설정·URL), `core/`(헬스체크), `regions/`(Region·Dong·DongHourly), `metrics/`(MetricDef·Metric) 앱으로 나눈다. 스키마의 기준은 `db/schema.sql`이고, 테스트는 로컬/CI Postgres 17 테스트 DB에 이 파일을 그대로 실행한 뒤 돈다. 테스트는 절대 Supabase에 붙지 않는다(`config/settings_test.py`가 DB 설정을 통째로 덮어씀).

**Tech Stack:** Python 3.14(하한 3.12), uv 0.13, Django 5.2 LTS, DRF 3.18, psycopg 3.3, django-environ 0.14, Gunicorn 26, pytest 9.2 + pytest-django 4.14, ruff 0.17, PostgreSQL 17

**Spec:** `docs/superpowers/specs/2026-11-08-django-setup-design.md`

## Global Constraints

- `requires-python = ">=3.12"`, `.python-version` = `3.14`, Docker·CI는 Python 3.14
- Django `>=5.2,<6`, DRF `>=3.18,<4`
- 모든 모델 `managed = False`, `db_table` = 실제 테이블 이름. 모델 필드는 DB 컬럼의 부분집합이어야 하고 DB에 없는 컬럼을 가지면 안 된다. 컬럼 정의는 `db/schema.sql`이 기준
- `INSTALLED_APPS`에 `django.contrib.*`를 넣지 않는다 (`ArrayField`는 `django.contrib.postgres.fields`에서 import만)
- `dong.geom`은 모델에 넣지 않는다
- FK가 복합 기본키에 들어가면 `CompositePrimaryKey`에 attname(`dong_id`, `metric_def_id`)을 쓴다
- FK는 `on_delete=models.DO_NOTHING` (삭제 동작은 DB가 정함)
- 헬스체크 실패 응답에 예외 메시지를 넣지 않는다
- ruff: `line-length = 100`, `target-version = "py312"`, 규칙 `E, F, I, UP, B, DJ`, 마이그레이션 제외
- 커밋 메시지는 Conventional Commits, 한국어 본문. 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- 비밀값(.env)은 커밋하지 않는다

## 파일 구조

| 파일 | 책임 | Task |
| --- | --- | --- |
| `pyproject.toml`, `uv.lock`, `.python-version` | 의존성·ruff·pytest 설정 | 1 |
| `manage.py` | Django 진입점 | 1 |
| `config/__init__.py`, `config/settings.py` | 운영·개발 설정 (환경변수) | 1 |
| `config/settings_test.py` | 테스트 설정. DB를 로컬 테스트 Postgres로 고정 | 1 |
| `config/urls.py`, `config/wsgi.py` | URL 루트, WSGI | 1 |
| `core/__init__.py`, `core/apps.py`, `core/views.py`, `core/urls.py` | 헬스체크 | 1 |
| `compose.yaml` | 테스트용 Postgres 17 | 1 |
| `.env.example` | 환경변수 이름 | 1 |
| `tests/__init__.py`, `tests/test_health.py` | 헬스체크 테스트 | 1 |
| `tests/conftest.py`, `tests/test_schema.py` | 테스트 DB에 `db/schema.sql` 로드 | 2 |
| `regions/` (`__init__`, `apps`, `models`, `migrations/`) | Region, Dong, DongHourly | 3 |
| `tests/test_regions_models.py` | | 3 |
| `metrics/` (`__init__`, `apps`, `models`, `migrations/`) | MetricDef, Metric | 4 |
| `tests/test_metrics_models.py` | | 4 |
| `.github/workflows/backend-ci.yml` | CI | 5 |
| `Dockerfile`, `.dockerignore` | 운영 이미지 | 6 |
| `README.md`, `docs/개발-가이드.md` | 문서 | 7 |

---

### Task 1: 프로젝트 뼈대와 헬스체크

**Files:**
- Create: `.python-version`, `pyproject.toml`, `uv.lock`(생성), `manage.py`, `config/__init__.py`, `config/settings.py`, `config/settings_test.py`, `config/urls.py`, `config/wsgi.py`, `core/__init__.py`, `core/apps.py`, `core/views.py`, `core/urls.py`, `compose.yaml`, `.env.example`, `tests/__init__.py`, `tests/test_health.py`

**Interfaces:**
- Produces: 설정 모듈 `config.settings`(운영), `config.settings_test`(pytest 기본). URL `GET /api/health` → 200 `{"status": "ok", "db": "ok"}` / 503 `{"status": "error", "db": "unavailable"}`. 뷰 함수 `core.views.health`, 모듈 전역 `core.views.connection`(테스트가 교체). `INSTALLED_APPS = ["rest_framework", "core"]` — Task 3·4가 `"regions"`, `"metrics"`를 추가. 테스트 DB 접속 기본값 `localhost:5432`, `postgres/postgres`, 환경변수 `TEST_DB_HOST/PORT/USER/PASSWORD`로 덮어씀.

- [ ] **Step 1: uv 업데이트와 Python 고정**

```bash
uv self update
uv --version            # 0.13.x
printf '3.14\n' > .python-version
```

- [ ] **Step 2: `pyproject.toml` 작성**

```toml
[project]
name = "workation-backend"
version = "0.1.0"
description = "워케이션맵 API 서버"
requires-python = ">=3.12"
dependencies = [
    "django>=5.2,<6",
    "djangorestframework>=3.18,<4",
    "psycopg[binary]>=3.3",
    "django-environ>=0.14",
    "gunicorn>=26",
]

[dependency-groups]
dev = [
    "pytest>=9.2",
    "pytest-django>=4.14",
    "ruff>=0.17",
]

[tool.uv]
package = false

[tool.ruff]
line-length = 100
target-version = "py312"
extend-exclude = ["**/migrations"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "DJ"]
# db/schema.sql 이 기준이라 NULL 허용 문자열 컬럼(null=True CharField)을 그대로 옮긴다
ignore = ["DJ001"]

[tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings_test"
testpaths = ["tests"]
```

- [ ] **Step 3: 의존성 설치**

Run: `uv sync`
Expected: Python 3.14 다운로드(처음이면) 후 `.venv` 생성, `uv.lock` 생성. `uv run python -c "import django; print(django.get_version())"` → `5.2.x`

- [ ] **Step 4: 테스트용 Postgres `compose.yaml` 작성하고 띄우기**

```yaml
# 로컬 테스트 전용 DB. 앱 · Traefik · Redis 는 배포 세팅 때 추가한다.
services:
  test-db:
    image: postgres:17
    environment:
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10
```

Run: `docker compose up -d --wait`
Expected: `test-db` healthy. (로컬 5432가 이미 쓰이고 있으면 포트를 `"5433:5432"`로 바꾸고 `export TEST_DB_PORT=5433`)

- [ ] **Step 5: 실패하는 헬스체크 테스트 작성**

`tests/__init__.py`: 빈 파일

`tests/test_health.py`:

```python
import pytest
from django.db import OperationalError

import core.views


@pytest.mark.django_db
def test_health_ok(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


class _BrokenConnection:
    def cursor(self):
        raise OperationalError('connection failed: password="secret-pw"')


def test_health_db_down_hides_error(client, monkeypatch):
    monkeypatch.setattr(core.views, "connection", _BrokenConnection())

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "db": "unavailable"}
    assert "secret-pw" not in response.content.decode()
```

- [ ] **Step 6: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_health.py -v`
Expected: 에러 — `config.settings_test` 모듈이 없음 (`ImportError` / `No module named 'config'`)

- [ ] **Step 7: 설정·URL·WSGI·manage.py 작성**

`config/__init__.py`: 빈 파일

`config/settings.py`:

```python
"""운영·개발 설정. 값은 환경변수(.env)에서 읽는다. 변수 목록은 .env.example."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DJANGO_DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Django 기본 앱(admin·auth·sessions)은 로그인(W-07) 작업 때 정한다.
# Supabase 에 migrate 로 테이블이 생기지 않게 지금은 넣지 않는다.
INSTALLED_APPS = [
    "rest_framework",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Supabase Session pooler(5432). 비밀번호에 기호가 있어도 되도록 URL 이 아니라 항목별로 받는다.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "NAME": env("DB_NAME", default="postgres"),
        "USER": env("DB_USER", default="postgres"),
        "PASSWORD": env("DB_PASSWORD", default=""),
        "OPTIONS": {"sslmode": env("DB_SSLMODE", default="prefer")},
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 인증 앱이 없으므로 DRF 가 django.contrib.auth 를 찾지 않게 한다.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "UNAUTHENTICATED_USER": None,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
```

`config/settings_test.py`:

```python
"""pytest 전용 설정. .env 에 Supabase 가 들어 있어도 테스트는 로컬 테스트 Postgres 만 쓴다."""

import os

os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-key")

from config.settings import *  # noqa: E402, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("TEST_DB_HOST", "localhost"),
        "PORT": os.environ.get("TEST_DB_PORT", "5432"),
        "NAME": "postgres",
        "USER": os.environ.get("TEST_DB_USER", "postgres"),
        "PASSWORD": os.environ.get("TEST_DB_PASSWORD", "postgres"),
    }
}
```

`config/urls.py`:

```python
from django.urls import include, path

urlpatterns = [
    path("api/", include("core.urls")),
]
```

`config/wsgi.py`:

```python
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
```

`manage.py`:

```python
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: 헬스체크 앱 작성**

`core/__init__.py`: 빈 파일

`core/apps.py`:

```python
from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
```

`core/views.py`:

```python
import logging

from django.db import DatabaseError, connection
from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET"])
def health(request):
    """DB 에 SELECT 1 을 보내 연결을 확인한다. 예외 문구(접속 정보 포함 가능)는 로그에만 남긴다."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("health check: database unavailable")
        return Response({"status": "error", "db": "unavailable"}, status=503)
    return Response({"status": "ok", "db": "ok"})
```

`core/urls.py`:

```python
from django.urls import path

from core import views

urlpatterns = [
    path("health", views.health, name="health"),
]
```

- [ ] **Step 9: 테스트 통과 확인**

Run: `uv run pytest tests/test_health.py -v`
Expected: 2 passed

- [ ] **Step 10: `.env.example` 작성**

```
# 복사해서 .env 로 쓴다. .env 는 커밋하지 않는다. 값은 데이터 담당에게 직접 받는다 (이슈 #2).
DJANGO_SECRET_KEY=
DJANGO_DEBUG=true
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1

# Supabase Session pooler (포트 5432). Transaction pooler(6543)는 쓰지 않는다.
DB_HOST=
DB_PORT=5432
DB_NAME=postgres
DB_USER=
DB_PASSWORD=
DB_SSLMODE=require
```

- [ ] **Step 11: ruff 확인**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: `All checks passed!` 그리고 `N files already formatted`. 포맷 차이가 있으면 `uv run ruff format .` 후 다시 확인.

- [ ] **Step 12: Django 시스템 체크**

Run: `DJANGO_SETTINGS_MODULE=config.settings_test uv run python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 13: 커밋**

```bash
git add .python-version pyproject.toml uv.lock manage.py config core compose.yaml .env.example tests
git commit -m "chore: Django 프로젝트 뼈대와 헬스체크 API 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 테스트 DB에 `db/schema.sql` 로드

**Files:**
- Create: `tests/conftest.py`, `tests/test_schema.py`

**Interfaces:**
- Consumes: Task 1의 `config.settings_test`, 테스트 Postgres
- Produces: 세션 픽스처 `django_db_setup` 덮어쓰기. 이후 모든 `db` 픽스처 테스트에서 `db/schema.sql`의 20개 테이블이 존재한다. Task 3·4 모델 테스트가 이것에 의존한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_schema.py`:

```python
from django.db import connection

EXPECTED_TABLES = {
    "region",
    "dong",
    "population_hourly",
    "population_demographic",
    "dong_month_summary",
    "dong_year_summary",
    "metric_def",
    "metric",
    "dong_hourly",
    "region_link",
    "app_user",
    "favorite",
    "photo",
    "tip",
    "tip_reaction",
    "tip_reply",
    "report",
    "metric_vote",
    "share_card",
    "nl_search_log",
}


def test_schema_sql_loaded_into_test_db(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        tables = {row[0] for row in cursor.fetchall()}

    assert EXPECTED_TABLES <= tables
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_schema.py -v`
Expected: FAIL — `assert EXPECTED_TABLES <= tables` (테스트 DB에 `django_migrations`만 있음)

- [ ] **Step 3: conftest 작성**

`tests/conftest.py`:

```python
"""모델이 managed = False 라 Django 는 테스트 DB 에 테이블을 만들지 않는다.
테스트 DB 를 만든 직후 db/schema.sql 을 그대로 실행해 실제 스키마로 테스트한다."""

from pathlib import Path

import pytest
from django.db import connection

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -v`
Expected: 3 passed (`test_health` 2개 + `test_schema` 1개).
여러 문장을 한 번에 실행하다 psycopg 에러가 나면 `schema.sql`에 `%` 문자가 생겼는지, Django가 파라미터 없이 `execute`를 부르는지 확인한다(파라미터를 넘기면 다중 문장이 막힌다).

- [ ] **Step 5: 커밋**

```bash
git add tests/conftest.py tests/test_schema.py
git commit -m "test: 테스트 DB에 db/schema.sql 로드

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `regions` 앱 — Region, Dong, DongHourly 모델

**Files:**
- Create: `regions/__init__.py`, `regions/apps.py`, `regions/models.py`, `regions/migrations/0001_initial.py`(생성), `regions/migrations/__init__.py`(생성), `tests/test_regions_models.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`에 `"regions"` 추가)

**Interfaces:**
- Consumes: Task 2의 스키마 로드
- Produces:
  - `regions.models.Region` — pk `region_id: int`, `sido_name: str`, `sigungu_name: str`, `sigungu_code: str | None`, `area_km2: Decimal | None`, `tags: list[str] | None`; 역참조 `dongs`
  - `regions.models.Dong` — pk `dong_id: int`, `region: Region`(`region_id`), `dong_name`, `adm_code`, `area_km2`, `is_excluded: bool`, `excluded_reason`, `tags: list[str]`, `coast_dist_km`; 역참조 `hourly`
  - `regions.models.DongHourly` — pk `(dong_id, month, day_group, hour)`, `dong: Dong`, `index_100: int | None`
  - `regions.models.DayGroup` (`WEEKDAY="weekday"`, `WEEKEND="weekend"`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regions_models.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_regions_models.py -v`
Expected: 수집 에러 — `ModuleNotFoundError: No module named 'regions'`

- [ ] **Step 3: 앱과 모델 작성**

`regions/__init__.py`: 빈 파일

`regions/apps.py`:

```python
from django.apps import AppConfig


class RegionsConfig(AppConfig):
    name = "regions"
```

`regions/models.py`:

```python
"""시군구·행정동과 24시간 붐빔 곡선. 테이블은 db/schema.sql 이 기준이고 Django 는 만들거나 바꾸지 않는다.
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
```

- [ ] **Step 4: 앱 등록**

`config/settings.py`의 `INSTALLED_APPS`를 다음으로 바꾼다:

```python
INSTALLED_APPS = [
    "rest_framework",
    "core",
    "regions",
]
```

- [ ] **Step 5: 마이그레이션 파일 생성**

Run: `DJANGO_SETTINGS_MODULE=config.settings_test uv run python manage.py makemigrations regions`
Expected: `regions/migrations/0001_initial.py` 생성, 모델 3개 모두 `options={"managed": False, "db_table": ...}`. (`managed = False`라 실행해도 테이블을 만들지 않는다)

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest -v`
Expected: 8 passed.
`CompositePrimaryKey` 관련 에러(FK를 복합키에 넣을 수 없음 등)가 나면 임의로 우회하지 말고 멈춰서 에러 전문을 보고한다. 스펙 13장의 대안(해당 모델을 조회 전용으로 두고 `.values()` 쿼리로 대체)은 사용자 확인 후 적용한다.

- [ ] **Step 7: ruff 확인 후 커밋**

```bash
uv run ruff check . && uv run ruff format --check .
git add regions config/settings.py tests/test_regions_models.py
git commit -m "feat: 시군구·행정동·24시간 곡선 ORM 모델 추가 (managed=False)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `metrics` 앱 — MetricDef, Metric 모델

**Files:**
- Create: `metrics/__init__.py`, `metrics/apps.py`, `metrics/models.py`, `metrics/migrations/0001_initial.py`(생성), `metrics/migrations/__init__.py`(생성), `tests/test_metrics_models.py`
- Modify: `config/settings.py` (`INSTALLED_APPS`에 `"metrics"` 추가)

**Interfaces:**
- Consumes: Task 2의 스키마 로드
- Produces:
  - `metrics.models.MetricDef` — pk `metric_key: str`, `name_ko`, `description: str | None`, `higher_is`, `is_core: bool`; 역참조 `values`
  - `metrics.models.Metric` — pk `(scope, unit_id, month, metric_def_id)`, `metric_def: MetricDef`(DB 컬럼 `metric_key`), `raw_value: float | None`, `score: int | None`, `grade: str | None`. `unit_id`는 scope 에 따라 region_id 또는 dong_id (FK 아님)
  - `metrics.models.Scope` (`REGION`, `DONG`), `Grade` (`S`, `A`, `B`, `C`), `HigherIs` (`GOOD`, `BAD`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_metrics_models.py`:

```python
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
        Metric.objects.create(
            scope=Scope.REGION, unit_id=1, month=10, metric_def=quiet, grade="X"
        )
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_metrics_models.py -v`
Expected: 수집 에러 — `ModuleNotFoundError: No module named 'metrics'`

- [ ] **Step 3: 앱과 모델 작성**

`metrics/__init__.py`: 빈 파일

`metrics/apps.py`:

```python
from django.apps import AppConfig


class MetricsConfig(AppConfig):
    name = "metrics"
```

`metrics/models.py`:

```python
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
```

- [ ] **Step 4: 앱 등록**

`config/settings.py`의 `INSTALLED_APPS`를 다음으로 바꾼다:

```python
INSTALLED_APPS = [
    "rest_framework",
    "core",
    "regions",
    "metrics",
]
```

- [ ] **Step 5: 마이그레이션 파일 생성**

Run: `DJANGO_SETTINGS_MODULE=config.settings_test uv run python manage.py makemigrations metrics`
Expected: `metrics/migrations/0001_initial.py` 생성, 두 모델 모두 `managed: False`

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest -v`
Expected: 12 passed

- [ ] **Step 7: ruff 확인 후 커밋**

```bash
uv run ruff check . && uv run ruff format --check .
git add metrics config/settings.py tests/test_metrics_models.py
git commit -m "feat: 지표 정의·산출값 ORM 모델 추가 (복합 기본키)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: GitHub Actions CI

**Files:**
- Create: `.github/workflows/backend-ci.yml`

**Interfaces:**
- Consumes: Task 1~4의 `uv.lock`, ruff·pytest 설정, `config.settings_test`의 테스트 DB 기본값(`localhost:5432`, `postgres/postgres`)

- [ ] **Step 1: 워크플로 작성**

`.github/workflows/backend-ci.yml`:

```yaml
name: backend-ci

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  check:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:17
        env:
          POSTGRES_PASSWORD: postgres
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
    steps:
      - uses: actions/checkout@v7
      - uses: astral-sh/setup-uv@v10
        with:
          enable-cache: true
      - name: 의존성 설치
        run: uv sync --frozen
      - name: ruff check
        run: uv run ruff check .
      - name: ruff format
        run: uv run ruff format --check .
      - name: pytest
        run: uv run pytest -v
```

- [ ] **Step 2: YAML 문법 확인**

Run: `uv run --with pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/backend-ci.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: CI와 같은 순서로 로컬 실행**

Run: `uv sync --frozen && uv run ruff check . && uv run ruff format --check . && uv run pytest`
Expected: 모두 통과, `12 passed`. (CI 자체는 PR을 올린 뒤 확인)

- [ ] **Step 4: 커밋**

```bash
git add .github/workflows/backend-ci.yml
git commit -m "ci: ruff · pytest 백엔드 CI 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Dockerfile

**Files:**
- Create: `Dockerfile`, `.dockerignore`

**Interfaces:**
- Consumes: `pyproject.toml`, `uv.lock`, `.python-version`, `config.wsgi`, `/api/health`

- [ ] **Step 1: `.dockerignore` 작성**

```
.git
.github
.venv
.env
.env.*
!.env.example
db
docs
tests
**/__pycache__
.pytest_cache
.ruff_cache
compose.yaml
```

- [ ] **Step 2: `Dockerfile` 작성**

```dockerfile
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY . .

RUN useradd --create-home app
USER app

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000

# gthread: LLM 호출이 수 초 걸려도 다른 요청이 막히지 않게 (설계/시스템-아키텍처.md)
CMD ["gunicorn", "config.wsgi", "--worker-class", "gthread", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8000"]
```

- [ ] **Step 3: 빌드**

Run: `docker build -t workation-backend:dev .`
Expected: 빌드 성공

- [ ] **Step 4: 컨테이너로 헬스체크 확인 (테스트 Postgres 사용)**

```bash
docker compose up -d --wait
docker run --rm -d --name wb-smoke -p 8000:8000 \
  -e DJANGO_SECRET_KEY=smoke -e DJANGO_ALLOWED_HOSTS=localhost \
  -e DB_HOST=host.docker.internal -e DB_PASSWORD=postgres \
  workation-backend:dev
sleep 3
curl -s localhost:8000/api/health
docker logs wb-smoke | head -5
docker stop wb-smoke
```

Expected: `{"status":"ok","db":"ok"}`, 로그에 `Using worker: gthread`

- [ ] **Step 5: 커밋**

```bash
git add Dockerfile .dockerignore
git commit -m "chore: Gunicorn gthread 운영 Dockerfile 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 문서

**Files:**
- Modify: `README.md` ("시작하기" 절)
- Create: `docs/개발-가이드.md`

- [ ] **Step 1: README "시작하기" 교체**

`README.md`의 다음 두 줄을

```
## 시작하기

스택 확정 후 작성 예정.
```

아래로 바꾼다:

````markdown
## 시작하기

Django 5.2 · Django REST Framework · Gunicorn. Python 패키지는 [uv](https://docs.astral.sh/uv/) 로 관리합니다.

```bash
uv sync                                   # Python 3.14 와 의존성 설치
cp .env.example .env                      # Supabase 접속 정보는 데이터 담당에게 직접 받기 (이슈 #2)
uv run python manage.py runserver
curl localhost:8000/api/health            # {"status":"ok","db":"ok"}
```

테스트는 Supabase 가 아니라 로컬 Postgres 컨테이너에서 돕니다.

```bash
docker compose up -d --wait               # 테스트용 postgres:17
uv run pytest
```

커밋 전 검사, 모델 규칙, 새 API 추가 방법은 [docs/개발-가이드.md](docs/개발-가이드.md) 를 참고하세요.
````

- [ ] **Step 2: `docs/개발-가이드.md` 작성**

````markdown
# 개발 가이드 (backend)

흐름 · 커밋 · PR 규칙은 [workation-docs 협업 규칙](https://github.com/TEAM-SKPJC/workation-docs/blob/main/협업/협업-규칙.md) 을 따릅니다. 이 문서는 백엔드 세부 사항만 다룹니다.

## 버전

| 항목 | 버전 |
| --- | --- |
| Python | 3.14 (하한 3.12) |
| Django | 5.2 LTS |
| Django REST Framework | 3.18 |
| DB | Supabase PostgreSQL 17 (테스트는 `postgres:17`) |

정확한 버전은 `uv.lock` 이 기준입니다. 의존성 추가는 `uv add <패키지>` (개발용은 `uv add --dev`).

## 커밋 전 검사

CI 와 같은 명령입니다.

```bash
uv run ruff check .
uv run ruff format --check .      # 고치려면 uv run ruff format .
docker compose up -d --wait
uv run pytest
```

## 구조

| 경로 | 내용 |
| --- | --- |
| `config/` | 설정(`settings.py`), 테스트 설정(`settings_test.py`), URL |
| `core/` | 헬스체크 `GET /api/health` |
| `regions/` | `Region`(시군구) · `Dong`(행정동) · `DongHourly`(24시간 곡선) |
| `metrics/` | `MetricDef`(지표 정의) · `Metric`(지표 값) |
| `db/` | 스키마 SQL. **스키마의 기준** |

새 화면 API 는 해당 도메인 앱의 `serializers.py` · `views.py` · `urls.py` 에 붙이고 `config/urls.py` 에 `include` 합니다.

## 모델 규칙

- 테이블은 `db/` 의 SQL 이 만듭니다. 모델은 전부 `managed = False` 이고 `migrate` 로 테이블을 만들거나 바꾸지 않습니다 ([db/README.md 스키마 변경 규칙](../db/README.md#스키마-변경-규칙)).
- 컬럼을 바꿀 때는 `db/migrations/` SQL 과 `db/schema.sql` 을 먼저 고치고, 같은 PR 에서 모델 필드를 맞춥니다.
- 모델 필드는 DB 컬럼의 부분집합이면 됩니다. DB 에 없는 필드를 넣으면 테스트가 깨집니다 (테스트 DB 가 `db/schema.sql` 로 만들어지기 때문).
- 복합 기본키 테이블(`metric`, `dong_hourly`)은 `models.CompositePrimaryKey` 를 씁니다.
- `metric.unit_id` 는 FK 가 아닙니다. 시군구 · 행정동 이름은 따로 조회해 합칩니다.
- 모델 파일을 바꾸면 `DJANGO_SETTINGS_MODULE=config.settings_test uv run python manage.py makemigrations` 로 마이그레이션 파일도 갱신해 커밋합니다 (테이블에는 영향 없음).

## 테스트

- `config/settings_test.py` 가 DB 를 로컬 테스트 Postgres(`localhost:5432`, `postgres/postgres`)로 고정합니다. `.env` 에 Supabase 가 있어도 테스트는 Supabase 에 붙지 않습니다.
- 포트를 바꾸려면 `TEST_DB_HOST` · `TEST_DB_PORT` · `TEST_DB_USER` · `TEST_DB_PASSWORD` 환경변수를 씁니다.
- `tests/conftest.py` 가 테스트 DB 를 만든 직후 `db/schema.sql` 을 실행합니다.

## Supabase 연결 확인 (수동)

1. `.env` 에 데이터 담당에게 받은 접속 정보를 넣습니다. **Session pooler (포트 5432)** 를 씁니다. Transaction pooler(6543) 는 쓰지 않습니다.
2. 비밀번호는 `DB_PASSWORD` 에 그대로 넣습니다. URL 이 아니라서 `@` · `!` 같은 기호를 인코딩하지 않아도 됩니다.
3. `uv run python manage.py runserver` 후 `curl localhost:8000/api/health` → `{"status":"ok","db":"ok"}`
4. 데이터 확인: `uv run python manage.py shell -c "from regions.models import Dong; print(Dong.objects.count())"` → `204`

## Docker

```bash
docker build -t workation-backend:dev .
docker run --rm -p 8000:8000 --env-file .env workation-backend:dev
```

Gunicorn gthread 워커(2 × 4 스레드)로 뜹니다. 배포 구성(Traefik · Redis)은 별도로 추가합니다.
````

- [ ] **Step 3: 수동 Supabase 확인 (`.env`가 있을 때만)**

`.env`가 있으면 위 가이드 4단계를 실행해 `204`를 확인한다. 없으면 이 단계는 건너뛰고 PR 본문에 "Supabase 수동 확인은 접속 정보를 받은 팀원이 진행"이라고 적는다.

- [ ] **Step 4: 커밋**

```bash
git add README.md docs/개발-가이드.md
git commit -m "docs: 백엔드 시작하기와 개발 가이드 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## 마무리 (PR)

- 전체 검사: `uv run ruff check . && uv run ruff format --check . && uv run pytest` → 12 passed
- PR 제목: `chore: Django 초기 세팅 — 프로젝트 뼈대 · ORM 모델 5개 · CI · Docker`
- PR 본문(템플릿 `.github/PULL_REQUEST_TEMPLATE.md`)에 넣을 것: `close #2`, Supabase 조직 초대 수락은 팀원 각자, 리전은 도쿄 유지, 협업 규칙 레포 표의 backend 스택 버전·CI 칸은 workation-docs 에 별도 PR, 웹 스펙 대비 스키마 빈 곳(사용자·커뮤니티)은 후속 이슈
