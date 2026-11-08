"""웹 화면 계약 API (/api/...). 계약은 workation-web src/features/*/types.ts.

화면 데이터는 /api/v1 과 같은 DB · 같은 점수 계산(metrics/scoring.py)을 쓰고
모양만 웹 계약에 맞춘다.
아직 데이터가 없는 것(랜드마크 · 접근 안내 · 팁 · 투표 · 즐겨찾기 · 조회수 · 로그인)은 빈 값이다.
NMF 계절 · 활동 유형과 저녁 잔차는 DB 에 없어 월별 혼잡과 24시간 곡선으로 만든 근사값이다.
"""

from __future__ import annotations

import re
from datetime import date
from statistics import mean

from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import bad_request, error, not_found
from metrics import scoring
from regions.models import DayGroup, Dong, DongHourly, Region
from regions.photos import dong_photos, region_photos
from search import engine
from webapi import data


def _month(request):
    return data.parse_month(request.query_params.get("month"))


# ───────────────────────── W-02 티어 · W-01 히트맵 ─────────────────────────


@api_view(["GET"])
def tier(request):
    try:
        month, month_str = _month(request)
    except data.BadMonth as ex:
        return bad_request(str(ex))
    scope = request.query_params.get("scope", "sigungu")
    if scope not in ("sigungu", "dong"):
        return bad_request("scope 는 sigungu 또는 dong 입니다.")
    regions = data.regions_by_id()
    region_scores = scoring.unit_scores("region", month)
    if scope == "sigungu":
        photos = data.one_photo_per_unit("region", list(regions), month)
        items = [
            {
                "id": r.sigungu_code,
                "name": r.sigungu_name,
                "province": r.sido_name,
                "photoUrl": photos.get(rid, {}).get("url", ""),
                "scores": data.web_scores(region_scores.get(rid, {})),
                "inherited": [],
                "terrain": data.terrain_of(r.tags),
            }
            for rid, r in sorted(regions.items())
        ]
    else:
        dongs = list(
            Dong.objects.filter(is_excluded=False, adm_code__isnull=False).order_by("dong_id")
        )
        merged = scoring.with_region_metrics(
            scoring.unit_scores("dong", month),
            region_scores,
            {d.dong_id: d.region_id for d in dongs},
        )
        dong_ph = data.one_photo_per_unit("dong", [d.dong_id for d in dongs], month)
        reg_ph = data.one_photo_per_unit("region", list(regions), month)
        items = []
        for d in dongs:
            r = regions[d.region_id]
            items.append(
                {
                    "id": d.adm_code,
                    "name": d.dong_name,
                    "province": r.sido_name,
                    "sigungu": {"id": r.sigungu_code, "name": r.sigungu_name},
                    "photoUrl": (dong_ph.get(d.dong_id) or reg_ph.get(d.region_id) or {}).get(
                        "url", ""
                    ),
                    "scores": data.web_scores(merged.get(d.dong_id, {})),
                    "inherited": ["crowd", "overnight"],
                    "terrain": data.terrain_of(r.tags),
                }
            )
    return Response({"month": month_str, "scope": scope, "items": items})


# ───────────────────────── W-01 인기 지역 ─────────────────────────


@api_view(["GET"])
def popular(request):
    try:
        month, month_str = _month(request)
    except data.BadMonth as ex:
        return bad_request(str(ex))
    regions = data.regions_by_id()
    scores = scoring.unit_scores("region", month)
    photos = data.one_photo_per_unit("region", list(regions), month)
    items = [
        {
            "id": r.sigungu_code,
            "name": r.sigungu_name,
            "province": r.sido_name,
            "photoUrl": photos.get(rid, {}).get("url", ""),
            "tags": data.tag_labels(r.tags, 3),
            "terrain": data.terrain_of(r.tags),
            "scores": data.web_scores(scores.get(rid, {})),
            "favoriteCount": 0,  # 즐겨찾기 · 조회 집계는 로그인 · 조회 로그가 생기면 채운다
            "viewCount": 0,
        }
        for rid, r in sorted(regions.items())
    ]
    return Response({"month": month_str, "asOf": date.today().isoformat(), "items": items})


