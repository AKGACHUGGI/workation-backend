"""가린 사진 격리 · 복구 · 숨김 · 영구 삭제, 올린 사람의 삭제 (스펙 4-6 · 4-7).

R2 버킷은 r2.dev 로 통째로 공개라 DB 상태만 바꾸면 URL 을 아는 사람은 계속 본다.
그래서 가린 사용자 사진은 추측할 수 없는 새 key(uploads/quarantine/)로 옮기고 원래 key 를 지운다.
관광공사 · 팀 사진(photos/)은 버킷 주인 파일이라 옮기지도 지우지도 않고 상태만 바꾼다.
뷰(uploads · community)와 manage.py photos 가 같이 쓴다. 관리자 화면을 만들면 여기를 부른다.
"""

from __future__ import annotations

import logging
import uuid
from urllib.parse import urlsplit

from django.db import connection, transaction

from webapi import storage

logger = logging.getLogger(__name__)

QUARANTINE = "uploads/quarantine/"
RETENTION_DAYS = 30  # hidden 이 된 뒤 영구 삭제까지


class DeleteFailed(RuntimeError):
    """R2 파일을 지우지 못했다 (502 DELETE_FAILED). DB 는 바꾸지 않았다."""


def key_of(url: str) -> str | None:
    """우리가 올린 사진(uploads/)의 R2 key. 관광공사 · 팀 사진(photos/)은 None.

    공개 도메인(R2_PUBLIC_URL)을 r2.dev 에서 바꿔도 예전 행을 알아보도록 URL 의 경로만 본다.
    """
    key = urlsplit(url).path.lstrip("/")
    return key if key.startswith("uploads/") else None


def is_quarantined(url: str) -> bool:
    key = key_of(url)
    return key is not None and key.startswith(QUARANTINE)


def _one(sql: str, params) -> tuple | None:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() if cur.description else None


def _move(photo_id: int, src: str, folder: str) -> None:
    """src 를 uploads/{folder}/{uuid}.webp 로 복사 → photo.url 갱신 → src 삭제.

    복사가 실패하면 StorageError 이고 아무것도 바뀌지 않는다. src 삭제 실패는 로그만
    (url 은 이미 새 위치라 화면 · 명령은 새 파일을 본다).
    """
    dst = f"uploads/{folder}/{uuid.uuid4()}.webp"
    storage.copy(src, dst)
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE photo SET url = %s WHERE photo_id = %s", [storage.public_url(dst), photo_id]
        )
    try:
        storage.delete(src)
    except storage.StorageError:
        logger.warning("R2 원래 파일 삭제 실패 (photo %s): %s", photo_id, src)


def quarantine(photo_id: int) -> bool:
    """가린(pending · hidden) 사용자 사진의 파일을 격리한다. 옮겼으면 True.

    공개 사진 · 관광공사 사진 · 이미 격리된 사진은 그대로 둔다. R2 실패는 StorageError.
    """
    row = _one("SELECT url, status FROM photo WHERE photo_id = %s AND source = 'user'", [photo_id])
    if row is None or row[1] == "approved":
        return False
    key = key_of(row[0])
    if key is None or key.startswith(QUARANTINE):
        return False
    _move(photo_id, key, "quarantine")
    return True


def try_quarantine(photo_id: int) -> None:
    """요청 처리 중의 격리. 실패해도 로그만 남긴다 — photos list 가 'URL 열림' 으로 보여 주고
    photos hide 가 다시 격리한다."""
    try:
        quarantine(photo_id)
    except storage.StorageError:
        logger.warning("격리 실패 (photo %s)", photo_id, exc_info=True)


def hide(photo_id: int) -> bool:
    """숨김 확정 후 격리. 없는 사진이면 False. 이미 hidden 이면 격리만 다시 한다(재시도용).

    R2 실패는 StorageError (상태는 hidden 으로 남는다).
    """
    row = _one(
        """UPDATE photo SET status = 'hidden', hidden_at = coalesce(hidden_at, now())
            WHERE photo_id = %s RETURNING photo_id""",
        [photo_id],
    )
    if row is None:
        return False
    quarantine(photo_id)
    return True


