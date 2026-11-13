"""탐색 v6 Postgres 읽기·쓰기. 이 앱의 SQL 은 이 파일에만 둔다.

테스트 DB 는 db/schema.sql 만 실행해 PostGIS(dong.geom)·pgvector(dong_feature)가 없다.
그래서 metric_vec 는 metric 표에서 직접 만들고(derive.metric_vec), 동 중심 좌표만
load_centers() 하나에서 PostGIS 를 쓴다 (테스트에서는 바꿔 끼운다).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.db import connection, transaction

from explore.derive import TypeScore

# 집계 제외 동(수동면)과 경계·코드 없는 동은 그래프·API 에서 뺀다
DONG_OK = "d.is_excluded = false AND d.adm_code IS NOT NULL"


def _rows(sql: str, params: list | tuple = ()) -> list[tuple]:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _float(v) -> float | None:
    return None if v is None else float(v)


def data_year() -> int:
    return _rows("SELECT max(year) FROM population_hourly")[0][0]


@dataclass(frozen=True)
class DeriveInput:
    dong_metrics: dict[int, dict[str, float]]  # derive.type_scores 입력
    region_metrics: dict[int, dict]
    morning: dict[int, float]
    region_of: dict[int, int]
    coast_km: dict[int, float | None]


def load_derive_input() -> DeriveInput:
    """연간(month 0) 지표가 모두 있는 동만. 시군구는 숙박 성향(연간)과 ease 원값(1~12월)."""
    metrics: dict[int, dict] = defaultdict(dict)
    for dong_id, key, score, raw in _rows(
        f"""SELECT m.unit_id, m.metric_key, m.score, m.raw_value
              FROM metric m JOIN dong d ON d.dong_id = m.unit_id
             WHERE m.scope = 'dong' AND m.month = 0 AND {DONG_OK}
               AND m.metric_key IN ('quiet', 'peer', 'evening', 'local_life')"""
    ):
        value = raw if key == "local_life" else score
        if value is not None:
            metrics[dong_id][key] = value
    morning = {
        dong_id: float(v)
        for dong_id, v in _rows(
            """SELECT dong_id, avg(index_100) / 100.0 FROM dong_hourly
                WHERE month = 0 AND day_group = 'weekday' AND hour BETWEEN 9 AND 11
                  AND index_100 IS NOT NULL
                GROUP BY dong_id"""
        )
    }
    regions: dict[int, dict] = defaultdict(lambda: {"ease_raw": {}})
    for region_id, month, key, score, raw in _rows(
        """SELECT unit_id, month, metric_key, score, raw_value FROM metric
            WHERE scope = 'region'
              AND ((metric_key = 'overnight' AND month = 0)
                   OR (metric_key = 'ease' AND month BETWEEN 1 AND 12))"""
    ):
        if key == "overnight" and score is not None:
            regions[region_id]["overnight"] = score
        elif key == "ease" and raw is not None:
            regions[region_id]["ease_raw"][month] = raw
    region_metrics = {r: v for r, v in regions.items() if "overnight" in v}

    region_of, coast = {}, {}
    for dong_id, region_id, km in _rows(
        f"SELECT dong_id, region_id, coast_dist_km FROM dong d WHERE {DONG_OK}"
    ):
        region_of[dong_id], coast[dong_id] = region_id, _float(km)
    complete = {
        d: m
        for d, m in metrics.items()
        if len(m) == 4 and d in morning and region_of.get(d) in region_metrics
    }
    return DeriveInput(complete, region_metrics, morning, region_of, coast)


def load_hourly(year: int) -> dict[int, dict[str, list[float | None]]]:
    """동 → day_group → 0~23시 차량 이동 인구 평균 (그 해 월·요일 평균)."""
    out: dict[int, dict[str, list]] = defaultdict(
        lambda: {"weekday": [None] * 24, "weekend": [None] * 24}
    )
    for dong_id, day, hour, v in _rows(
        """SELECT dong_id, day_group, hour, avg(driv_avg) FROM population_hourly
            WHERE year = %s GROUP BY dong_id, day_group, hour""",
        [year],
    ):
        out[dong_id][day][hour] = v
    return dict(out)


@transaction.atomic
def write_explore(
    types: dict[int, list[TypeScore]],
    hours: dict[int, dict[str, tuple[list, int | None, int | None]]],
    similar: dict[int, list[tuple[int, float]]],
) -> None:
    """세 파생 표를 한 트랜잭션에서 지우고 다시 넣는다 (스펙 §5-1)."""
    with connection.cursor() as cur:
        for table in ("dong_similar", "dong_hour_stat", "dong_type_score"):
            cur.execute(f"DELETE FROM {table}")
        cur.executemany(
            "INSERT INTO dong_type_score (dong_id, type, score, rank, reason)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(d, t.type, t.score, t.rank, t.reason) for d, ts in types.items() for t in ts],
        )
        cur.executemany(
            "INSERT INTO dong_hour_stat (dong_id, day_group, driv, peak_hour, quiet_hour)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(d, day, *row) for d, days in hours.items() for day, row in days.items()],
        )
        cur.executemany(
            "INSERT INTO dong_similar (dong_id, rank, other_id, l2) VALUES (%s, %s, %s, %s)",
            [
                (d, rank, other, l2)
                for d, rows in similar.items()
                for rank, (other, l2) in enumerate(rows, start=1)
            ],
        )
