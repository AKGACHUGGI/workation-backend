"""웹 화면 계약(workation-web src/features/*/types.ts)에 맞춰 DB 값을 모으는 함수들.

웹 계약과 DB 의 이름이 다르다:
- 지표 키: 웹 quiet · crowd · peer · overnight · vibe ↔ DB quiet · ease · peer · overnight · evening
- 월: 웹 '2025-10' · 'annual' ↔ DB 10 · 0(연간)
- 시군구 id = sigungu_code(5자리), 행정동 id = adm_code(10자리)
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from statistics import mean

from django.db import DatabaseError, connection, transaction

from regions.models import Dong, Region

logger = logging.getLogger(__name__)

DATA_YEAR = 2025
WEB_TO_DB = {
    "quiet": "quiet",
    "crowd": "ease",
    "peer": "peer",
    "overnight": "overnight",
    "vibe": "evening",
}
TERRAIN_TAGS = {"coast": "coast", "mountain": "mountain", "city": "city"}
TAG_KO = {
    "coast": "해안",
    "mountain": "산간",
    "city": "도시",
    "beach": "해수욕장",
    "island": "섬",
    "surf": "서핑",
    "cafe": "카페 많음",
    "food": "음식점 많음",
    "library": "도서관",
    "park": "공원",
    "market": "시장",
    "resort": "리조트",
    "long_stay": "장기 숙소",
}
TAG_ORDER = list(TAG_KO)


class BadMonth(ValueError):
    pass


def parse_month(raw: str | None) -> tuple[int | None, str]:
    """'2025-10' → (10, '2025-10'), 'annual' 또는 없음 → (None, 'annual')"""
    if raw in (None, "", "annual"):
        return None, "annual"
    m = re.fullmatch(rf"{DATA_YEAR}-(0[1-9]|1[0-2])", raw)
    if not m:
        raise BadMonth("month 는 'annual' 또는 '2025-01' ~ '2025-12' 입니다.")
    return int(m.group(1)), raw


def month_param(m: int | None) -> str:
    return f"{DATA_YEAR}-{m:02d}" if m else "annual"


def web_scores(scores: dict) -> dict:
    """DB 지표 묶음 → 웹 MetricScores (없으면 0)."""
    return {web: (scores.get(db, {}).get("score") or 0) for web, db in WEB_TO_DB.items()}


def terrain_of(tags) -> list[str]:
    return [t for t in ("coast", "mountain", "city") if t in (tags or [])]


def tag_labels(tags, limit: int | None = None) -> list[str]:
    out = [TAG_KO[t] for t in TAG_ORDER if t in (tags or [])]
    return out[:limit] if limit else out


def regions_by_id() -> dict[int, Region]:
    return {r.region_id: r for r in Region.objects.all()}


def one_photo_per_unit(scope: str, unit_ids: list[int], month: int | None) -> dict[int, dict]:
    """단위마다 대표 사진 1장 (같은 달 우선, 같은 순위 안에서 랜덤). {unit_id: {url, credit}}"""
    if not unit_ids:
        return {}
    sql = """
        SELECT DISTINCT ON (unit_id) unit_id, url, credit FROM photo
         WHERE status = 'approved' AND scope = %(scope)s AND unit_id = ANY(%(ids)s)
         ORDER BY unit_id,
                  CASE WHEN %(m)s::int IS NULL OR month = %(m)s THEN 0
                       WHEN least(abs(month - %(m)s), 12 - abs(month - %(m)s)) <= 1 THEN 1
                       WHEN month IS NULL THEN 2 ELSE 3 END,
                  random()"""
    with connection.cursor() as cur:
        cur.execute(sql, {"scope": scope, "ids": unit_ids, "m": month})
        return {u: {"url": url, "credit": credit} for u, url, credit in cur.fetchall()}


def monthly_crowd(region_id: int) -> dict[int, float]:
    """시군구 월 혼잡 지수 (ease 원값, 연중 최대 월 = 100)."""
    from metrics.models import Metric

    return dict(
        Metric.objects.filter(
            scope="region", unit_id=region_id, metric_def_id="ease", month__gte=1
        ).values_list("month", "raw_value")
    )


def season_type(crowd: dict[int, float]) -> str:
    """월별 혼잡으로 계절 유형을 고른다 (NMF 계절 유형의 근사)."""
    if len(crowd) < 12:
        return "flat"
    rest = lambda ms: mean(v for k, v in crowd.items() if k not in ms)  # noqa: E731
    peak = max(crowd, key=crowd.get)
    if peak in (7, 8) and mean(crowd[m] for m in (7, 8)) > 1.25 * rest((7, 8)):
        return "summer_beach"
    sa = (4, 5, 9, 10, 11)
    if peak in sa and mean(crowd[m] for m in sa) > 1.1 * rest(sa):
        return "spring_autumn"
    if mean(crowd[m] for m in range(7, 13)) > 1.15 * mean(crowd[m] for m in range(1, 7)):
        return "second_half_rise"
    return "flat"


def dong_visits(dong_ids: list[int]) -> dict[int, dict[int, float]]:
    """{dong_id: {month: 방문인구}} (dong_month_summary.vist_total)."""
    out: dict[int, dict[int, float]] = defaultdict(dict)
    if not dong_ids:
        return out
    with connection.cursor() as cur:
        cur.execute(
            "SELECT dong_id, month, vist_total FROM dong_month_summary WHERE dong_id = ANY(%s)",
            [dong_ids],
        )
        for d, m, v in cur.fetchall():
            out[d][m] = v or 0.0
    return out


def dong_bbox(dong_id: int) -> list[float] | None:
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "SELECT ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom) FROM dong"
                " WHERE dong_id = %s AND geom IS NOT NULL",
                [dong_id],
            )
            row = cur.fetchone()
    except DatabaseError:  # 테스트 DB 에는 PostGIS · geom 이 없다
        return None
    return [round(float(v), 5) for v in row] if row else None


def similar_dongs(dong_id: int, month: int | None, limit: int = 12) -> list[tuple]:
    """pgvector 지표 벡터가 가까운 동 [(dong_id, adm_code, name, region_id, dist)]. 없으면 []."""
    m = month or 0
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                """SELECT d.dong_id, d.adm_code, d.dong_name, d.region_id,
                          (f.metric_vec <-> me.metric_vec)::float
                     FROM dong_feature f JOIN dong d USING (dong_id),
                          (SELECT metric_vec FROM dong_feature WHERE dong_id = %s AND month = %s) me
                    WHERE f.month = %s AND f.dong_id <> %s AND d.adm_code IS NOT NULL
                    ORDER BY f.metric_vec <-> me.metric_vec LIMIT %s""",
                [dong_id, m, m, dong_id, limit],
            )
            return cur.fetchall()
    except DatabaseError:
        logger.warning("dong_feature 없음: 비슷한 동네를 비운다")
        return []


def activity_mix(
    weekday: list[int], weekend: list[int], weekend_gap: float | None
) -> dict[str, int]:
    """24시간 곡선의 시간대 비중으로 만든 활동 유형 근사 (NMF 활동 유형의 대체, 합 100).
    곡선은 주중 · 주말을 각각 최대 = 100 으로 맞춘 값이라 규모를 비교할 수 없어서, 주말 관광형은
    시군구의 주말 ÷ 주중 낮 체류(weekend_gap)가 클수록 커지게 한다."""
    band = lambda c, hs: sum(c[h] for h in hs)  # noqa: E731
    boost = min(max((weekend_gap or 1.0) - 0.85, 0.0), 0.6) * 2
    raw = {
        "eveningDowntown": band(weekday, range(18, 23)) / 5,
        "weekendTourist": band(weekend, range(10, 18)) / 8 * boost,
        "weekdayWork": band(weekday, range(9, 18)) / 9,
        "dawnResidential": band(weekday, range(0, 6)) / 6,
    }
    total = sum(raw.values()) or 1
    out = {k: round(v / total * 100) for k, v in raw.items()}
    top = max(out, key=out.get)
    out[top] += 100 - sum(out.values())
    return out


def excluded_dongs(region_id: int) -> list[dict]:
    return [
        {"name": d.dong_name, "reason": "special_facility"}
        for d in Dong.objects.filter(region_id=region_id, is_excluded=True)
    ]
