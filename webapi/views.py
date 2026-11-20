"""웹 화면 계약 API (/api/...). 계약은 workation-web src/features/*/types.ts.

화면 데이터는 /api/v1 과 같은 DB · 같은 점수 계산(metrics/scoring.py)을 쓰고
모양만 웹 계약에 맞춘다.
아직 데이터가 없는 것(랜드마크 · 접근 안내)은 빈 값이다. 체감 투표는 webapi/poll.py.
마이페이지 · 즐겨찾기(/api/me/*)는 webapi/me.py.
NMF 계절 · 활동 유형과 저녁 잔차는 DB 에 없어 월별 혼잡과 24시간 곡선으로 만든 근사값이다.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date
from statistics import mean

from django.db import connection, transaction
from django.db.utils import ProgrammingError
from rest_framework.decorators import api_view
from rest_framework.exceptions import AuthenticationFailed, ParseError, Throttled
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from accounts.authentication import SupabaseJWTAuthentication
from core.errors import bad_request, error, not_found
from metrics import scoring
from regions.models import DayGroup, Dong, DongHourly, Region
from regions.photos import dong_photos, region_photos
from search import engine
from webapi import community, data
from webapi.models import UserEvent

logger = logging.getLogger(__name__)


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
        dongs = data.active_dongs()
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
    views = _view_counts()
    favorites = _favorite_counts()
    items = [
        {
            "id": r.sigungu_code,
            "name": r.sigungu_name,
            "province": r.sido_name,
            "photoUrl": photos.get(rid, {}).get("url", ""),
            "tags": data.tag_labels(r.tags, 3),
            "terrain": data.terrain_of(r.tags),
            "scores": data.web_scores(scores.get(rid, {})),
            "favoriteCount": favorites.get(rid, 0),
            "viewCount": views.get(r.sigungu_code, 0),
        }
        for rid, r in sorted(regions.items())
    ]
    return Response({"month": month_str, "asOf": date.today().isoformat(), "items": items})


def _favorite_counts() -> dict[int, int]:
    """region_id → 그 시군구를 즐겨찾기한 사용자 수. 한 번의 집계 쿼리 (캐시하지 않는다)."""
    with connection.cursor() as cur:
        cur.execute(
            "SELECT unit_id, COUNT(*) FROM favorite WHERE scope = 'region' GROUP BY unit_id"
        )
        return dict(cur.fetchall())


def _view_counts() -> dict[str, int]:
    """시군구 코드 → 최근 30일 region_view 를 남긴 서로 다른 익명 ID 수.

    user_event 표가 아직 없는 DB(Supabase 에 migration 적용 전)에서도 인기 지역이 깨지지 않게
    세이브포인트 안에서 읽고, 표가 없으면 경고만 남기고 빈 집계(모두 0)를 돌려준다.
    """
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "SELECT target, COUNT(DISTINCT anon_id) FROM user_event"
                " WHERE kind = 'region_view' AND created_at >= now() - interval '30 days'"
                " GROUP BY target"
            )
            return dict(cur.fetchall())
    except ProgrammingError as ex:
        logger.warning("조회수 집계 실패 (user_event 표 확인 필요): %s", ex)
        return {}


# ───────────────────────── 개인화 랭킹 행동 기록 ─────────────────────────

# kind → target 형식. search 는 target 이 없다 (null)
EVENT_TARGET = {
    "search": None,
    "region_view": re.compile(r"[0-9]{5}"),  # 시군구 코드
    "dong_view": re.compile(r"[0-9]{10}"),  # 행정동 코드
}
PAYLOAD_MAX_BYTES = 2048


class EventsBurstThrottle(AnonRateThrottle):
    scope = "events_burst"


class EventsDayThrottle(AnonRateThrottle):
    scope = "events_day"


def _is_uuid4(value) -> bool:
    """하이픈 넣은 표준 표기의 UUID v4 인지 (crypto.randomUUID() 형식)."""
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


def _has_nul(value) -> bool:
    """키·문자열 값 어딘가에 NUL 문자가 있는지. PostgreSQL jsonb 는 NUL 을 받지 않는다."""
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_has_nul(k) or _has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_nul(v) for v in value)
    return False


def _payload_error(payload) -> str | None:
    """payload 는 객체 또는 null, 직렬화 2048바이트 이하, 저장 가능한 값만."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return "payload 는 객체 또는 null 입니다."
    try:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except ValueError:  # 1e999 같은 inf · nan
        return "payload 의 숫자는 유한한 값이어야 합니다."
    try:
        size = len(text.encode())
    except UnicodeEncodeError:  # 짝 없는 서로게이트 (\ud800 등)
        return "payload 에 UTF-8 로 쓸 수 없는 문자가 있습니다."
    if size > PAYLOAD_MAX_BYTES:
        return f"payload 는 {PAYLOAD_MAX_BYTES}바이트 이하입니다."
    if _has_nul(payload):
        return "payload 에 NUL 문자를 넣을 수 없습니다."
    return None


