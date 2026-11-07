-- 자연어 검색용 문장 임베딩 테이블 tag_vocab · dong_doc · 2026-11-07
-- 기준: migrations/20261107_pgvector-dong-feature.sql 이 적용된 DB (vector 확장). 두 번 실행해도 안전.
-- 임베딩 모델: CLOVA Studio 임베딩 v2 (1024차원). 검색할 때 질문도 같은 모델로 바꿔야 한다.
--   모델을 바꾸면 두 테이블의 embedding 을 전부 다시 만든다 (model 컬럼으로 확인).
-- 값은 db/tools/build_embeddings.py 가 만든 SQL 로 채운다. dong_doc.body 는 db/05_dong_doc.sql.
-- 행 수가 수백이라 ANN 인덱스(HNSW 등) 없이 전체 비교한다.

BEGIN;

CREATE TABLE IF NOT EXISTS tag_vocab (
  vocab_id     SERIAL                   PRIMARY KEY,
  target_kind  VARCHAR(6)               NOT NULL CHECK (target_kind IN ('tag', 'metric')),
  target_key   VARCHAR(20)              NOT NULL,
  direction    SMALLINT                 NOT NULL DEFAULT 1 CHECK (direction IN (1, -1)),
  phrase       VARCHAR(50)              NOT NULL,
  embedding    extensions.vector(1024),
  model        VARCHAR(40),
  updated_at   TIMESTAMPTZ              NOT NULL DEFAULT now(),
  UNIQUE (target_kind, target_key, direction, phrase)
);
COMMENT ON TABLE  tag_vocab             IS '자연어 표현 사전. 사용자 질문 조각을 임베딩해 가장 가까운 표현의 조건(태그·지표)으로 바꾼다. 원본은 db/seed/tag_vocab.csv';
COMMENT ON COLUMN tag_vocab.target_kind IS 'tag = dong.tags 코드, metric = metric_def.metric_key';
COMMENT ON COLUMN tag_vocab.direction   IS '지표 방향. 1 = 점수 높은 곳, -1 = 점수 낮은 곳 (예: "북적이는" → quiet -1). 태그는 항상 1';
COMMENT ON COLUMN tag_vocab.model       IS '임베딩 모델 이름 (예: clova-embedding-v2)';

CREATE TABLE IF NOT EXISTS dong_doc (
  dong_id      SMALLINT                 PRIMARY KEY REFERENCES dong (dong_id) ON DELETE CASCADE,
  body         TEXT                     NOT NULL,
  embedding    extensions.vector(1024),
  model        VARCHAR(40),
  updated_at   TIMESTAMPTZ              NOT NULL DEFAULT now()
);
COMMENT ON TABLE  dong_doc           IS '행정동 소개문 (연간 기준 203곳). 모호한 질문의 보조 검색과 LLM 답변 근거. 월별 수치는 metric 을 직접 조회';
COMMENT ON COLUMN dong_doc.body      IS '지표 등급·태그·붐비는 시간·조용한 달로 만든 문장. db/05_dong_doc.sql 이 생성';
COMMENT ON COLUMN dong_doc.embedding IS 'body 의 임베딩. body 가 바뀌면 NULL 로 돌리고 build_embeddings.py 로 다시 채운다';

ALTER TABLE tag_vocab ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_doc  ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON tag_vocab, dong_doc FROM anon, authenticated;
REVOKE ALL ON SEQUENCE tag_vocab_vocab_id_seq FROM anon, authenticated;

COMMIT;
