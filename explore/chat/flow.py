"""탐색 채팅 흐름 (v7 스펙 §3 + 시군구 채팅 스펙 §4). LangGraph StateGraph + 체크포인터.

START → resolve_dongs ─(안내)→ finish
                      ├(시군구 답)→ region_intent → guide | region_answer → finish
                      └(동 흐름)→ find_intent → guide | hours_both | single_answer | compare_answer
                                                → finish → END

노드는 상태를 받아 바뀐 필드만 돌려준다. 바깥 의존(읽기 · 의도 · 생성)은 Deps 로 받는다.
상태에는 의도 · 동네만 남기고(원재료는 1시간 캐시에서 다시 읽는다), 질문은 finish 에서 비운다.
실행은 durability="exit" 라 끝난 상태 하나만 저장된다 (v7 스펙 §7-2-1).
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
from redisvl.exceptions import RedisVLError

from explore import ask
from explore.brief import josa
from explore.chat import arrange, both, compare, region
from explore.chat.resolve import DongName, resolve, resolve_region

logger = logging.getLogger(__name__)

TTL_MINUTES = 30  # 대화 기억 (스펙 §7-1). 읽을 때마다 다시 30분
COMPARE_EXAMPLES = ("둘이 비슷해?", "주말엔 몇 시가 붐벼?", "언제 가면 한산해?")
RULE_ONLY = ("months", "similar")
KEYS = ("surface", "panel", "sigungu")  # 이 값이 바뀌면 새 대화
# 시군구 답 다음 동 이름만 물으면("효자2동은?") 이어받을 동 의도. 달 · 성격 의도는 잇지 않는다
REGION_TO_DONG = {
    "find_quiet": "quiet",
    "find_peer": "peer",
    "find_evening": "evening",
    "find_coast": "coast",
    "find_life": "life",
    "find_type": "type",
}


class ChatState(TypedDict, total=False):
    question: str  # 이번 질문. finish 에서 비운다
    surface: str  # region · dong
    panel: str | None  # 동 상세의 동 (surface=dong)
    sigungu: str | None  # 시군구 상세의 시군구 (surface=region)
    month: int | None  # 보행 기준 달 (None 이면 연간)
    walk: bool  # 보행 기준 문장을 쓸지 (요청에 month 가 있었다)
    criteria: dict  # arrange.clean_criteria 결과
    dongs: list[str]  # 이번 턴 동네 (동 답: 비교 대상 / 시군구 답: 찾은 후보)
    named: bool  # 질문에 같은 시군구 동 이름이 있었다
    scope: str  # 이번 턴 답이 시군구(region)인지 동(dong)인지
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
    find_intents: Callable[[str, str], tuple[list[str], float]]  # (질문, scope)
    single: Callable[[str, dict, list[str], str], tuple[list[str], str]]
    generate: compare.Generate | None
    vecs: Callable[[], dict]
    pairs: Callable[[], arrange.Pairs]
    region_raw: Callable[[str], dict | None]
    walk_peaks: Callable[[str, int | None], dict | None]
    best_months: Callable[[str], list[int]]


def settle_intents(found: list[str], prev: list[str], named: bool, count: int) -> list[str]:
    """의도를 못 찾았고(unknown_other) 질문에 동 이름이 있으면 직전 의도를 잇고("교1동은?"),
    이을 의도가 없으면 기준 비교(compare_summary). 동 이름이 없으면 비교 중이어도 안내 —
    "안녕"·"날씨 어때?" 에 직전 비교를 되풀이하지 않는다. "뭐가 달라?" 같은 비교 말투는
    ask_vocab 의 compare_summary 표현이 잡는다."""
    if found != ["unknown_other"] or not named:
        return found
    if prev and prev[0] not in ask.UNKNOWN:
        return prev
    return ["compare_summary"] if count >= 2 else found


def _sentences(texts: list[str], source: str) -> list[dict]:
    return [{"text": t, "evidence": None, "source": source} for t in texts]


def build(deps: Deps) -> StateGraph:
    def resolve_dongs(s: ChatState) -> dict:
        names = deps.names()
        if s.get("surface") == "region":
            follow = s.get("scope") == "dong"
            r = resolve_region(s["question"], s["sigungu"], s.get("dongs") or [], names, follow)
        else:
            panel = next(n for n in names if n.code == s["panel"])  # 뷰가 미리 확인한다
            r = resolve(s["question"], panel, s.get("dongs") or [], names)
        scope = "dong" if r.dongs else "region"
        if r.guide:  # 안내 턴은 직전 답의 성격(시군구 · 동)을 바꾸지 않는다
            scope = s.get("scope") or scope
        return {
            "dongs": r.dongs,
            "named": r.named,
            "scope": scope,
            "guide": r.guide,
            "note": r.note,
            "sentences": [],
            "source": "guide" if r.guide else "",
        }

    def after_resolve(s: ChatState) -> str:
        if s.get("guide"):
            return "finish"
        return "region_intent" if s["scope"] == "region" else "find_intent"

    def find_intent(s: ChatState) -> dict:
        found, _ = deps.find_intents(s["question"], "dong")
        # 직전 턴이 시군구 답이면 찾기 의도(find_*)는 같은 축의 동 의도로 바꿔 잇는다
        prev = [
            REGION_TO_DONG.get(i, i)
            for i in s.get("intents") or []
            if i in REGION_TO_DONG or i not in ask.REGION_INTENTS
        ]
        return {"intents": settle_intents(found, prev, s["named"], len(s["dongs"]))}

    def route(s: ChatState) -> str:
        first = s["intents"][0]
        if any(i in ask.HOURS for i in s["intents"]):
            return "hours_both"
        if len(s["dongs"]) >= 2 and first not in ask.UNKNOWN:
            return "compare_answer"
        if first in ask.GUIDE:
            return "guide"
        return "single_answer"

    def region_intent(s: ChatState) -> dict:
        found, _ = deps.find_intents(s["question"], "region")
        return {"intents": found}

    def route_region(s: ChatState) -> str:
        return "region_answer" if s["intents"][0] in ask.REGION_INTENTS else "guide"

    def guide(s: ChatState) -> dict:
        first = s["intents"][0]
        text = ask.REGION_HOURS_GUIDE if first in ask.HOURS else ask.GUIDE[first][0]
        return {"guide": text, "source": "guide"}

    def hours_both(s: ChatState) -> dict:
        day = next(i for i in s["intents"] if i in ask.HOURS).split("_")[0]
        month = s.get("month")
        label = (f"{month}월" if month else "연간") if s.get("walk") else None
        items = [(deps.raw(c), deps.walk_peaks(c, month) if label else None) for c in s["dongs"]]
        out = both.hours_both(items, day, label)
        source = "rule"
        rest = [i for i in s["intents"] if i not in ask.HOURS]
        if rest and len(s["dongs"]) == 1:  # "오전에 조용해?" → 조용함 답 + 시간대 답
            texts, src = deps.single(s["dongs"][0], items[0][0], rest, s["question"])
            out = [*_sentences(texts, src), *out]
            source = "rule" if src == "rule" else "mixed"
        return {"sentences": out, "source": source}

    def region_answer(s: ChatState) -> dict:
        raw = deps.region_raw(s["sigungu"])
        if raw is None:
            return {"sentences": _sentences([ask.NO_DATA], "rule"), "source": "rule", "dongs": []}
        intent = s["intents"][0]
        if intent == "region_months":
            out, source, codes = both.months_both(raw, deps.best_months(s["sigungu"])), "rule", []
        else:
            out, source, codes = region.answer(deps.generate, raw, intent, s["question"])
        logger.info("시군구 답 sigungu=%s intent=%s source=%s", s["sigungu"], intent, source)
        return {"sentences": out, "source": source, "dongs": codes}

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
            else:
                common = arrange.common_items(raws)
                out = compare.similarity_sentences(raws, common, deps.vecs(), deps.pairs())
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
        sentences = [{**x, "text": compare.tidy(x["text"])} for x in s.get("sentences") or []]
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
        ("region_intent", region_intent),
        ("guide", guide),
        ("hours_both", hours_both),
        ("single_answer", single_answer),
        ("compare_answer", compare_answer),
        ("region_answer", region_answer),
        ("finish", finish),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "resolve_dongs")
    g.add_conditional_edges(
        "resolve_dongs", after_resolve, ["finish", "region_intent", "find_intent"]
    )
    g.add_conditional_edges(
        "find_intent", route, ["guide", "hours_both", "single_answer", "compare_answer"]
    )
    g.add_conditional_edges("region_intent", route_region, ["guide", "region_answer"])
    for name in ("guide", "hours_both", "single_answer", "compare_answer", "region_answer"):
        g.add_edge(name, "finish")
    g.add_edge("finish", END)
    return g


_saver = None
_lock = threading.Lock()


def saver():
    """REDIS_URL 이 있으면 RedisSaver(redis:8), 없으면 메모리. 처음 쓸 때 한 번 만들고 setup 한다.
    Redis 가 죽어 있으면 RedisError, search · JSON 모듈이 없는 Redis(Homebrew redis 등)면
    RedisVLError 가 나고, 다음 요청에서 다시 만든다."""
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


def run(graph: StateGraph, conversation_id: str | None, inp: dict) -> tuple[str, dict]:
    """(대화 id, 끝난 상태). 없는 대화이거나 surface · 패널 동 · 시군구가 다르면 새 id.
    체크포인터(Redis) 오류면 기억 없이 한 번 더 실행한다 (v7 스펙 §7-1).
    모듈 없는 Redis 도 여기로 온다 — 답은 하고 대화만 이어지지 않는다."""
    try:
        app = graph.compile(checkpointer=saver())
        cid = conversation_id
        if cid:
            prev = app.get_state({"configurable": {"thread_id": cid}}).values
            if any(prev.get(k) != inp.get(k) for k in KEYS):
                cid = None
        cid = cid or uuid.uuid4().hex
        state = app.invoke(inp, {"configurable": {"thread_id": cid}}, durability="exit")
        return cid, state
    except (RedisError, RedisVLError) as ex:
        logger.warning("채팅 대화 기억(Redis) 실패 → 기억 없이 답함: %s", ex)
        return conversation_id or uuid.uuid4().hex, graph.compile().invoke(inp)


def _region_examples(dongs: list[str], by_code: dict[str, str]) -> list[str]:
    """시군구 답 뒤 예시: 찾은 동 이름으로. 찾은 동이 없으면 첫 화면 예시."""
    if not dongs:
        return list(ask.REGION_FIRST)
    first = by_code.get(dongs[0], dongs[0])
    out = [f"{josa(first)} 저녁에 어때?"]
    if len(dongs) >= 2:
        out.append(f"{josa(first, '이랑/랑')} {by_code.get(dongs[1], dongs[1])} 비교해 줘")
    out += [ask.REGION_CANONICAL["region_months"], ask.REGION_CANONICAL["find_coast"]]
    return out[:3]


def respond(conversation_id: str, state: dict, names: list[DongName]) -> dict:
    """v7 스펙 §8 응답 본문 + scope (시군구 채팅 스펙 §3)."""
    by_code = {n.code: n.name for n in names}
    intents = state.get("intents") or ["unknown_other"]
    dongs = state.get("dongs") or []
    scope = state.get("scope") or "dong"
    if scope == "region":
        examples = (
            list(ask.REGION_FIRST) if state.get("guide") else _region_examples(dongs, by_code)
        )
    elif len(dongs) >= 2 and not state.get("guide"):
        examples = list(COMPARE_EXAMPLES)
    else:
        examples = ask.examples_for(intents)
    return {
        "conversationId": conversation_id,
        "scope": scope,
        "dongs": [{"id": c, "name": by_code.get(c, c)} for c in dongs],
        "intent": intents[0],
        "sentences": [
            {"text": s["text"], "evidence": s["evidence"], "source": s["source"]}
            for s in state.get("sentences") or []
        ],
        "guide": state.get("guide"),
        "examples": examples,
    }
