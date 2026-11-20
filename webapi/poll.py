"""체감 투표 (스펙 4-5). GET /api/{regions|dongs}/{id}/poll · POST .../poll/vote.

질문 하나 = (scope, unit_id, month, preset) 에 사용자당 한 표 (poll_vote).
month 는 DB 에서 0 = 연간. 동은 preset 이 없어 '' 로 저장한다.
"""

from django.db import connection
from rest_framework.response import Response

from accounts.views import LoginAPIView
from core.errors import bad_request, not_found
from webapi import community, data
from webapi.me import MeAPIView

PRESETS = ("quiet", "lively", "custom")
TAILS = {"quiet": "실제로 조용했나요?", "lively": "실제로 활기 있었나요?"}
PHOTO_LIMIT = 6
CHOICES = {"yes": True, "no": False, None: None}


class BadPoll(ValueError):
    pass


def question(month: int | None, name: str, preset: str) -> str:
    """웹 mock(getPollMock)과 같은 문구. '10월 고성군, 실제로 조용했나요?'"""
    label = f"{month}월" if month else "연간"
    return f"{label} {name}, {TAILS.get(preset, '어땠나요?')}"


def _preset(scope: str, raw) -> str:
    if scope == "dong":
        return ""
    if raw not in PRESETS:
        raise BadPoll(f"preset 은 {', '.join(PRESETS)} 중 하나입니다.")
    return raw


def _month(raw) -> int | None:
    if not isinstance(raw, str) or not raw:
        raise BadPoll("month 는 'annual' 또는 '2025-01' ~ '2025-12' 입니다.")
    try:
        return data.parse_month(raw)[0]
    except data.BadMonth as ex:
        raise BadPoll(str(ex)) from ex


def result(t: community.Target, month: int | None, preset: str, user_id) -> dict:
    key = {"scope": t.scope, "unit": t.unit_id, "month": month or 0, "preset": preset}
    with connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FILTER (WHERE choice), count(*) FILTER (WHERE NOT choice),
                      bool_or(choice) FILTER (WHERE user_id = %(user)s)
                 FROM poll_vote
                WHERE scope = %(scope)s AND unit_id = %(unit)s
                  AND month = %(month)s AND preset = %(preset)s""",
            key | {"user": user_id},
        )
        yes, no, mine = cur.fetchone()
        cur.execute(
            """SELECT p.user_photo_id, p.url, coalesce(u.nickname, '')
                 FROM user_photo p LEFT JOIN app_user u ON u.user_id = p.user_id
                WHERE p.status = 'approved' AND p.tip_id IS NULL
                  AND p.scope = %(scope)s AND p.unit_id = %(unit)s
                  AND (%(month)s::int IS NULL OR p.month = %(month)s)
                ORDER BY p.created_at DESC, p.user_photo_id DESC
                LIMIT %(limit)s""",
            {"scope": t.scope, "unit": t.unit_id, "month": month, "limit": PHOTO_LIMIT},
        )
        photos = [{"id": str(pid), "url": url, "author": a} for pid, url, a in cur.fetchall()]
    return {
        "question": question(month, t.name, preset),
        "yes": yes,
        "no": no,
        "total": yes + no,
        "photos": photos,
        "myVote": None if mine is None else ("yes" if mine else "no"),
    }


def save_vote(user_id, t: community.Target, month: int | None, preset: str, choice) -> None:
    """choice True/False 면 넣거나 바꾸고, None 이면 지운다 (없어도 그대로)."""
    key = {
        "user": user_id,
        "scope": t.scope,
        "unit": t.unit_id,
        "month": month or 0,
        "preset": preset,
    }
    with connection.cursor() as cur:
        if choice is None:
            cur.execute(
                """DELETE FROM poll_vote WHERE user_id = %(user)s AND scope = %(scope)s
                     AND unit_id = %(unit)s AND month = %(month)s AND preset = %(preset)s""",
                key,
            )
        else:
            cur.execute(
                """INSERT INTO poll_vote (user_id, scope, unit_id, month, preset, choice)
                   VALUES (%(user)s, %(scope)s, %(unit)s, %(month)s, %(preset)s, %(choice)s)
                   ON CONFLICT (user_id, scope, unit_id, month, preset)
                   DO UPDATE SET choice = EXCLUDED.choice, created_at = now()""",
                key | {"choice": choice},
            )


class PollView(LoginAPIView):
    """GET /api/{regions|dongs}/{id}/poll?month=&preset=. 로그인하면 myVote 가 채워진다."""

    kind = ""

    def get(self, request, sgg_id: str | None = None, dong_id: str | None = None):
        t = community.target(self.kind, sgg_id or dong_id)
        if t is None:
            return not_found("투표 대상을 찾을 수 없습니다.")
        q = request.query_params
        try:
            month = _month(q.get("month") or "annual")
            preset = _preset(t.scope, q.get("preset"))
        except BadPoll as ex:
            return bad_request(str(ex))
        user_id = request.user.user_id if request.user else None
        return Response(result(t, month, preset, user_id))


class PollVoteView(MeAPIView):
    """POST /api/{regions|dongs}/{id}/poll/vote {month, preset?, choice}. 200 + 갱신된 투표."""

    kind = ""

    def post(self, request, sgg_id: str | None = None, dong_id: str | None = None):
        t = community.target(self.kind, sgg_id or dong_id)
        if t is None:
            return not_found("투표 대상을 찾을 수 없습니다.")
        body = request.data if isinstance(request.data, dict) else {}
        choice = body.get("choice", ...)  # 키가 없으면 Ellipsis → CHOICES 에 없어 400
        try:
            month = _month(body.get("month"))
            preset = _preset(t.scope, body.get("preset"))
            # 리스트 · dict 는 해시가 안 돼 in 검사 전에 거른다
            if not (choice is None or isinstance(choice, str)) or choice not in CHOICES:
                raise BadPoll("choice 는 'yes', 'no', null 중 하나입니다.")
        except BadPoll as ex:
            return bad_request(str(ex))
        user_id = request.user.user_id
        save_vote(user_id, t, month, preset, CHOICES[choice])
        return Response(result(t, month, preset, user_id))
