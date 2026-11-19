"""즐겨찾기 · 워케이션 노트(팁) · 내 활동을 DB 에서 읽고 쓰는 함수들.

표: favorite · tip · tip_reaction · tip_reply · photo · metric_vote (db/schema.sql).
계약: workation-web src/features/me/types.ts · src/features/region/types.ts (Note).

- scope + unit_id 는 논리 참조(FK 없음)라 시군구 · 행정동 이름은 여기서 합친다.
- 화면에는 공개 팁(status = 'public')과 숨기지 않은 사진만 나온다.
- 목록 커서는 건너뛸 개수를 문자열로 준 값이다. 클라이언트는 뜻을 모르고 그대로 돌려준다.
"""

from __future__ import annotations

import re

from django.db import connection
from django.utils import timezone

from metrics import scoring
from regions.models import Dong, Region
from webapi import data

FAVORITE_LIMIT = 50  # 종류(지역 · 동네)마다. 웹 FAVORITE_LIMIT 과 같다
PAGE_SIZE = 20
KIND_SCOPE = {"regions": "region", "dongs": "dong"}


class BadCursor(ValueError):
    """cursor 가 이 서버가 준 값이 아니다."""


def iso(dt) -> str:
    """한국 시각 ISO 8601 (GET /api/me 의 createdAt 과 같은 표기)."""
    return timezone.localtime(dt).isoformat()


# ───────────────────────── 코드 ↔ unit_id ─────────────────────────


def region_by_code(code: str) -> Region | None:
    """시군구 5자리 코드 → Region (정적 캐시에서 찾는다)."""
    return next((r for r in data.regions_by_id().values() if r.sigungu_code == code), None)


def dong_by_code(code: str) -> Dong | None:
    """행정동 10자리 코드 → Dong."""
    return Dong.objects.filter(adm_code=code).first()


def unit_id(scope: str, code: str) -> int | None:
    """웹 id(시군구 · 행정동 코드) → DB 의 unit_id. 없는 코드면 None."""
    if scope == "region":
        region = region_by_code(code)
        return region.region_id if region else None
    dong = dong_by_code(code)
    return dong.dong_id if dong else None


# ───────────────────────── 즐겨찾기 ─────────────────────────


def _favorite_rows(user_id) -> list[tuple]:
    """(scope, unit_id, created_at, adm_code, dong_name, region_id) 를 저장한 순서로."""
    with connection.cursor() as cur:
        cur.execute(
            """SELECT f.scope, f.unit_id, f.created_at, d.adm_code, d.dong_name, d.region_id
                 FROM favorite f
                 LEFT JOIN dong d ON f.scope = 'dong' AND d.dong_id = f.unit_id
                WHERE f.user_id = %s
                ORDER BY f.created_at, f.scope, f.unit_id""",
            [user_id],
        )
        return cur.fetchall()


def favorite_ids(user_id) -> dict[str, list[str]]:
    """하트 상태용 {regions: [시군구 코드], dongs: [행정동 코드]}. 저장한 순서."""
    regions = data.regions_by_id()
    out: dict[str, list[str]] = {"regions": [], "dongs": []}
    for scope, unit, _, adm_code, _, _ in _favorite_rows(user_id):
        if scope == "region" and unit in regions:
            out["regions"].append(regions[unit].sigungu_code)
        elif scope == "dong" and adm_code:
            out["dongs"].append(adm_code)
    return out


def favorites(user_id, month: int | None) -> tuple[list[dict], list[dict]]:
    """(FavoriteRegion[], FavoriteDong[]). 최근에 저장한 것부터.

    scores 는 /api/tier 와 같은 그 달 백분위. 동의 crowd · overnight 는 소속 시군구 값이다.
    """
    rows = _favorite_rows(user_id)[::-1]
    regions = data.regions_by_id()
    region_scores = scoring.unit_scores("region", month)

    saved_regions = [
        (regions[u], at) for scope, u, at, *_ in rows if scope == "region" and u in regions
    ]
    photos = data.one_photo_per_unit("region", [r.region_id for r, _ in saved_regions], month)
    region_items = [
        {
            "id": r.sigungu_code,
            "name": r.sigungu_name,
            "province": r.sido_name,
            "photoUrl": photos.get(r.region_id, {}).get("url", ""),
            "scores": data.web_scores(region_scores.get(r.region_id, {})),
            "tags": data.tag_labels(r.tags, 3),
            "addedAt": iso(at),
        }
        for r, at in saved_regions
    ]

    saved_dongs = [
        (u, at, adm_code, name, rid)
        for scope, u, at, adm_code, name, rid in rows
        if scope == "dong" and adm_code and rid in regions
    ]
    dong_scores = scoring.unit_scores("dong", month)
    annual = scoring.unit_scores("dong", None) if month else dong_scores
    merged = scoring.with_region_metrics(
        {u: dong_scores.get(u, {}) for u, *_ in saved_dongs},
        region_scores,
        {u: rid for u, *_, rid in saved_dongs},
    )
    dong_items = [
        {
            "id": adm_code,
            "name": name,
            "kind": scoring.dong_kind(u, annual),
            "sigungu": {"id": regions[rid].sigungu_code, "name": regions[rid].sigungu_name},
            "scores": data.web_scores(merged[u]),
            "addedAt": iso(at),
        }
        for u, at, adm_code, name, rid in saved_dongs
    ]
    return region_items, dong_items


