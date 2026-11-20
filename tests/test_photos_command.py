"""운영자 검수 명령 manage.py photos (스펙 5절)."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.photo_tools import r2  # noqa: F401
from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import OTHER_ID, me, run  # noqa: F401
from tests.test_photo_quarantine import user_photo
from webapi import photo_quarantine

pytestmark = pytest.mark.django_db


def photos_cmd(*args) -> str:
    out = StringIO()
    call_command("photos", *args, stdout=out, stderr=out)
    return out.getvalue()


def col(pid, name):
    return run(f"SELECT {name} FROM photo WHERE photo_id = %s", [pid])[0][0]


def test_list(me, r2):  # noqa: F811
    moved, _ = user_photo(r2, "pending")
    photo_quarantine.quarantine(moved)
    open_, _ = user_photo(r2, "pending")  # 격리 실패로 남은 사진
    public, _ = user_photo(r2, "approved")
    run(
        "INSERT INTO report (target_type, target_id, user_id, reason)"
        " VALUES ('photo', %s, %s, '광고'), ('photo', %s, %s, '무관한 사진')",
        [moved, me, moved, OTHER_ID],
    )
    out = photos_cmd("list")
    lines = {line.split("\t")[0]: line for line in out.splitlines() if "\t" in line}
    assert set(lines) == {str(moved), str(open_)}  # 공개 사진은 안 나온다
    assert "고성군" in lines[str(moved)] and "신고 2" in lines[str(moved)]
    assert "uploads/quarantine/" in lines[str(moved)] and "URL 열림" not in lines[str(moved)]
    assert "URL 열림" in lines[str(open_)]
    assert "광고 / 무관한 사진" in out
    assert str(public) not in lines


def test_restore_and_hide(me, r2):  # noqa: F811
    a, _ = user_photo(r2, "pending")
    b, _ = user_photo(r2, "approved")
    photo_quarantine.quarantine(a)
    assert "1장 복구" in photos_cmd("restore", str(a), "999999")
    assert col(a, "status") == "approved" and not photo_quarantine.is_quarantined(col(a, "url"))
    out = photos_cmd("hide", str(a), str(b))
    assert "2장 숨김" in out
    for pid in (a, b):
        assert col(pid, "status") == "hidden" and photo_quarantine.is_quarantined(col(pid, "url"))


def test_hide_r2_failure_reported_and_retryable(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "approved")
    r2.fail_copy = True
    out = photos_cmd("hide", str(pid))
    assert "R2 실패" in out and col(pid, "status") == "hidden" and key in r2.objects
    r2.fail_copy = False
    assert "1장 숨김" in photos_cmd("hide", str(pid))  # 다시 하면 격리된다
    assert photo_quarantine.is_quarantined(col(pid, "url"))


def test_purge(me, r2):  # noqa: F811
    pid, _ = user_photo(r2, "approved")
    photos_cmd("hide", str(pid))
    assert "0장 영구 삭제" in photos_cmd("purge")
    run("UPDATE photo SET hidden_at = now() - interval '3 days'")
    assert "0장 영구 삭제" in photos_cmd("purge")
    assert "1장 영구 삭제" in photos_cmd("purge", "--days", "2")
    assert run("SELECT count(*) FROM photo") == [(0,)]


def test_needs_ids():
    with pytest.raises(CommandError):
        photos_cmd("hide")
