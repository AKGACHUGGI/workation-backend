"""또래 밀도 개인화 검증용 더미 계정 (설계 2026-11-23-peer-personalization-design.md 5-5절).

  manage.py dummy_users create
      Supabase Auth 에 dummy-<연령대>@workcation.test 10개를 만들고(이미 있으면 그대로)
      app_user 에 provider='dummy' 행을 넣는다. 여러 번 실행해도 행이 늘지 않는다.
  manage.py dummy_users delete
      Supabase 계정을 먼저 지우고, 성공한 계정의 app_user 행을 지운다. 실패하면 종료 코드 1.
  manage.py dummy_users verify --base-url http://127.0.0.1:8000 --month 2025-10
      계정마다 로그인해 /api/tier(sigungu · dong) 의 scores.peer 를 DB metric 값과 맞춰 본다.

설정: SUPABASE_URL · SUPABASE_SERVICE_ROLE_KEY 는 django settings,
SUPABASE_ANON_KEY · DUMMY_USER_PASSWORD 는 환경 변수(.env)에서 읽는다.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from itertools import combinations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from accounts.services import AuthAdminError, delete_auth_user

TIMEOUT = 10
DOMAIN = "workcation.test"

# 연령대 → 또래 밀도 구간. 서버 상수(metrics.scoring.PEER_BASIS)가 틀렸을 때 검증도 함께 틀리지
# 않도록 scoring 을 import 하지 않고 설계 4-1절 표를 그대로 적는다. none = 연령대 없음(기본 peer).
ACCOUNTS: tuple[tuple[str, str | None, str], ...] = (
    # (이메일의 <age>, age_group, 화면 라벨)
    ("10s", "10s", "10대"),
    ("20s", "20s", "20대"),
    ("30s", "30s", "30대"),
    ("40s", "40s", "40대"),
    ("50s", "50s", "50대"),
    ("60s", "60s", "60대"),
    ("70s", "70s", "70대"),
    ("80s", "80s", "80대"),
    ("90s", "90s", "90대"),
    ("none", None, "없음"),
)
BUCKET_OF = {
    "10s": "under20",
    "20s": "20s",
    "30s": "30s",
    "40s": "40s",
    "50s": "50s",
    "60s": "60plus",
    "70s": "60plus",
    "80s": "60plus",
    "90s": "60plus",
}
SCOPES = ("sigungu", "dong")


def email_of(age: str) -> str:
    return f"dummy-{age}@{DOMAIN}"


# ───────────────────────── Supabase Admin API (테스트에서 바꿔 끼운다) ─────────────────────────


def _admin_headers() -> dict[str, str]:
    key = settings.SUPABASE_SERVICE_ROLE_KEY
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _send(req: urllib.request.Request) -> dict:
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def admin_create_or_get(email: str, password: str) -> str:
    """auth.users 에 이메일 계정을 만들고 id 를 돌려준다. 이미 있으면(422) 찾아서 그 id."""
    req = urllib.request.Request(
        f"{settings.SUPABASE_URL}/auth/v1/admin/users",
        method="POST",
        headers=_admin_headers(),
        data=json.dumps({"email": email, "password": password, "email_confirm": True}).encode(),
    )
    try:
        return _send(req)["id"]
    except urllib.error.HTTPError as ex:
        if ex.code != 422:
            raise AuthAdminError(f"계정 생성 실패 HTTP {ex.code}") from ex
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as ex:
        raise AuthAdminError(f"계정 생성 실패: {ex}") from ex
    uid = admin_find(email)
    if uid is None:
        raise AuthAdminError("422(이미 있음) 인데 계정을 찾지 못했습니다.")
    return uid


def admin_find(email: str) -> str | None:
    """auth.users 에서 이메일이 같은 사용자 id. 없으면 None."""
    req = urllib.request.Request(
        f"{settings.SUPABASE_URL}/auth/v1/admin/users?per_page=1000",
        method="GET",
        headers=_admin_headers(),
    )
    try:
        users = _send(req).get("users", [])
    except urllib.error.HTTPError as ex:
        raise AuthAdminError(f"계정 조회 실패 HTTP {ex.code}") from ex
    except (urllib.error.URLError, TimeoutError, ValueError) as ex:
        raise AuthAdminError(f"계정 조회 실패: {ex}") from ex
    target = email.lower()
    return next((u["id"] for u in users if (u.get("email") or "").lower() == target), None)


# ───────────────────────── 명령 ─────────────────────────


class Command(BaseCommand):
    help = "또래 밀도 검증용 더미 계정: create · delete · verify --base-url URL --month YYYY-MM"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["create", "delete", "verify"])
        parser.add_argument("--base-url", default="http://127.0.0.1:8000")
        parser.add_argument("--month", default=None, help="YYYY-MM (verify 에 필요)")

    def handle(self, *args, action, base_url, month, **options):
        if action == "create":
            self._require(url=True, service=True, password=True)
            return self._create()
        if action == "delete":
            self._require(url=True, service=True)
            return self._delete()
        self._require(url=True, anon=True, password=True)
        if not month or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise CommandError("--month 를 YYYY-MM 으로 주세요 (예: 2025-10).")
        return self._verify(base_url.rstrip("/"), month)

    def _require(self, url=False, service=False, anon=False, password=False):
        needed = []
        if url:
            needed.append(("SUPABASE_URL", settings.SUPABASE_URL))
        if service:
            needed.append(("SUPABASE_SERVICE_ROLE_KEY", settings.SUPABASE_SERVICE_ROLE_KEY))
        if anon:
            needed.append(("SUPABASE_ANON_KEY", os.environ.get("SUPABASE_ANON_KEY", "")))
        if password:
            needed.append(("DUMMY_USER_PASSWORD", os.environ.get("DUMMY_USER_PASSWORD", "")))
        missing = [name for name, value in needed if not value]
        if missing:
            raise CommandError(f"설정이 비어 있습니다: {', '.join(missing)}")

    # ───────── create ─────────

    def _create(self):
        password = os.environ["DUMMY_USER_PASSWORD"]
        failed = []
        for age, age_group, label in ACCOUNTS:
            email = email_of(age)
            try:
                uid = admin_create_or_get(email, password)
            except AuthAdminError as ex:
                failed.append((email, str(ex)))
                continue
            with transaction.atomic(), connection.cursor() as cur:
                # Supabase 쪽에서 다시 만들어져 id 가 바뀐 경우 옛 행을 먼저 지운다
                cur.execute(
                    "DELETE FROM app_user"
                    " WHERE provider = 'dummy' AND provider_uid = %s AND user_id <> %s",
                    [email, uid],
                )
                cur.execute(
                    "INSERT INTO app_user"
                    " (user_id, provider, provider_uid, nickname, agreed_at, age_group)"
                    " VALUES (%s, 'dummy', %s, %s, now(), %s)"
                    " ON CONFLICT (user_id) DO UPDATE SET age_group = EXCLUDED.age_group,"
                    " agreed_at = EXCLUDED.agreed_at, nickname = EXCLUDED.nickname",
                    [uid, email, f"더미{label}", age_group],
                )
            self.stdout.write(f"{email}  {uid}  age_group={age_group}")
        self._finish(failed, "생성", len(ACCOUNTS))

    # ───────── delete ─────────

    def _delete(self):
        failed = []
        for age, _, _ in ACCOUNTS:
            email = email_of(age)
            try:
                uid = admin_find(email)
                if uid is not None:
                    delete_auth_user(uid)
            except AuthAdminError as ex:
                # 행은 재시도 근거로 남긴다
                failed.append((email, str(ex)))
                continue
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM app_user WHERE provider = 'dummy' AND provider_uid = %s",
                    [email],
                )
            self.stdout.write(f"{email}  삭제" + ("" if uid else " (Supabase 계정 없음, 행만)"))
        self._finish(failed, "삭제", len(ACCOUNTS))

    def _finish(self, failed, verb, total):
        self.stdout.write(f"{total - len(failed)}/{total} {verb}")
        if failed:
            for email, reason in failed:
                self.stderr.write(f"실패 {email}: {reason}")
            raise CommandError(f"{len(failed)}개 {verb} 실패. 다시 실행하면 이어서 합니다.")

    # ───────── verify ─────────

    def _verify(self, base_url: str, month_str: str):
        month = int(month_str[5:])
        expected = self._expected(month)
        password = os.environ["DUMMY_USER_PASSWORD"]
        anon_key = os.environ["SUPABASE_ANON_KEY"]

        errors: list[str] = []
        responses: dict[str, dict[str, dict[str, int]]] = {}  # 주체 → scope → {id: peer}
        subjects: list[tuple[str, str | None]] = [("anonymous", None)]
        for age, _, _ in ACCOUNTS:
            email = email_of(age)
            try:
                subjects.append((age, self._login(email, password, anon_key)))
            except CommandError as ex:
                errors.append(f"{email} 로그인 실패: {ex}")

        for who, token in subjects:
            responses[who] = {}
            for scope in SCOPES:
                try:
                    responses[who][scope] = self._tier_peer(base_url, month_str, scope, token)
                except CommandError as ex:
                    errors.append(f"{who} {scope} 요청 실패: {ex}")

        rows = []
        mismatches = []
        for who, by_scope in responses.items():
            bucket = BUCKET_OF.get(who)
            key = f"peer_{bucket}" if bucket else "peer"
            for scope, got in by_scope.items():
                want = expected[scope].get(key, {})
                bad = [
                    (uid, want.get(uid, 0), v) for uid, v in got.items() if want.get(uid, 0) != v
                ]
                rows.append((who, scope, len(got) - len(bad), len(got)))
                mismatches += [(who, scope, *b) for b in bad]

        self.stdout.write(f"{'계정':<10} {'scope':<8} 일치/전체")
        for who, scope, ok, total in rows:
            self.stdout.write(f"{who:<10} {scope:<8} {ok}/{total}")

        # 검사 3: 구간 6개의 응답이 모든 쌍마다 적어도 한 단위에서 다르다
        by_bucket: dict[str, dict] = {}
        for age, bucket in BUCKET_OF.items():
            if age in responses and bucket not in by_bucket:
                by_bucket[bucket] = responses[age]
        for a, b in combinations(sorted(by_bucket), 2):
            if all(by_bucket[a].get(s) == by_bucket[b].get(s) for s in SCOPES):
                errors.append(f"구간 {a} 와 {b} 의 scores.peer 가 모든 단위에서 같습니다")
        missing_buckets = set(BUCKET_OF.values()) - set(by_bucket)
        if missing_buckets:
            errors.append(f"응답이 없는 구간: {', '.join(sorted(missing_buckets))}")

        for who, scope, uid, want, got in mismatches:
            self.stderr.write(f"어긋남 {who} {scope} {uid}: 기대 {want}, 실제 {got}")
        for e in errors:
            self.stderr.write(e)
        if mismatches or errors:
            raise CommandError(f"검증 실패: 어긋난 단위 {len(mismatches)}개, 오류 {len(errors)}개")
        self.stdout.write("검증 통과")

    def _expected(self, month: int) -> dict[str, dict[str, dict[str, int]]]:
        """{scope: {metric_key: {응답 id: score}}}. DB metric 을 직접 읽는다 (없으면 0)."""
        out: dict[str, dict[str, dict[str, int]]] = {s: {} for s in SCOPES}
        with connection.cursor() as cur:
            cur.execute(
                "SELECT r.sigungu_code, m.metric_key, m.score FROM metric m"
                " JOIN region r ON m.unit_id = r.region_id"
                " WHERE m.scope = 'region' AND m.month = %s"
                " AND (m.metric_key = 'peer' OR m.metric_key LIKE 'peer\\_%%')",
                [month],
            )
            for code, key, score in cur.fetchall():
                out["sigungu"].setdefault(key, {})[code] = score or 0
            cur.execute(
                "SELECT d.adm_code, m.metric_key, m.score FROM metric m"
                " JOIN dong d ON m.unit_id = d.dong_id"
                " WHERE m.scope = 'dong' AND m.month = %s AND d.adm_code IS NOT NULL"
                " AND (m.metric_key = 'peer' OR m.metric_key LIKE 'peer\\_%%')",
                [month],
            )
            for code, key, score in cur.fetchall():
                out["dong"].setdefault(key, {})[code] = score or 0
        return out

    def _login(self, email: str, password: str, anon_key: str) -> str:
        url = f"{settings.SUPABASE_URL}/auth/v1/token?grant_type=password"
        req = urllib.request.Request(
            url,
            method="POST",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            data=json.dumps({"email": email, "password": password}).encode(),
        )
        token = _http_json(req).get("access_token")
        if not token:
            raise CommandError("응답에 access_token 이 없습니다.")
        return token

    def _tier_peer(self, base_url: str, month: str, scope: str, token: str | None):
        query = urllib.parse.urlencode({"month": month, "scope": scope})
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        req = urllib.request.Request(f"{base_url}/api/tier?{query}", headers=headers)
        body = _http_json(req)
        return {item["id"]: item["scores"]["peer"] for item in body["items"]}


def _http_json(req: urllib.request.Request) -> dict:
    """요청을 보내 JSON 을 돌려준다. 실패하면 상태와 응답 본문을 담은 CommandError."""
    try:
        return _send(req)
    except urllib.error.HTTPError as ex:
        detail = ex.read().decode(errors="replace")[:500]
        raise CommandError(f"HTTP {ex.code} {detail}") from ex
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as ex:
        raise CommandError(str(ex)) from ex
