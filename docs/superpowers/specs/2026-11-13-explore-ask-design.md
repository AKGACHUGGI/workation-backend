# 탐색 v6.1 동네에 물어보기 (백엔드) 설계

- 작성일: 2026-11-13
- 상태: 초안 (브레인스토밍 결과, 사용자 검토 대기)
- 바탕 문서: [탐색 v6 설계](./2026-11-13-explore-graph-v6-design.md) (브랜치 `docs/explore-graph-spec`). 그래프·사실·검증 규칙은 v6를 그대로 쓴다
- 웹 짝 문서: workation-web `docs/superpowers/specs/2026-11-13-W-09-동네에-물어보기-design.md` (브랜치 `docs/W-09-explore-spec`). 응답 형식은 두 문서 §5가 같다
- 브랜치: `feat/explore-ask` (`feat/explore-graph-v6` 위)

## 1. 목표

동 패널에서 사용자가 **그 동네에 대해 자유 문장으로 물으면**, Neo4j 그래프에서 그 질문에 맞는 사실만 꺼내 HyperCLOVA X가 1~3문장으로 답한다. 그래프 RAG의 local search 방식에 v6 숫자 검증을 더한 구조다.

완료 기준:

- `POST /api/explore/dongs/{id}/ask`가 §5 형식으로 답한다.
- 답의 숫자·등급·달·시각이 모두 사실 목록과 일치한다(v6 `validate_sentence` + §6 출력 검사).
- **사용자 문장은 CLOVA에 들어가지 않는다**(§4-3). 프롬프트 인젝션 경로가 없다.
- 같은 동·의도·사실이면 CLOVA를 다시 부르지 않는다(§7).
- CLOVA 키가 없어도 규칙 문장으로 동작한다.
- `uv run pytest`가 Redis·Neo4j·CLOVA 없이 통과한다.

## 2. 결정 요약

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 질문 범위 | **이 동 하나에 대한 질문만** | 필요한 사실이 그 동의 한 칸 이웃 + 비슷한 동이라 정해진 Cypher(v6 `fetch_dong`)로 충분 |
| 입력 | 자유 입력 + 실시간 생성 | 사용자 결정 |
| 이어지는 질문 | **기억하지 않음.** 질문마다 따로 답한다 | 사용자 결정. 의도 단위 캐시를 쓸 수 있다 |
| 그래프 탐색 | 우리 코드(정해진 Cypher). CLOVA는 받은 사실로 문장만 쓴다 | LLM이 쓴 Cypher는 근거가 매번 달라 검증이 어렵다. 다른 동 찾기·비교 질문으로 넓힐 때 다시 검토 |
| 의도 찾기 | 질문 임베딩 → `ask_vocab` 최근접 (홈 검색 `tag_vocab`과 같은 방식) | 표현이 달라도 뜻이 같으면 같은 의도 → 캐시 적중 |
| CLOVA 입력 | 사용자 문장 대신 **그 의도의 대표 질문**(우리가 쓴 문장) | 인젝션 차단, 캐시 공유가 자연스러움 |
| 모르는 질문 | `unknown_*` 의도 + 고정 안내 문구(A안: 무엇을 보는지 + 대신 답할 수 있는 것) | 식당·숙소 등은 데이터에 없다. 억지로 가까운 지표로 답하지 않는다 |
| 답 저장 | **Postgres `dong_answer` = 원본**, Redis = 앞단 | 답은 비용을 들여 만든 데이터라 지워지면 안 되고, SQL로 품질을 본다 |
| 캐시 | Redis 하나를 홈 검색·탐색이 같이 쓴다. 기능별 키 접두어 | 임베딩 캐시를 두 기능이 공유 |
| 남용 방지 | IP당 10회/분 · 200회/일 (DRF throttle, Redis 카운터) | 서비스 중 CLOVA 호출이 생기는 첫 기능 |

