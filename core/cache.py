"""Redis 캐시 (REDIS_URL 이 있을 때 config/settings.py 가 고른다).

Redis 가 죽어도 요청은 캐시 없이 진행한다: 읽기 실패 = 없음, 쓰기 실패 = 무시, 경고 로그만.
그 동안 DRF throttle 카운터도 저장되지 않아 남용 방지가 꺼진다 (탐색 v6.1 스펙 §7-2).
"""

from __future__ import annotations

import logging

from django.core.cache.backends.base import DEFAULT_TIMEOUT
from django.core.cache.backends.redis import RedisCache
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class SafeRedisCache(RedisCache):
    def get(self, key, default=None, version=None):
        try:
            return super().get(key, default, version)
        except RedisError as ex:
            logger.warning("Redis 읽기 실패 → 캐시 없이 진행: %s", ex)
            return default

    def set(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            super().set(key, value, timeout, version)
        except RedisError as ex:
            logger.warning("Redis 쓰기 실패 → 무시: %s", ex)

    def add(self, key, value, timeout=DEFAULT_TIMEOUT, version=None):
        try:
            return super().add(key, value, timeout, version)
        except RedisError as ex:
            logger.warning("Redis 쓰기 실패 → 무시: %s", ex)
            return False

    def delete(self, key, version=None):
        try:
            return super().delete(key, version)
        except RedisError as ex:
            logger.warning("Redis 삭제 실패 → 무시: %s", ex)
            return False

    def delete_pattern(self, pattern: str) -> int:
        """`explore:ask:a:*` 처럼 앞부분이 같은 키를 지운다. 지운 개수, Redis 가 죽었으면 0."""
        client = self._cache.get_client(write=True)
        try:
            keys = list(client.scan_iter(match=self.make_key(pattern), count=500))
            return client.delete(*keys) if keys else 0
        except RedisError as ex:
            logger.warning("Redis 삭제 실패 → 무시: %s", ex)
            return 0
