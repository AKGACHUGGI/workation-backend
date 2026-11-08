"""지도 색칠 점수(GET /map/scores)와 티어리스트(GET /tiers).
명세: workation-docs 명세/api/openapi.yaml"""

from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import ParamError, bad_request, parse_choice, parse_month
from metrics import scoring
from regions.models import Dong, Region
from regions.photos import first_photo

METRIC_CHOICES = ("value", *scoring.CORE_KEYS)


def _weights(request, metric: str):
    """(가중치, preset, 직접 설정 지표). metric 이 value 일 때만.
    metrics(직접 설정)가 있으면 preset 보다 우선한다."""
    custom = request.query_params.get("metrics")
    if metric != "value":
        return None, None, None
    if custom:
        keys = [k.strip() for k in custom.split(",") if k.strip()]
        if not keys or any(k not in scoring.CORE_KEYS for k in keys):
            raise ParamError(f"metrics 는 {', '.join(scoring.CORE_KEYS)} 중에서 고릅니다.")
        weights = {k: (1 if k in keys else 0) for k in scoring.CORE_KEYS}
        return weights, None, keys
    preset = parse_choice(request, "preset", scoring.PRESETS, default="default")
    return scoring.PRESETS[preset], preset, None


def _units(level: str, month, metric: str, weights):
    """단위 목록 · 점수 · 등급 · 지표별 점수. 행정동은 집계 제외 · 코드 없는 곳을 뺀다."""
    region_scores = scoring.unit_scores("region", month)
    regions = {r.region_id: r for r in Region.objects.all()}
    if level == "region":
        units = [(rid, r.sigungu_code, r.sigungu_name, r.sido_name) for rid, r in regions.items()]
        scores = region_scores
    else:
        dongs = list(Dong.objects.filter(is_excluded=False, adm_code__isnull=False))
        units = [
            (d.dong_id, d.adm_code, d.dong_name, regions[d.region_id].sigungu_name) for d in dongs
        ]
        scores = scoring.with_region_metrics(
            scoring.unit_scores("dong", month),
            region_scores,
            {d.dong_id: d.region_id for d in dongs},
        )
    if metric == "value":
        values = {uid: scoring.weighted(scores.get(uid, {}), weights) for uid, *_ in units}
        grades = scoring.quartile_grades(values)
    else:
        values = {uid: scores.get(uid, {}).get(metric, {}).get("score") for uid, *_ in units}
        grades = {uid: scores.get(uid, {}).get(metric, {}).get("grade") for uid, *_ in units}
    return units, values, grades, scores


@api_view(["GET"])
def map_scores(request):
    try:
        level = parse_choice(request, "level", ("region", "dong"), required=True)
        metric = parse_choice(request, "metric", METRIC_CHOICES, required=True)
        month = parse_month(request)
        weights, _, _ = _weights(request, metric)
    except ParamError as ex:
        return bad_request(str(ex))
    units, values, grades, _ = _units(level, month, metric, weights)
    items = [
        {"id": code, "name": name, "score": values[uid], "grade": grades[uid]}
        for uid, code, name, _ in sorted(units, key=lambda u: u[0])
    ]
    return Response({"level": level, "metric": metric, "month": month, "items": items})


@api_view(["GET"])
def tiers(request):
    try:
        metric = parse_choice(request, "metric", METRIC_CHOICES, required=True)
        scope = parse_choice(request, "scope", ("region", "dong"), default="region")
        month = parse_month(request)
        weights, preset, keys = _weights(request, metric)
    except ParamError as ex:
        return bad_request(str(ex))
    if scope == "dong" and (metric in scoring.REGION_ONLY or metric == "value"):
        return bad_request("시기 여유 · 숙박 성향 · 종합 점수는 시군구(scope=region)만 지원합니다.")
    units, values, grades, scores = _units(scope, month, metric, weights)

    if metric == "value":
        desc = (
            "직접 설정 종합 점수 (" + " · ".join(scoring.SHORT[k] for k in keys) + ")"
            if keys
            else f"{scoring.PRESET_LABELS[preset]} 프리셋 종합 점수"
        )
    else:
        desc = scoring.LABELS[metric]
    groups = {g: [] for g in ("S", "A", "B", "C")}
    for uid, code, name, parent in units:
        if values[uid] is None or grades[uid] is None:
            continue
        item = {
            "id": code,
            "name": name,
            "parentName": parent,
            "score": values[uid],
            "photo": first_photo(uid, month) if scope == "region" else None,
        }
        if metric == "value":
            item["grades"] = {
                k: scores.get(uid, {}).get(k, {}).get("grade") for k in scoring.CORE_KEYS
            }
        groups[grades[uid]].append(item)
    tiers_out = [
        {
            "grade": g,
            "label": scoring.GRADE_LABELS[g],
            "description": desc,
            "count": len(items),
            "items": sorted(items, key=lambda x: -x["score"]),
        }
        for g, items in groups.items()
    ]
    return Response(
        {
            "metric": metric,
            "preset": preset,
            "metrics": keys,
            "month": month,
            "scope": scope,
            "tiers": tiers_out,
        }
    )
