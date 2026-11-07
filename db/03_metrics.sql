-- 지표 산출: metric_def 시드 + metric + dong_hourly (명세 v0.3 변경안 3장 기준)
-- 원천: dong_month_summary, population_hourly (2025년 단일 연도). DB 안에서만 계산한다.
-- 재실행 안전: 이 파일이 만드는 행을 지우고 다시 넣는다. 한 트랜잭션.
-- 실행: workation-data load_db.py 적재와 migrations 적용 뒤 SQL Editor 에서 전체 실행. 원천을 다시 적재하면 다시 실행한다.
--
-- 공통 규칙
--   month 0 = 연간. 연간 원값은 12개월 평균 재료로 같은 식을 적용한다(월별 비율의 평균이 아님).
--   집계 제외 행정동(dong.is_excluded, 수동면)은 metric 의 모집단·값에서 뺀다. dong_hourly 는 204곳 모두.
--   점수 = 같은 scope·같은 달 안의 백분위 0~100 (최고 100, 최저 0). 원값이 낮을수록 좋은 지표(ease, quiet)는 역순.
--   등급 = 점수 사분위. S ≥ 75 / A ≥ 50 / B ≥ 25 / C < 25.
--   동네 성격 그룹(quiet, peer, evening)의 시군구 값 = 소속 행정동의 분자 합 ÷ 분모 합(인구 가중).
--     명세 9장 열린 질문(중앙값 vs 인구 가중)에 대한 데이터 담당 초안.
--   evening(저녁 활기) = 평일 저녁 보행 ÷ 평일 저녁 체류 (2026-11-07 확정, 명세 v0.3 의 '÷ 낮 체류' 대체).
--     같은 시간대끼리 나눠 "저녁에 그 동네에 있는 사람 중 밖을 걷는 비율"을 잰다. 낮 체류를 분모로 쓰면
--     동네 조용함(낮 체류 ÷ 심야 체류)과 기계적으로 엮여(스피어만 −0.54 → −0.37) 도심 상권이 손해를 본다.
--   overnight(숙박 성향) = 숙박 계수 β (2026-11-07 확정). 시군구마다 동 고정효과 회귀
--       N(i,t) = α(i) + β·V(i,t) + γ·R(i,t)
--     N 심야 체류(주중 5 : 주말 2 가중), V 방문, R 거주, i 행정동, t 월(1~12).
--     β = 방문객 1명 증가당 심야 체류 증가분(1 이면 숙박형, 0 이면 당일형). 거주 R 을 통제해야 학생 이동 같은
--     거주 변동이 섞이지 않는다(전주시: 통제 전 2.07 → 통제 후 0.62). 월 변동으로 추정하므로 연간(month 0) 한 값.
--   보조 지표(weekend_gap, peak_share, local_life)는 원값만, 점수·등급은 NULL.

BEGIN;

-- 1. 지표 정의 ---------------------------------------------------------------
INSERT INTO metric_def (metric_key, name_ko, description, higher_is, is_core) VALUES
  ('ease',        '시기 여유',     '월 혼잡 지수 = 해당 월 방문인구 ÷ 연중 최대 월 방문인구 × 100. 혼잡이 낮을수록 점수가 높다(역백분위). 시군구 단위', 'good', true),
  ('quiet',       '동네 조용함',   '낮 유입 지수 = 평일 낮(10~16시) 평균 체류인구 ÷ 평일 심야(23~05시) 평균 체류인구. 1 미만이면 낮에 사람이 빠지는 주거지. 낮을수록 점수가 높다(역백분위)', 'good', true),
  ('peer',        '또래 밀도',     '20~40대 방문인구 ÷ 성별·연령이 확인된 방문인구', 'good', true),
  ('overnight',   '숙박 성향',     '숙박 계수 = 방문객 1명 증가당 심야(23~05시) 체류 증가분. 시군구별 동 고정효과 회귀(거주인구 통제)로 월 변동에서 추정. 1에 가까우면 숙박형, 0에 가까우면 당일형. 시군구 연간 값', 'good', true),
  ('evening',     '저녁 활기',     '평일 저녁(17~22시) 평균 보행인구 ÷ 평일 저녁 평균 체류인구. 저녁에 그 동네에 있는 사람 중 밖을 걷는 비율', 'good', true),
  ('weekend_gap', '주중·주말 차이', '주말 낮 체류 ÷ 주중 낮 체류. 0.9~1.1 균형형, 그 이상 주말형, 그 이하 주중형(경계 초안). 라벨용, 점수 없음', 'good', false),
  ('peak_share',  '성수기 집중도', '상위 3개월 방문인구 ÷ 연간 방문인구(%). 연간(month 0)만. 높을수록 성수기 쏠림', 'bad', false),
  ('local_life',  '생활권 지수',   '(거주 + 근무) ÷ (거주 + 근무 + 방문). 0.7 이상 생활권, 미만 관광지(경계 초안). 태그용, 점수 없음', 'good', false)
