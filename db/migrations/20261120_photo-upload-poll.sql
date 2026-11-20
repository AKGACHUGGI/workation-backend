-- 사진 업로드(R2) · 사진 신고 · 체감 투표 · 2026-11-20
-- 기준: schema.sql 의 photo · tip · app_user 가 있는 DB. 두 번 실행해도 안전 (IF [NOT] EXISTS).
-- 설계: docs/superpowers/specs/2026-11-20-photo-upload-poll-design.md 3절
-- photo.status 의미: approved = 공개(기본), pending = 신고 누적으로 가려짐, hidden = 숨김 확정

BEGIN;

-- 사진: 노트 첨부 연결 · 바로 게시
ALTER TABLE photo ADD COLUMN IF NOT EXISTS tip_id INTEGER REFERENCES tip(tip_id) ON DELETE CASCADE;
ALTER TABLE photo ADD COLUMN IF NOT EXISTS position SMALLINT;
ALTER TABLE photo ALTER COLUMN status SET DEFAULT 'approved';
CREATE INDEX IF NOT EXISTS photo_tip_idx ON photo (tip_id) WHERE tip_id IS NOT NULL;
COMMENT ON COLUMN photo.tip_id   IS '노트 첨부 사진이면 tip_id (갤러리에 섞지 않음)';
COMMENT ON COLUMN photo.position IS '노트 안 순서 0~2';
COMMENT ON COLUMN photo.status   IS 'approved(공개), pending(신고로 가려짐), hidden(숨김 확정)';

-- 격리 · 영구 삭제 (가린 사용자 사진은 uploads/quarantine/ 으로 옮긴다)
ALTER TABLE photo ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ;
ALTER TABLE photo ADD COLUMN IF NOT EXISTS deleted_by_user BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN photo.hidden_at       IS 'hidden 이 된 시각 (30일 뒤 manage.py photos purge)';
COMMENT ON COLUMN photo.deleted_by_user IS '가려진 사진을 올린 사람이 지움 (증거로 보관 중)';

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