def put_favorite(user_id, scope: str, unit: int) -> bool:
    """즐겨찾기에 넣는다. 이미 있으면 그대로 둔다. 종류별 상한을 넘으면 False.

    세기와 넣기를 한 문장으로 해서 DB 왕복이 한 번이다.
    """
    with connection.cursor() as cur:
        cur.execute(
            """WITH cur AS (
                 SELECT count(*) AS n, coalesce(bool_or(unit_id = %(unit)s), false) AS has
                   FROM favorite WHERE user_id = %(user)s AND scope = %(scope)s
               ), ins AS (
                 INSERT INTO favorite (user_id, scope, unit_id)
                 SELECT %(user)s, %(scope)s, %(unit)s FROM cur WHERE NOT has AND n < %(limit)s
                 ON CONFLICT DO NOTHING
               )
               SELECT has OR n < %(limit)s FROM cur""",
            {"user": user_id, "scope": scope, "unit": unit, "limit": FAVORITE_LIMIT},
        )
        return cur.fetchone()[0]


def delete_favorite(user_id, scope: str, unit: int) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM favorite WHERE user_id = %s AND scope = %s AND unit_id = %s",
            [user_id, scope, unit],
        )


# ───────────────────────── 워케이션 노트 (팁) ─────────────────────────

TIP_FILTERS = {
    "region": "t.region_id = %(id)s",  # 그 시군구의 팁 전부 (동네에 쓴 팁 포함)
    "dong": "t.dong_id = %(id)s",
    "user": "t.user_id = %(id)s",
}
TIP_ORDERS = {
    "helpful": "helpful DESC, created_at DESC, tip_id DESC",
    "recent": "created_at DESC, tip_id DESC",
}
# total · helpful_sum 은 창 함수라 페이지를 자르기 전 전체에 대한 값이다
_TIP_PAGE = """
    SELECT x.*, count(*) OVER () AS total, sum(helpful) OVER () AS helpful_sum
      FROM (SELECT t.tip_id, u.nickname, t.created_at, t.stay_days, t.visit_month, t.body,
                   t.region_id, d.adm_code, d.dong_name,
                   (SELECT count(*) FROM tip_reaction r WHERE r.tip_id = t.tip_id) AS helpful,
                   (SELECT count(*) FROM tip_reply p
                     WHERE p.tip_id = t.tip_id AND p.status = 'public') AS replies
              FROM tip t
              JOIN app_user u ON u.user_id = t.user_id
              LEFT JOIN dong d ON d.dong_id = t.dong_id
             WHERE t.status = 'public' AND {where}) x
     ORDER BY {order}
     LIMIT %(limit)s OFFSET %(offset)s"""


def parse_cursor(raw: str | None) -> int:
    if raw in (None, ""):
        return 0
    if not re.fullmatch(r"[0-9]{1,6}", raw):
        raise BadCursor("cursor 는 앞 응답의 nextCursor 값입니다.")
    return int(raw)


def stay_label(days: int | None) -> str | None:
    """체류 일수 → '2주 체류' · '한 달 체류'."""
    if not days or days < 1:
        return None
    if days < 7:
        return f"{days}일 체류"
    if days < 28:
        return f"{days // 7}주 체류"
    if days < 60:
        return "한 달 체류"
    return f"{days // 30}개월 체류"


def _note(row: tuple, with_target: bool) -> dict:
    tip_id, nickname, created_at, stay_days, visit_month, body, region_id, adm_code, dong_name = (
        row[:9]
    )
    note = {
        "id": str(tip_id),
        "author": {"nickname": nickname, "initial": nickname[:1]},
        "createdAt": iso(created_at),
        "body": body,
        "helpful": row[9],
        "replies": row[10],
    }
    if dong_name:
        note["dongName"] = dong_name
    if label := stay_label(stay_days):
        note["stayLabel"] = label
    if visit_month:
        note["visitMonth"] = data.month_param(visit_month)
    if with_target:
        region = data.regions_by_id()[region_id]
        note["target"] = (
            {"kind": "dong", "id": adm_code, "name": dong_name}
            if adm_code
            else {"kind": "region", "id": region.sigungu_code, "name": region.sigungu_name}
        )
    return note


