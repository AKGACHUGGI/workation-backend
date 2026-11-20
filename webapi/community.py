"""즐겨찾기 · 워케이션 노트(팁) · 내 활동을 DB 에서 읽고 쓰는 함수들.

표: favorite · tip · tip_reaction · tip_reply · user_photo · poll_vote (db/schema.sql).
계약: workation-web src/features/me/types.ts · src/features/region/types.ts (Note).

- scope + unit_id 는 논리 참조(FK 없음)라 시군구 · 행정동 이름은 여기서 합친다.
- 화면에는 공개 팁(status = 'public')과 숨기지 않은 사진만 나온다.
- 목록 커서는 건너뛸 개수를 문자열로 준 값이다. 클라이언트는 뜻을 모르고 그대로 돌려준다.
"""

from __future__ import annotations

import re
from contextlib import nullcontext
from typing import NamedTuple

from django.db import connection, transaction
from django.utils import timezone

from metrics import scoring
from regions.models import Dong, Region
from webapi import data, photo_quarantine, photo_upload

FAVORITE_LIMIT = 50  # 종류(지역 · 동네)마다. 웹 FAVORITE_LIMIT 과 같다
PAGE_SIZE = 20
TIP_MAX_LEN = 300  # tip.body 는 VARCHAR(300). 앞뒤 공백을 자른 뒤 글자 수 기준
TIP_BURST_LIMIT = 3  # 같은 사용자가 TIP_BURST_WINDOW 안에 이만큼 쓰면 더 못 쓴다
TIP_BURST_WINDOW = "1 minute"  # PostgreSQL interval 표기
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


class Target(NamedTuple):
    """사진 · 투표 대상. unit_id 는 region_id 또는 dong_id, region_id 는 소속 시군구."""

    scope: str
    unit_id: int
    region_id: int
    name: str


def target(kind: str, code: str) -> Target | None:
    """웹 경로의 (regions|dongs, 코드) → Target. 없는 코드 · 집계 제외 동은 None."""
    if kind == "regions":
        r = region_by_code(code)
        return Target("region", r.region_id, r.region_id, r.sigungu_name) if r else None
    d = dong_by_code(code)
    if d is None or d.is_excluded:
        return None
    return Target("dong", d.dong_id, d.region_id, d.dong_name)


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
                     WHERE p.tip_id = t.tip_id AND p.status = 'public') AS replies,
                   (SELECT coalesce(json_agg(json_build_object('id', ph.user_photo_id::text,
                                                               'url', ph.url)
                                             ORDER BY ph.position), '[]'::json)
                      FROM user_photo ph
                     WHERE ph.tip_id = t.tip_id AND ph.status = 'approved') AS photos
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
        "photos": row[11],
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
    # photos 열이 더해져 total · helpful_sum 은 12 · 13 번째
    total, helpful_sum = (totals[0][12], int(totals[0][13])) if totals else (0, 0)
    return {
        "items": [_note(r, with_target=by == "user") for r in rows[:PAGE_SIZE]],
        "total": total,
        "helpfulSum": helpful_sum,
        "nextCursor": str(offset + PAGE_SIZE) if len(rows) > PAGE_SIZE else None,
    }


class BadTip(ValueError):
    """팁 쓰기 본문이 계약에 맞지 않는다. 메시지는 그대로 400 에 담긴다."""


def parse_tip(payload) -> tuple[str, int | None]:
    """{"body", "visitMonth"?} → (앞뒤 공백을 자른 body, visit_month 1~12 또는 None)."""
    if not isinstance(payload, dict):
        raise BadTip("본문은 JSON 객체여야 합니다.")
    body = payload.get("body")
    if not isinstance(body, str):
        raise BadTip("body 는 문자열입니다.")
    body = body.strip()
    if not body:
        raise BadTip("팁 내용을 입력해 주세요.")
    if len(body) > TIP_MAX_LEN:
        raise BadTip(f"팁은 {TIP_MAX_LEN}자까지 쓸 수 있어요.")
    raw = payload.get("visitMonth")
    if raw is None:
        return body, None
    # 읽기가 visit_month 를 DATA_YEAR-MM 으로 되돌려 주므로 그 해의 달만 받는다
    if not isinstance(raw, str) or not re.fullmatch(rf"{data.DATA_YEAR}-(0[1-9]|1[0-2])", raw):
        raise BadTip(f"visitMonth 는 '{data.DATA_YEAR}-01' ~ '{data.DATA_YEAR}-12' 입니다.")
    return body, int(raw[-2:])


_TIP_INSERT = f"""
    WITH cur AS (
      SELECT count(*) AS n FROM tip
       WHERE user_id = %(user)s AND created_at > now() - interval '{TIP_BURST_WINDOW}'
    ), ins AS (
      INSERT INTO tip (user_id, region_id, dong_id, visit_month, body)
      SELECT %(user)s, %(region)s, %(dong)s, %(month)s, %(body)s
        FROM cur WHERE n < %(limit)s
      RETURNING tip_id, created_at
    )
    SELECT tip_id, created_at FROM ins"""


