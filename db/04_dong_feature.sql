-- 동네 특징 벡터 dong_feature 채우기 · 2026-11-07
-- 먼저 migrations/20261107_pgvector-dong-feature.sql 과 03_metrics.sql 을 적용해야 한다.
-- 지우고 다시 넣으므로 재실행 안전. 지표를 다시 계산하면 이 파일도 다시 실행한다.
--
-- metric_vec : quiet · peer · evening 점수(0~100)를 0~1 로, local_life 는 원값(0~1 비율) 그대로.
--              모두 같은 범위라 거리 계산에서 한 지표가 다른 지표를 누르지 않는다.
-- curve_vec  : dong_hourly 주중 24 + 주말 24 칸 (index_100 ÷ 100). 하루 리듬이 비슷한지 본다.
-- 집계 제외 행정동(수동면)은 metric 에 없으므로 자연히 빠진다.

BEGIN;
SET LOCAL search_path = public, extensions;

DELETE FROM dong_feature;

INSERT INTO dong_feature (dong_id, month, metric_vec, curve_vec)
WITH m AS (
  SELECT unit_id AS dong_id, month,
         max(score) FILTER (WHERE metric_key = 'quiet')       / 100.0 AS quiet,
         max(score) FILTER (WHERE metric_key = 'peer')        / 100.0 AS peer,
         max(score) FILTER (WHERE metric_key = 'evening')     / 100.0 AS evening,
         max(raw_value) FILTER (WHERE metric_key = 'local_life')      AS local_life
    FROM metric
   WHERE scope = 'dong' AND metric_key IN ('quiet', 'peer', 'evening', 'local_life')
   GROUP BY unit_id, month
),
c AS (
  SELECT dong_id, month,
         '[' || string_agg((index_100 / 100.0)::text, ',' ORDER BY day_group, hour) || ']' AS curve,
         count(*) AS n
    FROM dong_hourly
   GROUP BY dong_id, month
)
SELECT m.dong_id, m.month,
       ('[' || concat_ws(',', m.quiet, m.peer, m.evening, round(m.local_life::numeric, 4)) || ']')::vector,
       c.curve::vector
  FROM m JOIN c USING (dong_id, month)
 WHERE c.n = 48
   AND m.quiet IS NOT NULL AND m.peer IS NOT NULL AND m.evening IS NOT NULL AND m.local_life IS NOT NULL;

COMMIT;

-- 확인: 연간 기준 행 수 (기대 203) 와 전체 행 수 (기대 2,639)
SELECT count(*) FILTER (WHERE month = 0) AS annual, count(*) AS total FROM dong_feature;
