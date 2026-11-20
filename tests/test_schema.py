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


def test_photo_upload_columns(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, column_default FROM information_schema.columns"
            " WHERE table_name = 'photo' AND column_name IN"
            " ('tip_id', 'position', 'status', 'hidden_at', 'deleted_by_user')"
        )
        cols = dict(cursor.fetchall())
    assert set(cols) == {"tip_id", "position", "status", "hidden_at", "deleted_by_user"}
    assert cols["status"].startswith("'approved'")  # 올리면 바로 공개
    assert cols["deleted_by_user"] == "false"