ON CONFLICT (metric_key) DO UPDATE
  SET name_ko = EXCLUDED.name_ko, description = EXCLUDED.description,
      higher_is = EXCLUDED.higher_is, is_core = EXCLUDED.is_core;

DELETE FROM metric WHERE metric_key IN ('ease','quiet','peer','overnight','evening','weekend_gap','peak_share','local_life');

-- 2. 재료: 행정동 × 월(1~12) + 연간(0) ------------------------------------------
CREATE TEMP TABLE m_base ON COMMIT DROP AS
WITH monthly AS (
  SELECT s.dong_id, d.region_id, s.month,
         s.stay_weekday_day, s.stay_weekday_night, s.stay_weekend_day,
         s.stay_weekday_evening, s.stay_weekend_night,
         s.walk_weekday_evening, s.vist_2040, s.vist_known, s.vist_total,
         s.resd_total, s.wrk_total
    FROM dong_month_summary s JOIN dong d USING (dong_id)
   WHERE s.year = 2025 AND NOT d.is_excluded
)
SELECT * FROM monthly
UNION ALL
SELECT dong_id, region_id, 0,
       avg(stay_weekday_day), avg(stay_weekday_night), avg(stay_weekend_day),
       avg(stay_weekday_evening), avg(stay_weekend_night),
       avg(walk_weekday_evening), avg(vist_2040), avg(vist_known), avg(vist_total),
       avg(resd_total), avg(wrk_total)
  FROM monthly GROUP BY dong_id, region_id;

-- 3. 원값 ------------------------------------------------------------------
CREATE TEMP TABLE m_raw (scope varchar, unit_id smallint, month smallint, metric_key varchar, raw_value float8) ON COMMIT DROP;

-- 3-1. 행정동: quiet, peer, evening, local_life
INSERT INTO m_raw
SELECT 'dong', dong_id, month, k, v
  FROM m_base,
  LATERAL (VALUES
    ('quiet',      stay_weekday_day     / NULLIF(stay_weekday_night, 0)),
    ('peer',       vist_2040            / NULLIF(vist_known, 0)),
    ('evening',    walk_weekday_evening / NULLIF(stay_weekday_evening, 0)),
    ('local_life', (resd_total + wrk_total) / NULLIF(resd_total + wrk_total + vist_total, 0))
  ) AS x(k, v);

-- 3-2. 시군구: 동네 성격 그룹(인구 가중) + weekend_gap
INSERT INTO m_raw
SELECT 'region', region_id, month, k, v
  FROM (SELECT region_id, month,
               sum(stay_weekday_day) day_, sum(stay_weekday_night) night_, sum(stay_weekend_day) wkend_day,
               sum(stay_weekday_evening) eve_stay, sum(walk_weekday_evening) eve,
               sum(vist_2040) v2040, sum(vist_known) vknown
          FROM m_base GROUP BY region_id, month) r,
  LATERAL (VALUES
    ('quiet',       day_      / NULLIF(night_, 0)),
    ('peer',        v2040     / NULLIF(vknown, 0)),
    ('evening',     eve       / NULLIF(eve_stay, 0)),
    ('weekend_gap', wkend_day / NULLIF(day_, 0))
  ) AS x(k, v);

