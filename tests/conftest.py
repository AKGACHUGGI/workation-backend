"""모델이 managed = False 라 Django 는 테스트 DB 에 테이블을 만들지 않는다.
테스트 DB 를 만든 직후 db/schema.sql 을 그대로 실행해 실제 스키마로 테스트한다."""

from pathlib import Path

import pytest
from django.db import connection

SCHEMA_SQL = Path(__file__).resolve().parent.parent / "db" / "schema.sql"


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup, django_db_blocker):
    with django_db_blocker.unblock(), connection.cursor() as cursor:
        cursor.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
