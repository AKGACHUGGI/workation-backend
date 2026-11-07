-- 행정동 소개문 dong_doc.body 생성 · 2026-11-07
-- 먼저 migrations/20261107_text-embedding.sql 과 03_metrics.sql, 태그 SQL 을 적용해야 한다.
-- 연간(month 0) 지표 등급 · 속성 태그 · 평일 붐비는 시간 · 시군구의 한산한 달로 문장을 만든다.
-- 문장이 바뀐 행만 embedding 을 NULL 로 돌리므로, 이어서 build_embeddings.py doc 으로 빈 칸만 채우면 된다.
-- 재실행 안전.

BEGIN;

CREATE TEMP TABLE doc_src ON COMMIT DROP AS
WITH g AS (  -- 연간 동 지표
  SELECT unit_id AS dong_id,
         max(grade) FILTER (WHERE metric_key = 'quiet')   AS quiet_g,
         max(score) FILTER (WHERE metric_key = 'quiet')   AS quiet_s,
         max(grade) FILTER (WHERE metric_key = 'peer')    AS peer_g,
         max(score) FILTER (WHERE metric_key = 'peer')    AS peer_s,
         max(grade) FILTER (WHERE metric_key = 'evening') AS eve_g,
         max(score) FILTER (WHERE metric_key = 'evening') AS eve_s,
         max(raw_value) FILTER (WHERE metric_key = 'local_life') AS local_life
    FROM metric WHERE scope = 'dong' AND month = 0 GROUP BY unit_id
),
pk AS (  -- 평일 연간 곡선의 가장 붐비는 시간, 낮(9~18시) 중 가장 한산한 시간
  SELECT dong_id,
         (array_agg(hour ORDER BY index_100 DESC, hour))[1] AS peak_h,
         (array_agg(hour ORDER BY index_100, hour) FILTER (WHERE hour BETWEEN 9 AND 18))[1] AS calm_h
    FROM dong_hourly WHERE month = 0 AND day_group = 'weekday' GROUP BY dong_id
),
ez AS (  -- 시군구 시기 여유: 한산한 달 3개, 붐비는 달 3개
  SELECT unit_id AS region_id,
         (SELECT string_agg(m::text, '·' ORDER BY m) FROM unnest((array_agg(month ORDER BY score DESC, month))[1:3]) m) || '월' AS calm_m,
         (SELECT string_agg(m::text, '·' ORDER BY m) FROM unnest((array_agg(month ORDER BY score, month))[1:3]) m) || '월' AS busy_m
    FROM metric WHERE scope = 'region' AND metric_key = 'ease' AND month BETWEEN 1 AND 12 GROUP BY unit_id
),
ov AS (
  SELECT unit_id AS region_id, grade AS ov_g FROM metric WHERE scope = 'region' AND metric_key = 'overnight' AND month = 0
),
tg AS (  -- 태그를 사람이 읽는 말로
  SELECT d.dong_id,
         string_agg(CASE t
           WHEN 'coast'     THEN '바닷가(해안선 ' || round(d.coast_dist_km, 1) || ' km)'
           WHEN 'beach'     THEN '해수욕장'
           WHEN 'island'    THEN '섬'
           WHEN 'surf'      THEN '서핑'
           WHEN 'cafe'      THEN '카페 많음'
           WHEN 'food'      THEN '음식점 많음'
           WHEN 'library'   THEN '도서관'
           WHEN 'long_stay' THEN '장기 숙소(콘도·레지던스·게스트하우스)'
           WHEN 'park'      THEN '공원'
           WHEN 'market'    THEN '시장'
           WHEN 'resort'    THEN '리조트'
           WHEN 'city'      THEN '도심'
           WHEN 'mountain'  THEN '산·숲'
         END, ', ' ORDER BY array_position(ARRAY['coast','beach','island','surf','mountain','city','cafe','food','library','park','market','resort','long_stay'], t)) AS words
    FROM dong d, unnest(d.tags) t GROUP BY d.dong_id
)
SELECT d.dong_id,
       concat_ws(' ',
         r.sido_name || ' ' || r.sigungu_name || ' ' || d.dong_name || '.',
         CASE WHEN tg.words IS NOT NULL THEN '특징: ' || tg.words || '.' END,
         -- 등급을 말로 풀어 쓴다 (임베딩은 숫자보다 말의 뜻을 잘 잡는다)
         CASE WHEN g.quiet_g IN ('S','A') THEN '낮에도 외부에서 오는 사람이 적어 조용하다.'
              WHEN g.quiet_g = 'C'        THEN '낮에 외부에서 사람이 많이 몰려 붐빈다.'
              ELSE '조용함은 보통이다.' END,
         CASE WHEN g.eve_g IN ('S','A') THEN '저녁에 거리를 걷는 사람이 많아 활기차다.'
              WHEN g.eve_g = 'C'        THEN '저녁에는 거리가 한산하다.' END,
         CASE WHEN g.peer_g IN ('S','A') THEN '20~40대 방문객이 많다.'
              WHEN g.peer_g = 'C'        THEN '20~40대 방문객이 적다.' END,
         CASE WHEN g.local_life >= 0.7 THEN '주민·직장인 중심의 생활권 동네다.'
              WHEN g.local_life < 0.5 THEN '방문객 비중이 높은 동네다.'
              ELSE '주민과 방문객이 섞인 동네다.' END,
         '평일 가장 붐비는 시간 ' || pk.peak_h || '시, 낮 중 가장 한산한 시간 ' || pk.calm_h || '시.',
         '지표(연간): 동네 조용함 ' || g.quiet_g || '등급 ' || g.quiet_s || '점, 또래 밀도 ' || g.peer_g || '등급 ' || g.peer_s
           || '점, 저녁 활기 ' || g.eve_g || '등급 ' || g.eve_s || '점.',
         '시군구 기준 한산한 달 ' || ez.calm_m || ', 붐비는 달 ' || ez.busy_m || ', 숙박 성향 ' || ov.ov_g || '등급.'
       ) AS body
  FROM dong d
  JOIN region r USING (region_id)
  JOIN g  ON g.dong_id = d.dong_id
  JOIN pk ON pk.dong_id = d.dong_id
  JOIN ez ON ez.region_id = d.region_id
  JOIN ov ON ov.region_id = d.region_id
  LEFT JOIN tg ON tg.dong_id = d.dong_id
 WHERE NOT d.is_excluded;

DELETE FROM dong_doc WHERE dong_id NOT IN (SELECT dong_id FROM doc_src);
INSERT INTO dong_doc (dong_id, body)
SELECT dong_id, body FROM doc_src
ON CONFLICT (dong_id) DO UPDATE
   SET body = EXCLUDED.body, embedding = NULL, model = NULL, updated_at = now()
 WHERE dong_doc.body IS DISTINCT FROM EXCLUDED.body;

COMMIT;

-- 확인: 전체 203, 임베딩 빈 칸 수
SELECT count(*) AS docs, count(*) FILTER (WHERE embedding IS NULL) AS need_embedding FROM dong_doc;
