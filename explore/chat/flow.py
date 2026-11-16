"""탐색 v7 동 패널 채팅 흐름 (스펙 §3). LangGraph StateGraph + 체크포인터.

START → resolve_dongs ─(안내)→ finish
                      └→ find_intent → guide | single_answer | compare_answer → finish → END

노드는 상태를 받아 바뀐 필드만 돌려준다. 바깥 의존(읽기 · 의도 · 생성)은 Deps 로 받는다.
상태에는 의도 · 동네만 남기고(원재료는 1시간 캐시에서 다시 읽는다), 질문은 finish 에서 비운다.
실행은 durability="exit" 라 끝난 상태 하나만 저장된다 (스펙 §7-2-1).
"""

from __future__ import annotations

import logging
import operator
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, TypedDict

from django.conf import settings
from django.core.cache import cache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from redis.exceptions import RedisError

from explore import ask
from explore.chat import arrange, compare
from explore.chat.resolve import DongName, resolve

logger = logging.getLogger(__name__)

TTL_MINUTES = 30  # 대화 기억 (스펙 §7-1). 읽을 때마다 다시 30분
COMPARE_EXAMPLES = ("둘이 비슷해?", "주말엔 몇 시가 붐벼?", "언제 가면 한산해?")
RULE_ONLY = ("weekday_hours", "weekend_hours", "months", "similar")


class ChatState(TypedDict, total=False):
    question: str  # 이번 질문. finish 에서 비운다
    panel: str
    criteria: dict  # arrange.clean_criteria 결과
    dongs: list[str]  # 이번 턴 비교 대상 (패널 동 먼저)
    named: bool  # 질문에 같은 시군구 동 이름이 있었다
    intents: list[str]
    guide: str | None
    note: str | None
    sentences: list[dict]  # {text, evidence, source}
    source: str  # llm · mixed · rule · guide
    turns: Annotated[list[dict], operator.add]  # {intents, dongs} 턴 기록


@dataclass(frozen=True)
class Deps:
    names: Callable[[], list[DongName]]
    raw: Callable[[str], dict | None]
    find_intents: Callable[[str], tuple[list[str], float]]
    single: Callable[[str, dict, list[str], str], tuple[list[str], str]]
    generate: compare.Generate | None
    vecs: Callable[[], dict]
    pairs: Callable[[], arrange.Pairs]


def settle_intents(found: list[str], prev: list[str], named: bool, count: int) -> list[str]:
    """의도를 못 찾았고(unknown_other) 질문에 동 이름이 있으면 직전 의도를 잇는다("교1동은?").
    그 밖에 비교 중(동 2개 이상)이면 compare_summary — "뭐가 달라?"처럼 사전에 없는 말투도
    안내 대신 기준 비교로 답한다. 식당·숙소·교통(unknown_place 등)은 그대로 안내."""
    if found != ["unknown_other"]:
        return found
    if named and prev and prev[0] not in ask.UNKNOWN:
        return prev
    return ["compare_summary"] if count >= 2 else found


def _sentences(texts: list[str], source: str) -> list[dict]:
    return [{"text": t, "evidence": None, "source": source} for t in texts]


