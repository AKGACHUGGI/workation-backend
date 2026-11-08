"""지역 상세(GET /regions/{id}), 월별 혼잡(GET /regions/{id}/months), 동네 프로필(GET /dongs/{id}).
명세: workation-docs 명세/api/openapi.yaml. 경로의 id 는 공식 코드(시군구 5자리 · 행정동 10자리)다.
팁 · 투표 · 지원사업 링크는 아직 데이터가 없어 빈 값으로 내려준다. 사진은 regions/photos.py."""

import logging

from django.db import DatabaseError, connection, transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import ParamError, bad_request, not_found, parse_choice, parse_month
from metrics import scoring
from metrics.models import Metric
from regions.models import DayGroup, Dong, DongHourly, Region
from regions.photos import dong_photos, region_photos

logger = logging.getLogger(__name__)

TAG_LABELS = {
    "coast": "바닷가",
    "beach": "해수욕장",
    "island": "섬",
    "surf": "서핑",
    "mountain": "산·숲",
    "city": "도심",
    "cafe": "카페 많음",
    "food": "음식점 많음",
    "library": "도서관",
    "park": "공원",
    "market": "시장",
    "resort": "리조트",
    "long_stay": "장기 숙소",
}


def _score(entry: dict | None) -> dict | None:
    if not entry or entry.get("score") is None:
        return None
    return {"score": entry["score"], "grade": entry["grade"]}


def _when(month) -> str:
    return f"{month}월" if month else "연간"


# ───────────────────────── 지역 상세 ─────────────────────────


@api_view(["GET"])
def region_detail(request, region_id: str):
    try:
        month = parse_month(request)
        preset = parse_choice(request, "preset", scoring.PRESETS, default="default")
    except ParamError as ex:
        return bad_request(str(ex))
    region = Region.objects.filter(sigungu_code=region_id).first()
    if region is None:
        return not_found("시군구를 찾을 수 없습니다.")

    all_scores = scoring.unit_scores("region", month)
    mine = all_scores.get(region.region_id, {})
    values = {rid: scoring.weighted(s, scoring.PRESETS[preset]) for rid, s in all_scores.items()}
    quiet = {rid: s.get("quiet", {}).get("score") for rid, s in all_scores.items()}

    metrics = {k: _score(mine.get(k)) for k in scoring.CORE_KEYS}
    strong = [
        scoring.LABELS[k] + " " + v["grade"] for k, v in metrics.items() if v and v["grade"] == "S"
    ]
    ease_raw = mine.get("ease", {}).get("raw")
    overnight_raw = mine.get("overnight", {}).get("raw")
    gap = mine.get("weekend_gap", {}).get("raw")

    reason_parts = [
        f"{scoring.LABELS[k]} {v['grade']}"
        for k, v in metrics.items()
        if v and v["grade"] in ("S", "A")
    ]
    if overnight_raw is not None:
        reason_parts.append(f"숙박 계수 {overnight_raw:.2f}")
    return Response(
        {
            "regionId": region.sigungu_code,
            "name": region.sigungu_name,
            "sidoName": region.sido_name,
            "tags": sorted(region.tags or []),
            "month": month,
            "preset": preset,
            "score": values.get(region.region_id),
            "presetRank": {"rank": scoring.rank_of(values, region.region_id), "total": len(values)},
            "summary": " · ".join(strong)
            if strong
            else f"{region.sido_name} {region.sigungu_name}",
            "metrics": metrics,
            "stats": {
                "crowdIndex": round(ease_raw) if ease_raw is not None else None,
                "quietRank": scoring.rank_of(quiet, region.region_id),
                "overnightCoef": round(overnight_raw, 2) if overnight_raw is not None else None,
            },
            "weekendGap": _weekend_gap(gap),
            "reason": f"{_when(month)} " + " · ".join(reason_parts) if reason_parts else "",
            "links": [],
            "photos": region_photos(region.region_id, month),
            "tipCount": 0,
            "vote": None,
        }
    )


