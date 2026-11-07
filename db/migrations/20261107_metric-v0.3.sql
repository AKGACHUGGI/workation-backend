-- 지표 개편 (명세 v0.3 변경안 5장) · 2026-11-07 · 데이터 담당
-- 기준: db/schema.sql (ERD v0.3, PR #3) 가 적용된 DB 에 한 번 실행한다. 두 번 실행해도 안전.
-- 적용 기록: Supabase 미적용 (머지 후 SQL Editor 에서 실행하고 날짜를 적는다)
--
-- 바뀌는 것
--   1. dong        — 집계 제외 플래그 is_excluded + 사유 excluded_reason 추가, 수동면(고성군) 표시
--   2. metric_def  — 지표 키 주석을 새 지표(ease·quiet·peer·overnight·evening + 보조 3)로 교체.
--                    시드 INSERT 는 하지 않는다 (FastAPI 가 점수 계산을 붙일 때 같이 넣는다).
--   3. metric      — 종합 점수(value)는 프리셋별로 달라 저장하지 않고 FastAPI 가 요청 시 계산.
--                    동네 성격 그룹(quiet·peer·evening)은 scope=dong 원본 + scope=region 집계,
--                    시기·체류 그룹(ease·overnight)은 scope=region 만.
--   4. dong_hourly — index_100 의 원천을 보행인구(population_hourly.walk_avg)로 명시.
--
-- 보류 (모바일 결정 workation-mobile #2 이후): app_user.provider 의 apple 제거, 알림 설정 컬럼 3개.

BEGIN;

-- 1. dong: 집계 제외 플래그
ALTER TABLE dong
  ADD COLUMN IF NOT EXISTS is_excluded     BOOLEAN      NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS excluded_reason VARCHAR(100);

COMMENT ON TABLE  dong                 IS '읍면동 (204개, 집계 대상 203개). dong_id = region_id*100 + 시군구 내 가나다 순번';
COMMENT ON COLUMN dong.is_excluded     IS 'true 면 지표·등급·지도·백분위에서 제외하고 화면에 "집계 제외" 표기. 데이터 자체는 적재. 수동면(고성군)';
COMMENT ON COLUMN dong.excluded_reason IS '제외 사유 (is_excluded=true 일 때). 예: 특수시설 패턴';

-- 수동면(고성군): 특수시설 패턴으로 집계 제외 (명세 v0.3 변경안 2장)
UPDATE dong d
   SET is_excluded = true,
       excluded_reason = '특수시설 패턴 — 체류·방문 인구가 일반 생활권과 달라 백분위를 왜곡'
  FROM region r
 WHERE d.region_id = r.region_id
   AND r.sigungu_name = '고성군'
   AND d.dong_name = '수동면';

-- 2. metric_def: 지표 키 교체 (주석만)
COMMENT ON TABLE  metric_def            IS '지표 정의 (핵심 5 + 보조 3). 종합 value 는 프리셋별로 달라 정의·저장하지 않는다';
COMMENT ON COLUMN metric_def.metric_key IS '핵심: ease(시기 여유), quiet(동네 조용함), peer(또래 밀도), overnight(숙박 성향), evening(저녁 활기) / 보조: weekend_gap(주중·주말 차이), peak_share(성수기 집중도), local_life(생활권 지수)';
COMMENT ON COLUMN metric_def.higher_is  IS 'good, bad (점수 방향). v0.3 핵심 5종은 전부 good — 원값이 낮을수록 좋은 지표(ease·quiet)는 역백분위로 점수를 뒤집는다';
COMMENT ON COLUMN metric_def.is_core    IS '핵심 지표 여부 (종합 value 계산에 들어감). 보조 3종은 false';

-- 3. metric: 종합 점수는 저장하지 않음, 단위 규칙
COMMENT ON TABLE  metric            IS '지표 산출값 (시군구·읍면동 × 월 × 지표). 종합 value 는 프리셋(quiet·lively·custom) 가중 평균이라 저장하지 않고 FastAPI 가 요청 시 계산';
COMMENT ON COLUMN metric.scope      IS 'region, dong. 동네 성격 그룹(quiet·peer·evening)은 dong 원본 + region 집계 둘 다, 시기·체류 그룹(ease·overnight)은 region 만';
COMMENT ON COLUMN metric.raw_value  IS '원값 (카드 보조 표기). ease=월 혼잡 지수, quiet=낮 유입 지수, peer=20~40대 비율, overnight=숙박 계수, evening=저녁 보행÷낮 체류';
COMMENT ON COLUMN metric.score      IS '백분위 점수 0~100. 같은 scope(시군구 12 / 행정동 203)·같은 달 안에서, 높을수록 좋음';
COMMENT ON COLUMN metric.grade      IS 'S(상위 25%), A(25~50%), B(50~75%), C(하위 25%). 경계는 실데이터 보고 조정';

-- 4. dong_hourly: 원천 = 보행인구
COMMENT ON TABLE  dong_hourly           IS '읍면동 24시간 붐빔 곡선. population_hourly.walk_avg(보행인구)를 월·요일그룹·시간대로 평균';
COMMENT ON COLUMN dong_hourly.index_100 IS '보행인구 지수. 같은 dong·month·day_group 안에서 최대 시간대 = 100';

COMMIT;
