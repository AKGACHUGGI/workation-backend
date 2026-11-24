"""비교 재료 정리 (스펙 §5-2·§5-3). fetch_dong 원재료 여러 개 → 축 값 · 축별 순서 · 공통점 ·
연결 · 닮음, 사용자 기준 → 비교 축. 순수 함수.

축 값은 클수록 그 축이 강하다(바다는 해안선 거리에 − 를 붙이고, 내륙은 INLAND).
LLM 에는 등급과 순서만, 원래 숫자는 근거 줄에만 쓴다.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from explore import derive, queries
from explore.graph import GRADES, METRIC_KO, METRICS

INLAND = -99.0
LINK = "link"  # 최근 본 동과 이어짐. LLM 축이 아니라 규칙 문장 하나
MAX_AXES = 3
GRADE_AXES = frozenset(METRICS)
AXIS_KO = {
    **METRIC_KO,
    "coast": "바다 가까움",
    "life": "생활권 분위기",
    "tourist": "관광지 분위기",
    **{f"type:{t}": derive.TYPE_KO[t] for t in derive.TYPES},
}
# 기준 칩 키 → 축 (스펙 §5-3 칩 목록)
CHIPS = {
    "quiet": "quiet",
    "peer": "peer",
    "evening": "evening",
    "coast": "coast",
    "immersion": "type:immersion",
    "networking": "type:networking",
    "local": "type:local",
}
# 웹 관심 기록(features/interest)의 지표 키 → 축. crowd · overnight 는 동 단위 축이 없다
WEB_METRIC = {"quiet": "quiet", "peer": "peer", "vibe": "evening"}
# 동 2~3개일 때 의도 → 축. compare_summary 는 choose_axes 로 고른다
INTENT_AXES = {
    "quiet": ["quiet"],
    "peer": ["peer"],
    "evening": ["evening"],
    "coast": ["coast"],
    "life": ["life"],
    "type": [f"type:{t}" for t in derive.TYPES],
}
# 닮음 백분위(가까운 쪽 %) 구간
LIKENESS = ((30, "많이 닮은 편"), (70, "보통 정도로 닮은 편"), (100, "꽤 다른 편"))


@dataclass(frozen=True)
class Dong:
    code: str
    name: str
    values: dict[str, float]
    grades: dict[str, str]  # 등급 축만


@dataclass(frozen=True)
class Order:
    axis: str
    ranked: list[tuple[str, float]]  # (동 이름, 값) 높은 순. 같으면 원래 순서(패널 먼저)
    top: str | None  # 단독 1등. 1등이 둘 이상이면 None


@dataclass(frozen=True)
class Link:
    dong: str  # 비교 동 이름
    other: str  # 그 동의 비슷한 동(SIMILAR) 이름
    rank: int


@dataclass(frozen=True)
class Pairs:
    same: list[float]  # 바닷가 여부가 같은 동끼리 L2, 오름차순
    every: list[float]  # 모든 쌍


def dong_of(raw: dict) -> Dong:
    rec = raw["rec"]
    d = rec["dong"]
    values: dict[str, float] = {}
    grades: dict[str, str] = {}
    for metric, grade, score, *_ in rec["grades"]:
        values[metric], grades[metric] = score, grade
    values["coast"] = -rec["near"][0] if rec["near"] else INLAND
    by_name = {name: score for _, name, score, _ in rec["types"]}
    for t in derive.TYPES:
        if derive.TYPE_KO[t] in by_name:
            values[f"type:{t}"] = by_name[derive.TYPE_KO[t]]
    life = raw["facts"][d["code"]]["life"]
    if life and life[0].get("value") is not None:
        values["life"] = life[0]["value"]
    return Dong(d["code"], d["name"], values, grades)


def orders(dongs: list[Dong], axes: list[str]) -> list[Order]:
    """축마다 값 높은 순. 값이 있는 동이 둘 미만인 축은 뺀다."""
    out = []
    for axis in axes:
        have = [(d.name, d.values[axis]) for d in dongs if axis in d.values]
        if len(have) < 2:
            continue
        ranked = sorted(have, key=lambda p: -p[1])
        top = ranked[0][0] if ranked[0][1] > ranked[1][1] else None
        out.append(Order(axis, ranked, top))
    return out


def _head(axis: str) -> str:
    if axis == "coast":
        return "바다 가까운 순"
    if axis == "life":
        return "생활권 성격 강한 순"
    if axis.startswith("type:"):
        return f"{AXIS_KO[axis]}에 잘 맞는 순"
    return f"{AXIS_KO[axis]} 높은 순"


def _tag(axis: str, dong: Dong) -> str:
    if axis in GRADE_AXES:
        return f"({dong.grades[axis]})"
    if axis == "coast":
        return "(내륙)" if dong.values["coast"] <= INLAND else "(바닷가)"
    return ""


def order_line(o: Order, by_name: dict[str, Dong]) -> str:
    """LLM 입력 한 줄: '조용함 높은 순: 효자2동(S) > 여의동(A)'. 같은 값은 '='."""
    text = ""
    for i, (name, value) in enumerate(o.ranked):
        if i:
            text += " = " if value == o.ranked[i - 1][1] else " > "
        text += name + _tag(o.axis, by_name[name])
    return f"{_head(o.axis)}: {text}"


def evidence(o: Order, by_name: dict[str, Dong]) -> str:
    """근거 줄 (스펙 §6-3). 원래 숫자는 여기에만 쓴다."""

    def show(name: str, v: float) -> str:
        if o.axis in GRADE_AXES:
            return f"{name} {by_name[name].grades[o.axis]}({v}점)"
        if o.axis == "coast":
            return f"{name} {'내륙' if v <= INLAND else f'해안선 {-v:.1f}km'}"
        if o.axis == "life":
            return f"{name} {v:.2f}"
        return f"{name} {v}점"

    return f"{AXIS_KO[o.axis]}: " + " · ".join(show(n, v) for n, v in o.ranked)


def common_items(raws: list[dict]) -> list[tuple[str, str]]:
    """모든 동이 함께 가진 사실 [(key, 문구)]. 이어진 동이 적은(드문) 것부터.
    시군구는 뺀다(같은 시군구끼리라 늘 같다)."""
    lists = [queries.fact_list(r["facts"][r["rec"]["dong"]["code"]]) for r in raws]
    keys = set(lists[0]).intersection(*lists[1:])
    keys = {k for k in keys if not k.startswith("region:")}
    first = lists[0]
    return [(k, first[k][0]) for k in sorted(keys, key=lambda k: (first[k][1], k))]


def common(raws: list[dict]) -> list[str]:
    """모든 동이 함께 가진 사실 문구 (LLM 입력용)."""
    return [label for _, label in common_items(raws)]


def links(raws: list[dict], codes: list[str]) -> list[Link]:
    """비교 동의 비슷한 동(SIMILAR) 중 codes 에 있는 것. 비교 동 순서 → 순위 순."""
    out = []
    for r in raws:
        me = r["rec"]["dong"]["code"]
        for rank, code, name, _ in sorted(r["rec"]["similar"]):
            if code in codes and code != me:
                out.append(Link(r["rec"]["dong"]["name"], name, rank))
    return out


def pair_distances(vecs: dict[str, tuple[tuple[float, ...], bool]]) -> Pairs:
    """동 쌍 전체의 metric_vec L2 분포. 203동이면 약 2만 쌍이라 한 번 만들어 캐시한다."""
    codes = sorted(vecs)
    same, every = [], []
    for i, x in enumerate(codes):
        for y in codes[i + 1 :]:
            d = math.dist(vecs[x][0], vecs[y][0])
            every.append(d)
            if vecs[x][1] == vecs[y][1]:
                same.append(d)
    return Pairs(sorted(same), sorted(every))


def likeness(
    a: str, b: str, vecs: dict[str, tuple[tuple[float, ...], bool]], pairs: Pairs
) -> tuple[int, str] | None:
    """(가까운 쪽 %, 구간 말). 바닷가 여부가 같으면 같은 쪽 쌍끼리, 다르면 전체 쌍과 비교."""
    if a not in vecs or b not in vecs:
        return None
    pool = pairs.same if vecs[a][1] == vecs[b][1] else pairs.every
    if not pool:
        return None
    d = math.dist(vecs[a][0], vecs[b][0])
    pct = max(1, math.ceil(bisect.bisect_right(pool, d) / len(pool) * 100))
    return pct, next(word for limit, word in LIKENESS if pct <= limit)


def _weights(d) -> dict[str, float]:
    if not isinstance(d, dict):
        return {}
    return {
        k: float(v)
        for k, v in d.items()
        if isinstance(k, str) and isinstance(v, int | float) and not isinstance(v, bool)
    }


def clean_criteria(raw) -> dict:
    """웹 criteria → {chips, metric, terrain, recent}. 칩 키가 허용 목록 밖이면 ValueError."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("criteria 는 객체")
    chips = raw.get("chips") or []
    if not isinstance(chips, list) or any(c not in CHIPS for c in chips):
        raise ValueError(f"모르는 칩: {chips}")
    interest = raw.get("interest") or {}
    if not isinstance(interest, dict):
        raise ValueError("interest 는 객체")
    recent = interest.get("recent") or []
    return {
        "chips": list(dict.fromkeys(chips)),
        "metric": _weights(interest.get("metric")),
        "terrain": _weights(interest.get("terrain")),
        "recent": [c for c in recent if isinstance(c, str)][:3] if isinstance(recent, list) else [],
    }