-- 3-3. 시군구: ease (월 방문 ÷ 연중 최대 월 방문 × 100, 연간은 평균 ÷ 최대)
INSERT INTO m_raw
WITH rv AS (
  SELECT region_id, month, sum(vist_total) vist FROM m_base GROUP BY region_id, month
), mx AS (
  SELECT region_id, max(vist) vmax FROM rv WHERE month > 0 GROUP BY region_id
)
SELECT 'region', rv.region_id, rv.month, 'ease', 100 * rv.vist / NULLIF(mx.vmax, 0)
  FROM rv JOIN mx USING (region_id);

-- 3-4. 시군구: peak_share (연간만)
INSERT INTO m_raw
SELECT 'region', region_id, 0, 'peak_share', 100 * top3 / NULLIF(total, 0)
  FROM (SELECT region_id,
               sum(vist) FILTER (WHERE rk <= 3) top3, sum(vist) total
          FROM (SELECT region_id, month, sum(vist_total) vist,
                       row_number() OVER (PARTITION BY region_id ORDER BY sum(vist_total) DESC) rk
                  FROM m_base WHERE month > 0 GROUP BY region_id, month) t
         GROUP BY region_id) p;

-- 3-5. 시군구: overnight (숙박 계수 β, 연간만)
--   동별 평균을 빼 고정효과를 없앤 뒤(①) 합계 5개(②)로 2변수 최소제곱 해(③)를 구한다.
INSERT INTO m_raw
WITH b AS (
  SELECT dong_id, region_id,
         (5 * stay_weekday_night + 2 * stay_weekend_night) / 7.0 AS n,
         vist_total AS v, resd_total AS r
    FROM m_base WHERE month > 0
), d AS (                                                         -- ① 동 고정효과 제거
  SELECT region_id,
         n - avg(n) OVER (PARTITION BY dong_id) AS n,
         v - avg(v) OVER (PARTITION BY dong_id) AS v,
         r - avg(r) OVER (PARTITION BY dong_id) AS r
    FROM b
), s AS (                                                         -- ② 합계
  SELECT region_id, sum(v*v) svv, sum(r*r) srr, sum(v*r) svr, sum(v*n) svn, sum(r*n) srn
    FROM d GROUP BY region_id
)
SELECT 'region', region_id, 0, 'overnight',                       -- ③ β
       (srr * svn - svr * srn) / NULLIF(svv * srr - svr * svr, 0)
  FROM s;

-- 4. 점수·등급 후 저장 --------------------------------------------------------
INSERT INTO metric (scope, unit_id, month, metric_key, raw_value, score, grade)
SELECT scope, unit_id, month, metric_key, raw_value, score,
       CASE WHEN score IS NULL THEN NULL
            WHEN score >= 75 THEN 'S' WHEN score >= 50 THEN 'A' WHEN score >= 25 THEN 'B' ELSE 'C' END
  FROM (SELECT *,
               CASE WHEN metric_key IN ('ease','quiet','peer','evening','overnight') AND raw_value IS NOT NULL THEN
                 round(100 * percent_rank() OVER (
                   PARTITION BY scope, month, metric_key, (raw_value IS NULL)
                   ORDER BY CASE WHEN metric_key IN ('ease','quiet') THEN -raw_value ELSE raw_value END))::smallint
               END AS score
          FROM m_raw) s;

-- 5. 24시간 붐빔 곡선 (보행인구, 같은 동·월·요일그룹 안에서 최대 = 100) ------------
DELETE FROM dong_hourly;
INSERT INTO dong_hourly (dong_id, month, day_group, hour, index_100)
WITH h AS (
  SELECT dong_id, month, day_group, hour, avg(walk_avg) v
    FROM population_hourly WHERE year = 2025 GROUP BY dong_id, month, day_group, hour
  UNION ALL
  SELECT dong_id, 0, day_group, hour, avg(walk_avg)
    FROM population_hourly WHERE year = 2025 GROUP BY dong_id, day_group, hour
)
SELECT dong_id, month, day_group, hour,
       round(100 * v / NULLIF(max(v) OVER (PARTITION BY dong_id, month, day_group), 0))::smallint
  FROM h;

COMMIT;
