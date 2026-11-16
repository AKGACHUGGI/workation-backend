"""비교 답 (스펙 §6). 순수 함수 — 구조화 생성 함수는 인자로 받는다.

축별 순서(코드 계산)만 프롬프트에 넣고 LLM 은 상황 고르기와 문장만 맡는다.
문장마다 방향(pick 이 그 축 단독 1등) · 이름 · 숫자 · 금지어 · 모양을 검사하고,
걸린 문장은 그 축의 규칙 문장으로 바꾼다. 근거 줄은 코드가 만든다.
시각 · 달 · 닮음 비교는 상황을 고를 게 없어 규칙 문장만 쓴다.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, create_model

from explore import ask, brief
from explore.brief import josa
from explore.chat import arrange, llm
from explore.chat.arrange import AXIS_KO, GRADE_AXES, Dong, Link, Order, Pairs

logger = logging.getLogger(__name__)

Generate = Callable[[str, str, type[BaseModel]], BaseModel]

PROMPT_VERSION = "c1"  # 프롬프트 · 검사를 고치면 올린다 (캐시 키에 들어간다)
CACHE_TTL = 24 * 60 * 60  # 비교 답 캐시 1일 (스펙 §7-2)
JARGON = ("지표", "벡터", "거리", "백분위", "노드", "그래프", "데이터")
NO_CONCLUSION = ("모두", "둘 다", "셋 다")
QUESTION = {"compare_summary": "어느 동네가 나한테 맞아?"}

SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 비교 안내자다. 사용자가 준 축별 순서만 근거로 쓴다.",
        "- 문장마다 '…을 원한다면 ○○이 맞아요'처럼 상황 하나와 동네 하나를 고른다.",
        "- 존댓말 해요체, 문장마다 60자 이내.",
        "- 문장마다 다른 axis 를 쓴다. "
        "pick 은 그 축 순서의 맨 앞 동네이고, 문장 안에 그 이름을 쓴다.",
        "- 순서가 '='로 같은 축은 쓰지 않는다.",
        "- 동네 이름에서 받는 인상(읍·면은 시골이라 조용하다 등)이나 상식으로 판단하지 않는다.",
        "- 숫자와 등급 글자(S·A·B·C)를 쓰지 않는다.",
        "- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.",
        "- '지표', '벡터', '거리', '백분위', '노드', '그래프', '데이터' 같은 말을 쓰지 않는다.",
        "- '두 곳 모두'처럼 결론 없는 문장은 쓰지 않는다.",
    ]
)


def answer_model(axes: list[str], names: list[str]) -> type[BaseModel]:
    """이번 턴 축 · 동 이름만 고를 수 있는 응답 스키마. direction 은 받지 않는다(스펙 §6-1)."""
    sentence = create_model(
        "Sentence",
        text=(str, Field(description="상황별 안내 한 문장. 해요체, 60자 이내, 숫자 없이")),
        axis=(Literal[tuple(axes)], Field(description="이 문장이 쓰는 축")),
        pick=(Literal[tuple(names)], Field(description="그 축 순서의 맨 앞 동네")),
    )
    return create_model(
        "CompareAnswer",
        sentences=(list[sentence], Field(min_length=1, max_length=len(axes))),
    )


def build_prompt(
    intent: str, dongs: list[Dong], orders: list[Order], common: list[str], links: list[Link]
) -> str:
    """사용자 문장 없이 의도의 대표 질문 + 코드 정리만."""
    by_name = {d.name: d for d in dongs}
    lines = [
        f"질문: {QUESTION.get(intent) or ask.CANONICAL[intent]} (동네끼리 비교)",
        f"고른 동네: {', '.join(by_name)}",
        f"이번에 볼 기준: {', '.join(AXIS_KO[o.axis] for o in orders)}",
        "",
        "[축별 순서 · 코드 계산]",
        *(f"{o.axis}: {arrange.order_line(o, by_name)}" for o in orders),
    ]
    if common:
        lines.append("공통: " + " · ".join(common[:4]))
    lines += [f"연결: {josa(x.dong)} {josa(x.other, '과/와')} 비슷한 동 {x.rank}위" for x in links]
    return "\n".join(lines)


def problems(s: dict, by_axis: dict[str, Order], names: list[str], seen: set[str]) -> list[str]:
    """문제 목록. 비어 있으면 통과 (스펙 §6-2)."""
    text, axis, pick = s["text"], s["axis"], s["pick"]
    out = []
    order = by_axis.get(axis)
    if order is None or order.top != pick:
        top = order.top if order else None
        out.append(f"방향: {axis} 1등 {top}, 고른 동 {pick}")
    if pick not in text:
        out.append("이름: 문장에 고른 동 이름 없음")
    if axis in seen:
        out.append("같은 축 중복")
    plain = text
    for name in sorted(names, key=len, reverse=True):
        plain = plain.replace(name, "")  # '효자2동' 의 2 가 숫자로 걸리지 않게
    stray = brief.NUMBER.findall(plain) + brief.GRADE.findall(plain)
    if stray:
        out.append(f"숫자·등급: {stray}")
    if len(text) > brief.MAX_LEN:
        out.append(f"{brief.MAX_LEN}자 초과")
    banned = [w for w in (*brief.BANNED, *JARGON, *NO_CONCLUSION) if w in text]
    if banned:
        out.append(f"금지어: {banned}")
    if not text.rstrip().rstrip(".!?").endswith("요"):
        out.append("해요체 아님")
    return out


def rule_sentence(o: Order) -> str:
    ko = AXIS_KO[o.axis]
    if o.top is None:
        tied = "·".join(n for n, v in o.ranked if v == o.ranked[0][1])
        same = "같은 등급이에요" if o.axis in GRADE_AXES else "비슷해요"
        return f"{josa(ko)} {josa(tied, '이/가')} {same}."
    return f"{josa(ko, '을/를')} 원한다면 {josa(o.top, '이/가')} 맞아요."


def _item(text: str, o: Order, by_name: dict[str, Dong], source: str) -> dict:
    return {"text": text, "evidence": arrange.evidence(o, by_name), "source": source}


def compose(
    generate: Generate | None, user: str, dongs: list[Dong], orders: list[Order]
) -> tuple[list[dict], str]:
    """생성 → 검사 → 실패한 문장 · 빠진 축은 규칙 문장. 축 순서대로. (문장들, source)"""
    if not orders:
        return [], "rule"
    by_axis = {o.axis: o for o in orders}
    by_name = {d.name: d for d in dongs}
    names = [d.name for d in dongs]
    items: list[dict] = []
    if generate is not None:
        try:
            reply = generate(SYSTEM, user, answer_model(list(by_axis), names))
            items = [s.model_dump() for s in reply.sentences]
        except Exception as ex:  # HCX 오류 · 시간 초과 · 스키마 불일치 모두 규칙 문장으로
            logger.warning("비교 답 생성 실패: %s", ex)
    done: dict[str, dict] = {}
    seen: set[str] = set()
    for s in items:
        found = problems(s, by_axis, names, seen)
        seen.add(s["axis"])
        if s["axis"] in done:
            continue
        o = by_axis[s["axis"]]
        if found:
            logger.info("비교 답 문장 교체 %r: %s", s["text"], found)
            done[o.axis] = _item(rule_sentence(o), o, by_name, "rule")
        else:
            done[o.axis] = _item(s["text"], o, by_name, "llm")
    out = [done.get(o.axis) or _item(rule_sentence(o), o, by_name, "rule") for o in orders]
    passed = sum(s["source"] == "llm" for s in out)
    source = "llm" if passed == len(out) else "rule" if passed == 0 else "mixed"
    return out, source


def _rule(text: str, evidence: str | None = None) -> dict:
    return {"text": text, "evidence": evidence, "source": "rule"}


def hours_sentences(raws: list[dict], day: str) -> list[dict]:
    """동별 나란히: 평일은 붐비는 시각 · 업무시간 한산 시각, 주말은 붐비는 시각만(스펙 §11)."""
    ko = "평일" if day == "weekday" else "주말"
    out = []
    for r in raws:
        rec = r["rec"]
        hours = brief._hours(rec)
        peak, quiet = hours.get(("PEAK_AT", day)), hours.get(("QUIET_AT", day))
        if peak is None:
            continue
        name = josa(rec["dong"]["name"])
        if day == "weekday" and quiet is not None:
            text = f"{name} 평일 {peak}시에 가장 붐비고, 업무시간엔 {quiet}시가 한산해요."
        else:
            text = f"{name} {ko} {peak}시에 차가 가장 많아요."
        driv = rec["dong"][f"driv_{day}"][peak]
        out.append(_rule(text, f"{ko} 차량 이동 인구 가장 많은 시각: {peak}시 {driv}명"))
    return out or [_rule(ask.NO_DATA)]


def months_sentences(raws: list[dict]) -> list[dict]:
    """같은 시군구라 한산한 달은 늘 같다 → 공통점 한 문장."""
    rec = raws[0]["rec"]
    quiet = sorted(m for m, _, q in rec["months"] if q)
    if not quiet:
        return [_rule(ask.NO_DATA)]
    sgg = rec["region"]["name"]
    names = "·".join(r["rec"]["dong"]["name"] for r in raws)
    months = "·".join(map(str, quiet))
    text = f"{josa(names)} 같은 {josa(sgg, '이라/라')} {months}월이 가장 한산해요."
    return [_rule(text, f"월별 붐빔은 {sgg} 단위 값이에요")]


def similarity_sentences(
    raws: list[dict],
    common: list[str],
    vecs: dict[str, tuple[tuple[float, ...], bool]],
    pairs: Pairs,
) -> list[dict]:
    """패널 동과 나머지 동의 닮음 구간 + 공통점."""
    panel = raws[0]["rec"]["dong"]
    out = []
    for r in raws[1:]:
        other = r["rec"]["dong"]
        like = arrange.likeness(panel["code"], other["code"], vecs, pairs)
        if like is None:
            continue
        pct, word = like
        pair = f"{josa(panel['name'], '과/와')} {other['name']}"
        out.append(
            _rule(
                f"{josa(pair)} 분위기가 {word}이에요.",
                f"두 동네 닮음: 전국 동네 쌍 중 가까운 쪽 {pct}%",
            )
        )
    if common:
        labels = " · ".join(common[:3])
        out.append(_rule(f"공통점은 {josa(labels, '이에요/예요')}."))
    return out or [_rule(ask.NO_DATA)]


def link_sentence(link: Link) -> dict:
    return _rule(
        f"최근 보신 {josa(link.other, '과/와')} 비슷한 쪽은 {josa(link.dong, '이에요/예요')}.",
        f"{link.dong}의 비슷한 동 {link.rank}위: {link.other}",
    )


def cache_key(codes: list[str], intent: str, axes: list[str], user: str) -> str:
    """사용자 문장은 넣지 않는다. 프롬프트 해시가 축별 순서 · 공통점 · 연결을 대신한다."""
    digest = hashlib.sha256(user.encode()).hexdigest()[:16]
    return (
        f"explore:chat:a:{'+'.join(sorted(codes))}:{intent}:{'+'.join(axes)}:{digest}"
        f":{llm.MODEL}:{PROMPT_VERSION}"
    )