v6에서 바뀌는 원칙: v6 §1 "서비스 실행 중 LLM 호출 0회"는 **기본 브리핑**에만 해당한다. 질문 답은 처음 묻는 (동, 의도, 사실) 조합에서만 CLOVA를 부르고, 사용자 입력은 넘기지 않으며, 결과는 저장해 다시 쓴다.

넣지 않는 것: 다른 동 찾기·비교 질문, 대화 맥락, 서버 쪽 대화 저장, 홈 검색 결과 캐시(다음 작업), 사용자 팁·투표.

## 3. 의도 (intent)

### 3-1. 답할 수 있는 의도 10개

| intent | 대표 질문 (CLOVA에 넘기는 문장) | 쓰는 사실 (v6 `brief.collect_facts` id) |
| --- | --- | --- |
| `weekday_hours` | 평일엔 몇 시가 붐비고 몇 시가 한산해? | F2-1 |
| `weekend_hours` | 주말엔 몇 시가 붐비고 몇 시가 한산해? | F2-2 |
| `months` | 1년 중 언제 가면 한산해? | F3-1 |
| `quiet` | 이 동네 조용해? | F1-quiet, F1-quiet-sgg |
| `peer` | 또래 사람들이 많아? | F1-peer, F1-peer-sgg |
| `evening` | 저녁에 활기 있어? | F1-evening, F1-evening-sgg |
| `type` | 어떤 워케이션에 잘 맞아? | F4-1 ~ F4-4 |
| `coast` | 바다가 가까워? | F5-1 |
| `life` | 생활권이야, 관광지야? | F7-1 (신규, §4-2) |
| `similar` | 비슷한 동네는 어디야? | F6-1 ~ F6-3 |

### 3-2. 답할 수 없는 의도 4개 (CLOVA 호출 없음)

고정 안내 문구 + 예시 질문 3개를 돌려준다. 문구는 "무엇을 보는지 → 대신 알려 드릴 수 있는 것" 순서다(사용자 결정 A안).

| intent | 예시 표현 | 안내 문구 | 예시 질문 |
| --- | --- | --- | --- |
| `unknown_place` | 밥먹기 좋아? · 맛집 있어? · 카페 많아? · 볼거리 있어? | 이 지도는 동네의 붐빔과 분위기를 보고 있어서 식당·카페는 아직 몰라요. 대신 점심·저녁에 이 동네가 얼마나 북적이는지는 알려 드릴 수 있어요. | 평일엔 몇 시가 붐비고 몇 시가 한산해? · 저녁에 활기 있어? · 이 동네 조용해? |
| `unknown_stay` | 숙소 어때? · 방값 비싸? · 한 달 살기 집 있어? | 이 지도는 동네의 붐빔과 분위기를 보고 있어서 숙소 정보는 아직 몰라요. 대신 오래 머물기 맞는 동네인지, 언제 가면 한산한지는 알려 드릴 수 있어요. | 어떤 워케이션에 잘 맞아? · 1년 중 언제 가면 한산해? · 비슷한 동네는 어디야? |
| `unknown_transport` | 교통 편해? · 버스 자주 와? · 주차 돼? | 이 지도는 동네의 붐빔과 분위기를 보고 있어서 교통편은 아직 몰라요. 대신 시간대별로 차가 얼마나 다니는지는 알려 드릴 수 있어요. | 평일엔 몇 시가 붐비고 몇 시가 한산해? · 주말엔 몇 시가 붐비고 몇 시가 한산해? · 이 동네 조용해? |
| `unknown_other` | 날씨 어때? · 치안 괜찮아? · 와이파이 잘 돼? · 물가 비싸? | 이 지도는 동네의 붐빔과 분위기를 보고 있어서 그 질문엔 아직 답하기 어려워요. 이런 건 알려 드릴 수 있어요. | 이 동네 조용해? · 1년 중 언제 가면 한산해? · 비슷한 동네는 어디야? |

유사도가 기준(§4-1) 미만이면 `unknown_other`와 같은 응답이다.

