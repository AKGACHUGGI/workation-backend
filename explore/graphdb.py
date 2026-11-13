"""Neo4j 접속. 서비스 코드(API·브리핑 조회)는 read() 만 쓴다 (읽기 트랜잭션).

커뮤니티판은 읽기 전용 계정이 없어서 쓰기는 관리 명령(explore/graph_write.py)에만 둔다 (스펙 §5-2).
"""

from __future__ import annotations

from django.conf import settings
from neo4j import GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError

_drivers: dict[tuple[str, str], object] = {}


class GraphUnavailable(Exception):
    """NEO4J_URI 미설정 · 연결 실패 · 동기화 전(Meta 없음). API 는 마지막 응답 또는 503."""


def get_driver():
    """프로세스마다 드라이버 하나를 재사용한다 (드라이버가 연결 풀을 가진다)."""
    if not settings.NEO4J_URI:
        raise GraphUnavailable("NEO4J_URI 미설정")
    key = (settings.NEO4J_URI, settings.NEO4J_USER)
    if key not in _drivers:
        _drivers[key] = GraphDatabase.driver(
            settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )
    return _drivers[key]


def read(work, **params):
    """work(tx, **params) 를 읽기 트랜잭션 하나에서 실행한다. 드라이버 오류는 GraphUnavailable."""
    try:
        with get_driver().session() as session:
            return session.execute_read(work, **params)
    except (DriverError, Neo4jError) as ex:
        raise GraphUnavailable(str(ex)) from ex
