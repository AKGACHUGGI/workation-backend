"""채팅이 쓰는 읽기 (캐시 포함). 동 원재료 1시간, 동 이름 사전 · 닮음 분포 1일.

Redis 가 죽어도 SafeRedisCache 가 캐시 없이 진행시킨다. Neo4j 문제(GraphUnavailable)는 위로 올린다.
"""

from __future__ import annotations

from django.core.cache import cache

from explore import queries, sources
from explore.chat import arrange
from explore.chat.resolve import DongName

RAW_TTL = 60 * 60
STATIC_TTL = 24 * 60 * 60


def dong_raw(code: str) -> dict | None:
    """fetch_dong 원재료. 없는 동(None)은 캐시하지 않는다. 키는 v6.1 질문 API 와 같이 쓴다."""
    key = f"explore:dong:raw:{code}"
    raw = cache.get(key)
    if raw is None:
        raw = queries.fetch_dong(code)
        if raw is not None:
            cache.set(key, raw, RAW_TTL)
    return raw


def _static(key: str, load):
    hit = cache.get(key)
    if hit is None:
        hit = load()
        cache.set(key, hit, STATIC_TTL)
    return hit


def dong_names() -> list[DongName]:
    return _static(
        "explore:chat:names", lambda: [DongName(*row) for row in sources.load_dong_names()]
    )


def metric_vecs() -> dict[str, tuple[tuple[float, ...], bool]]:
    return _static("explore:chat:vecs", sources.load_metric_vecs)


def pairs() -> arrange.Pairs:
    return _static("explore:chat:pairs", lambda: arrange.pair_distances(metric_vecs()))
