-- 탐색 v6 파생 표 4개 · 2026-11-13
-- 기준: migrations/20261107_pgvector-dong-feature.sql 까지 적용된 DB. 두 번 실행해도 안전.
-- 값은 manage.py build_explore(dong_type_score · dong_hour_stat · dong_similar)와
-- build_briefs(dong_brief)가 채운다. 설계: docs/superpowers/specs/2026-11-13-explore-graph-v6-design.md §4

BEGIN;

CREATE TABLE IF NOT EXISTS dong_type_score (
  dong_id       SMALLINT     NOT NULL REFERENCES dong(dong_id) ON DELETE CASCADE,
  type          VARCHAR(12)  NOT NULL,
  score         SMALLINT     NOT NULL,
  rank          SMALLINT     NOT NULL,
  reason        VARCHAR(80)  NOT NULL,
  PRIMARY KEY (dong_id, type),
  CHECK (type IN ('immersion', 'networking', 'local', 'long_stay')),
  CHECK (score BETWEEN 0 AND 100),
  CHECK (rank BETWEEN 1 AND 4)
);
COMMENT ON TABLE  dong_type_score        IS '탐색 v6 워케이션 유형 4개 점수 (연간). explore/derive.py type_scores';
COMMENT ON COLUMN dong_type_score.rank   IS '그 동 안 순위 1~4. 동점이면 immersion → networking → local → long_stay';
COMMENT ON COLUMN dong_type_score.reason IS '화면 이유 문구 (예: 숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달))';

CREATE TABLE IF NOT EXISTS dong_hour_stat (
  dong_id       SMALLINT     NOT NULL REFERENCES dong(dong_id) ON DELETE CASCADE,
  day_group     VARCHAR(7)   NOT NULL,
  driv          REAL[]       NOT NULL,
  peak_hour     SMALLINT,
  quiet_hour    SMALLINT,
  PRIMARY KEY (dong_id, day_group),
  CHECK (day_group IN ('weekday', 'weekend')),
  CHECK (cardinality(driv) = 24)
);
COMMENT ON TABLE  dong_hour_stat            IS '탐색 v6 24시간 차량 이동 인구 (데이터 연도 월·요일 평균, population_hourly.driv_avg)';
COMMENT ON COLUMN dong_hour_stat.quiet_hour IS '평일 = 9~17시 중 최소, 주말 = 하루 전체 중 최소. 같은 값이면 이른 시각';

CREATE TABLE IF NOT EXISTS dong_similar (
  dong_id       SMALLINT     NOT NULL REFERENCES dong(dong_id) ON DELETE CASCADE,
  rank          SMALLINT     NOT NULL,
  other_id      SMALLINT     NOT NULL REFERENCES dong(dong_id) ON DELETE CASCADE,
  l2            REAL         NOT NULL,
  PRIMARY KEY (dong_id, rank),
  CHECK (rank BETWEEN 1 AND 3),
  CHECK (other_id <> dong_id)
);
COMMENT ON TABLE  dong_similar    IS '탐색 v6 비슷한 동 3개. 바닷가 여부가 같은 동 중 연간 metric_vec L2 가까운 순';
COMMENT ON COLUMN dong_similar.l2 IS '(quiet, peer, evening 점수 ÷ 100, local_life 원값) 거리';

CREATE TABLE IF NOT EXISTS dong_brief (
  dong_id       SMALLINT     PRIMARY KEY REFERENCES dong(dong_id) ON DELETE CASCADE,
  sentences     JSONB        NOT NULL,
  source        VARCHAR(5)   NOT NULL,
  model         VARCHAR(20),
  facts_hash    CHAR(64)     NOT NULL,
  generated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CHECK (source IN ('llm', 'mixed', 'rule'))
);
COMMENT ON TABLE  dong_brief            IS '탐색 v6 동네 브리핑 3문장. 월 배치에서 HyperCLOVA 생성 → 코드 검증 → 실패 문장만 규칙 문장';
COMMENT ON COLUMN dong_brief.source     IS 'llm(3문장 모두 생성 통과) · mixed(일부 규칙 문장) · rule(전부 규칙 문장)';
COMMENT ON COLUMN dong_brief.facts_hash IS '사실 목록 SHA-256. 같으면 다시 만들지 않는다';

ALTER TABLE dong_type_score ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_hour_stat  ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_similar    ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_brief      ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON dong_type_score, dong_hour_stat, dong_similar, dong_brief FROM anon, authenticated;

COMMIT;