def _weekend_gap(ratio):
    if ratio is None:
        return None
    weekday, weekend = (round(100 / ratio), 100) if ratio >= 1 else (100, round(100 * ratio))
    pct = round(abs(ratio - 1) * 100)
    if 0.9 <= ratio <= 1.1:
        message = "주중과 주말의 낮 체류인구가 비슷합니다."
    elif ratio > 1:
        message = f"주말에 낮 체류인구가 {pct}% 늘어납니다."
    else:
        message = f"주말에 낮 체류인구가 {pct}% 줄어듭니다."
    return {
        "ratio": round(ratio, 2),
        "weekdayIndex": weekday,
        "weekendIndex": weekend,
        "message": message,
    }


@api_view(["GET"])
def region_months(request, region_id: str):
    region = Region.objects.filter(sigungu_code=region_id).first()
    if region is None:
        return not_found("시군구를 찾을 수 없습니다.")
    rows = {
        m: raw
        for m, raw in Metric.objects.filter(
            scope="region", unit_id=region.region_id, metric_def_id="ease", month__gte=1
        ).values_list("month", "raw_value")
    }
    ordered = sorted(rows, key=lambda m: rows[m])  # 혼잡 낮은 달부터
    best, avoid = sorted(ordered[:3]), sorted(ordered[-3:])
    items = [
        {
            "month": m,
            "crowdIndex": round(rows[m]),
            "status": "good" if m in best else "avoid" if m in avoid else "normal",
        }
        for m in sorted(rows)
    ]
    stay = None
    if ordered:
        m = ordered[0]
        stay = {"month": m, "note": f"연중 가장 한산 · 혼잡 지수 {round(rows[m])}"}
    return Response(
        {
            "regionId": region.sigungu_code,
            "items": items,
            "bestMonths": best,
            "avoidMonths": avoid,
            "stayMonth": stay,
        }
    )


# ───────────────────────── 동네 프로필 ─────────────────────────


def _curves(dong_id: int, month):
    m = month or 0
    rows = DongHourly.objects.filter(dong_id=dong_id, month=m).values_list(
        "day_group", "hour", "index_100"
    )
    curves = {DayGroup.WEEKDAY: [0] * 24, DayGroup.WEEKEND: [0] * 24}
    found = False
    for group, hour, idx in rows:
        curves[group][hour] = idx or 0
        found = True
    return (curves[DayGroup.WEEKDAY], curves[DayGroup.WEEKEND]) if found else (None, None)


def _peak(curve, hours=range(24), highest=True):
    pick = max if highest else min
    hour = pick(hours, key=lambda h: (curve[h], -h if highest else h))
    return {"hour": hour, "index": curve[hour]}


def _similar(dong_id: int, month, my_quiet) -> list[dict]:
    """pgvector 로 지표 벡터가 가까운 동네 5곳.
    벡터 테이블이 없는 환경(테스트 DB 등)에서는 빈 목록."""
    sql = """
        SELECT d.adm_code, d.dong_name, r.sigungu_name,
               (f.metric_vec <-> me.metric_vec)::float AS dist,
               (SELECT score FROM metric WHERE scope = 'dong' AND unit_id = d.dong_id
                  AND metric_key = 'quiet' AND month = %s) AS quiet
          FROM dong_feature f
          JOIN dong d USING (dong_id)
          JOIN region r USING (region_id),
               (SELECT metric_vec FROM dong_feature WHERE dong_id = %s AND month = %s) me
         WHERE f.month = %s AND f.dong_id <> %s AND d.adm_code IS NOT NULL
         ORDER BY f.metric_vec <-> me.metric_vec
         LIMIT 5"""
    m = month or 0
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, [m, dong_id, m, m, dong_id])
            rows = cur.fetchall()
    except DatabaseError:
        logger.warning("similar dongs unavailable (dong_feature 없음?)")
        return []
    return [
        {
            "dongId": code,
            "name": name,
            "regionName": region,
            "distance": round(dist, 3),
            "quieter": my_quiet is not None and quiet is not None and quiet > my_quiet,
        }
        for code, name, region, dist, quiet in rows
    ]