def restore(photo_id: int) -> bool:
    """pending 사진을 다시 공개한다. 격리 파일을 새 공개 key 로 옮기고(원래 key 는 캐시에 404 가
    남았을 수 있어 쓰지 않음) approved 로, 그 사진의 신고를 지운다. pending 이 아니면 False."""
    row = _one(
        "SELECT url, tip_id FROM photo WHERE photo_id = %s AND status = 'pending'", [photo_id]
    )
    if row is None:
        return False
    url, tip_id = row
    if is_quarantined(url):
        _move(photo_id, key_of(url), "tip" if tip_id else "photo")
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("UPDATE photo SET status = 'approved' WHERE photo_id = %s", [photo_id])
        cur.execute("DELETE FROM report WHERE target_type = 'photo' AND target_id = %s", [photo_id])
    return True


def purge(days: int = RETENTION_DAYS) -> tuple[int, list[int]]:
    """hidden 이 된 지 days 일 지난 사용자 사진을 영구 삭제 → (지운 수, R2 실패로 남긴 id).

    파일을 먼저 지우고 성공한 것만 행 · 신고를 지운다. 실패한 행은 다음 purge 때 다시.
    관광공사 · 팀 사진은 버킷 주인 파일이라 행도 남긴다.
    """
    with connection.cursor() as cur:
        cur.execute(
            """SELECT photo_id, url FROM photo
                WHERE status = 'hidden' AND source = 'user'
                  AND hidden_at < now() - make_interval(days => %s)""",
            [days],
        )
        rows = cur.fetchall()
    gone, failed = [], []
    for pid, url in rows:
        try:
            if key := key_of(url):
                storage.delete(key)
        except storage.StorageError:
            logger.warning("purge: R2 삭제 실패 (photo %s)", pid)
            failed.append(pid)
            continue
        gone.append(pid)
    if gone:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("DELETE FROM photo WHERE photo_id = ANY(%s)", [gone])
            cur.execute(
                "DELETE FROM report WHERE target_type = 'photo' AND target_id = ANY(%s)", [gone]
            )
    return len(gone), failed


def delete_files(urls: list[str]) -> None:
    """공개 사진 파일을 지운다. 하나라도 실패하면 DeleteFailed (다시 시도하면 된다 —
    S3 삭제는 없는 key 도 성공)."""
    for url in urls:
        if key := key_of(url):
            try:
                storage.delete(key)
            except storage.StorageError as ex:
                raise DeleteFailed(str(ex)) from ex


def delete_mine(user_id, photo_id: int) -> bool:
    """올린 사람이 이달의 사진을 지운다 (스펙 4-6).

    내 이달의 사진이 아니거나 이미 hidden 이면 False.

    공개 사진: 파일을 먼저 지우고(실패 → DeleteFailed, 행 그대로) 행 · 신고를 지운다.
    가려진(pending) 사진: 증거라 hidden · deleted_by_user 로 남기고 격리한다 (30일 뒤 purge).
    """
    row = _one(
        """SELECT url, status FROM photo
            WHERE photo_id = %s AND user_id = %s AND tip_id IS NULL AND status <> 'hidden'""",
        [photo_id, user_id],
    )
    if row is None:
        return False
    url, status = row
    if status == "approved":
        with transaction.atomic(), connection.cursor() as cur:
            # 신고(uploads._report 도 FOR UPDATE)와 엇갈려 그사이 가려졌으면 증거라 지우지 않는다
            cur.execute("SELECT status FROM photo WHERE photo_id = %s FOR UPDATE", [photo_id])
            now = cur.fetchone()
            if now is None:
                return False
            if now[0] == "approved":
                delete_files([url])  # 실패하면 DeleteFailed 로 되돌린다
                cur.execute("DELETE FROM photo WHERE photo_id = %s", [photo_id])
                cur.execute(
                    "DELETE FROM report WHERE target_type = 'photo' AND target_id = %s",
                    [photo_id],
                )
                return True
    with connection.cursor() as cur:
        cur.execute(
            """UPDATE photo SET status = 'hidden', hidden_at = now(), deleted_by_user = true
                WHERE photo_id = %s""",
            [photo_id],
        )
    try_quarantine(photo_id)
    return True
