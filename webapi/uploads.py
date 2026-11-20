"""이달의 사진 올리기 · 사진 신고 (스펙 4-3 · 4-4). 사진 처리는 webapi/photo_upload.py.

사진은 올리면 바로 공개(approved)되고, 신고가 PHOTO_REPORT_THRESHOLD 건 쌓이면 가려진다(pending).
가려진 사진은 운영자가 manage.py photos 로 복구하거나 숨김을 확정한다.
"""

from django.conf import settings
from django.db import connection
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from core.errors import bad_request, error, not_found
from webapi import community, data, photo_quarantine, photo_upload
from webapi.me import MeAPIView

PHOTO_HOURLY_LIMIT = 30  # 사용자당 최근 1시간 이달의 사진 장수
REASON_MAX_LEN = 30  # report.reason VARCHAR(30)


def _bad_photo(message: str) -> Response:
    return error(400, "INVALID_PHOTO", message)


class MonthPhotoView(MeAPIView):
    """POST /api/{regions|dongs}/{id}/photos. 201 {items: MyPhoto[]}.

    multipart: month · photos(1~3장).
    """

    kind = ""
    parser_classes = [MultiPartParser]

    def post(self, request, sgg_id: str | None = None, dong_id: str | None = None):
        t = community.target(self.kind, sgg_id or dong_id)
        if t is None:
            return not_found("사진을 올릴 지역 · 동네를 찾을 수 없습니다.")
        raw = request.data.get("month")
        if not raw:
            return _bad_photo("month 는 'annual' 또는 '2025-01' ~ '2025-12' 입니다.")
        try:
            month, month_str = data.parse_month(raw)
            blobs = photo_upload.process(request.FILES.getlist("photos"), min_count=1)
        except data.BadMonth as ex:
            return _bad_photo(str(ex))
        except photo_upload.BadPhoto as ex:
            return _bad_photo(str(ex))
        user_id = request.user.user_id
        if _recent_count(user_id) + len(blobs) > PHOTO_HOURLY_LIMIT:
            return error(
                429,
                "PHOTO_RATE_LIMIT",
                f"사진은 1시간에 {PHOTO_HOURLY_LIMIT}장까지 올릴 수 있어요.",
            )
        try:
            uploaded = photo_upload.upload("photo", blobs)
        except photo_upload.UploadFailed:
            return error(
                502, "UPLOAD_FAILED", "사진을 올리지 못했어요. 잠시 후 다시 시도해 주세요."
            )
        try:
            ids = _insert(t, month, user_id, [url for _, url in uploaded])
        except Exception:
            photo_upload.discard([key for key, _ in uploaded])
            raise
        region_name = data.regions_by_id()[t.region_id].sigungu_name
        items = [
            {
                "id": str(pid),
                "url": url,
                "month": month_str,
                "regionName": region_name,
                "status": "approved",
            }
            for pid, (_, url) in zip(ids, uploaded, strict=True)
        ]
        return Response({"items": items}, status=201)


def _recent_count(user_id) -> int:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM photo WHERE user_id = %s AND tip_id IS NULL"
            " AND created_at > now() - interval '1 hour'",
            [user_id],
        )
        return cur.fetchone()[0]


def _insert(t: community.Target, month: int | None, user_id, urls: list[str]) -> list[int]:
    """사진 행을 한 문장으로 넣는다 → photo_id (urls 순서)."""
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO photo (scope, unit_id, month, url, source, user_id, status)
               SELECT %(scope)s, %(unit)s, %(month)s, u.url, 'user', %(user)s, 'approved'
                 FROM unnest(%(urls)s::text[]) WITH ORDINALITY AS u(url, n)
               RETURNING photo_id, url""",
            {"scope": t.scope, "unit": t.unit_id, "month": month, "user": user_id, "urls": urls},
        )
        by_url = {url: pid for pid, url in cur.fetchall()}
    return [by_url[url] for url in urls]


class PhotoReportView(MeAPIView):
    """POST /api/photos/{photoId}/report {reason}. 204.

    같은 사람의 다시 신고도 204 (한 번만 센다).
    """

    def post(self, request, photo_id: str):
        reason = request.data.get("reason") if isinstance(request.data, dict) else None
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= REASON_MAX_LEN:
            return bad_request(f"reason 은 1~{REASON_MAX_LEN}자입니다.")
        pid = photo_id_or_none(photo_id)
        found, hid = _report(pid, request.user.user_id, reason.strip()) if pid else (False, False)
        if not found:
            return not_found("사진을 찾을 수 없습니다.")
        if hid:  # 가렸으면 원래 URL 도 막는다. 실패해도 응답은 204 (스펙 4-4)
            photo_quarantine.try_quarantine(pid)
        return Response(status=204)


def photo_id_or_none(raw: str) -> int | None:
    """경로의 photoId. photo_id 는 SERIAL 이라 그 범위의 숫자만 본다."""
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 9 else None


def _report(photo_id: int, user_id, reason: str) -> tuple[bool, bool]:
    """신고를 넣고, 신고 수가 기준에 닿으면 가린다 → (공개 사진이 있었나, 이번에 가렸나).

    한 문장(= 한 트랜잭션). CTE 는 같은 문장에서 넣은 행을 못 보므로
    기존 신고 수 + 이번에 넣은 수로 센다.
    """
    with connection.cursor() as cur:
        cur.execute(
            """WITH p AS (
                 SELECT photo_id FROM photo
                  WHERE photo_id = %(id)s AND status = 'approved' FOR UPDATE
               ), ins AS (
                 INSERT INTO report (target_type, target_id, user_id, reason)
                 SELECT 'photo', photo_id, %(user)s, %(reason)s FROM p
                 ON CONFLICT DO NOTHING
                 RETURNING 1
               ), up AS (
                 UPDATE photo SET status = 'pending'
                  WHERE photo_id IN (SELECT photo_id FROM p)
                    AND (SELECT count(*) FROM report
                          WHERE target_type = 'photo' AND target_id = %(id)s)
                        + (SELECT count(*) FROM ins) >= %(threshold)s
                 RETURNING 1
               )
               SELECT (SELECT count(*) FROM p), (SELECT count(*) FROM up)""",
            {
                "id": photo_id,
                "user": user_id,
                "reason": reason,
                "threshold": settings.PHOTO_REPORT_THRESHOLD,
            },
        )
        found, hid = cur.fetchone()
    return found > 0, hid > 0


class MyPhotoView(MeAPIView):
    """DELETE /api/me/photos/{photoId}. 204. 내 이달의 사진이 아니면 404 (스펙 4-6)."""

    def delete(self, request, photo_id: str):
        pid = photo_id_or_none(photo_id)
        try:
            deleted = pid is not None and photo_quarantine.delete_mine(request.user.user_id, pid)
        except photo_quarantine.DeleteFailed:
            return error(
                502, "DELETE_FAILED", "사진을 지우지 못했어요. 잠시 후 다시 시도해 주세요."
            )
        if not deleted:
            return not_found("내 사진을 찾을 수 없습니다.")
        return Response(status=204)
