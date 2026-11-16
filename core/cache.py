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
        """`static:*` 처럼 앞부분이 같은 키를 지운다. 지운 개수, Redis 가 죽었으면 0."""
        client = self._cache.get_client(write=True)
        try:
            keys = list(client.scan_iter(match=self.make_key(pattern), count=500))
            return client.delete(*keys) if keys else 0
        except RedisError as ex:
            logger.warning("Redis 삭제 실패 → 무시: %s", ex)
            return 0


# ───────────────────────── 정적 데이터 캐시 ─────────────────────────
# 지표 점수 · 시군구 · 행정동 목록 · 경계 bbox · 비슷한 동네처럼 월 배치(SQL 적재)로만 바뀌는 값.
# DB 왕복 수가 곧 응답 시간이라 요청마다 다시 읽지 않는다. 적재 직후에는 STATIC_TTL 이 지나거나
# Redis 에서 `static:*` 키를 지우면(delete_pattern) 새 값이 보인다.

STATIC_TTL = 10 * 60
_MISS = object()


def static(key: str, load):
    """`static:{key}` 에 load() 결과를 STATIC_TTL 동안 둔다. None 결과도 캐시한다.
    load() 가 예외를 내면 캐시하지 않고 그대로 올린다."""
    from django.core.cache import cache

    full = f"static:{key}"
    hit = cache.get(full, _MISS)
    if hit is not _MISS:
        return hit
    value = load()
    cache.set(full, value, STATIC_TTL)
    return value
