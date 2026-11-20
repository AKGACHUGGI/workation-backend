"""가린 사진 격리 · 복구 · 숨김 · 영구 삭제 · 사용자 삭제 (스펙 4-6 · 4-7)."""

import pytest

from tests.photo_tools import PUBLIC, r2, user_photo  # noqa: F401
from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import OTHER_ID, me, run  # noqa: F401
from webapi import photo_quarantine as pq
from webapi import storage

pytestmark = pytest.mark.django_db


def tour_photo(status="pending"):
    """photo 표의 관광공사 사진 (버킷 주인 파일 photos/). 격리 모듈은 건드리지 않는다."""
    return run(
        "INSERT INTO photo (scope, unit_id, url, source, credit, status)"
        " VALUES ('region', 1, %s, 'tourapi', '한국관광공사', %s) RETURNING photo_id",
        [f"{PUBLIC}/photos/1.webp", status],
    )[0][0]


def tour_row(pid):
    return run("SELECT url, status FROM photo WHERE photo_id = %s", [pid])[0]


def col(pid, name):
    return run(f"SELECT {name} FROM user_photo WHERE user_photo_id = %s", [pid])[0][0]


def key_now(pid):
    return pq.key_of(col(pid, "url"))


def test_key_of(r2):  # noqa: F811
    assert pq.key_of(f"{PUBLIC}/uploads/photo/a.webp") == "uploads/photo/a.webp"
    assert pq.key_of(f"{PUBLIC}/photos/1.webp") is None  # 버킷 주인 파일
    # 공개 도메인이 바뀌어도 경로로 알아본다
    assert pq.key_of("https://cdn.example/uploads/photo/a.webp") == "uploads/photo/a.webp"


def test_quarantine_moves_file(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "pending")
    assert pq.quarantine(pid) is True
    new = key_now(pid)
    assert new.startswith(pq.QUARANTINE) and pq.is_quarantined(col(pid, "url"))
    assert key not in r2.objects and r2.objects[new] == (b"img", "image/webp")
    assert pq.quarantine(pid) is False  # 이미 격리됨
    assert key_now(pid) == new


def test_quarantine_skips(me, r2):  # noqa: F811
    approved, key = user_photo(r2, "approved")
    assert pq.quarantine(approved) is False and key in r2.objects
    assert pq.quarantine(999999) is False


def test_tour_photos_untouched(me, r2):  # noqa: F811
    """photo 표의 관광공사 사진은 id 가 같아도 격리 · 숨김 · purge 대상이 아니다."""
    tour = tour_photo("approved")
    run("SELECT setval('user_photo_user_photo_id_seq', %s, false)", [tour])
    pid, _ = user_photo(r2, "pending")
    assert pid == tour  # 두 표의 id 가 겹친다
    run(
        "INSERT INTO report (target_type, target_id, user_id, reason)"
        " VALUES ('photo', %s, %s, 'x')",
        [tour, OTHER_ID],
    )
    pq.hide(pid)
    run(
        "UPDATE user_photo SET hidden_at = now() - interval '31 days' WHERE user_photo_id = %s",
        [pid],
    )
    assert pq.purge() == (1, [])
    assert tour_row(tour) == (f"{PUBLIC}/photos/1.webp", "approved")
    assert run("SELECT target_type FROM report WHERE target_id = %s", [tour]) == [("photo",)]


def test_quarantine_copy_failure_changes_nothing(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "pending")
    r2.fail_copy = True
    with pytest.raises(storage.StorageError):
        pq.quarantine(pid)
    assert key_now(pid) == key and key in r2.objects
    pq.try_quarantine(pid)  # 삼키고 로그만
    assert key_now(pid) == key


def test_quarantine_original_delete_failure_still_moves(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "pending")
    r2.fail_delete = True
    assert pq.quarantine(pid) is True
    assert pq.is_quarantined(col(pid, "url"))  # url 은 격리 위치 (원래 파일 삭제만 실패, 로그)


def test_hide(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "approved")
    assert pq.hide(pid) is True
    assert col(pid, "status") == "hidden" and col(pid, "hidden_at") is not None
    assert pq.is_quarantined(col(pid, "url")) and key not in r2.objects
    assert pq.hide(pid) is True  # 다시 해도 된다 (격리 실패 뒤 재시도용)
    assert pq.hide(999999) is False