### 3-3. 사전 `ask_vocab`

- 원본 `db/seed/ask_vocab.csv` (`intent,phrase,canonical`). 의도마다 표현 5~8개, 답할 수 있는 의도는 대표 질문 1개에 `canonical=true`.
- 표: `ask_vocab(vocab_id SERIAL PK, intent VARCHAR(24), phrase VARCHAR(100) UNIQUE, canonical BOOLEAN, embedding vector(1024), model VARCHAR(40), updated_at)`. `CHECK (intent IN (…14개))`, `UNIQUE (intent) WHERE canonical`. `vector` 컬럼이라 `tag_vocab`과 같이 **migration에만** 둔다(테스트 DB에는 pgvector가 없다).
- 임베딩은 `db/tools/build_embeddings.py ask`(신규 모드)로 채운다. 표현을 추가하면 그 행만 채운다.
- 사전 버전 = `max(updated_at)`. 질문→의도 캐시 키에 넣는다(§7).

## 4. 흐름

```
POST /api/explore/dongs/{id}/ask {"question": "..."}
 ① 정리      앞뒤 공백 제거, 1~100자 아니면 400. 캐시 키용 정규형 = 공백·?.!~ 제거 + 소문자
 ② 동 확인   v6 fetch_dong(id) (캐시 explore:dong:raw:{id}) → None 이면 404, GraphUnavailable 이면 503
 ③ 의도      캐시(질문 정규형 + 사전 버전) → 없으면 임베딩 → ask_vocab 최근접 (§4-1)
 ④ 안내      unknown_* 이면 §3-2 고정 문구로 응답 (CLOVA 없음, 저장 없음)
 ⑤ 사실      brief.collect_facts(raw) + F7-1 → 의도에 맞는 사실만 (§3-1 표)
 ⑥ 답 찾기   Redis → dong_answer(dong_id, intents, facts_hash) → 있으면 응답
 ⑦ 생성      CLOVA (대표 질문 + 사실, §4-3) → 출력 검사 (§6) → 실패 문장만 규칙 문장 (§4-4)
 ⑧ 저장      dong_answer INSERT … ON CONFLICT DO NOTHING, Redis 에 넣기, nl_search_log 기록
```

### 4-1. 의도 찾기 (`explore/ask_match.py`)

- 질문 임베딩(`core.clova.embed`, §7-3 캐시) → `ask_vocab`에서 코사인 유사도 상위 5개(`1 - (embedding <=> q)`).
- 1위 유사도 < `THRESHOLD`(0.68, 홈 검색과 같은 값에서 시작) → `unknown_other`.
- 1위 의도를 고른다. 2위 **다른** 의도가 `THRESHOLD` 이상이고 1위와 차이가 0.05 이내이며 둘 다 답할 수 있는 의도면 같이 고른다. 최대 2개, 이름순 정렬.
- 임베딩 실패(`ClovaError`, 키 없음) → 문자열 매칭: `ask_vocab.phrase`를 정규형으로 바꾼 뒤 질문 정규형과 공통 2글자 조각이 가장 많은 표현. 공통이 없으면 `unknown_other`. 홈 검색 `_match_text`와 같은 자리.

### 4-2. 사실 추가 F7-1

v6 `collect_facts`에는 생활권 사실이 없다. `explore/ask.py`에서 raw 의 `facts[code]["life"]`로 하나 더 만든다: `F7-1: 생활권 (생활권 지수 0.74)` / `관광지 (생활권 지수 0.42)`, values = 지수 문자열. 브리핑(v6)에는 넣지 않는다.

### 4-3. 프롬프트

system (고정):

```
너는 워케이션 지도 서비스의 동네 안내자다. 사용자가 준 사실 목록만 근거로 질문에 답한다.
- 1~3문장. 문장마다 60자 이내, 존댓말 해요체.
- 목록에 없는 숫자·달·시각·등급·장소 이름·추측을 쓰지 않는다.
- 목록으로 답할 수 없는 부분은 쓰지 않는다.
- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.
- 문장마다 근거로 쓴 사실 id 를 facts 에 적는다.
- JSON 하나만 출력한다: {"sentences": [{"text": "...", "facts": ["F2-1"]}]}
```

