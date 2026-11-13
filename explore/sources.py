"""탐색 v6 Postgres 읽기·쓰기. 이 앱의 SQL 은 이 파일에만 둔다.

테스트 DB 는 db/schema.sql 만 실행해 PostGIS(dong.geom)·pgvector(dong_feature)가 없다.
그래서 metric_vec 는 metric 표에서 직접 만들고(derive.metric_vec), 동 중심 좌표만
load_centers() 하나에서 PostGIS 를 쓴다 (테스트에서는 바꿔 끼운다).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass

from django.db import DatabaseError, connection, transaction

from explore.derive import TypeScore
from explore.graph import DongRow, GraphInput, RegionRow

logger = logging.getLogger(__name__)

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
    morning: dict[int, float]
    coast_km: dict[int, float | None]


def load_derive_input() -> DeriveInput:
    """연간(month 0) 지표 4개와 평일 오전 보행 지수가 모두 있는 동만."""
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
    coast = {
        dong_id: _float(km)
        for dong_id, km in _rows(f"SELECT dong_id, coast_dist_km FROM dong d WHERE {DONG_OK}")
    }
    complete = {d: m for d, m in metrics.items() if len(m) == 4 and d in morning and d in coast}
    return DeriveInput(complete, morning, coast)


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


def load_centers() -> dict[int, tuple[float, float]]:
    """동 경계 안의 한 점 (lat, lng). 웹 public/geo 의 labelLat/labelLng 와 같은 값 (PostGIS)."""
    rows = _rows(
        """SELECT dong_id, extensions.st_y(p), extensions.st_x(p)
             FROM (SELECT dong_id, extensions.st_pointonsurface(geom) AS p
                     FROM dong WHERE geom IS NOT NULL) s"""
    )
    return {d: (round(lat, 5), round(lng, 5)) for d, lat, lng in rows}


def load_graph_input() -> GraphInput:
    """sync_graph 재료. build_explore 가 만든 파생 표 + 지표 원천."""
    regions = [
        RegionRow(*r)
        for r in _rows(
            """SELECT region_id, sigungu_code, sigungu_name, sido_name FROM region
                WHERE sigungu_code IS NOT NULL ORDER BY region_id"""
        )
    ]
    types: dict[int, list[TypeScore]] = defaultdict(list)
    for d, t, score, rank, reason in _rows(
        "SELECT dong_id, type, score, rank, reason FROM dong_type_score ORDER BY dong_id, rank"
    ):
        types[d].append(TypeScore(t, score, rank, reason))
    grades: dict[int, dict] = defaultdict(dict)
    local_life = {}
    for d, key, grade, score, raw in _rows(
        """SELECT unit_id, metric_key, grade, score, raw_value FROM metric
            WHERE scope = 'dong' AND month = 0
              AND metric_key IN ('quiet', 'peer', 'evening', 'local_life')"""
    ):
        if key == "local_life":
            local_life[d] = raw
        elif grade is not None and raw is not None:
            grades[d][key] = (grade, score, raw)
    centers = load_centers()
    dongs = []
    for d, code, name, region_id, km, tags in _rows(
        f"""SELECT dong_id, adm_code, dong_name, region_id, coast_dist_km, tags
              FROM dong d WHERE {DONG_OK} ORDER BY dong_id"""
    ):
        if d in types and local_life.get(d) is not None:
            lat, lng = centers.get(d, (None, None))
            dongs.append(
                DongRow(d, code, name, region_id, lat, lng, _float(km), list(tags), local_life[d])
            )
    hours: dict[int, dict] = defaultdict(dict)
    for d, day, driv, peak, quiet in _rows(
        "SELECT dong_id, day_group, driv, peak_hour, quiet_hour FROM dong_hour_stat"
    ):
        hours[d][day] = (list(driv), peak, quiet)
    similar: dict[int, list] = defaultdict(list)
    for d, other, l2 in _rows(
        "SELECT dong_id, other_id, l2 FROM dong_similar ORDER BY dong_id, rank"
    ):
        similar[d].append((other, l2))
    ease: dict[int, dict[int, float]] = defaultdict(dict)
    for r, month, raw in _rows(
        """SELECT unit_id, month, raw_value FROM metric
            WHERE scope = 'region' AND metric_key = 'ease' AND month BETWEEN 1 AND 12
              AND raw_value IS NOT NULL"""
    ):
        ease[r][month] = raw
    return GraphInput(
        regions,
        dongs,
        dict(grades),
        dict(types),
        dict(hours),
        dict(similar),
        dict(ease),
        data_year(),
    )


def load_brief_targets() -> list[tuple[int, str]]:
    """브리핑을 만들 동 (dong_id, adm_code). build_explore 가 유형 점수를 만든 동."""
    return _rows(
        f"""SELECT DISTINCT d.dong_id, d.adm_code FROM dong d
              JOIN dong_type_score t ON t.dong_id = d.dong_id
             WHERE {DONG_OK} ORDER BY d.dong_id"""
    )


def load_brief_hashes() -> dict[int, str]:
    return dict(_rows("SELECT dong_id, facts_hash FROM dong_brief"))


def save_brief(
    dong_id: int, sentences: list[str], source: str, model: str | None, facts_hash: str
) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO dong_brief (dong_id, sentences, source, model, facts_hash, generated_at)
               VALUES (%s, %s::jsonb, %s, %s, %s, now())
               ON CONFLICT (dong_id) DO UPDATE SET
                 sentences = EXCLUDED.sentences, source = EXCLUDED.source,
                 model = EXCLUDED.model, facts_hash = EXCLUDED.facts_hash,
                 generated_at = EXCLUDED.generated_at""",
            [dong_id, json.dumps(sentences, ensure_ascii=False), source, model, facts_hash],
        )


