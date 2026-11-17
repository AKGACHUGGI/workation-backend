"""탐색 v6 API (스펙 §6). 응답 1시간 캐시, Neo4j 가 안 되면 마지막 응답 또는 503.
v7 동 패널 채팅 POST /api/explore/chat (v7 스펙 §8): 같은 시군구 비교 · 대화 이어받기."""

import logging

from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError, Throttled
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from core.errors import error
from explore import ask, ask_match, queries, sources
from explore.chat import arrange, flow, llm
from explore.chat import data as chat_data
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


# ──────────────── 동 1개 답 (v6.1 질문 답 재사용, 채팅이 부른다) ────────────────

ANSWER_TTL = 7 * 24 * 60 * 60  # 데이터가 월마다 바뀌고 질문 원문까지 같아야 다시 쓰여서 짧게
MAX_QUESTION = 100


class AskBurstThrottle(AnonRateThrottle):
    scope = "explore_ask_burst"


class AskDayThrottle(AnonRateThrottle):
    scope = "explore_ask_day"


def answer_one(dong_id: str, raw: dict, intents: list[str], question: str) -> tuple[list[str], str]:
    """동 1개 답: Redis → 생성. 생성기가 없거나 실패해 나간 규칙 답은 저장하지 않는다."""
    digest = ask.prompt_hash(ask.facts_for(raw, intents, question), question)
    key = f"explore:ask:a:{dong_id}:{'+'.join(intents)}:{digest}"
    hit = cache.get(key)
    if hit is not None:
        return hit[0], hit[1]
    result = ask.answer(raw, intents, llm.generate if llm.enabled() else None, question=question)
    if not result.fallback:
        cache.set(key, [result.sentences, result.source], ANSWER_TTL)
    return result.sentences, result.source


# ───────────────────────── 동 패널 채팅 (v7) ─────────────────────────


def _chat_deps() -> flow.Deps:
    """요청마다 만든다(테스트에서 바꿔 끼운 함수를 그대로 쓰게)."""
    return flow.Deps(
        names=chat_data.dong_names,
        raw=chat_data.dong_raw,
        find_intents=ask_match.find_intents,
        single=lambda *a: answer_one(*a),  # 테스트가 views.answer_one 을 바꿔 끼운다
        generate=llm.generate if llm.enabled() else None,
        vecs=chat_data.metric_vecs,
        pairs=chat_data.pairs,
    )


class ExploreChat(APIView):
    """POST /api/explore/chat {conversationId, panel, question, criteria} (v7 스펙 §8).
    질문 원문은 어디에도 기록하지 않는다(로그에는 동네 · 의도 · source 만).
    429 를 공통 오류 모양으로 내려고 클래스 뷰."""

    throttle_classes = [AskBurstThrottle, AskDayThrottle]

    def handle_exception(self, exc):
        if isinstance(exc, Throttled):
            res = error(429, "TOO_MANY_REQUESTS", "잠시 후 다시 물어봐 주세요.")
            if exc.wait is not None:
                res["Retry-After"] = str(int(exc.wait))
            return res
        return super().handle_exception(exc)

    def post(self, request):
        try:
            body = request.data if isinstance(request.data, dict) else {}
        except ParseError:
            body = {}
        question = body.get("question")
        question = question.strip() if isinstance(question, str) else ""
        if not 1 <= len(question) <= MAX_QUESTION:
            return error(400, "QUESTION_INVALID", "질문은 1~100자로 적어 주세요.")
        panel = body.get("panel")
        if not isinstance(panel, str) or not panel:
            return error(400, "PANEL_INVALID", "동네를 먼저 골라 주세요.")
        try:
            criteria = arrange.clean_criteria(body.get("criteria"))
        except ValueError:
            return error(400, "CRITERIA_INVALID", "비교 기준이 올바르지 않아요.")
        cid = body.get("conversationId")
        cid = cid if isinstance(cid, str) and cid else None

        names = chat_data.dong_names()
        try:
            if all(n.code != panel for n in names) or chat_data.dong_raw(panel) is None:
                return error(404, "DONG_NOT_FOUND", "동네를 찾을 수 없어요.")
            cid, state = flow.run(flow.build(_chat_deps()), cid, panel, question, criteria)
        except GraphUnavailable as ex:
            logger.warning("탐색 그래프 조회 실패 chat %s: %s", panel, ex)
            return error(503, "GRAPH_UNAVAILABLE", "탐색 데이터를 불러오지 못했어요.")
        logger.info(
            "동 패널 채팅 dongs=%s intents=%s source=%s",
            state.get("dongs"),
            state.get("intents"),
            state.get("source"),
        )
        return Response(flow.respond(cid, state, names))