user:

```
동네: {시도} {시군구} {동}
질문: {의도의 대표 질문}            ← 의도가 2개면 두 줄
사실:
{id}: {text}                       ← §3-1 의 사실만
```

**사용자 문장은 어디에도 넣지 않는다.** CLOVA가 보는 글은 전부 코드·사전이 만든 것이다.

### 4-4. 규칙 문장 (CLOVA 실패·키 없음·검증 실패 자리)

의도마다 1~2문장. 숫자는 사실 값 그대로라 검증하지 않는다.

| intent | 규칙 문장 |
| --- | --- |
| `weekday_hours` | 평일 차량은 {peak}시에 가장 많고, 업무시간엔 {quiet}시가 가장 한산해요. (v6 브리핑 1번과 같음) |
| `weekend_hours` | 주말 차량은 {peak}시에 가장 많고, {quiet}시가 가장 한산해요. |
| `months` | {시군구}{은/는} {m·m·m}월이 가장 한산하고 {b}월이 가장 붐벼요. (v6 2번과 같음) |
| `quiet`·`peer`·`evening` | {지표}{은/는} {grade}등급({score}점)으로, 전국 {of}동 중 {rank}위예요. + 시군구 1위면 `{시군구} 안에서는 {지표} 1위예요.` |
| `type` | {1위 유형} {점수}점으로 가장 잘 맞고, 다음은 {2위 유형} {점수}점이에요. |
| `coast` | F5-1 있음: `해안선까지 {km}km인 바닷가 동네예요.` 없음: `해안선에서 1km 넘게 떨어진 동네예요.` |
| `life` | `생활권 성격이 강한 동네예요.` / `관광지 성격이 강한 동네예요.` |
| `similar` | 지표가 비슷한 동네는 {시군구 동}, {…}, {…}이에요. |

사실이 없는 의도(예: 시각 데이터가 없는 동)는 `이 동네는 그 내용을 계산할 데이터가 부족해요.` 한 문장.

## 5. API

### `POST /api/explore/dongs/{dongId}/ask`

요청 `{"question": "아침 9시쯤 카공하기 어때?"}`

200:

```json
{
  "question": "아침 9시쯤 카공하기 어때?",
  "answer": ["평일엔 9시가 가장 한산해서 오전 업무에 맞아요."],
  "intents": ["weekday_hours"],
  "source": "llm",
  "examples": ["이 동네 조용해?", "주말엔 몇 시가 붐비고 몇 시가 한산해?", "비슷한 동네는 어디야?"]
}
```

- `source`: `llm`(전부 생성 통과) · `mixed` · `rule` · `guide`(§3-2 안내).
- `examples`: 다음에 누를 예시 질문 3개(대표 질문). `guide`면 §3-2 표의 예시, 아니면 고른 의도를 뺀 답할 수 있는 의도 중 §3-1 표 순서로 3개. 화면 칩에 쓴다.
- 사실 id는 응답에 넣지 않는다.

오류 (`core.errors.error`):

| 상태 | code | message |
| --- | --- | --- |
| 400 | `QUESTION_INVALID` | 질문은 1~100자로 적어 주세요. |
| 404 | `DONG_NOT_FOUND` | 동네를 찾을 수 없어요. |
| 429 | `TOO_MANY_REQUESTS` | 잠시 후 다시 물어봐 주세요. |
| 503 | `GRAPH_UNAVAILABLE` | 탐색 데이터를 불러오지 못했어요. |

CLOVA 실패는 오류가 아니다(규칙 문장 200).

## 6. 출력 검사 (생성 문장마다, 하나라도 걸리면 그 자리만 규칙 문장)

