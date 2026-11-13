-- 탐색 워케이션 유형 3개 (장기체류형 삭제) · 2026-11-14
-- 기준: migrations/20261113_explore-v6.sql 까지 적용된 DB. 두 번 실행해도 안전.
-- 남은 행 순위를 1~3 으로 당긴 뒤 CHECK 를 바꾼다. 값은 이어서 manage.py build_explore 가 다시 쓴다.
-- 설계: docs/superpowers/specs/2026-11-14-explore-type-3-design.md §4-2
-- 관광공사 태그 dong.tags 의 long_stay(장기 숙소)는 다른 것이라 건드리지 않는다.

BEGIN;

DELETE FROM dong_type_score WHERE type = 'long_stay';

-- 순위 다시 매기기: 점수 높은 순, 같으면 immersion → networking → local (derive.TYPES 순서)
UPDATE dong_type_score t
   SET rank = r.new_rank
  FROM (SELECT dong_id, type,
               row_number() OVER (
                 PARTITION BY dong_id
                 ORDER BY score DESC,
                          array_position(ARRAY['immersion', 'networking', 'local'], type::text)
               ) AS new_rank
          FROM dong_type_score) r
 WHERE t.dong_id = r.dong_id AND t.type = r.type AND t.rank <> r.new_rank;

ALTER TABLE dong_type_score DROP CONSTRAINT IF EXISTS dong_type_score_type_check;
ALTER TABLE dong_type_score DROP CONSTRAINT IF EXISTS dong_type_score_rank_check;
ALTER TABLE dong_type_score
  ADD CONSTRAINT dong_type_score_type_check CHECK (type IN ('immersion', 'networking', 'local'));
ALTER TABLE dong_type_score
  ADD CONSTRAINT dong_type_score_rank_check CHECK (rank BETWEEN 1 AND 3);

COMMENT ON TABLE  dong_type_score        IS '탐색 워케이션 유형 3개 점수 (연간). explore/derive.py type_scores. 장기체류형은 2026-11-14 삭제';
COMMENT ON COLUMN dong_type_score.rank   IS '그 동 안 순위 1~3. 동점이면 immersion → networking → local';
COMMENT ON COLUMN dong_type_score.reason IS '화면 이유 문구 (예: 생활권 지수 0.74)';

COMMIT;
