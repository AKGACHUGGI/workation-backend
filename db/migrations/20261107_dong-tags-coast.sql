-- 행정동 속성 태그 · 해안 거리 · 2026-11-07
-- 기준: migrations/20261107_metric-formulas-django.sql 까지 적용된 DB. 두 번 실행해도 안전.
--
-- 바뀌는 것
--   1. dong.tags 추가. 명세 v0.3 의 "해안 · 산간 · 도시" 필터를 행정동 단위로 쓰고, 워케이션 조건(카페 · 도서관 ·
--      장기 숙소 등)으로 넓힌다. 자연어 검색 "바닷가", 홈 카테고리 스트립용. region.tags 는 행정동 태그의 합집합
--   2. dong.coast_dist_km 추가. 경계에서 가장 가까운 해안선까지 거리. coast 태그의 근거이자 "바다 가까운 순" 정렬용
--   값은 db/tools/build_coast_sql.py(해안), build_poi_tags_sql.py(관광공사 장소), build_tags_sql.py(팀 시트: city · mountain)
--   출력으로 이 순서대로 채운다.

BEGIN;

ALTER TABLE dong
  ADD COLUMN IF NOT EXISTS tags          TEXT[]       NOT NULL DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS coast_dist_km NUMERIC(7,3);

COMMENT ON COLUMN dong.tags          IS '속성 태그. 자동: coast(해안선 1 km 이내), beach · island · surf · library · long_stay · park · market · resort(관광공사 장소 1곳 이상), cafe(카페 3곳 이상), food(음식점 10곳 이상). 팀 입력: city · mountain';
COMMENT ON COLUMN dong.coast_dist_km IS '경계에서 가장 가까운 해안선까지 거리(km, OSM natural=coastline). 0 = 해안에 닿음, NULL = 내륙(약 18 km 이상) 또는 경계 없음';
COMMENT ON COLUMN region.tags         IS '속성 태그. 소속 행정동 태그의 합집합 (dong.tags 와 같은 코드)';

COMMIT;