def test_restore(me, r2):  # noqa: F811
    tip = run(
        "INSERT INTO tip (user_id, region_id, body) VALUES (%s, 1, '팁') RETURNING tip_id", [me]
    )[0][0]
    pid, _ = user_photo(r2, "pending", tip_id=tip)
    pq.quarantine(pid)
    q_key = key_now(pid)
    run(
        "INSERT INTO report (target_type, target_id, user_id, reason) VALUES (%s, %s, %s, 'x')",
        [pq.REPORT_TYPE, pid, OTHER_ID],
    )
    assert pq.restore(pid) is True
    assert col(pid, "status") == "approved"
    assert key_now(pid).startswith("uploads/tip/")  # 노트 사진은 tip 폴더로
    assert q_key not in r2.objects and key_now(pid) in r2.objects
    assert run("SELECT count(*) FROM report WHERE target_id = %s", [pid]) == [(0,)]
    assert pq.restore(pid) is False  # pending 이 아니다


def test_purge(me, r2, monkeypatch):  # noqa: F811
    old, _ = user_photo(r2, "pending")
    fresh, _ = user_photo(r2, "pending")
    stuck, _ = user_photo(r2, "pending")
    for pid in (old, fresh, stuck):
        pq.hide(pid)
    run(
        "UPDATE user_photo SET hidden_at = now() - interval '31 days'"
        " WHERE user_photo_id IN (%s, %s)",
        [old, stuck],
    )
    old_key, stuck_key = key_now(old), key_now(stuck)

    real_delete = r2.delete

    def delete(key):  # stuck 의 파일만 지우지 못한다
        if key == stuck_key:
            raise storage.StorageError("가짜 실패")
        real_delete(key)

    monkeypatch.setattr(storage, "delete", delete)
    assert pq.purge() == (1, [stuck])
    assert run("SELECT user_photo_id FROM user_photo ORDER BY 1") == sorted([(fresh,), (stuck,)])
    assert old_key not in r2.objects and stuck_key in r2.objects


def test_delete_mine_approved(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "approved")
    r2.fail_delete = True
    with pytest.raises(pq.DeleteFailed):
        pq.delete_mine(me, pid)
    assert col(pid, "status") == "approved"  # 행은 그대로, 다시 시도하면 된다
    r2.fail_delete = False
    assert pq.delete_mine(me, pid) is True
    assert run("SELECT count(*) FROM user_photo") == [(0,)] and key not in r2.objects


def test_delete_mine_hidden_by_report_meanwhile(me, r2, monkeypatch):  # noqa: F811
    """처음 읽을 때는 공개였는데 지우기 직전에 신고로 가려졌으면 증거로 남긴다."""
    pid, key = user_photo(r2, "approved")
    real_one = pq._one
    reported = []

    def one_then_report(sql, params):
        row = real_one(sql, params)
        if not reported:  # 처음 읽은 직후 한 번만 가려진다
            reported.append(1)
            run("UPDATE user_photo SET status = 'pending' WHERE user_photo_id = %s", [pid])
        return row

    monkeypatch.setattr(pq, "_one", one_then_report)
    assert pq.delete_mine(me, pid) is True
    assert (col(pid, "status"), col(pid, "deleted_by_user")) == ("hidden", True)


def test_delete_mine_pending_is_kept_as_evidence(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "pending")  # 아직 격리 전
    assert pq.delete_mine(me, pid) is True
    assert (col(pid, "status"), col(pid, "deleted_by_user")) == ("hidden", True)
    assert col(pid, "hidden_at") is not None
    assert pq.is_quarantined(col(pid, "url")) and key not in r2.objects
    assert pq.delete_mine(me, pid) is False  # hidden 은 다시 못 지운다


def test_delete_mine_not_mine(me, r2):  # noqa: F811
    others, _ = user_photo(r2, "approved", user_id=OTHER_ID)
    tip = run(
        "INSERT INTO tip (user_id, region_id, body) VALUES (%s, 1, '팁') RETURNING tip_id", [me]
    )[0][0]
    note_photo, _ = user_photo(r2, "approved", tip_id=tip)
    assert pq.delete_mine(me, others) is False
    assert pq.delete_mine(me, note_photo) is False  # 노트 사진은 노트를 지운다
    assert pq.delete_mine(me, 999999) is False