def build(deps: Deps) -> StateGraph:
    def resolve_dongs(s: ChatState) -> dict:
        names = deps.names()
        panel = next(n for n in names if n.code == s["panel"])  # 뷰가 미리 확인한다
        r = resolve(s["question"], panel, s.get("dongs") or [], names)
        return {
            "dongs": r.dongs,
            "named": r.named,
            "guide": r.guide,
            "note": r.note,
            "sentences": [],
            "source": "guide" if r.guide else "",
        }

    def find_intent(s: ChatState) -> dict:
        found, _ = deps.find_intents(s["question"])
        prev = s.get("intents") or []
        return {"intents": settle_intents(found, prev, s["named"], len(s["dongs"]))}

    def route(s: ChatState) -> str:
        first = s["intents"][0]
        if len(s["dongs"]) >= 2 and first not in ask.UNKNOWN:
            return "compare_answer"
        if first in ask.GUIDE:
            return "guide"
        return "single_answer"

    def guide(s: ChatState) -> dict:
        return {"guide": ask.GUIDE[s["intents"][0]][0], "source": "guide"}

    def single_answer(s: ChatState) -> dict:
        code = s["dongs"][0]
        texts, source = deps.single(code, deps.raw(code), s["intents"], s["question"])
        return {"sentences": _sentences(texts, source), "source": source}

    def compare_answer(s: ChatState) -> dict:
        raws = [deps.raw(c) for c in s["dongs"]]
        intent = s["intents"][0]
        if intent in RULE_ONLY:
            if intent == "months":
                out = compare.months_sentences(raws)
            elif intent == "similar":
                common = arrange.common(raws)
                out = compare.similarity_sentences(raws, common, deps.vecs(), deps.pairs())
            else:
                out = compare.hours_sentences(raws, intent.split("_")[0])
            return {"sentences": out, "source": "rule"}
        dongs = [arrange.dong_of(r) for r in raws]
        criteria = s.get("criteria") or arrange.clean_criteria(None)
        recent = [c for c in criteria["recent"] if c not in s["dongs"]]
        recent_links = arrange.links(raws, recent)
        axes = arrange.INTENT_AXES.get(intent) or arrange.choose_axes(
            criteria, dongs, bool(recent_links)
        )
        orders = arrange.orders(dongs, [a for a in axes if a != arrange.LINK])
        user = compare.build_prompt(
            intent, dongs, orders, arrange.common(raws), arrange.links(raws, s["dongs"])
        )
        key = compare.cache_key(s["dongs"], intent, axes, user)
        hit = cache.get(key)
        if hit is None:
            hit = compare.compose(deps.generate, user, dongs, orders)
            if hit[1] != "rule":  # HCX 가 잠깐 안 될 때의 규칙 답은 1일 남기지 않는다
                cache.set(key, hit, compare.CACHE_TTL)
        sentences, source = hit
        if arrange.LINK in axes and recent_links:
            sentences = [*sentences, compare.link_sentence(recent_links[0])]
        if not sentences:
            sentences = _sentences([ask.NO_DATA], "rule")
        logger.info(
            "비교 답 dongs=%s intent=%s axes=%s source=%s", s["dongs"], intent, axes, source
        )
        return {"sentences": sentences, "source": source}

    def finish(s: ChatState) -> dict:
        sentences = s.get("sentences") or []
        if s.get("note"):
            sentences = [*_sentences([s["note"]], "rule"), *sentences]
        return {
            "question": "",
            "sentences": sentences,
            "turns": [{"intents": s.get("intents") or [], "dongs": s["dongs"]}],
        }

    g = StateGraph(ChatState)
    for name, fn in (
        ("resolve_dongs", resolve_dongs),
        ("find_intent", find_intent),
        ("guide", guide),
        ("single_answer", single_answer),
        ("compare_answer", compare_answer),
        ("finish", finish),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "resolve_dongs")
    g.add_conditional_edges(
        "resolve_dongs",
        lambda s: "finish" if s.get("guide") else "find_intent",
        ["finish", "find_intent"],
    )
    g.add_conditional_edges("find_intent", route, ["guide", "single_answer", "compare_answer"])
    for name in ("guide", "single_answer", "compare_answer"):
        g.add_edge(name, "finish")
    g.add_edge("finish", END)
    return g


_saver = None
_lock = threading.Lock()


def saver():
    """REDIS_URL 이 있으면 RedisSaver(redis:8), 없으면 메모리. 처음 쓸 때 한 번 만들고 setup 한다.
    Redis 가 죽어 있으면 RedisError 가 나고, 다음 요청에서 다시 만든다."""
    global _saver
    with _lock:
        if _saver is None:
            if settings.REDIS_URL:
                from langgraph.checkpoint.redis import RedisSaver

                redis_saver = RedisSaver(
                    redis_url=settings.REDIS_URL,
                    ttl={"default_ttl": TTL_MINUTES, "refresh_on_read": True},
                )
                redis_saver.setup()
                _saver = redis_saver
            else:
                _saver = InMemorySaver()
        return _saver


def run(
    graph: StateGraph, conversation_id: str | None, panel: str, question: str, criteria: dict
) -> tuple[str, dict]:
    """(대화 id, 끝난 상태). 없는 대화이거나 패널이 다르면 새 id.
    체크포인터(Redis) 오류면 기억 없이 한 번 더 실행한다 (스펙 §7-1)."""
    inp = {"question": question, "panel": panel, "criteria": criteria}
    try:
        app = graph.compile(checkpointer=saver())
        cid = conversation_id
        if cid:
            prev = app.get_state({"configurable": {"thread_id": cid}}).values
            if prev.get("panel") != panel:
                cid = None
        cid = cid or uuid.uuid4().hex
        state = app.invoke(inp, {"configurable": {"thread_id": cid}}, durability="exit")
        return cid, state
    except RedisError as ex:
        logger.warning("채팅 대화 기억(Redis) 실패 → 기억 없이 답함: %s", ex)
        return conversation_id or uuid.uuid4().hex, graph.compile().invoke(inp)


def respond(conversation_id: str, state: dict, names: list[DongName]) -> dict:
    """스펙 §8 응답 본문."""
    by_code = {n.code: n.name for n in names}
    intents = state.get("intents") or ["unknown_other"]
    dongs = state.get("dongs") or []
    if len(dongs) >= 2 and not state.get("guide"):
        examples = list(COMPARE_EXAMPLES)
    else:
        examples = ask.examples_for(intents)
    return {
        "conversationId": conversation_id,
        "dongs": [{"id": c, "name": by_code.get(c, c)} for c in dongs],
        "intent": intents[0],
        "sentences": [
            {"text": s["text"], "evidence": s["evidence"], "source": s["source"]}
            for s in state.get("sentences") or []
        ],
        "guide": state.get("guide"),
        "examples": examples,
    }