# ───────────────────────── W-03 지역 상세 ─────────────────────────


@api_view(["GET"])
def region_detail(request, sgg_id: str):
    try:
        month, month_str = _month(request)
    except data.BadMonth as ex:
        return bad_request(str(ex))
    region = Region.objects.filter(sigungu_code=sgg_id).first()
    if region is None:
        return not_found("시군구를 찾을 수 없습니다.")
    rid = region.region_id
    all_scores = scoring.unit_scores("region", month)
    mine = all_scores.get(rid, {})
    raw = lambda k: mine.get(k, {}).get("raw")  # noqa: E731

    crowd = data.monthly_crowd(rid)
    months = [{"month": m, "crowd": round(crowd[m])} for m in sorted(crowd)]
    by_crowd = sorted(crowd, key=crowd.get)
    peer_by_month = {
        m: s.get("peer", {}).get("raw") or 0
        for m, s in ((m, scoring.unit_scores("region", m).get(rid, {})) for m in range(1, 13))
    }
    offpeak = sorted(crowd, key=lambda m: -(peer_by_month.get(m, 0) * (100 - crowd[m])))[:2]

    quiet_raw = {u: s.get("quiet", {}).get("raw") for u, s in all_scores.items()}
    quiet_rank = 1 + sum(
        1
        for v in quiet_raw.values()
        if v is not None and raw("quiet") is not None and v < raw("quiet")
    )

    dongs = list(Dong.objects.filter(region_id=rid, is_excluded=False, adm_code__isnull=False))
    dong_scores = scoring.unit_scores("dong", month)
    annual = scoring.unit_scores("dong", None) if month else dong_scores
    visits = data.dong_visits([d.dong_id for d in dongs])
    dong_items = []
    for d in dongs:
        v = visits.get(d.dong_id, {})
        peak = max(v, key=v.get) if v else 1
        local = annual.get(d.dong_id, {}).get("local_life", {}).get("raw") or 0
        dong_items.append(
            {
                "id": d.adm_code,
                "name": d.dong_name,
                "kind": "living" if local >= 0.7 else "tourist",
                "landmark": "",
                "quietScore": dong_scores.get(d.dong_id, {}).get("quiet", {}).get("score") or 0,
                "peakMonth": peak,
                "crowdOfPeak": round(v.get(month, 0) / v[peak] * 100)
                if (month and v.get(peak))
                else 100,
            }
        )
    dong_items.sort(key=lambda x: -x["quietScore"])
    photos = region_photos(rid, month)["featured"]
    ease = raw("ease")
    return Response(
        {
            "id": region.sigungu_code,
            "name": region.sigungu_name,
            "province": region.sido_name,
            "tags": data.tag_labels(region.tags, 3),
            "seasonType": data.season_type(crowd),
            "photos": [{"url": p["url"], "credit": p["credit"]} for p in photos],
            "month": month_str,
            "stats": {
                "crowdIndex": round(
                    ease if month and ease is not None else mean(crowd.values()) if crowd else 0
                ),
                "dayInflow": round(raw("quiet") or 0, 2),
                "quietRank": quiet_rank,
                "peerShare": round((raw("peer") or 0) * 100),
                "overnightCoef": round(raw("overnight") or 0, 2),
                "weekendRatio": round(raw("weekend_gap") or 1, 2),
            },
            "months": months,
            "bestMonths": sorted(by_crowd[:2]),
            "avoidMonths": sorted(by_crowd[-2:]),
            "peerOffpeakMonths": sorted(offpeak),
            "dongs": dong_items,
            "excludedDongs": data.excluded_dongs(rid),
            "links": [],
        }
    )


# ───────────────────────── W-04 동네 프로필 ─────────────────────────


