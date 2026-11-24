"""시군구 채팅 답 (시군구 채팅 스펙 §6). fetch_region 원재료 → 조건 축 → 후보 동 최대 3곳 →
축별 순서(코드) → compare.compose(생성 · 검사 · 규칙 문장) → 근거 줄은 시군구 순위로.

후보끼리의 순서만 LLM 에 넣고, 원래 숫자 · 시군구 순위는 근거 줄에만 쓴다.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from django.core.cache import cache

from explore import derive
from explore.brief import josa
from explore.chat import arrange, compare
from explore.chat.arrange import AXIS_KO, GRADE_AXES, INLAND, Order

MAX_SPOTS = 3
# 시군구 답 전용 검사 · 프롬프트를 고치면 올린다 (캐시 키에 compare.PROMPT_VERSION 과 함께 들어간다)
PROMPT_VERSION = "r1"
SUPERLATIVE = ("가장", "제일", "1위", "최고")
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
    """그 축 조건에 드는 동, 강한 순. 같으면 등급 축은 시군구 순위, 그다음 동 코드 순."""
    have = [s for s in spots if _eligible(s, axis)]
    return sorted(
        have,
        key=lambda s: (-s.dong.values[axis], s.ranks.get(axis, (0, 0))[0], s.dong.code),
    )


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
    if conditions == TYPE_AXES:  # 유형을 안 정한 유형 질문: 기준을 넘는 동이 있는 유형마다 1위
        tops = {a: ranked(spots, a)[:1] for a in TYPE_AXES}
        axes = [a for a in TYPE_AXES if tops[a]]
        cands = list({s.dong.code: s for a in axes for s in tops[a]}.values())
        return Plan(conditions, axes, cands)
    if len(conditions) == 1:
        cands = ranked(spots, conditions[0])[:MAX_SPOTS]
        if not cands:
            return Plan(conditions, list(conditions), [])
        return Plan(conditions, [*conditions, *_strengths(cands, conditions)], cands)
    first, second = conditions
    keep = {s.dong.code for s in _pool(spots, second)}
    cands = [s for s in _pool(spots, first) if s.dong.code in keep][:MAX_SPOTS]
    return Plan(conditions, list(conditions), cands)


def _rule(text: str, evidence: str | None = None) -> dict:
    return {"text": text, "evidence": evidence, "source": "rule"}


def evidence(axis: str, spots: list[Spot], sgg: str) -> str:
    """근거 줄: 축 값 높은 순. 등급 축은 시군구 순위를 붙인다."""

    def show(s: Spot) -> str:
        d = s.dong
        if axis in GRADE_AXES:
            rank, of = s.ranks[axis]
            return f"{d.name} {d.grades[axis]}({sgg} {rank}위/{of})"
        if axis == "coast":
            v = d.values["coast"]
            return f"{d.name} {'내륙' if v <= INLAND else f'해안선 {-v:.1f}km'}"
        if axis in ("life", "tourist"):
            return f"{d.name} {s.life or '-'}"
        return f"{d.name} {d.values.get(axis, 0):g}점"

    order = sorted(spots, key=lambda s: -s.dong.values.get(axis, INLAND))
    return f"{AXIS_KO[axis]}: " + " · ".join(show(s) for s in order)


def build_prompt(sgg: str, p: Plan, orders: list[Order]) -> str:
    """사용자 문장 없이 조건 + 후보끼리의 축별 순서만. 숫자는 넣지 않는다."""
    by_name = {s.dong.name: s.dong for s in p.spots}
    cond = "·".join(AXIS_KO[a] for a in p.conditions)
    return "\n".join(
        [
            f"질문: {cond} 좋은 동네 어디야? ({sgg} 안에서 동네 찾기)",
            f"찾은 동네: {', '.join(by_name)} ({sgg}에서 {cond} 기준으로 고른 곳)",
            f"이번에 볼 기준: {', '.join(AXIS_KO[o.axis] for o in orders)}",
            "",
            "[축별 순서 · 코드 계산]",
            *(f"{o.axis}: {arrange.order_line(o, by_name)}" for o in orders),
        ]
    )


def _want(axis: str, name: str) -> str:
    return f"{josa(AXIS_KO[axis], '을/를')} 원한다면 {josa(name, '이/가')} 맞아요."


def _none(p: Plan, spots: list[Spot], sgg: str) -> tuple[list[dict], list[str]]:
    """(문장들, 문장에 쓴 동 코드)."""
    if p.conditions == TYPE_AXES:
        return [_rule(f"{sgg}에는 유형이 뚜렷한 동네가 없어요.")], []
    if len(p.conditions) == 1:
        axis = p.conditions[0]
        what = "바다 가까운 동네" if axis == "coast" else f"{AXIS_KO[axis]} 조건에 맞는 동네"
        return [_rule(f"{sgg}에는 {josa(what, '이/가')} 없어요.")], []
    a, b = p.conditions
    out = [_rule(f"{josa(AXIS_KO[a], '과/와')} {josa(AXIS_KO[b], '을/를')} 다 갖춘 곳은 없어요.")]
    codes: list[str] = []
    for axis in p.conditions:
        top = ranked(spots, axis)[:1]
        if top:
            out.append(_rule(_want(axis, top[0].dong.name), evidence(axis, top, sgg)))
            codes.append(top[0].dong.code)
    return out, list(dict.fromkeys(codes))


def _one(p: Plan, sgg: str) -> dict:
    s = p.spots[0]
    if p.conditions == TYPE_AXES:  # 후보가 1위인 유형 축 (plan 이 그런 축만 남긴다)
        axis = p.axes[0]
        return _rule(_want(axis, s.dong.name), evidence(axis, p.spots, sgg))
    ev = evidence(p.conditions[0], p.spots, sgg)
    if len(p.conditions) == 1:
        return _rule(_want(p.conditions[0], s.dong.name), ev)
    a, b = p.conditions
    text = (
        f"{josa(AXIS_KO[a], '과/와')} {josa(AXIS_KO[b], '을/를')} 다 갖춘 곳은 "
        f"{josa(s.dong.name, '이에요/예요')}."
    )
    return _rule(text, ev)


def profile(raw: dict, spots: list[Spot]) -> list[dict]:
    """시군구 성격 (규칙 문장 2개). 수는 근거 줄에."""
    sgg = raw["region"]["name"]
    tops: Counter[str] = Counter()
    for d in raw["dongs"]:
        first = [(name, score) for rank, name, score in d["types"] if rank == 1]
        if first and first[0][1] >= derive.TYPE_MIN:
            tops[first[0][0]] += 1
    local = sum(s.life == "생활권" for s in spots)
    tourist = sum(s.life == "관광지" for s in spots)
    coastal = sum(s.dong.values["coast"] > INLAND for s in spots)
    out = []
    if tops:
        out.append(f"{josa(sgg)} {tops.most_common(1)[0][0]} 동네가 가장 많아요.")
    if local == tourist:
        mood = "생활권과 관광지 동네가 비슷하게 있고"
    else:
        mood = f"{'생활권' if local > tourist else '관광지'} 성격의 동네가 더 많고"
    sea = f"바다 가까운 동네는 {coastal}곳이에요." if coastal else "바다와 닿은 동네는 없어요."
    out.append(f"{mood}, {sea}")
    counts = [f"{name} {n}곳" for name, n in tops.most_common()]
    ev = " · ".join([*counts, f"생활권 {local}곳", f"관광지 {tourist}곳"])
    return [_rule(out[0], ev), *(_rule(t) for t in out[1:])]


def problems(text: str, sgg: str, cands: list[str], every: list[str]) -> list[str]:
    """시군구 답 생성 문장 추가 검사 (compare.problems 다음). 후보끼리만 견준 순서라
    시군구 전체 1등 같은 범위 주장과 후보 아닌 동 이름은 근거가 없다."""
    out = []
    stem = sgg[:-1] if len(sgg) >= 3 else sgg
    if stem in text and any(w in text for w in SUPERLATIVE):
        out.append("범위: 시군구 최상급")
    others = [n for n in dict.fromkeys(compare.names_in(text, every)) if n not in cands]
    if others:
        out.append(f"이름: 후보 아닌 동 {others}")
    return out


def answer(
    generate: compare.Generate | None, raw: dict, intent: str, question: str
) -> tuple[list[dict], str, list[str]]:
    """(문장들, source, 후보 동 코드). 생성 답은 compare 와 같은 키 방식으로 1일 캐시한다."""
    spots = spots_of(raw)
    sgg = raw["region"]["name"]
    if intent == "region_profile":
        return profile(raw, spots), "rule", []
    p = plan(spots, condition_axes(question, intent))
    if not p.spots:
        out, codes = _none(p, spots, sgg)
        return out, "rule", codes
    codes = [s.dong.code for s in p.spots]
    if len(p.spots) == 1:
        return [_one(p, sgg)], "rule", codes
    dongs = [s.dong for s in p.spots]
    orders = arrange.orders(dongs, p.axes)
    user = build_prompt(sgg, p, orders)
    key = f"{compare.cache_key(codes, intent, p.axes, user)}:{PROMPT_VERSION}"
    hit = cache.get(key)
    if hit is None:
        cands = [d.name for d in dongs]
        every = [d["name"] for d in raw["dongs"]]
        check = lambda text: problems(text, sgg, cands, every)  # noqa: E731
        hit = compare.compose(generate, user, dongs, orders, check)
        if hit[1] != "rule":  # HCX 가 잠깐 안 될 때의 규칙 답은 1일 남기지 않는다
            cache.set(key, hit, compare.CACHE_TTL)
    sentences, source = hit
    sentences = [
        {**s, "evidence": evidence(o.axis, p.spots, sgg)}
        for s, o in zip(sentences, orders, strict=True)
    ]
    return sentences or [_one(p, sgg)], source, codes
