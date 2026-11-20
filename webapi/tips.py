"""워케이션 노트의 도움돼요 · 답글 (/api/tips/{id}/*, /api/me/replies/{id}).

표: tip_reaction (도움돼요, 사용자당 1번) · tip_reply (답글, status public · hidden).
공개 팁에만 누르고 답글을 단다. 답글 읽기는 로그인 없이 된다.
"""

from django.db import connection, transaction
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import bad_request, error, not_found
from webapi import community
from webapi.me import MeAPIView, int_id
from webapi.views import viewer_id

REPLY_MAX_LEN = 300
REPLY_BURST_LIMIT = 5  # 사용자당 1분에 답글 수
REPLY_BURST_WINDOW = "1 minute"


def _public_tip(tip_id: int):
    """공개 팁의 작성자 user_id, 없으면 None."""
    with connection.cursor() as cur:
        cur.execute("SELECT user_id FROM tip WHERE tip_id = %s AND status = 'public'", [tip_id])
        row = cur.fetchone()
    return row[0] if row else None


def _tip_not_found() -> Response:
    return not_found("팁을 찾을 수 없습니다.")


# ───────────────────────── 도움돼요 ─────────────────────────


def _helpful_count(cur, tip_id: int) -> int:
    cur.execute("SELECT count(*) FROM tip_reaction WHERE tip_id = %s", [tip_id])
    return cur.fetchone()[0]


class HelpfulView(MeAPIView):
    """PUT · DELETE /api/tips/{id}/helpful. 200 {helpful, myHelpful}. 여러 번 불러도 결과가 같다."""

    def put(self, request, tip_id: str):
        tid = int_id(tip_id)
        author = _public_tip(tid) if tid is not None else None
        if author is None:
            return _tip_not_found()
        if author == request.user.user_id:
            return error(400, "OWN_TIP", "내 팁에는 도움돼요를 누를 수 없어요.")
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO tip_reaction (tip_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [tid, request.user.user_id],
            )
            return Response({"helpful": _helpful_count(cur, tid), "myHelpful": True})

    def delete(self, request, tip_id: str):
        tid = int_id(tip_id)
        if tid is None or _public_tip(tid) is None:
            return _tip_not_found()
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM tip_reaction WHERE tip_id = %s AND user_id = %s",
                [tid, request.user.user_id],
            )
            return Response({"helpful": _helpful_count(cur, tid), "myHelpful": False})


# ───────────────────────── 답글 ─────────────────────────


def _reply(row: tuple, viewer) -> dict:
    reply_id, user_id, nickname, created_at, body = row
    reply = {
        "id": str(reply_id),
        "author": {"nickname": nickname, "initial": nickname[:1]},
        "createdAt": community.iso(created_at),
        "body": body,
    }
    if viewer is not None and user_id == viewer:
        reply["mine"] = True
    return reply


@api_view(["GET"])
def replies(request, tip_id: str):
    """GET /api/tips/{id}/replies. {items: Reply[]} 공개 답글, 오래된 순."""
    tid = int_id(tip_id)
    if tid is None or _public_tip(tid) is None:
        return _tip_not_found()
    with connection.cursor() as cur:
        cur.execute(
            """SELECT p.reply_id, p.user_id, u.nickname, p.created_at, p.body
                 FROM tip_reply p JOIN app_user u ON u.user_id = p.user_id
                WHERE p.tip_id = %s AND p.status = 'public'
                ORDER BY p.created_at, p.reply_id""",
            [tid],
        )
        rows = cur.fetchall()
    viewer = viewer_id(request)
    return Response({"items": [_reply(r, viewer) for r in rows]})


class ReplyCreateView(MeAPIView):
    """POST /api/tips/{id}/replies {body}. 201 + 만든 답글(Reply). 같은 경로의 GET 은 replies."""

    def post(self, request, tip_id: str):
        tid = int_id(tip_id)
        if tid is None or _public_tip(tid) is None:
            return _tip_not_found()
        body = request.data.get("body") if isinstance(request.data, dict) else None
        if not isinstance(body, str) or not body.strip():
            return bad_request("답글 내용을 입력해 주세요.")
        body = body.strip()
        if len(body) > REPLY_MAX_LEN:
            return bad_request(f"답글은 {REPLY_MAX_LEN}자까지 쓸 수 있어요.")
        user = request.user
        with transaction.atomic(), connection.cursor() as cur:
            # 같은 사용자의 답글 쓰기를 줄 세워 한도 세기와 넣기 사이에 끼어들지 않게 한다
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"reply:{user.user_id}"])
            cur.execute(
                f"""SELECT count(*) FROM tip_reply
                     WHERE user_id = %s AND created_at > now() - interval '{REPLY_BURST_WINDOW}'""",
                [user.user_id],
            )
            if cur.fetchone()[0] >= REPLY_BURST_LIMIT:
                return error(
                    429,
                    "REPLY_RATE_LIMIT",
                    "답글을 너무 빠르게 올리고 있어요. 잠시 후 다시 시도해 주세요.",
                )
            cur.execute(
                """INSERT INTO tip_reply (tip_id, user_id, body) VALUES (%s, %s, %s)
                   RETURNING reply_id, created_at""",
                [tid, user.user_id, body],
            )
            reply_id, created_at = cur.fetchone()
        return Response(
            _reply((reply_id, user.user_id, user.nickname, created_at, body), user.user_id),
            status=201,
        )


class MyReplyView(MeAPIView):
    """DELETE /api/me/replies/{id}. 내 답글만 지운다. 204."""

    def delete(self, request, reply_id: str):
        rid = int_id(reply_id)
        if rid is not None:
            with connection.cursor() as cur:
                cur.execute(
                    "DELETE FROM tip_reply WHERE reply_id = %s AND user_id = %s",
                    [rid, request.user.user_id],
                )
                if cur.rowcount:
                    return Response(status=204)
        return not_found("내 답글을 찾을 수 없습니다.")