1. v6 `brief.validate_sentence` 그대로: 60자, 인용 id 존재, 금지어, 사실에 없는 숫자·등급.
2. **근거어**: 인용한 사실의 근거어가 문장에 하나 이상 있어야 한다. 근거어 = 사실 `values` ∪ 사실별 핵심어(F1 지표 이름, F2 `평일`/`주말`, F3 `월`, F4 유형 이름, F5 `바다`/`해안`, F6 동 이름, F7 `생활권`/`관광지`). 사실과 상관없는 문장을 막는다.
3. **모양 차단**: URL(`http`, `www.`, `.com` 등), 이메일, 전화번호 모양(`\d{2,4}-\d{3,4}-\d{4}`), 영문 2글자 이상 단어.
4. 문장 수 1~3이 아니거나 JSON이 깨지면 전부 규칙 문장(`rule`).

## 7. 저장과 캐시

### 7-1. `dong_answer` (Postgres, 답 원본)

```sql
CREATE TABLE dong_answer (
  dong_id      SMALLINT     NOT NULL REFERENCES dong(dong_id) ON DELETE CASCADE,
  intents      VARCHAR(40)  NOT NULL,          -- 이름순 '+' 연결 (예: 'quiet+weekday_hours')
  facts_hash   CHAR(64)     NOT NULL,          -- 프롬프트에 넣은 사실의 SHA-256 (v6 facts_hash)
  sentences    JSONB        NOT NULL,
  source       VARCHAR(5)   NOT NULL CHECK (source IN ('llm', 'mixed', 'rule')),
  model        VARCHAR(20),
  created_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (dong_id, intents, facts_hash)
);
```

- `schema.sql` 끝 + `db/migrations/20261113_explore-ask.sql`(ask_vocab 포함), RLS on, anon·authenticated REVOKE.
- 매달 그래프가 바뀌면 사실 해시가 달라져 새 행이 생긴다. `build_briefs` 끝에 `created_at` 60일 넘은 행을 지운다.
- `rule` 답도 저장한다. CLOVA 키를 나중에 넣었을 때는 `build_briefs --force`처럼 `ask` 쪽도 지울 수단이 필요하다 → `manage.py clear_answers [--source rule]`.
- 동시에 같은 첫 질문이 오면 CLOVA가 두 번 불릴 수 있다. 드문 경우라 받아들인다(INSERT는 ON CONFLICT DO NOTHING).

### 7-2. Redis (앞단)

- 설정: `REDIS_URL` 이 있으면 Django 기본 `django.core.cache.backends.redis.RedisCache`, 없으면 지금처럼 LocMem. `KEY_PREFIX = "workation"`. 의존성 `redis>=5`.
- compose: `redis:7` 서비스, `127.0.0.1:6379`, `--maxmemory 256mb --maxmemory-policy allkeys-lru`. 단위 테스트는 Redis 없이(LocMem).
- 홈 검색과 탐색이 **같은 Redis**를 쓴다. 키 접두어로 나눈다:

| 키 | 값 | 기한 |
| --- | --- | --- |
| `emb:{모델}:{sha1(문장)}` | 임베딩 1024개 | 없음 (같은 문장이면 같은 값, LRU가 정리) |
| `explore:ask:q:{sha1(정규형)}:{사전 버전}` | 의도 목록 | 30일 |
| `explore:ask:a:{dong}:{intents}:{facts_hash}` | 답 | 40일 |
| `explore:dong:raw:{id}` | v6 `fetch_dong` raw | 1시간 (v6 응답 캐시와 같은 기한) |
| `explore:pins:*`, `explore:dong:*` | v6 그대로 | v6 그대로 |
| `throttle_explore_ask_*` | DRF 카운터 | DRF |

- Redis가 죽으면 Django 캐시 오류를 잡아 캐시 없이 진행한다(`dong_answer`는 그대로 동작). 단, throttle은 그 동안 동작하지 않는다 — 로그 경고.

### 7-3. 임베딩 캐시 (`core.clova.embed`)

