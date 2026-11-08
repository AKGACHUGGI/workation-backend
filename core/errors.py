"""API 공통 에러 응답. 명세 형식: { "error": { "code", "message" } }"""

from rest_framework.response import Response


def error(status: int, code: str, message: str) -> Response:
    return Response({"error": {"code": code, "message": message}}, status=status)


def bad_request(message: str) -> Response:
    return error(400, "BAD_REQUEST", message)


def not_found(message: str = "대상을 찾을 수 없습니다.") -> Response:
    return error(404, "NOT_FOUND", message)


class ParamError(ValueError):
    """쿼리 파라미터 검증 실패. 뷰에서 잡아 400 으로 바꾼다."""


def parse_month(request) -> int | None:
    """month 파라미터 (1~12). 없으면 None = 연간."""
    raw = request.query_params.get("month")
    if raw in (None, ""):
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= 12:
        raise ParamError("month 는 1~12 입니다.")
    return int(raw)


def parse_choice(request, name: str, choices, default=None, required=False):
    raw = request.query_params.get(name)
    if raw in (None, ""):
        if required:
            raise ParamError(f"{name} 가 필요합니다.")
        return default
    if raw not in choices:
        raise ParamError(f"{name} 는 {', '.join(choices)} 중 하나입니다.")
    return raw
