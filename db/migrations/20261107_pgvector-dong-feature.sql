-- pgvector 확장 · 동네 특징 벡터 테이블 dong_feature · 2026-11-07
-- 기준: migrations/20261107_dong-tags-coast.sql 이 적용된 DB. 두 번 실행해도 안전.
-- 값은 db/04_dong_feature.sql 이 채운다 (재실행 안전).
--
-- 용도: 동네 프로필 · 동네 비교에서 "이 동네와 성격이 비슷한 동네" 찾기, 자연어 검색(F-09)의 근거 후보.
-- 행 수가 행정동 203 × 13개월 = 2,639 라 ANN 인덱스(HNSW 등) 없이 전체 비교해도 수 ms 다.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

CREATE TABLE IF NOT EXISTS dong_feature (
  dong_id     SMALLINT                  NOT NULL REFERENCES dong (dong_id) ON DELETE CASCADE,
  month       SMALLINT                  NOT NULL CHECK (month BETWEEN 0 AND 12),
  metric_vec  extensions.vector(4)      NOT NULL,
  curve_vec   extensions.vector(48)     NOT NULL,
  updated_at  TIMESTAMPTZ               NOT NULL DEFAULT now(),
  PRIMARY KEY (dong_id, month)
);
COMMENT ON TABLE  dong_feature            IS '행정동 × 월 특징 벡터 (pgvector). 비슷한 동네 찾기용. db/04_dong_feature.sql 이 metric · dong_hourly 에서 계산';
COMMENT ON COLUMN dong_feature.month      IS '0 = 연간, 1~12';
COMMENT ON COLUMN dong_feature.metric_vec IS '[동네 조용함 점수, 또래 밀도 점수, 저녁 활기 점수, 생활권 지수] 모두 0~1 (점수 ÷ 100, 생활권 지수는 원값 비율)';
COMMENT ON COLUMN dong_feature.curve_vec  IS '24시간 붐빔 곡선 [주중 0~23시, 주말 0~23시] index_100 ÷ 100';

ALTER TABLE dong_feature ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON dong_feature FROM anon, authenticated;

COMMIT;
