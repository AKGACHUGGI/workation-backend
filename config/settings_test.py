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

# .env 에 Neo4j 접속 정보가 있어도 단위 테스트는 붙지 않는다 (통합 테스트는 NEO4J_TEST_URI)
NEO4J_URI = ""

# .env 에 REDIS_URL 이 있어도 단위 테스트는 프로세스 메모리 캐시만 쓴다
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
