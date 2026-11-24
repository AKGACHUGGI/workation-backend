"""시군구 채팅 답 (시군구 채팅 스펙 §6). fetch_region 원재료 → 조건 축 → 후보 동 최대 3곳 →
축별 순서(코드) → compare.compose(생성 · 검사 · 규칙 문장) → 근거 줄은 시군구 순위로.

후보끼리의 순서만 LLM 에 넣고, 원래 숫자 · 시군구 순위는 근거 줄에만 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from explore import derive
from explore.chat import arrange
from explore.chat.arrange import GRADE_AXES, INLAND

MAX_SPOTS = 3
TYPE_AXES = [f"type:{t}" for t in derive.TYPES]
TYPE_BY_KO = {ko: key for key, ko in derive.TYPE_KO.items()}
STRENGTHS = ("quiet", "evening", "peer", "coast")  # 조건 외 강점 축 (문장 2·3번째)
# 질문 속 조건 낱말 → 축. 위에서부터가 아니라 질문에 나온 순서로 고른다
KEYWORDS = (
    ("quiet", ("조용", "한적", "한산")),
    ("peer", ("또래", "젊은", "2030")),
    ("evening", ("저녁", "밤", "활기")),
    ("coast", ("바다", "바닷가", "해변", "해안")),
    ("life", ("생활권", "주민")),
    ("tourist", ("관광",)),
    ("type:immersion", ("몰입", "집중")),
    ("type:networking", ("네트워킹", "교류")),
    ("type:local", ("로컬",)),
)
NEGATION = ("말고", "빼고", "아닌")
MAX_CONDITIONS = 2
INTENT_AXES = {
    "find_quiet": ["quiet"],
    "find_peer": ["peer"],
    "find_evening": ["evening"],
    "find_coast": ["coast"],
    "find_life": ["life"],
    "find_type": TYPE_AXES,
}


@dataclass(frozen=True)
class Spot:
    dong: arrange.Dong
    ranks: dict[str, tuple[int, int]]  # 지표 → (시군구 순위, 시군구 동 수)
    life: str | None  # 생활권 · 관광지


@dataclass(frozen=True)
class Plan:
    conditions: list[str]
    axes: list[str]  # 문장 순서: 조건 축 → 강점 축
    spots: list[Spot]  # 후보, 비어 있으면 조건에 맞는 동이 없다


def spots_of(raw: dict) -> list[Spot]:
    out = []
    for d in raw["dongs"]:
        values: dict[str, float] = {}
        grades: dict[str, str] = {}
        ranks: dict[str, tuple[int, int]] = {}
        for metric, grade, score, rank, of in d["grades"]:
            values[metric], grades[metric], ranks[metric] = score, grade, (rank, of)
        values["coast"] = -min(d["near"]) if d["near"] else INLAND
        for _, name, score in d["types"]:
            if name in TYPE_BY_KO:
                values[f"type:{TYPE_BY_KO[name]}"] = score
        tag, value = d["life"][0] if d["life"] else (None, None)
        if value is not None:
            values["life"], values["tourist"] = value, -value
        out.append(Spot(arrange.Dong(d["code"], d["name"], values, grades), ranks, tag))
    return out


def condition_axes(question: str, intent: str) -> list[str]:
    """질문 속 조건 낱말 → 축, 나온 순서대로 최대 2개. '○○ 말고' 앞의 조건은 뺀다.
    낱말이 없으면 의도의 기본 축(유형 의도는 세 유형)."""
    dropped = ""
    for word in NEGATION:
        i = question.find(word)
        if i >= 0:
            dropped, question = question[:i], question[i + len(word) :]
            break
    hits = []
    for axis, words in KEYWORDS:
        found = [question.find(w) for w in words if w in question]
        if found and not any(w in dropped for w in words):
            hits.append((min(found), axis))
    picked = [axis for _, axis in sorted(hits)][:MAX_CONDITIONS]
    return picked or list(INTENT_AXES.get(intent, []))


def _eligible(spot: Spot, axis: str) -> bool:
    v = spot.dong.values
    if axis == "coast":
        return v["coast"] > INLAND
    if axis == "life":
        return spot.life == "생활권"
    if axis == "tourist":
        return spot.life == "관광지"
    if axis.startswith("type:"):
        return v.get(axis, -1) >= derive.TYPE_MIN
    return axis in v


def ranked(spots: list[Spot], axis: str) -> list[Spot]:
    """그 축 조건에 드는 동, 강한 순(같으면 동 코드 순)."""
    have = [s for s in spots if _eligible(s, axis)]
    return sorted(have, key=lambda s: (-s.dong.values[axis], s.dong.code))


def _pool(spots: list[Spot], axis: str) -> list[Spot]:
    """여러 조건을 겹칠 때 그 축에서 '맞는' 동.
    등급 축은 상위 절반, 나머지는 조건에 드는 동 전부."""
    order = ranked(spots, axis)
    return order[: math.ceil(len(order) / 2)] if axis in GRADE_AXES else order


def _strengths(cands: list[Spot], conditions: list[str]) -> list[str]:
    """후보끼리 단독 1위가 조건 1위가 아닌 다른 동인 강점 축, 동마다 하나씩 최대 2개."""
    used = {cands[0].dong.name}
    out: list[str] = []
    dongs = [s.dong for s in cands]
    for axis in STRENGTHS:
        if axis in conditions or len(out) == arrange.MAX_AXES - len(conditions):
            continue
        order = next(iter(arrange.orders(dongs, [axis])), None)
        if order and order.top and order.top not in used:
            used.add(order.top)
            out.append(axis)
    return out


def plan(spots: list[Spot], conditions: list[str]) -> Plan:
    if conditions == TYPE_AXES:  # 유형을 안 정한 유형 질문: 유형마다 1위
        tops = [ranked(spots, a)[:1] for a in TYPE_AXES]
        cands = list({s.dong.code: s for top in tops for s in top}.values())
        return Plan(conditions, list(TYPE_AXES), cands)
    if len(conditions) == 1:
        cands = ranked(spots, conditions[0])[:MAX_SPOTS]
        if not cands:
            return Plan(conditions, list(conditions), [])
        return Plan(conditions, [*conditions, *_strengths(cands, conditions)], cands)
    first, second = conditions
    keep = {s.dong.code for s in _pool(spots, second)}
    cands = [s for s in _pool(spots, first) if s.dong.code in keep][:MAX_SPOTS]
    return Plan(conditions, list(conditions), cands)