def fetch_briefing(adm_code: str) -> list[str]:
    """API 용. 행이 없거나 조회가 실패하면 [] (나머지 응답은 정상, 스펙 §6-4)."""
    try:
        with connection.cursor() as cur:
            cur.execute(
                """SELECT b.sentences::text FROM dong_brief b JOIN dong d ON d.dong_id = b.dong_id
                    WHERE d.adm_code = %s""",
                [adm_code],
            )
            row = cur.fetchone()
    except DatabaseError as ex:
        logger.warning("dong_brief 조회 실패: %s", ex)
        return []
    # Django 의 psycopg 백엔드는 jsonb 를 문자열로 돌려준다 → ::text 로 읽어 직접 푼다
    return json.loads(row[0]) if row else []


# ───────────────────────── 동네에 물어보기 (v6.1) ─────────────────────────
# ask_vocab 은 vector 컬럼이라 테스트 DB 에 없다. pgvector SQL 은 nearest_vocab 하나에만 둔다.
# 답 저장·질문 기록이 실패해도 답은 내려간다 → savepoint 안에서 실행하고 경고만 남긴다.


def _vector(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def nearest_vocab(vec: list[float], k: int = 5) -> list[tuple[str, str, float]]:
    """질문 임베딩에 가까운 사전 표현 k개 (intent, phrase, 코사인 유사도), 가까운 순. pgvector."""
    q = _vector(vec)
    return [
        (intent, phrase, float(sim))
        for intent, phrase, sim in _rows(
            """SELECT intent, phrase, 1 - (embedding <=> %s::vector) AS sim
                 FROM ask_vocab WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s::vector LIMIT %s""",
            [q, q, k],
        )
    ]


def load_vocab() -> list[tuple[str, str, bool]]:
    """문자열 매칭용 사전 전체 (intent, phrase, canonical). 임베딩이 없어도 읽는다."""
    return _rows("SELECT intent, phrase, canonical FROM ask_vocab ORDER BY intent, phrase")


def vocab_version() -> str:
    """사전 버전 = max(updated_at) 의 epoch 초 문자열. 비어 있으면 "0". 캐시 키에 쓴다."""
    sql = "SELECT coalesce(extract(epoch FROM max(updated_at))::bigint, 0) FROM ask_vocab"
    return str(_rows(sql)[0][0])


def load_answer(adm_code: str, intents: str, facts_hash: str) -> tuple[list[str], str] | None:
    """저장된 답 (sentences, source). 없거나 조회가 실패하면 None (새로 만든다)."""
    try:
        with transaction.atomic():
            rows = _rows(
                """SELECT a.sentences::text, a.source FROM dong_answer a
                     JOIN dong d ON d.dong_id = a.dong_id
                    WHERE d.adm_code = %s AND a.intents = %s AND a.facts_hash = %s""",
                [adm_code, intents, facts_hash],
            )
    except DatabaseError as ex:
        logger.warning("dong_answer 조회 실패: %s", ex)
        return None
    return (json.loads(rows[0][0]), rows[0][1]) if rows else None


def save_answer(
    adm_code: str,
    intents: str,
    facts_hash: str,
    sentences: list[str],
    source: str,
    model: str | None,
) -> None:
    """같은 (동, 의도, 사실) 행이 있으면 그대로 둔다 (동시에 같은 첫 질문이 와도 안전)."""
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                """INSERT INTO dong_answer (dong_id, intents, facts_hash, sentences, source, model)
                   SELECT d.dong_id, %s, %s, %s::jsonb, %s, %s FROM dong d WHERE d.adm_code = %s
                   ON CONFLICT (dong_id, intents, facts_hash) DO NOTHING""",
                [
                    intents,
                    facts_hash,
                    json.dumps(sentences, ensure_ascii=False),
                    source,
                    model,
                    adm_code,
                ],
            )
    except DatabaseError as ex:
        logger.warning("dong_answer 저장 실패: %s", ex)


def log_question(question: str, parsed: dict) -> None:
    """nl_search_log 에 질문과 해석 결과 (사전 보강용). 실패는 무시한다."""
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(
                "INSERT INTO nl_search_log (query, parsed) VALUES (%s, %s::jsonb)",
                [question[:300], json.dumps(parsed, ensure_ascii=False)],
            )
    except DatabaseError as ex:
        logger.warning("nl_search_log 기록 실패: %s", ex)


def delete_old_answers(days: int = 60) -> int:
    """만든 지 days 일이 지난 답을 지운다 (그래프가 바뀌면 쓰이지 않는 행). 지운 행 수."""
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM dong_answer WHERE created_at < now() - make_interval(days => %s)", [days]
        )
        return cur.rowcount


def clear_answers(source: str | None = None) -> int:
    """manage.py clear_answers. source 가 있으면 그 출처 행만 지운다. 지운 행 수."""
    with connection.cursor() as cur:
        if source is None:
            cur.execute("DELETE FROM dong_answer")
        else:
            cur.execute("DELETE FROM dong_answer WHERE source = %s", [source])
        return cur.rowcount