def tip_page(by: str, target_id, sort: str, cursor: str | None) -> dict:
    """공개 팁 한 쪽. by = region(시군구) · dong(행정동) · user(내 팁, target 포함).

    {"items", "total", "helpfulSum", "nextCursor"(다음 쪽이 없으면 None)}
    """
    offset = parse_cursor(cursor)
    sql = _TIP_PAGE.format(where=TIP_FILTERS[by], order=TIP_ORDERS[sort])
    with connection.cursor() as cur:
        # 한 건 더 읽어 다음 쪽이 있는지 본다
        cur.execute(sql, {"id": target_id, "limit": PAGE_SIZE + 1, "offset": offset})
        rows = cur.fetchall()
        totals = rows
        if not rows and offset:  # 끝을 지난 커서. 합계만 다시 읽는다
            cur.execute(sql, {"id": target_id, "limit": 1, "offset": 0})
            totals = cur.fetchall()
    total, helpful_sum = (totals[0][11], int(totals[0][12])) if totals else (0, 0)
    return {
        "items": [_note(r, with_target=by == "user") for r in rows[:PAGE_SIZE]],
        "total": total,
        "helpfulSum": helpful_sum,
        "nextCursor": str(offset + PAGE_SIZE) if len(rows) > PAGE_SIZE else None,
    }


def delete_tip(user_id, tip_id: int) -> bool:
    """내 팁을 지운다 (도움돼요 · 답글은 ON DELETE CASCADE). 내 팁이 아니면 False."""
    with connection.cursor() as cur:
        cur.execute("DELETE FROM tip WHERE tip_id = %s AND user_id = %s", [tip_id, user_id])
        return cur.rowcount > 0


# ───────────────────────── MY ─────────────────────────


def _short_name(sigungu_name: str) -> str:
    """'강릉시' → '강릉' (프로필 한 줄 '강릉 2회 · 고성 1회 체류')."""
    return (
        sigungu_name[:-1]
        if len(sigungu_name) > 2 and sigungu_name[-1] in "시군구"
        else sigungu_name
    )


def summary(user_id) -> dict:
    """MeSummary 의 집계 부분. createdAt 은 뷰가 app_user 에서 넣는다.

    stays 는 체류 기록 표가 없어 내 공개 팁의 시군구별 개수다.
    voteCount 는 투표한 대상(지역 · 동네 × 월) 수 — metric_vote 는 지표별 행이라 행 수가 아니다.
    """
    with connection.cursor() as cur:
        cur.execute(
            """WITH s AS (
                 SELECT region_id, count(*) AS n FROM tip
                  WHERE user_id = %(u)s AND status = 'public' GROUP BY region_id
               )
               SELECT
                 (SELECT array_agg(region_id ORDER BY n DESC, region_id) FROM s),
                 (SELECT array_agg(n ORDER BY n DESC, region_id) FROM s),
                 (SELECT count(*) FROM photo WHERE user_id = %(u)s AND status <> 'hidden'),
                 (SELECT count(*) FROM favorite WHERE user_id = %(u)s),
                 (SELECT count(DISTINCT (scope, unit_id, month))
                    FROM metric_vote WHERE user_id = %(u)s)""",
            {"u": user_id},
        )
        region_ids, counts, photo_count, favorite_count, vote_count = cur.fetchone()
    regions = data.regions_by_id()
    stays = [
        {"regionName": _short_name(regions[rid].sigungu_name), "count": n}
        for rid, n in zip(region_ids or [], counts or [], strict=True)
    ]
    return {
        "stays": stays,
        "tipCount": sum(counts or []),
        "photoCount": photo_count,
        "favoriteCount": favorite_count,
        "voteCount": vote_count,
    }


def my_photos(user_id) -> list[dict]:
    """내가 올린 사진 (숨김 제외), 최근 것부터. 동네 사진의 regionName 은 소속 시군구."""
    with connection.cursor() as cur:
        cur.execute(
            """SELECT p.photo_id, p.url, p.month, p.status,
                      CASE WHEN p.scope = 'region' THEN p.unit_id ELSE d.region_id END
                 FROM photo p
                 LEFT JOIN dong d ON p.scope = 'dong' AND d.dong_id = p.unit_id
                WHERE p.user_id = %s AND p.status <> 'hidden'
                ORDER BY p.created_at DESC, p.photo_id DESC""",
            [user_id],
        )
        rows = cur.fetchall()
    regions = data.regions_by_id()
    return [
        {
            "id": str(photo_id),
            "url": url,
            "month": data.month_param(month),
            "regionName": regions[rid].sigungu_name if rid in regions else "",
            "status": status,
        }
        for photo_id, url, month, status, rid in rows
    ]
