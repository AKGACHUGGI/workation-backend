-- 동네에 물어보기 답 테이블 dong_answer 삭제 · 2026-11-17
-- 동 1개 답은 Redis 에만 7일 둔다(실패 답은 저장하지 않고, 버전 상수로 옛 답을 무효로 한다).
-- 두 번 실행해도 안전 (IF EXISTS).
-- 설계: docs/superpowers/specs/2026-11-17-explore-ask-hcx007-design.md §5

BEGIN;
DROP TABLE IF EXISTS dong_answer;
COMMIT;
