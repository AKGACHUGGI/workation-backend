"""지역 · 동네 사진 고르기. photo 테이블(파일은 Cloudflare R2)에서 승인된 사진만.

- 같은 달 → 앞뒤 한 달 → 월 모름 → 그 밖의 달 순으로, 같은 순위 안에서는 랜덤.
- 동네는 그 동 사진(scope=dong)을 먼저, 모자라면 소속 시군구 사진으로 채운다.
- 관광공사 사진은 공공누리 1유형이라 credit(출처)을 화면에 함께 보여 줘야 한다.
"""

from django.db import connection

MAX_FEATURED = 5

_ORDER = """
  CASE WHEN %(month)s::int IS NULL THEN 0
       WHEN month = %(month)s THEN 0
       WHEN least(abs(month - %(month)s), 12 - abs(month - %(month)s)) <= 1 THEN 1
       WHEN month IS NULL THEN 2
       ELSE 3 END"""


def _pick(where: str, params: dict, limit: int) -> tuple[list[dict], int]:
    sql = f"""
        SELECT photo_id, url, credit, count(*) OVER () AS total
          FROM (SELECT DISTINCT ON (url) photo_id, url, credit, month, prio FROM (
                  SELECT photo_id, url, credit, month, {where} AS prio
                    FROM photo WHERE status = 'approved' AND ({params.pop("_filter")})
                ) a ORDER BY url, prio) b
         ORDER BY prio, {_ORDER}, random()
         LIMIT %(limit)s"""
    with connection.cursor() as cur:
        cur.execute(sql, params | {"limit": limit})
        rows = cur.fetchall()
    total = rows[0][3] if rows else 0
    return [{"photoId": pid, "url": url, "credit": credit} for pid, url, credit, _ in rows], total


def region_photos(region_id: int, month: int | None, limit: int = MAX_FEATURED) -> dict:
    featured, total = _pick(
        "0",
        {
            "_filter": "scope = 'region' AND unit_id = %(region)s",
            "region": region_id,
            "month": month,
        },
        limit,
    )
    return {"featured": featured, "totalCount": total}


def dong_photos(dong_id: int, region_id: int, month: int | None, limit: int = MAX_FEATURED) -> dict:
    featured, total = _pick(
        "CASE WHEN scope = 'dong' THEN 0 ELSE 1 END",
        {
            "_filter": "(scope = 'dong' AND unit_id = %(dong)s)"
            " OR (scope = 'region' AND unit_id = %(region)s)",
            "dong": dong_id,
            "region": region_id,
            "month": month,
        },
        limit,
    )
    return {"featured": featured, "totalCount": total}


def first_photo(region_id: int, month: int | None) -> dict | None:
    featured = region_photos(region_id, month, limit=1)["featured"]
    return featured[0] if featured else None
