"""시군구 채팅 답 (시군구 채팅 스펙 §6). fetch_region 원재료 → 조건 축 → 후보 동 최대 3곳 →
축별 순서(코드) → compare.compose(생성 · 검사 · 규칙 문장) → 근거 줄은 시군구 순위로.

후보끼리의 순서만 LLM 에 넣고, 원래 숫자 · 시군구 순위는 근거 줄에만 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from django.core.cache import cache

from explore import derive
from explore.brief import josa
from explore.chat import arrange, compare
from explore.chat.arrange import AXIS_KO, GRADE_AXES, INLAND, Order

MAX_SPOTS = 3
# 시군구 답 전용 검사 · 프롬프트를 고치면 올린다 (캐시 키에 compare.PROMPT_VERSION 과 함께 들어간다)
PROMPT_VERSION = "r5"
SUPERLATIVE = ("가장", "제일", "1위", "최고")
NAMED_MAX = 3  # 성격 답에서 1위 유형 동을 이름으로 부르는 최대 수 (넘으면 개수로)
TYPE_AXES = [f"type:{t}" for t in derive.TYPES]
TYPE_BY_KO = {ko: key for key, ko in derive.TYPE_KO.items()}
PLAIN = compare.PLAIN  # 유형 이름 대신 풀어 쓴 말
STRENGTHS = ("quiet", "evening", "peer", "coast")  # 조건 외 강점 축 (문장 2·3번째)
OPPOSITE = {"quiet": "evening", "evening": "quiet"}  # 조건과 반대 방향이라 부가 문장으로 안 쓰는 축
# 생활권 · 관광지는 동의 성격이다. 시설 · 개수로 넓힌 말은 근거가 없다
WIDE = ("관광지가 많", "관광지 많", "명소", "갖춰", "잘 갖춘", "시설", "중심")
# 축마다 그 축을 말하는 낱말. 한 문장에 다른 축 낱말이 나오면 섞인 것이다.
# 생활권과 로컬살이는 같은 '현지' 이야기라 한 묶음으로 본다
AXIS_WORDS = {
    "quiet": ("조용", "한적", "한산"),
    "evening": ("저녁", "밤", "활기", "북적"),
    "peer": ("또래", "젊은", "나이대"),
    "coast": ("바다", "바닷가", "해변", "해안"),
    "local": ("생활권", "현지", "주민", "로컬"),
    "tourist": ("관광", "여행"),
    "type:immersion": ("혼자", "집중"),
    "type:networking": ("교류", "사람들과"),
}
WORD_GROUP = {"life": "local", "type:local": "local"}
# 질문 속 조건 낱말 → 축. 위에서부터가 아니라 질문에 나온 순서로 고른다
KEYWORDS = (
    ("quiet", ("조용", "한적", "한산")),
    ("peer", ("또래", "젊은", "2030")),
    ("evening", ("저녁", "밤", "활기")),
    ("coast", ("바다", "바닷가", "해변", "해안")),
    ("life", ("생활권", "주민")),
    ("tourist", ("관광",)),
    ("type:immersion", ("몰입", "집중", "혼자")),
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


def sgg_rank(spot: Spot, axis: str, spots: list[Spot]) -> tuple[int, int]:
    """(시군구 순위, 동 수). 등급 축은 원재료 순위, 나머지는 축 값으로 센다(같으면 같은 순위)."""
    if axis in spot.ranks:
        return spot.ranks[axis]
    v = spot.dong.values[axis]
    have = [s.dong.values[axis] for s in spots if axis in s.dong.values]
    return 1 + sum(x > v for x in have), len(have)


def _strengths(cands: list[Spot], conditions: list[str], spots: list[Spot]) -> list[str]:
    """후보끼리 단독 1위가 조건 1위가 아닌 다른 동인 강점 축, 동마다 하나씩 최대 2개.
    조건과 반대 방향인 축, 그 1위가 시군구 상위 절반이 아닌 축은 뺀다
    (하위권 동을 '조용한 곳을 찾는다면 ○○' 처럼 단정하지 않게)."""
    used = {cands[0].dong.name}
    by_name = {s.dong.name: s for s in cands}
    opposite = {OPPOSITE.get(c) for c in conditions}
    out: list[str] = []
    dongs = [s.dong for s in cands]
    for axis in STRENGTHS:
        if axis in conditions or axis in opposite:
            continue
        if len(out) == arrange.MAX_AXES - len(conditions):
            break
        order = next(iter(arrange.orders(dongs, [axis])), None)
        if not order or not order.top or order.top in used:
            continue
        rank, of = sgg_rank(by_name[order.top], axis, spots)
        if rank * 2 <= of:
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
        return Plan(conditions, [*conditions, *_strengths(cands, conditions, spots)], cands)
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


SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 찾기 안내자다. 사용자가 준 축별 순서만 근거로 쓴다.",
        "- 문장마다 상황 하나와 그 상황에 맞는 동네 하나를 고른다. 한 문장에 동네 이름은 하나만.",
        "- 축마다 이런 문형으로 쓰고, 문장끼리 끝맺음은 서로 다르게 한다. "
        "그대로 베끼지 말고 상황에 맞게 바꿔 쓴다.",
        "  quiet: '조용히 일하고 싶다면 ○○이 맞아요'",
        "  evening: '저녁에 활기를 찾는다면 ○○ 쪽이 나아요'",
        "  peer: '또래가 많은 곳을 찾는다면 ○○이 좋아요', "
        "'비슷한 나이대가 많은 곳을 원한다면 ○○이 맞아요'",
        "  coast: '바다 가까이 머물고 싶다면 ○○이 좋아요'",
        "  life: '현지 생활처럼 지내고 싶다면 ○○이 맞아요'",
        "  tourist: '여행하듯 지내고 싶다면 ○○이 맞아요'",
        "  유형: '혼자 집중해서 일하고 싶다면', '사람들과 어울리며 일하고 싶다면', "
        "'현지 주민처럼 지내고 싶다면'",
        "- 생활권 · 관광지는 그 동네의 성격이다. "
        "'관광지가 많은', '생활권이 잘 갖춰진'처럼 시설 · 개수로 넓히지 않는다.",
        "- 한 문장엔 그 축 이야기만 쓴다. 바다 축 문장에 '조용하면서'처럼 다른 축을 섞지 않는다.",
        *compare.TONE_RULES,
        "- 시군구 이름은 쓰지 않는다. '○○군의 ○○읍'처럼 붙이지 않는다.",
        "- '반면', '대신', '하지만'으로 문장을 시작하지 않는다.",
        "- '가장', '제일', '최고', '1위' 같은 최상급을 쓰지 않는다.",
        "- '몰입형', '네트워킹형', '로컬살이형' 같은 유형 이름을 쓰지 않고 "
        "'혼자 집중해서 일하기'처럼 풀어 쓴다.",
        "- 존댓말 해요체, 문장마다 60자 이내.",
        "- 문장마다 다른 axis 를 쓰고, 그 축 순서의 맨 앞 동네 이름 하나만 쓴다. "
        "맨 앞이 '='로 같으면 그중 하나를 쓴다.",
        "- 동네 이름에서 받는 인상(읍·면은 시골이라 조용하다 등)이나 상식으로 판단하지 않는다.",
        "- 숫자와 등급 글자(S·A·B·C)를 쓰지 않는다.",
        "- '지표', '벡터', '거리', '백분위', '노드', '그래프', '데이터' 같은 말을 쓰지 않는다.",
    ]
)


def _ko(axis: str) -> str:
    return PLAIN.get(axis) or AXIS_KO[axis]


def build_prompt(sgg: str, p: Plan, orders: list[Order]) -> str:
    """사용자 문장 없이 조건 + 후보끼리의 축별 순서만. 숫자는 넣지 않는다.
    유형 축은 유형 이름 대신 풀어 쓴 말로 넣는다."""
    by_name = {s.dong.name: s.dong for s in p.spots}
    cond = "·".join(_ko(a) for a in p.conditions)

    def line(o: Order) -> str:
        text = arrange.order_line(o, by_name)
        return text.replace(AXIS_KO[o.axis], PLAIN[o.axis]) if o.axis in PLAIN else text

    return "\n".join(
        [
            f"질문: {cond} 좋은 동네 어디야? ({sgg} 안에서 동네 찾기)",
            f"찾은 동네: {', '.join(by_name)} ({sgg}에서 {cond} 기준으로 고른 곳)",
            f"이번에 볼 기준: {', '.join(_ko(o.axis) for o in orders)}",
            "",
            "[축별 순서 · 코드 계산]",
            *(f"{o.axis}: {line(o)}" for o in orders),
        ]
    )


def rule_sentence(o: Order) -> str:
    """규칙 문장. 1등이 동점이면 동점인 동을 모두 쓴다."""
    return compare.want(o.axis, "·".join(compare.tied_top(o)))


def _none(p: Plan, spots: list[Spot], sgg: str) -> tuple[list[dict], list[str]]:
    """(문장들, 문장에 쓴 동 코드)."""
    if p.conditions == TYPE_AXES:
        return [_rule(f"{sgg}에는 유형이 뚜렷한 동네가 없어요.")], []
    if len(p.conditions) == 1:
        what = f"{compare.LINK[p.conditions[0]][2]} 동네"
        return [_rule(f"{sgg}에는 {josa(what, '이/가')} 없어요.")], []
    out = [_rule(f"{compare.both_phrase(*p.conditions)} 곳은 없어요.")]
    codes: list[str] = []
    for axis in p.conditions:
        top = ranked(spots, axis)[:1]
        if top:
            out.append(_rule(compare.want(axis, top[0].dong.name), evidence(axis, top, sgg)))
            codes.append(top[0].dong.code)
    return out, list(dict.fromkeys(codes))


def _one(p: Plan, sgg: str) -> dict:
    s = p.spots[0]
    if p.conditions == TYPE_AXES:  # 후보가 1위인 유형 축 (plan 이 그런 축만 남긴다)
        axis = p.axes[0]
        return _rule(compare.want(axis, s.dong.name), evidence(axis, p.spots, sgg))
    ev = evidence(p.conditions[0], p.spots, sgg)
    if len(p.conditions) == 1:
        return _rule(compare.want(p.conditions[0], s.dong.name), ev)
    what = compare.both_phrase(*p.conditions)
    return _rule(f"{what} 곳을 원한다면 {josa(s.dong.name, '이/가')} 맞아요.", ev)


def profile(raw: dict, spots: list[Spot]) -> tuple[list[dict], list[str]]:
    """시군구 성격 (규칙 문장 2개, 이름을 쓴 동 코드). 수는 근거 줄에."""
    sgg = raw["region"]["name"]
    tops: dict[str, list[tuple[int, str, str]]] = {}  # 1위 유형 → [(점수, 동 이름, 코드)]
    for d in raw["dongs"]:
        first = [(name, score) for rank, name, score in d["types"] if rank == 1]
        if first and first[0][1] >= derive.TYPE_MIN:
            tops.setdefault(first[0][0], []).append((first[0][1], d["name"], d["code"]))
    local = sum(s.life == "생활권" for s in spots)
    tourist = sum(s.life == "관광지" for s in spots)
    coastal = sum(s.dong.values["coast"] > INLAND for s in spots)
    out, codes = [], []
    if tops:  # 1위 유형 동이 몇 곳 안 되면 이름으로, 많으면 전체 중 몇 곳인지로. 동점이면 이어 쓴다
        top = max(len(v) for v in tops.values())
        parts = []
        for name, found in tops.items():
            if len(found) != top:
                continue
            what = f"{PLAIN[f'type:{TYPE_BY_KO[name]}']} 좋"
            if top > NAMED_MAX:
                parts.append((f"{top}곳이", what))
                continue
            found.sort(key=lambda f: -f[0])
            parts.append((josa("·".join(n for _, n, _ in found), "이/가"), what))
            codes += [c for _, _, c in found]
        head = f"{sgg} 동네 {len(raw['dongs'])}곳 중" if top > NAMED_MAX else f"{sgg}에서는"
        body = ", ".join(f"{who} {what}고" for who, what in parts[:-1])
        last = f"{parts[-1][0]} {parts[-1][1]}아요."
        out.append(f"{head} {body + ', ' if body else ''}{last}")
    if local == tourist:
        mood = "생활권과 관광지 동네가 비슷하게 있고"
    else:
        mood = f"{'생활권' if local > tourist else '관광지'} 성격의 동네가 더 많고"
    sea = f"바다 가까운 동네는 {coastal}곳이에요." if coastal else "바다와 닿은 동네는 없어요."
    out.append(f"{mood}, {sea}")
    counts = [f"{name} {len(v)}곳" for name, v in sorted(tops.items(), key=lambda t: -len(t[1]))]
    ev = " · ".join([*counts, f"생활권 {local}곳", f"관광지 {tourist}곳"])
    return [_rule(out[0], ev), *(_rule(t) for t in out[1:])], codes


def problems(text: str, sgg: str, cands: list[str], every: list[str]) -> list[str]:
    """시군구 답 생성 문장 추가 검사 (compare.problems 다음, 말투는 거기서 본다).
    후보끼리만 견준 순서라 최상급 같은 범위 주장과 후보 아닌 동 이름은 근거가 없다.
    시군구 이름 반복 · 유형 이름 · 생활권 · 관광지를 시설 · 개수로 넓힌 말도 본다."""
    out = []
    others = [n for n in dict.fromkeys(compare.names_in(text, every)) if n not in cands]
    if others:
        out.append(f"이름: 후보 아닌 동 {others}")
    plain = text
    for name in sorted(every, key=len, reverse=True):
        plain = plain.replace(name, "\0")  # '고성읍' 속 '고성' 을 시군구 이름으로 잡지 않게
    stem = sgg[:-1] if len(sgg) >= 3 else sgg
    if stem in plain:
        out.append(f"시군구 이름: {stem}")
    if any(w in text for w in SUPERLATIVE):
        out.append("최상급")
    types = [ko for ko in derive.TYPE_KO.values() if ko in text]
    if types:
        out.append(f"유형 이름: {types}")
    wide = [w for w in WIDE if w in plain]
    if wide:
        out.append(f"넓힘: {wide}")
    return out


def mixed(text: str, axis: str, names: tuple[str, ...] | list[str] = ()) -> list[str]:
    """한 축 문장에 다른 축 낱말이 섞였는지. 동 이름 속 낱말('해안면')은 보지 않는다."""
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, "\0")
    own = WORD_GROUP.get(axis, axis)
    hit = [w for g, words in AXIS_WORDS.items() if g != own for w in words if w in text]
    return [f"섞임: {hit}"] if hit else []


def answer(
    generate: compare.Generate | None, raw: dict, intent: str, question: str
) -> tuple[list[dict], str, list[str]]:
    """(문장들, source, 후보 동 코드). 생성 답은 compare 와 같은 키 방식으로 1일 캐시한다."""
    spots = spots_of(raw)
    sgg = raw["region"]["name"]
    if intent == "region_profile":
        out, codes = profile(raw, spots)
        return out, "rule", codes
    p = plan(spots, condition_axes(question, intent))
    if not p.spots:
        out, codes = _none(p, spots, sgg)
        return out, "rule", codes
    codes = [s.dong.code for s in p.spots]
    if len(p.spots) == 1:
        return [_one(p, sgg)], "rule", codes
    dongs = [s.dong for s in p.spots]
    orders = arrange.orders(dongs, p.axes)
    # 후보 전원이 동점인 축(해안선 0.0km 셋 등)은 고를 동이 없어 LLM 에 넘기지 않고 규칙 문장
    asked = [o for o in orders if len(compare.tied_top(o)) < len(o.ranked)]
    user = build_prompt(sgg, p, asked)
    key = f"{compare.cache_key(codes, intent, p.axes, user)}:{PROMPT_VERSION}"
    hit = cache.get(key)
    if hit is None:
        cands = [d.name for d in dongs]
        every = [d["name"] for d in raw["dongs"]]

        def check(s: dict) -> list[str]:
            return [*problems(s["text"], sgg, cands, every), *mixed(s["text"], s["axis"], every)]

        hit = compare.compose(
            generate, user, dongs, asked, check, system=SYSTEM, rule=rule_sentence, ties_ok=True
        )
        if hit[1] != "rule":  # HCX 가 잠깐 안 될 때의 규칙 답은 1일 남기지 않는다
            cache.set(key, hit, compare.CACHE_TTL)
    made = dict(zip((o.axis for o in asked), hit[0], strict=True))
    sentences = []
    for o in orders:
        s = made.get(o.axis) or _rule(rule_sentence(o))
        head = next((c for c in compare.CONTRAST if s["text"].startswith(c)), None)
        text = s["text"][len(head) :] if head else s["text"]  # 시군구 답 문장끼리는 대비가 아니다
        sentences.append({**s, "text": text, "evidence": evidence(o.axis, p.spots, sgg)})
    passed = sum(s["source"] == "llm" for s in sentences)
    source = "llm" if passed == len(sentences) else "rule" if passed == 0 else "mixed"
    return sentences or [_one(p, sgg)], source, codes