def _personality(metrics: dict, local_life) -> str:
    evening = (metrics.get("evening") or {}).get("grade")
    quiet = (metrics.get("quiet") or {}).get("grade")
    if evening in ("S", "A"):
        return "저녁 상권형. 저녁에 거리를 걷는 사람이 많음."
    if quiet == "C":
        return "낮 유입형. 낮에 외부에서 사람이 몰림."
    if local_life is not None and local_life >= 0.7:
        return "조용한 생활권. 주민 중심."
    return "주민과 방문객이 섞인 동네."


@api_view(["GET"])
def dong_detail(request, dong_id: str):
    try:
        month = parse_month(request)
    except ParamError as ex:
        return bad_request(str(ex))
    dong = Dong.objects.select_related("region").filter(adm_code=dong_id).first()
    if dong is None:
        return not_found("행정동을 찾을 수 없습니다.")
    region = dong.region
    base = {
        "dongId": dong.adm_code,
        "name": dong.dong_name,
        "regionId": region.sigungu_code,
        "regionName": region.sigungu_name,
        "sidoName": region.sido_name,
        "month": month,
        "tags": sorted(dong.tags or []),
        "photos": dong_photos(dong.dong_id, region.region_id, month),
        "tipCount": 0,
        "vote": None,
        "location": {
            "label": f"{dong.dong_name} · {region.sigungu_name}",
            "note": (
                f"해안선까지 {float(dong.coast_dist_km):.1f} km"
                if dong.coast_dist_km is not None
                else "내륙"
            ),
        },
    }
    if dong.is_excluded:
        return Response(
            base
            | {
                "lifeType": "local",
                "summary": "집계 제외 행정동입니다.",
                "hidden": True,
                "metrics": None,
                "curves": None,
                "peaks": None,
                "stats": None,
                "rankInRegion": None,
                "similar": [],
            }
        )

    dong_scores = scoring.unit_scores("dong", month)
    mine = dong_scores.get(dong.dong_id, {})
    local_life = mine.get("local_life", {}).get("raw")
    metrics = {k: _score(mine.get(k)) for k in scoring.DONG_KEYS}
    metrics["local_life"] = round(local_life, 2) if local_life is not None else None
    my_quiet = mine.get("quiet", {}).get("score")

    siblings = Dong.objects.filter(region_id=region.region_id, is_excluded=False).values_list(
        "dong_id", flat=True
    )
    sibling_quiet = {d: dong_scores.get(d, {}).get("quiet", {}).get("score") for d in siblings}
    region_values = scoring.region_values(month, scoring.PRESETS["default"])

    weekday, weekend = _curves(dong.dong_id, month)
    peaks = None
    summary = ""
    if weekday:
        peaks = {
            "weekdayPeak": _peak(weekday),
            "weekendPeak": _peak(weekend),
            "quietest": _peak(weekday, range(9, 19), highest=False),
        }
        kind = "생활권" if (local_life or 0) >= 0.7 else "방문객이 많은"
        summary = f"평일 {peaks['weekdayPeak']['hour']}시에 가장 붐비는 {kind} 동네"
    quiet_raw = mine.get("quiet", {}).get("raw")
    peer_raw = mine.get("peer", {}).get("raw")
    return Response(
        base
        | {
            "lifeType": "local" if (local_life or 0) >= 0.7 else "tourist",
            "summary": summary,
            "comment": _personality(metrics, local_life),
            "hidden": False,
            "metrics": metrics,
            "rankInRegion": scoring.rank_of(sibling_quiet, dong.dong_id),
            "curves": {"weekday": weekday, "weekend": weekend} if weekday else None,
            "peaks": peaks,
            "stats": {
                "quietTopPercent": max(1, 100 - my_quiet) if my_quiet is not None else None,
                "dayInflow": round(quiet_raw, 2) if quiet_raw is not None else None,
                "peerShare": round(peer_raw * 100) if peer_raw is not None else None,
            },
            "region": {
                "score": region_values.get(region.region_id),
                "rank": scoring.rank_of(region_values, region.region_id),
            },
            "similar": _similar(dong.dong_id, month, my_quiet),
        }
    )
