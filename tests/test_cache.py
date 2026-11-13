"""캐시 설정과 core.cache.SafeRedisCache. 실제 Redis 없이 확인한다."""

import logging
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings

from core.cache import SafeRedisCache

BASE_DIR = Path(__file__).resolve().parent.parent
LOCMEM = "django.core.cache.backends.locmem.LocMemCache"


def backend_with(redis_url: str) -> str:
    """config.settings 를 새 프로세스에서 읽어 (BACKEND, KEY_PREFIX) 를 돌려준다."""
    code = (
        "from config import settings as s; c = s.CACHES['default'];"
        " print(c['BACKEND'], c['KEY_PREFIX'])"
    )
    env = {**os.environ, "DJANGO_SECRET_KEY": "x", "REDIS_URL": redis_url}
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=BASE_DIR, env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_redis_url_selects_safe_redis_cache():
    assert backend_with("redis://127.0.0.1:6379/0") == "core.cache.SafeRedisCache workation"
    assert backend_with("") == f"{LOCMEM} workation"


def test_test_settings_use_locmem_and_throttle_rates():
    assert settings.CACHES["default"]["BACKEND"] == LOCMEM
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] == {
        "explore_ask_burst": "10/min",
        "explore_ask_day": "200/day",
    }


def test_safe_redis_cache_survives_redis_down(caplog):
    # 포트 1 에는 아무것도 없다 → 연결 거부 (RedisError)
    down = SafeRedisCache("redis://127.0.0.1:1/0", {"OPTIONS": {"socket_connect_timeout": 1}})
    with caplog.at_level(logging.WARNING, logger="core.cache"):
        assert down.get("k") is None
        assert down.get("k", "기본값") == "기본값"
        down.set("k", 1)
        assert down.add("k", 1) is False
        assert down.delete("k") is False
        assert down.delete_pattern("explore:ask:a:*") == 0
    assert "Redis 읽기 실패" in caplog.text and "Redis 쓰기 실패" in caplog.text
