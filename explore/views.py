"""탐색 v6 API (스펙 §6). 응답 1시간 캐시, Neo4j 가 안 되면 마지막 응답 또는 503.
v6.1 동네에 물어보기 POST …/ask (v6.1 스펙 §4·§5): 의도 → 사실 → 답(캐시 → dong_answer → 생성)."""

import logging

from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError, Throttled
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from core import clova
from core.errors import error
from explore import ask, ask_match, brief, queries, sources
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


# ───────────────────────── 동네에 물어보기 (v6.1) ─────────────────────────

ANSWER_TTL = 40 * 24 * 60 * 60
MAX_QUESTION = 100
CHAT_TIMEOUT = 10  # 초. 사용자가 기다리는 요청이라 브리핑 배치(20초)보다 짧게


class AskBurstThrottle(AnonRateThrottle):
    scope = "explore_ask_burst"


class AskDayThrottle(AnonRateThrottle):
    scope = "explore_ask_day"


def _chat(system: str, user: str) -> str:
    return clova.chat(system, user, max_tokens=300, temperature=0.3, timeout=CHAT_TIMEOUT)


def _raw(dong_id: str) -> dict | None:
    """fetch_dong 원재료 1시간 캐시. 없는 동(None)은 캐시하지 않는다. GraphUnavailable 은 위로."""
    key = f"explore:dong:raw:{dong_id}"
    raw = cache.get(key)
    if raw is None:
        raw = queries.fetch_dong(dong_id)
        if raw is not None:
            cache.set(key, raw, TTL)
    return raw


class DongAsk(APIView):
    """POST /api/explore/dongs/{id}/ask {"question"}. 429 를 공통 오류 모양으로 내려고 클래스 뷰."""

    throttle_classes = [AskBurstThrottle, AskDayThrottle]

    def handle_exception(self, exc):
        if isinstance(exc, Throttled):
            res = error(429, "TOO_MANY_REQUESTS", "잠시 후 다시 물어봐 주세요.")
            if exc.wait is not None:
                res["Retry-After"] = str(int(exc.wait))
            return res
        return super().handle_exception(exc)

    def post(self, request, dong_id: str):
        try:
            question = request.data.get("question") if isinstance(request.data, dict) else None
        except ParseError:
            question = None
        question = question.strip() if isinstance(question, str) else ""
        if not 1 <= len(question) <= MAX_QUESTION:
            return error(400, "QUESTION_INVALID", "질문은 1~100자로 적어 주세요.")
        try:
            raw = _raw(dong_id)
        except GraphUnavailable as ex:
            logger.warning("탐색 그래프 조회 실패 ask %s: %s", dong_id, ex)
            return error(503, "GRAPH_UNAVAILABLE", "탐색 데이터를 불러오지 못했어요.")
        if raw is None:
            return error(404, "DONG_NOT_FOUND", "동네를 찾을 수 없어요.")

        intents, top_sim = ask_match.find_intents(question)
        if intents[0] in ask.GUIDE:
            sentences, source = [ask.GUIDE[intents[0]][0]], "guide"
        else:
            sentences, source = self._answer(dong_id, raw, intents)
        sources.log_question(
            question,
            {
                "scope": "dong_ask",
                "dong": dong_id,
                "intents": intents,
                "top_sim": round(top_sim, 4),
                "source": source,
            },
        )
        return Response(
            {
                "question": question,
                "answer": sentences,
                "intents": intents,
                "source": source,
                "examples": ask.examples_for(intents),
            }
        )

    @staticmethod
    def _answer(dong_id: str, raw: dict, intents: list[str]) -> tuple[list[str], str]:
        """Redis → dong_answer → 생성·저장 (스펙 §4 ⑥~⑧)."""
        joined = "+".join(intents)
        digest = brief.facts_hash(ask.facts_for(raw, intents))
        key = f"explore:ask:a:{dong_id}:{joined}:{digest}"
        hit = cache.get(key)
        if hit is not None:
            return hit[0], hit[1]
        stored = sources.load_answer(dong_id, joined, digest)
        if stored is None:
            result = ask.answer(raw, intents, _chat if clova.enabled() else None)
            sources.save_answer(
                dong_id, joined, result.facts_hash, result.sentences, result.source, result.model
            )
            stored = (result.sentences, result.source)
        cache.set(key, list(stored), ANSWER_TTL)
        return stored
