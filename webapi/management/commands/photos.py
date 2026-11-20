"""신고로 가려진 사용자 사진(user_photo) 검수 (스펙 5절). 일은 webapi/photo_quarantine.py 가 한다.

  manage.py photos list               가려진(pending) 사진 · 신고 사유 · 격리 위치 URL
  manage.py photos restore <id…>      다시 공개 (파일을 공개 위치로, 신고 삭제)
  manage.py photos hide <id…>         숨김 확정 + 격리 (공개 중인 사진도 바로 내릴 수 있다)
  manage.py photos purge [--days 30]  숨김 뒤 30일 지난 사용자 사진을 영구 삭제

R2 가 실패한 id 는 다시 실행하면 된다.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from webapi import photo_quarantine, storage

ACTIONS = {"restore": (photo_quarantine.restore, "복구"), "hide": (photo_quarantine.hide, "숨김")}


class Command(BaseCommand):
    help = "신고로 가려진 사진 검수: list · restore <id…> · hide <id…> · purge [--days 30]"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["list", "restore", "hide", "purge"])
        parser.add_argument("ids", nargs="*", type=int)
        parser.add_argument("--days", type=int, default=photo_quarantine.RETENTION_DAYS)

    def handle(self, *args, action, ids, days, **options):
        if action == "list":
            return self._list()
        if action == "purge":
            n, failed = photo_quarantine.purge(days)
            self.stdout.write(f"{n}장 영구 삭제")
            if failed:
                self.stdout.write(f"R2 실패로 남김 (다음에 다시): {_ids(failed)}")
            return
        if not ids:
            raise CommandError("사진 id 를 하나 이상 주세요.")
        fn, verb = ACTIONS[action]
        done, skipped, failed = [], [], []
        for pid in ids:
            try:
                (done if fn(pid) else skipped).append(pid)
            except storage.StorageError as ex:
                failed.append(pid)
                self.stderr.write(f"{pid}: R2 실패 — {ex}")
        self.stdout.write(f"{len(done)}장 {verb}: {_ids(done)}")
        if skipped:
            self.stdout.write(f"건너뜀 (없거나 상태가 맞지 않음): {_ids(skipped)}")
        if failed:
            self.stdout.write(f"R2 실패 (다시 실행하세요): {_ids(failed)}")

    def _list(self):
        with connection.cursor() as cur:
            cur.execute(
                """SELECT p.user_photo_id, coalesce(r.sigungu_name, d.dong_name, '?'),
                          p.tip_id IS NOT NULL, p.url, u.nickname,
                          count(rp.report_id),
                          coalesce(string_agg(rp.reason, ' / ' ORDER BY rp.created_at), '')
                     FROM user_photo p
                     LEFT JOIN region r ON p.scope = 'region' AND r.region_id = p.unit_id
                     LEFT JOIN dong d ON p.scope = 'dong' AND d.dong_id = p.unit_id
                     LEFT JOIN app_user u ON u.user_id = p.user_id
                     LEFT JOIN report rp ON rp.target_type = %s
                                        AND rp.target_id = p.user_photo_id
                    WHERE p.status = 'pending'
                    GROUP BY p.user_photo_id, r.sigungu_name, d.dong_name, u.nickname
                    ORDER BY p.user_photo_id""",
                [photo_quarantine.REPORT_TYPE],
            )
            rows = cur.fetchall()
        if not rows:
            self.stdout.write("가려진 사진이 없습니다.")
        for pid, name, is_note, url, nickname, n, reasons in rows:
            where = f"{name} (노트)" if is_note else name
            flag = ""
            if not photo_quarantine.is_quarantined(url):
                flag = "\t※ URL 열림 — hide 로 다시 격리"
            self.stdout.write(f"{pid}\t{where}\t신고 {n}\t{nickname or '-'}\t{url}{flag}")
            self.stdout.write(f"    사유: {reasons or '-'}")


def _ids(ids) -> str:
    return ", ".join(map(str, sorted(ids))) or "-"
