import logging

from django.db import DatabaseError, connection
from rest_framework.decorators import api_view
from rest_framework.response import Response

logger = logging.getLogger(__name__)


@api_view(["GET"])
def health(request):
    """DB 에 SELECT 1 을 보내 연결을 확인한다. 예외 문구(접속 정보 포함 가능)는 로그에만 남긴다."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except DatabaseError:
        logger.exception("health check: database unavailable")
        return Response({"status": "error", "db": "unavailable"}, status=503)
    return Response({"status": "ok", "db": "ok"})


@api_view(["GET"])
def live(request):
    """프로세스가 요청을 받는지만 본다. DB 는 보지 않는다 (컨테이너 헬스체크용)."""
    return Response({"status": "ok"})