def _gap(dongs: list[Dong], metric: str) -> tuple[int, float]:
    have = [d for d in dongs if metric in d.values]
    if len(have) < 2:
        return (-1, -1)
    steps = [GRADES.index(d.grades[metric]) for d in have]
    scores = [d.values[metric] for d in have]
    return (max(steps) - min(steps), max(scores) - min(scores))


def choose_axes(criteria: dict, dongs: list[Dong], has_link: bool) -> list[str]:
    """사용자 기준 → 비교 축 (스펙 §5-3). 칩 > 지표 관심 상위 2 > 바다 관심 > 최근 본 동 연결 >
    등급 차 큰 축. 최대 3, 같은 축은 한 번만, 값이 있는 동이 둘 미만인 축은 뺀다."""
    picked = [CHIPS[k] for k in criteria["chips"]]
    liked = sorted(criteria["metric"].items(), key=lambda kv: -kv[1])
    picked += [WEB_METRIC[k] for k, w in liked if k in WEB_METRIC and w > 0][:2]
    terrain = criteria["terrain"]
    if terrain.get("coast", 0) > 0 and terrain["coast"] >= max(terrain.values()):
        picked.append("coast")
    if has_link:
        picked.append(LINK)
    picked += sorted(METRICS, key=lambda m: _gap(dongs, m), reverse=True)
    out: list[str] = []
    for axis in picked:
        usable = axis == LINK or sum(axis in d.values for d in dongs) >= 2
        if usable and axis not in out:
            out.append(axis)
    return out[:MAX_AXES]