def _event_error(body) -> str | None:
    """POST /api/events 본문 검증. 문제가 있으면 400 메시지, 없으면 None."""
    if not isinstance(body, dict):
        return "본문은 JSON 객체입니다."
    if not _is_uuid4(body.get("anonId")):
        return "anonId 는 UUID v4 문자열입니다."
    kind = body.get("kind")
    if not isinstance(kind, str) or kind not in EVENT_TARGET:
        return "kind 는 search · region_view · dong_view 중 하나입니다."
    target, pattern = body.get("target"), EVENT_TARGET[kind]
    if pattern is None and target is not None:
        return "search 의 target 은 null 입니다."
    if pattern is not None and not (isinstance(target, str) and pattern.fullmatch(target)):
        return "region_view 는 숫자 5자리, dong_view 는 숫자 10자리 target 이 필요합니다."
    return _payload_error(body.get("payload"))


class EventsView(APIView):
    """POST /api/events. 개인화 랭킹 행동 기록. 웹은 응답을 기다리지 않고 실패도 무시한다.

    429 · 잘못된 JSON 을 공통 오류 모양({"error": {...}})으로 내려고 클래스 뷰.
    """

    throttle_classes = [EventsBurstThrottle, EventsDayThrottle]

    def handle_exception(self, exc):
        if isinstance(exc, Throttled):
            res = error(429, "TOO_MANY_REQUESTS", "잠시 후 다시 시도해 주세요.")
            if exc.wait is not None:
                res["Retry-After"] = str(int(exc.wait))
            return res
        if isinstance(exc, ParseError):
            return bad_request("본문은 JSON 객체입니다.")
        return super().handle_exception(exc)

    def post(self, request):
        body = request.data
        message = _event_error(body)
        if message:
            return bad_request(message)
        UserEvent.objects.create(
            anon_id=body["anonId"],
            kind=body["kind"],
            target=body.get("target"),
            payload=body.get("payload"),
        )
        return Response(status=204)


