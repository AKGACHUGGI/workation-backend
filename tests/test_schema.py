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
    "metric_vote",
    "share_card",
    "nl_search_log",
}


def test_schema_sql_loaded_into_test_db(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        tables = {row[0] for row in cursor.fetchall()}

    assert EXPECTED_TABLES <= tables
