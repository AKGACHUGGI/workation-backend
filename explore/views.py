"""탐색 v6 API (스펙 §6). 응답 1시간 캐시, Neo4j 가 안 되면 마지막 응답 또는 503."""

import logging

from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import error
from explore import queries, sources
from explore.graphdb import GraphUnavailable

logger = logging.getLogger(__name__)

TTL = 60 * 60


def _serve(key: str, load, nf_code: str, nf_msg: str) -> Response:
    """캐시 → load() → 실패 시 `{key}:last`(만료 없음) → 503. 없는 id(None)는 캐시하지 않는다."""
    body = cache.get(key)
    if body is not None:
        return Response(body)
    try:
        body = load()
    except GraphUnavailable as ex:
        logger.warning("탐색 그래프 조회 실패 %s: %s", key, ex)
        last = cache.get(f"{key}:last")
        if last is None:
            return error(503, "GRAPH_UNAVAILABLE", "탐색 데이터를 불러오지 못했어요.")
        return Response(last)
    if body is None:
        return error(404, nf_code, nf_msg)
    cache.set(key, body, TTL)
    cache.set(f"{key}:last", body, None)
    return Response(body)


@api_view(["GET"])
def sigungu_pins(request, sigungu_id: str):
    return _serve(
        f"explore:pins:{sigungu_id}",
        lambda: queries.fetch_pins(sigungu_id),
        "SIGUNGU_NOT_FOUND",
        "시군구를 찾을 수 없어요.",
    )


@api_view(["GET"])
def dong_detail(request, dong_id: str):
    def load():
        raw = queries.fetch_dong(dong_id)
        return None if raw is None else queries.shape_dong(raw, sources.fetch_briefing(dong_id))

    return _serve(f"explore:dong:{dong_id}", load, "DONG_NOT_FOUND", "동네를 찾을 수 없어요.")