@api_view(["GET"])
def dong_detail(request, dong_id: str):
    try:
        month, month_str = _month(request)
    except data.BadMonth as ex:
        return bad_request(str(ex))
    dong = Dong.objects.select_related("region").filter(adm_code=dong_id).first()
    if dong is None:
        return not_found("행정동을 찾을 수 없습니다.")
    region = dong.region
    all_scores = scoring.unit_scores("dong", month)
    mine = all_scores.get(dong.dong_id, {})
    raw = lambda k: mine.get(k, {}).get("raw")  # noqa: E731

    curves = {DayGroup.WEEKDAY: [0] * 24, DayGroup.WEEKEND: [0] * 24}
    for g, h, v in DongHourly.objects.filter(dong_id=dong.dong_id, month=month or 0).values_list(
        "day_group", "hour", "index_100"
    ):
        curves[g][h] = v or 0
    peaks = {}
    for g, c in curves.items():
        low = min(range(9, 19), key=lambda h: (c[h], h))
        peaks[g] = {
            "peakHour": max(range(24), key=lambda h: (c[h], -h)),
            "lowHour": low,
            "lowValue": c[low],
        }

    evening_all = [s.get("evening", {}).get("raw") for s in all_scores.values()]
    evening_all = [v for v in evening_all if v]
    surplus = (raw("evening") or 0) / mean(evening_all) if evening_all else 1.0

    my_visits = data.dong_visits([dong.dong_id]).get(dong.dong_id, {})
    my_quiet = mine.get("quiet", {}).get("score")
    cands = data.similar_dongs(dong.dong_id, month)
    cand_visits = data.dong_visits([c[0] for c in cands])
    twins = []
    for cid, code, name, crid, dist in cands:
        their_quiet = all_scores.get(cid, {}).get("quiet", {}).get("score")
        mv = my_visits.get(month) if month else sum(my_visits.values())
        tv = (
            cand_visits.get(cid, {}).get(month) if month else sum(cand_visits.get(cid, {}).values())
        )
        if crid == region.region_id or not mv or not tv or tv >= mv:
            continue  # 다른 시군구 · 방문객이 더 적은 곳만 ("비슷한데 더 조용한 동네")
        if my_quiet is not None and their_quiet is not None and their_quiet < my_quiet:
            continue
        twins.append((cid, code, name, crid, dist, tv / mv))
        if len(twins) == 3:
            break
    regions = data.regions_by_id()
    twin_photos = data.one_photo_per_unit("dong", [t[0] for t in twins], month)
    twin_region_photos = data.one_photo_per_unit("region", [t[3] for t in twins], month)
    photos = dong_photos(dong.dong_id, region.region_id, month)["featured"]
    local = raw("local_life") or (
        scoring.unit_scores("dong", None).get(dong.dong_id, {}).get("local_life", {}).get("raw")
        or 0
    )
    return Response(
        {
            "id": dong.adm_code,
            "name": dong.dong_name,
            "sigungu": {"id": region.sigungu_code, "name": region.sigungu_name},
            "province": region.sido_name,
            "kind": "living" if local >= 0.7 else "tourist",
            "tags": data.tag_labels(dong.tags),
            "photos": [{"url": p["url"], "credit": p["credit"]} for p in photos],
            "landmarks": [],
            "accessNote": (
                f"해안선까지 {float(dong.coast_dist_km):.1f} km"
                if dong.coast_dist_km is not None
                else ""
            ),
            "month": month_str,
            "stats": {
                "dayInflow": round(raw("quiet") or 0, 2),
                "localLife": round(local * 100),
                "peerShare": round((raw("peer") or 0) * 100),
                "eveningVibe": round(raw("evening") or 0, 2),
                "eveningSurplus": round(surplus, 2),  # 근사: 전체 행정동 평균 대비 배율
            },
            "hourly": {"weekday": curves[DayGroup.WEEKDAY], "weekend": curves[DayGroup.WEEKEND]},
            "peaks": {"weekday": peaks[DayGroup.WEEKDAY], "weekend": peaks[DayGroup.WEEKEND]},
            "activityMix": data.activity_mix(
                curves[DayGroup.WEEKDAY],
                curves[DayGroup.WEEKEND],
                scoring.unit_scores("region", month)
                .get(region.region_id, {})
                .get("weekend_gap", {})
                .get("raw"),
            ),
            "seasonType": data.season_type(data.monthly_crowd(region.region_id)),
            "quieterTwins": [
                {
                    "id": code,
                    "name": name,
                    "sigunguId": regions[crid].sigungu_code,
                    "sigunguName": regions[crid].sigungu_name,
                    "similarity": round(max(0.0, 1 - dist / 2), 2),
                    "visitorRatio": round(ratio, 2),
                    "photoUrl": (twin_photos.get(cid) or twin_region_photos.get(crid) or {}).get(
                        "url", ""
                    ),
                }
                for cid, code, name, crid, dist, ratio in twins
            ],
            "bbox": data.dong_bbox(dong.dong_id) or [0, 0, 0, 0],
        }
        | ({"special": "special_facility"} if dong.is_excluded else {})
    )