`embed(text)` 안에서 `emb:` 키를 먼저 본다. 홈 검색(`search.engine.match`)도 자동으로 같은 캐시를 쓴다.

## 8. 남용 방지

- DRF `AnonRateThrottle` 두 개를 이 뷰에만: `explore_ask_burst` 10/min, `explore_ask_day` 200/day. 카운터는 Django 캐시 = Redis라 워커 사이에 공유된다.
- 프록시 뒤 IP: `REST_FRAMEWORK["NUM_PROXIES"] = env.int("DRF_NUM_PROXIES", default=None)` (Traefik 뒤면 1).
- 사용자 문장은 CLOVA·Cypher 어디에도 들어가지 않는다(로그 `nl_search_log`에만, 300자 컬럼 안).

## 9. 질문 기록

기존 `nl_search_log`(지금은 쓰는 곳이 없다)를 재사용: `query` = 질문, `parsed` = `{"scope": "dong_ask", "dong": "5115025000", "intents": [...], "top_sim": 0.84, "source": "llm"}`. 사전 보강용(기준 미만·잘못 간 질문 찾기). 기록 실패는 무시하고 응답한다.

## 10. 파일

```
explore/ask.py          순수: INTENTS, CANONICAL, GUIDE, normalize, facts_for, life_fact, anchors,
                        rule_answer, build_prompt, check_sentence, compose_answer, examples_for
explore/ask_match.py    find_intents(question) — 임베딩 → ask_vocab SQL, 문자열 매칭 대체
explore/sources.py      + load_answer, save_answer, log_question, vocab_version, delete_old_answers
explore/views.py        + ask 뷰 (throttle 두 개), urls.py + 경로
explore/management/commands/clear_answers.py
core/clova.py           embed 캐시
config/settings.py      CACHES(REDIS_URL), NUM_PROXIES, throttle 비율
compose.yaml            redis 서비스, .env.example REDIS_URL
db/seed/ask_vocab.csv, db/migrations/20261113_explore-ask.sql, db/schema.sql(dong_answer)
db/tools/build_embeddings.py  + ask 모드
```

## 11. 테스트

- **순수(`test_explore_ask.py`)**: 주문진 `jumunjin_raw` 로 의도별 사실 고르기, F7-1, 규칙 문장 10개, 프롬프트에 사용자 문장이 없음, 출력 검사(근거어 없음·URL·영문 단어·숫자 불일치 → 교체), 문장 수 이상 → `rule`, `examples_for`, 정규형.
- **매칭**: pgvector 없는 테스트 DB → 문자열 매칭 경로, 기준 미만 → `unknown_other`, 2의도 규칙(차이 0.05)은 유사도 목록을 가짜로 넣어 순수 함수로.
- **API(`test_explore_ask_api.py`)**: 정상, 같은 질문 두 번째는 CLOVA 미호출(dong_answer 적중), `unknown_place` → `guide`·CLOVA 미호출, 400·404·503·429, CLOVA 실패 → `rule` 200, 임베딩 실패 → 문자열 매칭. CLOVA·임베딩·`fetch_dong`은 monkeypatch.
- **저장**: `save_answer` 멱등, `delete_old_answers` 60일.
- **수동 (CLOVA 키·Redis 있을 때, PR 체크리스트)**: 질문 30개(의도별 2개, 모르는 질문 6개, 인젝션 문장 4개) → 의도 정답률, `source` 비율, 인젝션 문장이 답에 영향 없음, 첫 응답·캐시 응답 시간, Redis 키 확인.

## 12. 규모가 커질 때

- `ask_vocab` 행이 수천을 넘으면 HNSW 인덱스.
- `dong_answer`는 실제로 물은 조합만 쌓인다(동 3,500 × 의도 14 상한). 미리 전부 만들지 않는다.
- 질문 범위를 다른 동 찾기·비교로 넓히면 그때 LLM이 탐색 도구를 고르는 방식(에이전트형)이나 Text2Cypher를 검토한다.
