"""지표 점수 묶기와 종합 점수(워케이션 밸류) 계산.

- 점수·등급은 db/03_metrics.sql 이 metric 테이블에 넣은 값을 그대로 쓴다.
- 종합 점수는 프리셋 가중 평균이라 저장하지 않고 여기서 계산한다 (명세 v0.3).
- month 는 None 이면 연간(0). 숙박 성향(overnight)은 연간 한 값만 있다.
- 시기 여유(ease) · 숙박 성향(overnight)은 시군구 지표라, 행정동 종합 점수는 소속 시군구 값을 쓴다.
"""

from __future__ import annotations

from collections import defaultdict

from metrics.models import Metric

CORE_KEYS = ("quiet", "ease", "peer", "overnight", "evening")
DONG_KEYS = ("quiet", "peer", "evening")
REGION_ONLY = ("ease", "overnight")
LABELS = {
    "quiet": "동네 조용함",
    "ease": "시기 여유",
    "peer": "또래 밀도",
    "overnight": "숙박 성향",
    "evening": "저녁 활기",
}
SHORT = {"quiet": "조용", "ease": "시기", "peer": "또래", "overnight": "숙박", "evening": "활기"}
# 가중치 순서: 조용함 · 시기 여유 · 또래 · 숙박 · 저녁 활기
PRESETS = {
    "default": {"quiet": 1, "ease": 1, "peer": 1, "overnight": 1, "evening": 1},
    "quiet": {"quiet": 3, "ease": 3, "peer": 1, "overnight": 1, "evening": 0},
    "lively": {"quiet": 0, "ease": 1, "peer": 3, "overnight": 2, "evening": 3},
}
PRESET_LABELS = {"default": "기본", "quiet": "조용한 곳", "lively": "활기 있는 한 달"}
GRADE_LABELS = {"S": "상위 25%", "A": "25~50%", "B": "50~75%", "C": "하위 25%"}


def unit_scores(scope: str, month: int | None) -> dict[int, dict[str, dict]]:
    """{unit_id: {metric_key: {"score", "grade", "raw"}}}. 그 달 값, overnight 는 연간 값."""
    m = month or 0
    rows = Metric.objects.filter(scope=scope, month__in={m, 0}).values_list(
        "unit_id", "metric_def_id", "month", "raw_value", "score", "grade"
    )
    out: dict[int, dict[str, dict]] = defaultdict(dict)
    for unit_id, key, mon, raw, score, grade in rows:
        if mon == m or (key == "overnight" and mon == 0):
            out[unit_id][key] = {"score": score, "grade": grade, "raw": raw}
    return out


def weighted(scores: dict[str, dict], weights: dict[str, float]) -> int | None:
    """가중치가 0 보다 큰 지표 중 점수가 있는 것만 가중 평균. 하나도 없으면 None."""
    num = den = 0.0
    for key, w in weights.items():
        s = scores.get(key, {}).get("score")
        if w > 0 and s is not None:
            num += w * s
            den += w
    return round(num / den) if den else None


def quartile_grades(values: dict) -> dict:
    """{id: 점수} → {id: 등급}. 나보다 점수가 높은 비율로 사분위 (동점은 같은 등급)."""
    present = [v for v in values.values() if v is not None]
    n = len(present)
    out = {}
    for key, v in values.items():
        if v is None or n == 0:
            out[key] = None
            continue
        higher = sum(1 for x in present if x > v) / n
        out[key] = "S" if higher < 0.25 else "A" if higher < 0.5 else "B" if higher < 0.75 else "C"
    return out


def rank_of(values: dict, key) -> int | None:
    """점수 내림차순 순위 (동점은 같은 순위)."""
    v = values.get(key)
    if v is None:
        return None
    return 1 + sum(1 for x in values.values() if x is not None and x > v)


def region_values(month: int | None, weights: dict[str, float]) -> dict[int, int | None]:
    """시군구 12곳의 종합 점수."""
    return {rid: weighted(s, weights) for rid, s in unit_scores("region", month).items()}


def with_region_metrics(dong_scores: dict, region_scores: dict, dong_region: dict) -> dict:
    """행정동 점수에 소속 시군구의 ease · overnight 를 붙인다 (행정동 종합 점수용)."""
    out = {}
    for dong_id, scores in dong_scores.items():
        merged = dict(scores)
        parent = region_scores.get(dong_region.get(dong_id), {})
        for key in REGION_ONLY:
            if key in parent:
                merged[key] = parent[key]
        out[dong_id] = merged
    return out
