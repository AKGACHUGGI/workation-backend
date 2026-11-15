-- 개인화 랭킹 행동 기록 user_event · 2026-11-15
-- 기준: schema.sql 의 app_user 가 있는 DB. 두 번 실행해도 안전 (IF NOT EXISTS).
-- 계산은 웹이 하고 서버는 기록만 받는다. GET /api/regions/popular 의 viewCount 가 이 표를 센다.
-- 설계: workation-web docs/superpowers/specs/2026-11-15-개인화-랭킹-design.md 5-6절

BEGIN;

CREATE TABLE IF NOT EXISTS user_event (
  id          BIGSERIAL    PRIMARY KEY,
  anon_id     UUID         NOT NULL,
  user_id     UUID         REFERENCES app_user (user_id) ON DELETE SET NULL,
  kind        VARCHAR(16)  NOT NULL,
  target      VARCHAR(10),
  payload     JSONB,
  created_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CHECK (kind IN ('search', 'region_view', 'dong_view'))
);
CREATE INDEX IF NOT EXISTS user_event_anon_idx   ON user_event (anon_id, created_at);
CREATE INDEX IF NOT EXISTS user_event_target_idx ON user_event (kind, target, created_at);
ALTER TABLE user_event ENABLE ROW LEVEL SECURITY;
COMMENT ON TABLE  user_event         IS '사용자 행동 기록 (개인화 랭킹). 계산은 웹, 서버는 기록과 viewCount 집계';
COMMENT ON COLUMN user_event.anon_id IS '브라우저 익명 ID (localStorage)';
COMMENT ON COLUMN user_event.user_id IS '로그인 뒤 연결. 지금은 NULL';
COMMENT ON COLUMN user_event.target  IS 'region_view: 시군구 코드 5자리, dong_view: 행정동 코드 10자리, search: NULL';
COMMENT ON COLUMN user_event.payload IS 'search: {q, preset, terrain, month}, view: {sigunguId, snapshot}';

COMMIT;
