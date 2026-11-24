"""비교 답 (스펙 §6). 순수 함수 — 구조화 생성 함수는 인자로 받는다.

축별 순서(코드 계산)만 프롬프트에 넣고 LLM 은 상황 고르기와 문장만 맡는다.
문장마다 방향(문장 속 동 이름이 그 축 단독 1등) · 이름 · 숫자 · 금지어 · 모양을 검사하고,
걸린 문장은 그 축의 규칙 문장으로 바꾼다. 근거 줄은 코드가 만든다.
달 · 닮음 비교는 상황을 고를 게 없어 규칙 문장만 쓴다(시각은 chat/both.py).
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, create_model

from explore import ask, brief
from explore.brief import josa
from explore.chat import arrange, llm
from explore.chat.arrange import AXIS_KO, GRADE_AXES, Dong, Link, Order, Pairs

logger = logging.getLogger(__name__)

Generate = Callable[[str, str, type[BaseModel]], BaseModel]

PROMPT_VERSION = "c3"  # 프롬프트 · 검사를 고치면 올린다 (캐시 키에 들어간다)
CACHE_TTL = 24 * 60 * 60  # 비교 답 캐시 1일 (스펙 §7-2)
JARGON = ("지표", "벡터", "거리", "백분위", "노드", "그래프", "데이터")
NO_CONCLUSION = ("모두", "둘 다", "셋 다")
QUESTION = {"compare_summary": "어느 동네가 나한테 맞아?"}

SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 비교 안내자다. 사용자가 준 축별 순서만 근거로 쓴다.",
        "- 문장마다 상황 하나와 그 상황에 맞는 동네 하나를 고른다.",
        "- 문장마다 문형을 다르게 쓴다. 예: '조용히 일하고 싶다면 ○○이 맞아요', "
        "'저녁에 활기를 찾는다면 ○○ 쪽이 나아요', '또래가 많은 곳은 ○○이에요'. "
        "예시를 그대로 베끼지 말고 상황에 맞게 바꿔 쓴다.",
        "- 앞 문장과 다른 동네를 고른 문장은 '반면', '대신'으로 시작해도 된다.",
        "- 존댓말 해요체, 문장마다 60자 이내.",
        "- 문장마다 다른 axis 를 쓰고, 그 축 순서의 맨 앞 동네 이름 하나만 문장에 쓴다.",
        "- 순서가 '='로 같은 축은 쓰지 않는다.",
        "- 동네 이름에서 받는 인상(읍·면은 시골이라 조용하다 등)이나 상식으로 판단하지 않는다.",
        "- 숫자와 등급 글자(S·A·B·C)를 쓰지 않는다.",
        "- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.",
        "- '지표', '벡터', '거리', '백분위', '노드', '그래프', '데이터' 같은 말을 쓰지 않는다.",
        "- '두 곳 모두'처럼 결론 없는 문장은 쓰지 않는다.",
    ]
)


def answer_model(axes: list[str]) -> type[BaseModel]:
    """이번 턴 축만 고를 수 있는 응답 스키마. direction · pick 은 받지 않는다.
    고른 동은 문장 속 이름으로 본다(HCX-007 이 문장은 맞게 쓰고 pick 칸만 틀리는 일이 잦았다,
    2026-11-17 로컬 확인)."""
    sentence = create_model(
        "Sentence",
        text=(str, Field(description="상황별 안내 한 문장. 해요체, 60자 이내, 숫자 없이")),
        axis=(Literal[tuple(axes)], Field(description="이 문장이 쓰는 축")),
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


def names_in(text: str, names: list[str]) -> list[str]:
    """문장에 나온 비교 동 이름, 나온 순서대로.
    긴 이름부터 찾고 지워서 '신교동' 안의 '교동'을 따로 잡지 않는다."""
    hits: list[tuple[int, str]] = []
    for name in sorted(names, key=len, reverse=True):
        start = text.find(name)
        while start >= 0:
            hits.append((start, name))
            text = text[:start] + "\0" * len(name) + text[start + len(name) :]
            start = text.find(name, start + len(name))
    return [name for _, name in sorted(hits)]


def problems(s: dict, by_axis: dict[str, Order], names: list[str], seen: set[str]) -> list[str]:
    """문제 목록. 비어 있으면 통과 (스펙 §6-2). 문장 속 비교 동 이름이 정확히 하나여야 하고,
    그 동이 그 축의 단독 1등이어야 한다."""
    text, axis = s["text"], s["axis"]
    out = []
    named = list(dict.fromkeys(names_in(text, names)))
    order = by_axis.get(axis)
    top = order.top if order else None
    if not named:
        out.append("이름: 문장에 비교 동 이름 없음")
    elif len(named) > 1:
        out.append(f"이름: 동 이름 여러 개 {named}")
    elif top != named[0]:
        out.append(f"방향: {axis} 1등 {top}, 문장 속 동 {named[0]}")
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


# 문장 끝 합쇼체 → 해요체. 단일 동 답(HCX-005)이 '조용한 편입니다' 처럼 끝나는 일이 있다
HAEYO = (
    ("있습니다", "있어요"), ("없습니다", "없어요"), ("많습니다", "많아요"), ("적습니다", "적어요"),
    ("같습니다", "같아요"), ("좋습니다", "좋아요"), ("합니다", "해요"), ("됩니다", "돼요"),
)  # fmt: skip
END = re.compile(r"[.!?]+$")


def tidy(text: str) -> str:
    """답 문장 끝 맞추기: 합쇼체는 해요체로, 마침표가 없으면 붙인다."""
    body = END.sub("", text.rstrip())
    mark = text.rstrip()[len(body) :] or "."
    for formal, haeyo in HAEYO:
        if body.endswith(formal):
            body = body[: -len(formal)] + haeyo
            break
    else:
        if body.endswith("입니다") and len(body) > 3:
            stem = body[:-3]
            body = josa(stem, "이에요/예요")
    return body + mark


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
            reply = generate(SYSTEM, user, answer_model(list(by_axis)))
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
    out = _fix_contrast(out, names)
    passed = sum(s["source"] == "llm" for s in out)
    source = "llm" if passed == len(out) else "rule" if passed == 0 else "mixed"
    return out, source


CONTRAST = ("반면 ", "반면에 ", "대신 ", "하지만 ")


def _fix_contrast(items: list[dict], names: list[str]) -> list[dict]:
    """'반면'·'대신' 은 앞 문장과 다른 동네를 말할 때만 남긴다. 문장은 축 순서로 다시 놓이므로
    LLM 이 쓴 이음말이 첫 문장이나 같은 동네 뒤에 올 수 있다."""
    out, prev = [], None
    for item in items:
        text = item["text"]
        named = names_in(text, names)
        head = next((c for c in CONTRAST if text.startswith(c)), None)
        if head and (prev is None or named[:1] == prev[:1]):
            item = {**item, "text": text[len(head) :]}
        out.append(item)
        prev = named
    return out


def _rule(text: str, evidence: str | None = None) -> dict:
    return {"text": text, "evidence": evidence, "source": "rule"}


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
    common: list[tuple[str, str]],
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
    text = common_sentence(common, len(raws))
    if text:
        out.append(_rule(text))
    return out or [_rule(ask.NO_DATA)]


def common_sentence(items: list[tuple[str, str]], count: int) -> str | None:
    """공통 사실 [(key, 문구)] → '둘 다 바닷가에 있고, 조용함 수준이 같아요.'
    등급 글자는 쓰지 않고, 시각별 한산(quiet:*)은 뺀다. 마디는 3개까지."""
    clauses: list[tuple[str, str]] = []
    metrics = []
    for key, label in items:
        if key.startswith("place:"):
            clauses.append((f"{label}에 있고", f"{label}에 있어요"))
        elif key.startswith("life:"):
            clauses.append((f"{label} 성격이 강하고", f"{label} 성격이 강해요"))
        elif key.startswith("type:"):
            name = josa(label.removeprefix("1위 "), "이/가")
            clauses.append((f"{name} 가장 잘 맞고", f"{name} 가장 잘 맞아요"))
        elif key.startswith("grade:"):
            metrics.append(label.rsplit(" ", 1)[0])
    if metrics:
        what = "·".join(metrics[:2])
        clauses.append((f"{what} 수준이 같고", f"{what} 수준이 같아요"))
    clauses = clauses[:3]
    if not clauses:
        return None
    who = "둘 다" if count == 2 else "셋 다"
    parts = [c[0] for c in clauses[:-1]] + [clauses[-1][1]]
    return f"{who} {', '.join(parts)}."


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