events = EventsView.as_view()


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

    monthly = data.region_monthly(rid, ("ease", "peer"))
    crowd = monthly["ease"]
    months = [{"month": m, "crowd": round(crowd[m])} for m in sorted(crowd)]
    by_crowd = sorted(crowd, key=crowd.get)
    peer_by_month = {m: v or 0 for m, v in monthly["peer"].items()}
    offpeak = sorted(crowd, key=lambda m: -(peer_by_month.get(m, 0) * (100 - crowd[m])))[:2]

    quiet_raw = {u: s.get("quiet", {}).get("raw") for u, s in all_scores.items()}
    quiet_rank = 1 + sum(
        1
        for v in quiet_raw.values()
        if v is not None and raw("quiet") is not None and v < raw("quiet")
    )

    region_dongs = list(Dong.objects.filter(region_id=rid))
    dongs = [d for d in region_dongs if not d.is_excluded and d.adm_code is not None]
    dong_scores = scoring.unit_scores("dong", month)
    annual = scoring.unit_scores("dong", None) if month else dong_scores
    visits = data.dong_visits([d.dong_id for d in dongs])
    dong_items = []
    for d in dongs:
        v = visits.get(d.dong_id, {})
        peak = max(v, key=v.get) if v else 1
        dong_items.append(
            {
                "id": d.adm_code,
                "name": d.dong_name,
                "kind": scoring.dong_kind(d.dong_id, annual),
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
            "excludedDongs": data.excluded_dongs(region_dongs),
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

    my_quiet = mine.get("quiet", {}).get("score")
    cands = data.similar_dongs(dong.dong_id, month)
    # 나와 후보 동의 방문인구를 한 번에 읽는다
    cand_visits = data.dong_visits([dong.dong_id, *(c[0] for c in cands)])
    my_visits = cand_visits.get(dong.dong_id, {})
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
    region_scores = scoring.unit_scores("region", month)
    # 모바일 M-04 지표 카드 · 고정 바: /api/tier?scope=dong 의 같은 동 scores 와 같은 값
    # (crowd · overnight 는 시군구 값). 집계 제외 동은 scores 를 넣지 않는다.
    scores = (
        {}
        if dong.is_excluded or not mine
        else scoring.with_region_metrics(
            {dong.dong_id: mine}, region_scores, {dong.dong_id: region.region_id}
        )[dong.dong_id]
    )
    return Response(
        {
            "id": dong.adm_code,
            "name": dong.dong_name,
            "sigungu": {"id": region.sigungu_code, "name": region.sigungu_name},
            "province": region.sido_name,
            "kind": scoring.dong_kind(dong.dong_id),
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
            # W-05 비교 곡선: 시간대별 평균 체류인구(명)
            "stayHourly": data.dong_stay_hourly(dong.dong_id, month),
            "peaks": {"weekday": peaks[DayGroup.WEEKDAY], "weekend": peaks[DayGroup.WEEKEND]},
            "activityMix": data.activity_mix(
                curves[DayGroup.WEEKDAY],
                curves[DayGroup.WEEKEND],
                region_scores.get(region.region_id, {}).get("weekend_gap", {}).get("raw"),
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
            # 백분위를 매긴 행정동 수 (집계 제외 빼고)
            "dongCount": len(data.active_dongs()),
        }
        | ({"scores": data.web_scores(scores)} if scores else {})
        | ({"special": "special_facility"} if dong.is_excluded else {})
    )


# ───────────────────────── 워케이션 노트 (읽기) ─────────────────────────


def viewer_id(request):
    """로그인 없이 읽는 API 에서 보는 사람의 user_id.

    토큰이 없거나 틀리면 None (읽기는 막지 않는다).
    """
    try:
        found = SupabaseJWTAuthentication().authenticate(request)
    except AuthenticationFailed:
        return None
    return found[0].user_id if found and found[0] else None


def _notes(request, by: str, target_id: int) -> Response:
    """NotesResponse. 공개 팁만, 로그인 없이 읽는다. sort 기본값은 helpful.

    토큰이 있으면 내 팁에 mine, 내가 도움돼요 한 팁에 myHelpful 을 붙인다.
    """
    sort = request.query_params.get("sort") or "helpful"
    if sort not in community.TIP_ORDERS:
        return bad_request("sort 는 helpful 또는 recent 입니다.")
    try:
        page = community.tip_page(
            by, target_id, sort, request.query_params.get("cursor"), viewer=viewer_id(request)
        )
    except community.BadCursor as ex:
        return bad_request(str(ex))
    body = {"total": page["total"], "items": page["items"]}
    if page["nextCursor"]:
        body["nextCursor"] = page["nextCursor"]
    return Response(body)


@api_view(["GET"])
def region_notes(request, sgg_id: str):
    """시군구의 팁 전부 (그 시군구의 동네에 쓴 팁 포함, dongName 으로 구분)."""
    region = community.region_by_code(sgg_id)
    if region is None:
        return not_found("시군구를 찾을 수 없습니다.")
    return _notes(request, "region", region.region_id)


@api_view(["GET"])
def dong_notes(request, dong_id: str):
    dong = community.dong_by_code(dong_id)
    if dong is None:
        return not_found("행정동을 찾을 수 없습니다.")
    return _notes(request, "dong", dong.dong_id)


@api_view(["POST"])
def record_view(request, **kwargs):
    """예전 W-03 조회 기록 엔드포인트. 웹은 이제 POST /api/events 로 조회를 기록하고
    viewCount 도 그 기록(user_event)으로 센다. 이 엔드포인트는 이전 클라이언트 호환용으로
    받기만 한다."""
    return Response(status=204)


# ───────────────────────── 예전 로그인 경로 ─────────────────────────


@api_view(["GET", "PATCH", "DELETE", "POST", "PUT"])
def unauthorized(request, *args, **kwargs):
    """/api/auth/* 는 Supabase 로그인으로 없어졌다. 웹은 401 을 로그아웃으로 처리한다."""
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
