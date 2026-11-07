-- 행정동 속성 태그 · 해안 거리 · 2026-11-07
-- 기준: migrations/20261107_metric-formulas-django.sql 까지 적용된 DB. 두 번 실행해도 안전.
--
-- 바뀌는 것
--   1. dong.tags 추가. region.tags 와 같은 코드 {coast, mountain, city}. 명세 v0.3 의 "해안 · 산간 · 도시" 필터를
--      행정동 단위로도 쓰기 위함 (자연어 검색 "바닷가", 홈 카테고리 스트립)
--   2. dong.coast_dist_km 추가. 경계에서 가장 가까운 해안선까지 거리. coast 태그의 근거이자 "바다 가까운 순" 정렬용
--   값은 db/tools/build_coast_sql.py 출력(OSM 해안선)으로 채운다. mountain · city 는 팀이 직접 입력한다.

BEGIN;

ALTER TABLE dong
  ADD COLUMN IF NOT EXISTS tags          TEXT[]       NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS coast_dist_km NUMERIC(7,3);

COMMENT ON COLUMN dong.tags          IS '속성 태그 {coast, mountain, city}. coast 는 coast_dist_km ≤ 1 이면 자동, mountain · city 는 팀 입력';
COMMENT ON COLUMN dong.coast_dist_km IS '경계에서 가장 가까운 해안선까지 거리(km, OSM natural=coastline). 0 = 해안에 닿음, NULL = 내륙(약 18 km 이상) 또는 경계 없음';
COMMENT ON COLUMN region.tags         IS '속성 태그 {coast, mountain, city}. coast 는 소속 행정동 중 하나라도 coast 면 자동';

COMMIT;
