from django.db import connection

EXPECTED_TABLES = {
    "region",
    "dong",
    "population_hourly",
    "population_demographic",
    "dong_month_summary",
    "dong_year_summary",
    "metric_def",
    "metric",
    "dong_hourly",
    "region_link",
    "app_user",
    "favorite",
    "photo",
    "user_photo",
    "tip",
    "tip_reaction",
    "tip_reply",
    "report",
    "poll_vote",
    "share_card",
    "nl_search_log",
    "dong_type_score",
    "dong_hour_stat",
    "dong_similar",
    "dong_brief",
}


def test_schema_sql_loaded_into_test_db(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        tables = {row[0] for row in cursor.fetchall()}

    assert EXPECTED_TABLES <= tables
    assert "dong_answer" not in tables  # 2026-11-17 제거: 답은 Redis 에만
    assert "metric_vote" not in tables  # 2026-11-20 poll_vote 로 바뀜


def test_photo_table_unchanged(db):
    """관광공사 사진 표는 main 그대로. 사용자 사진은 user_photo 에 따로."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, column_default FROM information_schema.columns"
            " WHERE table_name = 'photo'"
        )
        cols = dict(cursor.fetchall())
    assert not {"tip_id", "position", "hidden_at", "deleted_by_user"} & set(cols)
    assert cols["status"].startswith("'pending'")


def test_user_photo_columns(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, column_default FROM information_schema.columns"
            " WHERE table_name = 'user_photo'"
        )
        cols = dict(cursor.fetchall())
    assert set(cols) == {
        "user_photo_id",
        "user_id",
        "scope",
        "unit_id",
        "month",
        "url",
        "tip_id",
        "position",
        "status",
        "hidden_at",
        "deleted_by_user",
        "created_at",
    }
    assert cols["status"].startswith("'approved'")  # 올리면 바로 공개
    assert cols["deleted_by_user"] == "false"


def test_report_accepts_upload(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'report'::regclass AND contype = 'c'"
        )
        checks = " ".join(row[0] for row in cursor.fetchall())
    assert "'upload'" in checks