def _insert_tip_photos(tip_id, scope, unit, month, user_id, urls) -> list[dict]:
    """노트 사진 행을 한 문장으로 넣는다 → [{id, url}] (올린 순서)."""
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO user_photo (scope, unit_id, month, url, user_id, tip_id, position)
               SELECT %(scope)s, %(unit)s, %(month)s, u.url, %(user)s, %(tip)s, u.n - 1
                 FROM unnest(%(urls)s::text[]) WITH ORDINALITY AS u(url, n)
               RETURNING user_photo_id, url, position""",
            {
                "scope": scope,
                "unit": unit,
                "month": month,
                "user": user_id,
                "tip": tip_id,
                "urls": urls,
            },
        )
        rows = sorted(cur.fetchall(), key=lambda r: r[2])
    return [{"id": str(pid), "url": url} for pid, url, _ in rows]


def create_tip(
    user, region_id: int, dong: Dong | None, body: str, visit_month: int | None, photos=()
):
    """공개 팁을 쓰고 읽기와 같은 모양(Note)으로 돌려준다. 연달아 쓰는 한도를 넘으면 None.

    최근 TIP_BURST_WINDOW 안의 내 팁을 세는 것과 넣는 것이 한 문장이라 DB 왕복이 한 번이다.
    photos(WebP 바이트)가 있으면 한 트랜잭션 안에서 팁을 넣고(한도를 넘으면 올리지 않음) →
    R2 에 올리고 → 사진 행을 넣는다. 업로드 실패는 UploadFailed(팁도 되돌림),
    사진 행 · 커밋 실패는 올린 파일을 지우고 그 에러를 다시 던진다.
    """
    attached: list[dict] = []
    uploaded: list[tuple[str, str]] = []
    try:
        with transaction.atomic() if photos else nullcontext():
            with connection.cursor() as cur:
                if photos:
                    # R2 에 올리는 동안 트랜잭션이 열려 있어 새 팁이 다른 요청의 한도 세기에
                    # 보이지 않는다. 같은 사용자의 사진 노트 쓰기를 줄 세운다 (트랜잭션 끝에 풀림)
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))", [f"tip:{user.user_id}"]
                    )
                cur.execute(
                    _TIP_INSERT,
                    {
                        "user": user.user_id,
                        "region": region_id,
                        "dong": dong.dong_id if dong else None,
                        "month": visit_month,
                        "body": body,
                        "limit": TIP_BURST_LIMIT,
                    },
                )
                row = cur.fetchone()
            if row is None:
                return None
            tip_id, created_at = row
            if photos:
                uploaded = photo_upload.upload("tip", list(photos))
                scope, unit = ("dong", dong.dong_id) if dong else ("region", region_id)
                attached = _insert_tip_photos(
                    tip_id, scope, unit, visit_month, user.user_id, [url for _, url in uploaded]
                )
    except Exception:
        # 사진 행 실패 · 커밋 실패 모두. 팁이 되돌려졌으니 올린 파일도 지운다
        if uploaded:
            photo_upload.discard([key for key, _ in uploaded])
        raise
    return _note(
        (
            tip_id,
            user.nickname,
            created_at,
            None,
            visit_month,
            body,
            region_id,
            dong.adm_code if dong else None,
            dong.dong_name if dong else None,
            0,
            0,
            attached,
        ),
        with_target=False,
    )


def delete_tip(user_id, tip_id: int) -> bool:
    """내 팁을 지운다 (도움돼요 · 답글은 ON DELETE CASCADE). 내 팁이 아니면 False.

    첨부 사진 (스펙 4-6):
    - 공개(approved) 사진은 R2 파일을 먼저 지운다. 실패하면 DeleteFailed 이고 팁은 그대로
      (다시 시도하면 된다). 행은 팁과 함께 지워진다 (CASCADE).
    - 가려진(pending · hidden) 사진은 증거라 팁에서 떼어(tip_id = NULL) hidden · deleted_by_user 로
      남긴다. 30일 뒤 manage.py photos purge 가 지운다.
    """
    with connection.cursor() as cur:
        cur.execute(
            """SELECT p.user_photo_id, p.url, p.status
                 FROM tip t JOIN user_photo p ON p.tip_id = t.tip_id
                WHERE t.tip_id = %s AND t.user_id = %s""",
            [tip_id, user_id],
        )
        photos = cur.fetchall()
    photo_quarantine.delete_files([url for _, url, status in photos if status == "approved"])
    kept = [pid for pid, _, status in photos if status != "approved"]
    with transaction.atomic(), connection.cursor() as cur:
        if kept:
            cur.execute(
                """UPDATE user_photo SET tip_id = NULL, status = 'hidden',
                          hidden_at = coalesce(hidden_at, now()), deleted_by_user = true
                    WHERE user_photo_id = ANY(%s)""",
                [kept],
            )
        cur.execute("DELETE FROM tip WHERE tip_id = %s AND user_id = %s", [tip_id, user_id])
        deleted = cur.rowcount > 0
    for pid in kept:  # 보통 이미 격리돼 있다 (신고로 가려질 때)
        photo_quarantine.try_quarantine(pid)
    return deleted


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
    voteCount 는 투표한 질문 수 (poll_vote 는 질문 하나에 한 행). photoCount 는 이달의 사진만.
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
                 (SELECT count(*) FROM user_photo
                   WHERE user_id = %(u)s AND status <> 'hidden' AND tip_id IS NULL),
                 (SELECT count(*) FROM favorite WHERE user_id = %(u)s),
                 (SELECT count(*) FROM poll_vote WHERE user_id = %(u)s)""",
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
    """내가 올린 이달의 사진 (숨김 · 노트 사진 제외), 최근 것부터.

    동네 사진의 regionName 은 소속 시군구.
    """
    with connection.cursor() as cur:
        cur.execute(
            """SELECT p.user_photo_id, p.url, p.month, p.status,
                      CASE WHEN p.scope = 'region' THEN p.unit_id ELSE d.region_id END
                 FROM user_photo p
                 LEFT JOIN dong d ON p.scope = 'dong' AND d.dong_id = p.unit_id
                WHERE p.user_id = %s AND p.status <> 'hidden' AND p.tip_id IS NULL
                ORDER BY p.created_at DESC, p.user_photo_id DESC""",
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
