-- 사진 업로드(R2) · 사진 신고 · 체감 투표 · 2026-11-20
-- 기준: schema.sql 의 tip · app_user · report 가 있는 DB. 두 번 실행해도 안전 (IF [NOT] EXISTS).
-- 설계: docs/superpowers/specs/2026-11-20-photo-upload-poll-design.md 3절
-- photo(관광공사 · 팀 사진, 데이터 담당 적재)는 바꾸지 않는다. 사용자 사진은 user_photo 에 따로 둔다.

BEGIN;

-- 사용자 사진 (노트 첨부 · 이달의 사진)
CREATE TABLE IF NOT EXISTS user_photo (
  user_photo_id   SERIAL       PRIMARY KEY,
  user_id         UUID         NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  scope           VARCHAR(6)   NOT NULL,
  unit_id         SMALLINT     NOT NULL,
  month           SMALLINT,
  url             TEXT         NOT NULL,
  tip_id          INTEGER      REFERENCES tip(tip_id) ON DELETE CASCADE,
  position        SMALLINT,
  status          VARCHAR(8)   NOT NULL DEFAULT 'approved',
  hidden_at       TIMESTAMPTZ,
  deleted_by_user BOOLEAN      NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CHECK (scope IN ('region', 'dong')),
  CHECK (month BETWEEN 1 AND 12),
  CHECK (status IN ('approved', 'pending', 'hidden'))
);
CREATE INDEX IF NOT EXISTS user_photo_unit_idx ON user_photo (scope, unit_id, month)
  WHERE status = 'approved' AND tip_id IS NULL;
CREATE INDEX IF NOT EXISTS user_photo_tip_idx  ON user_photo (tip_id) WHERE tip_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS user_photo_user_idx ON user_photo (user_id, created_at DESC);
ALTER TABLE user_photo ENABLE ROW LEVEL SECURITY;
COMMENT ON TABLE  user_photo                 IS '사용자 사진 (노트 첨부 · 이달의 사진). 파일은 R2 uploads/';
COMMENT ON COLUMN user_photo.unit_id         IS 'region_id 또는 dong_id (논리 참조)';
COMMENT ON COLUMN user_photo.month           IS '1~12, NULL = 연간. 노트 사진은 노트의 방문 월';
COMMENT ON COLUMN user_photo.url             IS 'R2 공개 URL (가려지면 uploads/quarantine/ 위치)';
COMMENT ON COLUMN user_photo.tip_id          IS '노트 첨부 사진이면 tip_id, 이달의 사진이면 NULL';
COMMENT ON COLUMN user_photo.position        IS '노트 안 순서 0~2';
COMMENT ON COLUMN user_photo.status          IS 'approved(공개), pending(신고로 가려짐), hidden(숨김 확정 · 본인 삭제)';
COMMENT ON COLUMN user_photo.hidden_at       IS 'hidden 이 된 시각 (30일 뒤 manage.py photos purge)';
COMMENT ON COLUMN user_photo.deleted_by_user IS '가려진 사진을 올린 사람이 지움 (증거로 보관 중)';

-- 신고 대상에 사용자 사진(upload) 추가. target_type 은 VARCHAR(6) 라 'upload'
ALTER TABLE report DROP CONSTRAINT IF EXISTS report_target_type_check;
ALTER TABLE report ADD CONSTRAINT report_target_type_check
  CHECK (target_type IN ('tip', 'reply', 'photo', 'upload'));
COMMENT ON COLUMN report.target_type IS 'tip, reply, photo, upload(user_photo)';
COMMENT ON COLUMN report.target_id   IS 'tip_id / reply_id / photo_id / user_photo_id (논리 참조)';

-- 체감 투표: metric_vote(지표별) 를 버리고 질문 하나에 한 표
DROP TABLE IF EXISTS metric_vote;
CREATE TABLE IF NOT EXISTS poll_vote (
  user_id     UUID        NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  scope       VARCHAR(6)  NOT NULL,
  unit_id     SMALLINT    NOT NULL,
  month       SMALLINT    NOT NULL,
  preset      VARCHAR(8)  NOT NULL DEFAULT '',
  choice      BOOLEAN     NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, scope, unit_id, month, preset),
  CHECK (scope IN ('region', 'dong')),
  CHECK (month BETWEEN 0 AND 12),
  CHECK (preset IN ('', 'quiet', 'lively', 'custom'))
);
CREATE INDEX IF NOT EXISTS poll_vote_target_idx ON poll_vote (scope, unit_id, month, preset);
ALTER TABLE poll_vote ENABLE ROW LEVEL SECURITY;
COMMENT ON TABLE  poll_vote        IS '체감 투표 (질문 하나에 한 표)';
COMMENT ON COLUMN poll_vote.month  IS '0 = 연간(annual), 1~12';
COMMENT ON COLUMN poll_vote.preset IS 'region: quiet, lively, custom / dong: 빈 문자열';
COMMENT ON COLUMN poll_vote.choice IS 'true = yes';

COMMIT;
