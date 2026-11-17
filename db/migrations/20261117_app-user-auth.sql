-- 로그인(Supabase Auth): app_user 에 마스킹 이메일 · 약관 동의 시각 · 2026-11-17
-- 기준: schema.sql 의 app_user 가 있는 DB. 두 번 실행해도 안전 (IF NOT EXISTS).
-- 설계: workation-backend docs/superpowers/specs/2026-11-17-login-supabase-design.md 3-4절

BEGIN;

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS email_masked VARCHAR(100);
ALTER TABLE app_user ADD COLUMN IF NOT EXISTS agreed_at    TIMESTAMPTZ;

COMMENT ON COLUMN app_user.email_masked IS '마스킹한 이메일 (du***@kakao.com). 원본은 Supabase auth.users 에만';
COMMENT ON COLUMN app_user.agreed_at    IS '약관 동의 시각. NULL 이면 온보딩 필요';

COMMIT;
