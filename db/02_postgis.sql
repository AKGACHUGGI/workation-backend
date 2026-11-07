-- 선택: 읍면동 경계 폴리곤. Supabase 대시보드 Database → Extensions 에서 postgis 를 켠 뒤 실행.
-- (Supabase 는 확장을 extensions 스키마에 설치한다)
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;

ALTER TABLE dong ADD COLUMN IF NOT EXISTS geom extensions.geometry(MultiPolygon, 4326);
COMMENT ON COLUMN dong.geom IS '경계 폴리곤 (WGS84). 행정동 경계 shp 에서 adm_code 로 매핑해 채움';
CREATE INDEX IF NOT EXISTS dong_geom_idx ON dong USING gist (geom);
