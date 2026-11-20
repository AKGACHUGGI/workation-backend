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
        # 여러 작업 폴더(worktree)에서 테스트를 동시에 돌릴 때 테스트 DB 이름이 겹치지 않게.
        # 비우면 Django 기본(test_postgres)
        "TEST": {"NAME": os.environ.get("TEST_DB_NAME") or None},
    }
}

# .env 에 Neo4j 접속 정보가 있어도 단위 테스트는 붙지 않는다 (통합 테스트는 NEO4J_TEST_URI)
NEO4J_URI = ""

# .env 에 REDIS_URL 이 있어도 단위 테스트는 프로세스 메모리 캐시만 쓴다
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# .env 에 CLOVA 키가 있어도 단위 테스트는 실제 CLOVA 를 부르지 않는다 (CI 와 같게).
# 키가 필요한 테스트는 settings 픽스처로 직접 켠다
CLOVA_API_KEY = ""

# .env 에 Supabase 키가 있어도 단위 테스트는 실제 Supabase 를 부르지 않는다.
# 필요한 테스트는 tests/supabase_tokens.py 의 supabase 픽스처로 켠다
SUPABASE_URL = ""
SUPABASE_JWT_SECRET = ""
SUPABASE_SERVICE_ROLE_KEY = ""
