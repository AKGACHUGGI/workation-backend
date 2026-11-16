"""탐색 v6 API (스펙 §6). 응답 1시간 캐시, Neo4j 가 안 되면 마지막 응답 또는 503.
v6.1 동네에 물어보기 POST …/ask (v6.1 스펙 §4·§5): 의도 → 사실 → 답(캐시 → dong_answer → 생성).
v7 동 패널 채팅 POST /api/explore/chat (v7 스펙 §8): 같은 시군구 비교 · 대화 이어받기."""

import logging

from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.exceptions import ParseError, Throttled
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from core import clova
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


def answer_one(dong_id: str, raw: dict, intents: list[str], question: str) -> tuple[list[str], str]:
    """동 1개 답: Redis → dong_answer → 생성·저장 (v6.1 스펙 §4 ⑥~⑧). 채팅도 같이 쓴다."""
    joined = "+".join(intents)
    digest = ask.prompt_hash(ask.facts_for(raw, intents, question), question)
    key = f"explore:ask:a:{dong_id}:{joined}:{digest}"
    hit = cache.get(key)
    if hit is not None:
        return hit[0], hit[1]
    stored = sources.load_answer(dong_id, joined, digest)
    if stored is None:
        result = ask.answer(raw, intents, _chat if clova.enabled() else None, question=question)
        sources.save_answer(
            dong_id, joined, result.facts_hash, result.sentences, result.source, result.model
        )
        stored = (result.sentences, result.source)
    cache.set(key, list(stored), ANSWER_TTL)
    return stored


class AskView(APIView):
    """동네에 물어보기 · 채팅 공통: throttle 과 429 를 공통 오류 모양으로."""

    throttle_classes = [AskBurstThrottle, AskDayThrottle]

    def handle_exception(self, exc):
        if isinstance(exc, Throttled):
            res = error(429, "TOO_MANY_REQUESTS", "잠시 후 다시 물어봐 주세요.")
            if exc.wait is not None:
                res["Retry-After"] = str(int(exc.wait))
            return res
        return super().handle_exception(exc)


class DongAsk(AskView):
    """POST /api/explore/dongs/{id}/ask {"question"}."""

    def post(self, request, dong_id: str):
        try:
            question = request.data.get("question") if isinstance(request.data, dict) else None
        except ParseError:
            question = None
        question = question.strip() if isinstance(question, str) else ""
        if not 1 <= len(question) <= MAX_QUESTION:
            return error(400, "QUESTION_INVALID", "질문은 1~100자로 적어 주세요.")
        try:
            raw = chat_data.dong_raw(dong_id)
        except GraphUnavailable as ex:
            logger.warning("탐색 그래프 조회 실패 ask %s: %s", dong_id, ex)
            return error(503, "GRAPH_UNAVAILABLE", "탐색 데이터를 불러오지 못했어요.")
        if raw is None:
            return error(404, "DONG_NOT_FOUND", "동네를 찾을 수 없어요.")

        intents, top_sim = ask_match.find_intents(question)
        if intents[0] in ask.GUIDE:
            sentences, source = [ask.GUIDE[intents[0]][0]], "guide"
        else:
            sentences, source = answer_one(dong_id, raw, intents, question)
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


class ExploreChat(AskView):
    """POST /api/explore/chat {conversationId, panel, question, criteria} (v7 스펙 §8).
    질문 원문은 어디에도 기록하지 않는다(로그에는 동네 · 의도 · source 만)."""

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
