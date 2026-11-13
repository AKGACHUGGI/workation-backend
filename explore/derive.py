"""탐색 v6 파생값 (순수 함수). DB 를 모른다. 입력은 explore/sources.py 가 읽어 온 값이다.

식과 기준은 스펙 §4, 실측 재료는 _notes/2026-11-11-explore-graphrag/graph_data/build.py 와 같다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 동점이면 이 순서가 앞선다 (스펙 §4-1 표 순서)
TYPES = ("immersion", "networking", "local", "long_stay")
TYPE_KO = {
    "immersion": "몰입형",
    "networking": "네트워킹형",
    "local": "로컬살이형",
    "long_stay": "장기체류형",
}
EASY_RAW = 80  # 한산한 달 = ease 원값 ≤ 80 (연중 최대 달보다 방문 20% 이상 적은 달)
COAST_KM = 1.0
LOCAL_LIFE_MIN = 0.7
WORK_HOURS = range(9, 18)  # 평일 한산 시각은 9~17시 안에서


@dataclass(frozen=True)
class TypeScore:
    type: str
    score: int
    rank: int
    reason: str


def pct(vals: list[float], v: float) -> float:
    """v 이하인 값의 비율 × 100 (build.py 와 같은 백분위)."""
    return 100 * sum(1 for x in vals if x <= v) / len(vals)


def rank_of(vals: list[float], v: float, desc: bool = True) -> int:
    """나보다 앞선 값 수 + 1. desc=False 면 낮을수록 1위."""
    return 1 + sum(1 for x in vals if (x > v if desc else x < v))


def easy_months(ease_raw: dict[int, float]) -> list[int]:
    return sorted(m for m, raw in ease_raw.items() if raw <= EASY_RAW)


def quiet_months(ease_raw: dict[int, float], n: int = 3) -> list[int]:
    """원값 낮은 n개 달(같으면 이른 달), 달 순으로."""
    return sorted(sorted(ease_raw, key=lambda m: (ease_raw[m], m))[:n])


def busiest_month(ease_raw: dict[int, float]) -> int | None:
    if not ease_raw:
        return None
    return min(ease_raw, key=lambda m: (-ease_raw[m], m))


def type_scores(
    dong_metrics: dict[int, dict[str, float]],
    region_metrics: dict[int, dict],
    morning: dict[int, float],
    region_of: dict[int, int],
) -> dict[int, list[TypeScore]]:
    """동마다 유형 4개 점수·순위·이유 (스펙 §4-1).

    dong_metrics: {dong_id: {"quiet", "peer", "evening": 점수, "local_life": 원값}}
    region_metrics: {region_id: {"overnight": 점수, "ease_raw": {1~12: 원값}}}
    morning: {dong_id: 평일 9~11시 보행 지수 평균 (0~1)}
    """
    mornings = [morning[d] for d in dong_metrics]
    lives = [m["local_life"] for m in dong_metrics.values()]
    out = {}
    for d, m in dong_metrics.items():
        region = region_metrics[region_of[d]]
        calm_am = 100 - pct(mornings, morning[d])
        n_easy = len(easy_months(region["ease_raw"]))
        raw = {
            "immersion": (
                0.6 * m["quiet"] + 0.4 * calm_am,
                f"조용함 {m['quiet']} · 오전 한산 {round(calm_am)}",
            ),
            "networking": (
                0.5 * m["peer"] + 0.5 * m["evening"],
                f"또래 밀도 {m['peer']} · 저녁 활기 {m['evening']}",
            ),
            "local": (pct(lives, m["local_life"]), f"생활권 지수 {m['local_life']:.2f}"),
            "long_stay": (
                0.5 * region["overnight"] + 0.5 * (n_easy / 12 * 100),
                f"숙박 성향 {region['overnight']} · 한산한 달 {n_easy}개"
                "(성수기보다 20% 이상 적은 달)",
            ),
        }
        # round() 는 반올림 짝수: 0.5·39 + 0.5·62 = 50.5 → 50 (실측 주문진읍 네트워킹형과 같음)
        scored = sorted(TYPES, key=lambda t: (-round(raw[t][0]), TYPES.index(t)))
        out[d] = [
            TypeScore(t, round(raw[t][0]), i, raw[t][1]) for i, t in enumerate(scored, start=1)
        ]
    return out


def hour_stat(driv: list[float | None], day_group: str) -> tuple[int | None, int | None]:
    """(가장 붐비는 시각, 가장 한산한 시각). 같은 값이면 이른 시각.
    평일 한산은 9~17시 안에서, 주말은 하루 전체에서 고른다. 값 없는 시각(None)은 건너뛴다."""
    hours = [h for h in range(24) if driv[h] is not None]
    quiet_pool = [h for h in hours if h in WORK_HOURS] if day_group == "weekday" else hours
    peak = min(hours, key=lambda h: (-driv[h], h)) if hours else None
    quiet = min(quiet_pool, key=lambda h: (driv[h], h)) if quiet_pool else None
    return peak, quiet


def is_coastal(km: float | None) -> bool:
    return km is not None and km <= COAST_KM


def life_tag(local_life: float) -> str:
    return "local" if local_life >= LOCAL_LIFE_MIN else "tourist"


def metric_vec(quiet: int, peer: int, evening: int, local_life: float) -> tuple[float, ...]:
    """db/04_dong_feature.sql 의 metric_vec 와 같은 정의."""
    return (quiet / 100, peer / 100, evening / 100, round(local_life, 4))


def similar_dongs(
    vecs: dict[int, tuple[float, ...]], coastal: dict[int, bool], k: int = 3
) -> dict[int, list[tuple[int, float]]]:
    """바닷가 여부가 같은 동 중 L2 가까운 k개 [(other_id, l2)]. 같은 거리면 id 순.

    지금은 전수 비교(203동). 동이 수천을 넘으면 이 함수만 pgvector HNSW 조회로 바꾼다 (스펙 §4-3).
    """
    out = {}
    for d, v in vecs.items():
        pool = [(math.dist(v, vecs[o]), o) for o in vecs if o != d and coastal[o] == coastal[d]]
        out[d] = [(o, dist) for dist, o in sorted(pool)[:k]]
    return out
