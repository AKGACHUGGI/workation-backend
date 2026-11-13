-- 탐색 v6.1 동네에 물어보기: 의도 사전 ask_vocab · 답 dong_answer · 2026-11-13
-- 기준: migrations/20261113_explore-v6.sql 까지 적용된 DB (vector 확장). 두 번 실행해도 안전.
-- ask_vocab 은 db/tools/build_embeddings.py ask 가 만든 SQL 로 채운다 (원본 db/seed/ask_vocab.csv).
-- dong_answer 는 POST /api/explore/dongs/{id}/ask 가 채운다. 설계: docs/superpowers/specs/2026-11-13-explore-ask-design.md §3-3 · §7-1

BEGIN;

CREATE TABLE IF NOT EXISTS ask_vocab (
  vocab_id     SERIAL                   PRIMARY KEY,
  intent       VARCHAR(24)              NOT NULL,
  phrase       VARCHAR(100)             NOT NULL UNIQUE,
  canonical    BOOLEAN                  NOT NULL DEFAULT false,
  embedding    extensions.vector(1024),
  model        VARCHAR(40),
  updated_at   TIMESTAMPTZ              NOT NULL DEFAULT now(),
  CHECK (intent IN ('weekday_hours', 'weekend_hours', 'months', 'quiet', 'peer', 'evening',
                    'type', 'coast', 'life', 'similar',
                    'unknown_place', 'unknown_stay', 'unknown_transport', 'unknown_other'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ask_vocab_canonical ON ask_vocab (intent) WHERE canonical;
COMMENT ON TABLE  ask_vocab            IS '동네에 물어보기 의도 사전. 질문을 임베딩해 가장 가까운 표현의 의도로 바꾼다. 원본은 db/seed/ask_vocab.csv';
COMMENT ON COLUMN ask_vocab.canonical  IS '그 의도의 대표 질문 (CLOVA 에 사용자 문장 대신 넘기는 문장). 답할 수 있는 의도마다 1개';
COMMENT ON COLUMN ask_vocab.model      IS '임베딩 모델 이름 (예: clova-embedding-v2)';
COMMENT ON COLUMN ask_vocab.updated_at IS 'max(updated_at) = 사전 버전. 질문→의도 캐시 키에 들어간다';

CREATE TABLE IF NOT EXISTS dong_answer (
  dong_id       SMALLINT     NOT NULL REFERENCES dong(dong_id) ON DELETE CASCADE,
  intents       VARCHAR(40)  NOT NULL,
  facts_hash    CHAR(64)     NOT NULL,
  sentences     JSONB        NOT NULL,
  source        VARCHAR(5)   NOT NULL,
  model         VARCHAR(20),
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (dong_id, intents, facts_hash),
  CHECK (source IN ('llm', 'mixed', 'rule'))
);
COMMENT ON TABLE  dong_answer            IS '탐색 v6.1 동네에 물어보기 답. (동, 의도, 사실, 질문) 조합마다 한 번 만들어 다시 쓴다. build_briefs 가 60일 지난 행을 지운다';
COMMENT ON COLUMN dong_answer.intents    IS '의도 이름순 + 연결 (예: quiet+weekday_hours). unknown_* 안내는 저장하지 않는다';
COMMENT ON COLUMN dong_answer.facts_hash IS '프롬프트(사실 + 정규화한 질문)의 SHA-256. 질문이나 그래프가 바뀌면 새 행이 생긴다';
COMMENT ON COLUMN dong_answer.source     IS 'llm(전부 생성 통과) · mixed(일부 규칙 문장) · rule(전부 규칙 문장)';

ALTER TABLE ask_vocab   ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_answer ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ask_vocab, dong_answer FROM anon, authenticated;
REVOKE ALL ON SEQUENCE ask_vocab_vocab_id_seq FROM anon, authenticated;

COMMIT;
