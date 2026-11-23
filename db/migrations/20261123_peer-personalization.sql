-- 또래 밀도 개인화: app_user 연령대 · 더미 provider · 2026-11-23
-- 설계: workation-backend docs/superpowers/specs/2026-11-23-peer-personalization-design.md 4-2절
-- 실행 전 확인: SELECT conname FROM pg_constraint WHERE conrelid = 'app_user'::regclass AND contype = 'c';
--   provider CHECK 이름이 app_user_provider_check 가 아니면 이 파일을 실행하지 않는다.
BEGIN;

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS age_group VARCHAR(3);
ALTER TABLE app_user DROP CONSTRAINT IF EXISTS app_user_age_group_check;
ALTER TABLE app_user ADD CONSTRAINT app_user_age_group_check
  CHECK (age_group IN ('10s','20s','30s','40s','50s','60s','70s','80s','90s'));
ALTER TABLE app_user DROP CONSTRAINT IF EXISTS app_user_provider_check;
ALTER TABLE app_user ADD CONSTRAINT app_user_provider_check
  CHECK (provider IN ('kakao', 'google', 'apple', 'dummy'));

COMMENT ON COLUMN app_user.age_group IS '연령대 10s~90s. 또래 밀도 개인화 기준. NULL 이면 기본(20~40대)';
COMMENT ON COLUMN app_user.provider  IS 'kakao, google, apple, dummy(검증용 더미 계정, 지워도 됨)';

COMMIT;
