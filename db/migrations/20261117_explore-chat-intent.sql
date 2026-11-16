-- 탐색 v7 동 패널 채팅: ask_vocab 의도에 compare_summary 추가 · 2026-11-17
-- 기준: 20261113_explore-ask.sql 이 적용된 DB. 두 번 실행해도 안전 (DROP … IF EXISTS 후 다시 만든다).
-- 이 파일을 먼저 실행한 뒤 db/tools/build_embeddings.py ask 로 만든 SQL 을 실행한다.
-- 설계: docs/superpowers/specs/2026-11-16-explore-chat-design.md §5-1

ALTER TABLE ask_vocab DROP CONSTRAINT IF EXISTS ask_vocab_intent_check;
ALTER TABLE ask_vocab ADD CONSTRAINT ask_vocab_intent_check
  CHECK (intent IN ('weekday_hours', 'weekend_hours', 'months', 'quiet', 'peer', 'evening',
                    'type', 'coast', 'life', 'similar', 'compare_summary',
                    'unknown_place', 'unknown_stay', 'unknown_transport', 'unknown_other'));