# ───────────────────────── 노트 · 투표 (데이터 없음) ─────────────────────────


@api_view(["GET"])
def empty_notes(request, **kwargs):
    return Response({"total": 0, "items": []})


@api_view(["GET"])
def empty_poll(request, **kwargs):
    return Response(
        {"question": "이 시기, 정말 이랬나요?", "yes": 0, "no": 0, "total": 0, "photos": []}
    )


@api_view(["POST"])
def record_view(request, **kwargs):
    """W-03 조회 기록. 인기 지역 조회수 집계 테이블이 생기기 전까지는 받기만 한다."""
    return Response(status=204)


# ───────────────────────── 로그인 · 마이페이지 (미구현) ─────────────────────────


@api_view(["GET", "PATCH", "DELETE", "POST", "PUT"])
def unauthorized(request, *args, **kwargs):
    """로그인 기능 전까지는 항상 로그아웃 상태로 응답한다 (웹은 401 을 로그아웃으로 처리)."""
    return error(401, "UNAUTHORIZED", "로그인이 필요합니다.")


# ───────────────────────── W-01 자연어 검색 ─────────────────────────

STAY_PATTERNS = [
    (r"한\s*달|1\s*개월|한달", "month", "한 달"),
    (r"2\s*주|이\s*주", "two_weeks", "2주"),
    (r"일주일|1\s*주|한\s*주", "week", "1주"),
]
TERRAIN_FROM_TAG = {
    "coast": "coast",
    "beach": "coast",
    "island": "coast",
    "surf": "coast",
    "mountain": "mountain",
    "city": "city",
}
TERRAIN_LABEL = {"coast": "해안", "mountain": "산간", "city": "도시"}
PRESET_LABEL = {"quiet": "조용한 곳", "lively": "활기 있는 한 달"}


@api_view(["POST"])
def search_parse(request):
    text = (request.data or {}).get("text") if isinstance(request.data, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 200:
        return bad_request("text 는 1~200자 문자열입니다.")
    cond = engine.understand(text.strip())
    intent: dict = {"tags": []}
    if cond.month:
        intent["month"] = data.month_param(cond.month)
        intent["tags"].append({"axis": "month", "label": f"{cond.month}월"})
    keys = {k: d for k, d in cond.metrics}
    preset = None
    if keys.get("evening") == 1 or keys.get("peer") == 1 or keys.get("quiet") == -1:
        preset = "lively"
    elif keys.get("quiet") == 1 or keys.get("ease") == 1:
        preset = "quiet"
    if preset:
        intent["preset"] = preset
        intent["tags"].append({"axis": "preset", "label": PRESET_LABEL[preset]})
    terrain = []
    for t in cond.tags:
        tt = TERRAIN_FROM_TAG.get(t)
        if tt and tt not in terrain:
            terrain.append(tt)
    if terrain:
        intent["terrain"] = terrain
        for t in terrain:
            intent["tags"].append({"axis": "terrain", "label": TERRAIN_LABEL[t]})
    stay = next(((v, lab) for pat, v, lab in STAY_PATTERNS if re.search(pat, text)), None)
    if stay is None and "long_stay" in cond.tags:
        stay = ("month", "한 달")
    if stay:
        intent["stay"] = stay[0]
        intent["tags"].append({"axis": "stay", "label": stay[1]})
    if keys.get("peer") == 1:
        intent["peer"] = True
        intent["tags"].append({"axis": "peer", "label": "또래"})
    return Response(intent)
