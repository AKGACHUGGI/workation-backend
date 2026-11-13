# 탐색 동네 근거 그래프 v6 (백엔드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 월 배치 명령 세 개(`build_explore` → `sync_graph` → `build_briefs`)로 Postgres 파생 표 · Neo4j 동네 근거 그래프 · 3문장 브리핑을 만들고, `GET /api/explore/sigungu/{id}/pins` · `GET /api/explore/dongs/{id}` 로 웹 W-09 v6 §6 형식을 내려준다.

**Architecture:** 새 앱 `explore/`. 계산은 순수 함수(`derive.py` 파생값, `graph.py` 그래프 모양, `queries.py` 응답 모양, `brief.py` 브리핑)에 두고, SQL 은 `sources.py`, Neo4j 쓰기는 `graph_write.py`(명령 전용), 읽기는 `graphdb.read`(읽기 트랜잭션) 한 곳으로만 지난다. Postgres 가 원천이고 Neo4j 는 `sync_id` 로 MERGE 하는 읽기 전용 복사본이다. API 는 1시간 캐시 + 마지막 응답 보관, Neo4j 가 안 되면 마지막 응답 또는 503. 서비스 중 LLM 호출은 없다.

**Tech Stack:** Django 5.2 · DRF 3.18 · PostgreSQL 17 · Neo4j 5 Community + 공식 드라이버 `neo4j` 5.28 · HyperCLOVA X(HCX-005, `core/clova.py`) · pytest-django · ruff

**Spec:** `docs/superpowers/specs/2026-11-13-explore-graph-v6-design.md` (브랜치 `docs/explore-graph-spec`). 웹 짝 문서: workation-web `docs/superpowers/specs/2026-11-13-W-09-지도탐색-v6-design.md` §6 (응답 형식이 같다).
**실측 재료:** `_notes/2026-11-11-explore-graphrag/graph_data/` (`facts.json`, `build.py`, `README.md`, `examples.md`)
**선행:** `origin/main`(24ee989, 웹 화면 계약 API #12)에 `core/clova.py` · `core/errors.py` · `webapi/data.py` 가 있다. 스펙 문서는 `docs/explore-graph-spec` 브랜치에만 있으므로 구현 중에는 `git show docs/explore-graph-spec:docs/superpowers/specs/2026-11-13-explore-graph-v6-design.md` 로 읽는다.

## Global Constraints

- 브랜치: `origin/main` 에서 `feat/explore-graph-v6` 를 새로 만든다. `main` 에 직접 커밋하지 않는다.
- 커밋 메시지는 한국어 `feat:` / `test:` / `docs:` / `chore:`, 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 커밋 전 검사: `uv run ruff check .` · `uv run ruff format --check .` · `docker compose up -d --wait test-db` · `uv run pytest`. 줄 길이 100 (한글은 2칸으로 센다). **ruff format 은 마크다운 안 파이썬 블록도 검사한다.**
- 테스트 DB = compose `postgres:17` + `tests/conftest.py` 가 `db/schema.sql` 만 실행. **pgvector · PostGIS 없음** → `dong_feature` 와 `dong.geom` 을 테스트에서 못 쓴다. 그래서 `metric_vec` 는 `metric` 표에서 만들고(`04_dong_feature.sql` 과 같은 정의), 동 중심 좌표는 `sources.load_centers()` 하나에서만 PostGIS 를 쓰고 테스트에서는 바꿔 끼운다.
- `uv run pytest` 는 Neo4j · CLOVA · Supabase 없이 통과해야 한다. 실제 Neo4j 테스트는 `tests/integration/` 의 `@pytest.mark.neo4j` (`NEO4J_TEST_URI` 있을 때만).
- 사실은 LG U+ 유동인구 지표에서만 만든다. 관광공사(TourAPI)는 보조: `dong.tags` 를 핀 아이콘용 `PoiTag` 로만 넣는다. `food` 태그 제외. 외부 후기 수집 없음.
- 판단 기준은 지금 203동이 아니라 동이 늘었을 때: 핀은 시군구 단위, 상세는 동 단위 응답. Neo4j 쓰기는 1000개씩 UNWIND. 비슷한 동 계산은 `derive.similar_dongs` 한 함수.
- Neo4j 커뮤니티판은 읽기 전용 계정이 없다 → 서비스 코드는 `graphdb.read`(`session.execute_read`)만, 쓰기는 `graph_write.py`(관리 명령)만.
- Neo4j 리스트 속성에는 null 을 못 넣는다 → `driv_weekday` · `driv_weekend` 24칸은 반올림 정수, 값 없음 = `-1`, 읽을 때 `None`.
- id 규칙: 시군구 = `sigungu_code`(5자리), 동 = `adm_code`(10자리), 응답 id 는 문자열. Neo4j 노드 `id`: `sido:<2자리>` · `region:<5자리>` · `dong:<10자리>` · `grade:<metric>:<S|A|B|C>` · `type:<immersion|networking|local|long_stay>` · `hour:<0~23>` · `month:<1~12>` · `place:coast` · `life:<local|tourist>` · `poi:<태그>` · `meta`.
- 오류 응답은 `core.errors.error(status, code, message)`: 503 `GRAPH_UNAVAILABLE` "탐색 데이터를 불러오지 못했어요.", 404 `SIGUNGU_NOT_FOUND` · `DONG_NOT_FOUND`.
- 캐시는 Django 기본(LocMem, `CACHES` 설정 없음). 키 `explore:pins:{id}` · `explore:dong:{id}` 1시간, `…:last` 만료 없음.
- 브리핑: 서비스 중 LLM 호출 0회. 문장의 숫자·등급·달·시각은 코드 검증(`brief.validate_sentence`)을 통과해야 하고, 실패한 자리만 규칙 문장.
- 화면 문구에 사실 id(F1~F6)를 쓰지 않는다(내부 값).
- DB 실측 확인은 읽기 전용으로만: `.venv/bin/python` + `.env`, 접속 옵션 `-c default_transaction_read_only=on`.

## 스펙과 다르게(또는 더) 정한 것

| 항목 | 스펙 | 이 계획 | 이유 |
| --- | --- | --- | --- |
| `metric_vec` · 오전 블록 | `dong_feature`(pgvector) | `metric` 표에서 같은 정의로 계산, 오전 블록은 `dong_hourly` month 0 평일 9~11시 `avg(index_100)/100` | 테스트 DB 에 pgvector 가 없다. 값은 `04_dong_feature.sql` 과 같다 |
| `HAS_GRADE` 속성 | score | score + **rank, of, rank_sgg, of_sgg** (원값 기준, quiet 은 낮을수록 1위) | 브리핑 사실 "강릉시 21동 중 저녁 활기 1위"(F1-…-sgg) |
| `Meta` 속성 | synced_at, data_year | + `dong_count`, `region_count` | 흔함 = degree ÷ dong_count, 시군구 1위 사실의 흔함 = region_count ÷ dong_count |
| 노드 속성 | §3 표 | Dong + `code`, `pg_id` / Region `code`, `degree` / PoiTag `code`, `order`(0~11) / 사실 노드 `degree`, Hour `degree_weekday` · `degree_weekend` | 조회·정렬·공통점에 필요 |
| 동 상세 Cypher | 3개(동·이웃, 비슷한 동·공통점, 시군구 달) | `META` + `DONG`(시군구 달 포함) + `FACTS`(이 동 + 비슷한 동의 공통점 재료) | 달은 패턴 컴프리헨션으로 DONG 에 합침. Meta 없음 = 503 판정용 |
| 재시도 | 3번 다시 시도 | 첫 호출 + 3번 = 최대 4번 | 스펙 문장 그대로 |
| 사실 흔함 | 같은 사실을 가진 동 비율 | 1위가 아닌 유형(F4-2~4)과 비슷한 동(F6)은 1.0 (목록 뒤로) | degree 가 없는 사실. 강점 문장에 쓰지 않게 |
| 404 문구 | 없음 | "시군구를 찾을 수 없어요." · "동네를 찾을 수 없어요." | 웹은 코드로 분기 |
| 핀 순서 | 없음 | 동 이름 가나다 순 | 같은 데이터면 같은 응답 |
| `build_briefs` | — | `--force` (사실이 같아도 다시 생성) | 프롬프트를 바꿨을 때 |
| compose | neo4j 서비스 | + healthcheck. 단위 테스트는 `docker compose up -d --wait test-db` | neo4j 를 기다리지 않게 |
| 웹 스펙 §6-2 예시 | 노학동 id `5121055000`, 주말 한산 4시 | 테스트·mock 은 실측 `5121057000`, 주말 3시 | 웹 스펙도 "형식 예시"라고 적어 둠 |

## File Structure

```
explore/__init__.py, apps.py
explore/derive.py        순수 계산: 유형 점수·한산한 달·시각·바닷가·생활권·metric_vec·비슷한 동
explore/sources.py       Postgres 읽기·쓰기 SQL 전부 (load_derive_input, load_hourly, write_explore,
                         load_centers, load_graph_input, 브리핑 행)
explore/graph.py         Node/Rel/Graph/GraphInput, build_graph (순수), expected_counts, label_of
explore/graphdb.py       get_driver, GraphUnavailable, read(work, **params)  ← 서비스는 이것만
explore/graph_write.py   ensure_constraints, write_graph, count_graph, compare_counts  ← 명령만
explore/queries.py       Cypher(META, PINS, DONG, FACTS), fetch_pins, fetch_dong(raw),
                         shape_pins, shape_dong, fact_list, common_facts, score_diff
explore/brief.py         Fact, Brief, collect_facts, facts_hash, validate_sentence, rule_sentences,
                         josa, build_prompt, parse_reply, compose, generate
explore/views.py, urls.py
explore/management/commands/build_explore.py, sync_graph.py, build_briefs.py
tests/explore_samples.py  실측 상수, sample_graph_input(), jumunjin_raw()  ← 여러 테스트 공용
tests/test_explore_derive.py, test_explore_build.py(DB), test_explore_graph.py,
tests/test_explore_graph_write.py, test_explore_queries.py, test_explore_api.py,
tests/test_explore_brief.py, test_explore_briefs_command.py
tests/integration/__init__.py, test_neo4j_sync.py  (@pytest.mark.neo4j)
db/schema.sql (끝에 표 4개 + RLS), db/migrations/20261113_explore-v6.sql
db/05_dong_doc.sql (한산한 달 원값 기준), db/README.md, docs/개발-가이드.md
config/settings.py, config/settings_test.py, config/urls.py, core/clova.py, compose.yaml,
.env.example, pyproject.toml, uv.lock
```

## 이후 Task 가 쓰는 인터페이스 (요약)

| 이름 | 위치 | 시그니처 |
| --- | --- | --- |
| `TYPES`, `TYPE_KO`, `TypeScore(type, score, rank, reason)` | `explore/derive.py` | 동점 순서 = `TYPES` 순서 |
| `pct`, `rank_of`, `easy_months`, `quiet_months`, `busiest_month`, `type_scores`, `hour_stat`, `is_coastal`, `life_tag`, `metric_vec`, `similar_dongs` | `explore/derive.py` | Task 2 |
| `DeriveInput`, `load_derive_input()`, `data_year()`, `load_hourly(year)`, `write_explore(types, hours, similar)` | `explore/sources.py` | Task 3 |
| `load_centers()`, `load_graph_input() -> GraphInput` | `explore/sources.py` | Task 4 |
| `load_brief_targets()`, `load_brief_hashes()`, `save_brief(dong_id, sentences, source, model, facts_hash)`, `fetch_briefing(adm_code)` | `explore/sources.py` | Task 7 |
| `Node(label, id, props)`, `Rel(type, src, dst, props, key)`, `Graph(nodes, rels, meta)`, `RegionRow`, `DongRow`, `GraphInput`, `build_graph`, `expected_counts`, `label_of`, `LABELS`, `METRICS`, `METRIC_KO`, `GRADES`, `POI_TAGS` | `explore/graph.py` | Task 4 |
| `GraphUnavailable`, `get_driver()`, `read(work, **params)` | `explore/graphdb.py` | Task 5 |
| `ensure_constraints(driver)`, `write_graph(driver, graph, sync_id, synced_at)`, `count_graph(driver)`, `compare_counts(expected, actual)` | `explore/graph_write.py` | Task 5 |
| `fetch_pins(code) -> dict \| None`, `fetch_dong(code) -> dict \| None`, `shape_pins`, `shape_dong(raw, briefing)`, `fact_list`, `common_facts`, `scores_of`, `score_diff` | `explore/queries.py` | Task 6 |
| `Fact`, `Brief(sentences, source, model)`, `collect_facts(raw)`, `facts_hash`, `validate_sentence`, `rule_sentences`, `generate(raw, facts, chat)` | `explore/brief.py` | Task 8 |

`fetch_dong` 의 raw 모양 (Task 6 이 만들고 Task 8·9 가 쓴다):

```
{"rec": {"dong": {code, name, coast_km, driv_weekday, driv_weekend},
         "region": {code, name}, "sido": str,
         "types": [[rank, 이름, score, reason]], "grades": [[metric, grade, score, rank, of, rank_sgg, of_sgg]],
         "hours": [["QUIET_AT"|"PEAK_AT", day, h]], "near": [km], "months": [[m, raw, quiet]],
         "similar": [[rank, code, name, 시군구 이름]]},
 "facts": {code: {"code", "grades": [{metric, grade, score, degree}], "top": [{id, name, degree}],
                  "quiet": [{day, h, degree}], "near": [{id, kind, degree}], "life": [{id, name, degree}],
                  "region": {id, name, degree}}},
 "synced_at": str, "dong_count": int, "region_count": int}
```

---

### Task 0: 작업 브랜치

- [ ] **Step 1: 브랜치 만들기와 기준 확인**

```bash
cd workation-backend
git fetch origin
git checkout -b feat/explore-graph-v6 origin/main
docker compose up -d --wait test-db
uv run pytest -q
```

Expected: 기존 테스트 전부 PASS (실패가 있으면 시작 전에 원인을 먼저 본다).

---

### Task 1: 앱 뼈대 · 설정 · 파생 표 4개

**Files:**
- Create: `explore/__init__.py` (빈 파일), `explore/apps.py`, `db/migrations/20261113_explore-v6.sql`
- Modify: `pyproject.toml`, `uv.lock` (`neo4j`), `config/settings.py`, `config/settings_test.py`, `compose.yaml`, `.env.example`, `db/schema.sql` (끝), `tests/test_schema.py`

**Interfaces:**
- Consumes: 없음
- Produces: 표 `dong_type_score(dong_id, type, score, rank, reason)`, `dong_hour_stat(dong_id, day_group, driv REAL[24], peak_hour, quiet_hour)`, `dong_similar(dong_id, rank, other_id, l2)`, `dong_brief(dong_id, sentences JSONB, source, model, facts_hash, generated_at)`. 설정 `settings.NEO4J_URI` · `NEO4J_USER` · `NEO4J_PASSWORD`. 앱 `explore` 등록.

- [ ] **Step 1: 실패하는 테스트 — 표 이름 4개 추가**

`tests/test_schema.py` 의 `EXPECTED_TABLES` 끝(`"nl_search_log",` 다음)에 추가:

```text
    "dong_type_score",
    "dong_hour_stat",
    "dong_similar",
    "dong_brief",
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_schema.py -v`
Expected: FAIL — `assert EXPECTED_TABLES <= tables` (새 표 4개가 없다)

- [ ] **Step 3: `db/schema.sql` 끝(마지막 `ALTER TABLE nl_search_log … ENABLE ROW LEVEL SECURITY;` 다음)에 추가**

```sql
-- ───────────────────────── 탐색 v6 파생 표 (2026-11-13) ─────────────────────────
-- 원천은 metric · population_hourly. manage.py build_explore 가 세 표를 한 트랜잭션에서 다시 만들고,
-- build_briefs 가 dong_brief 를 채운다. Neo4j 는 이 표를 복사만 한다 (sync_graph).

CREATE TABLE dong_type_score (
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

CREATE TABLE dong_hour_stat (
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

CREATE TABLE dong_similar (
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

CREATE TABLE dong_brief (
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

ALTER TABLE dong_type_score        ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_hour_stat         ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_similar           ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_brief             ENABLE ROW LEVEL SECURITY;
```

- [ ] **Step 4: Supabase 용 migration `db/migrations/20261113_explore-v6.sql`**

```sql
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
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 6: 의존성 · 앱 · 설정**

Run: `uv add "neo4j>=5.28,<6"`
Expected: `pyproject.toml` dependencies 에 `"neo4j>=5.28,<6"`, `uv.lock` 갱신.

`explore/__init__.py` 는 빈 파일. `explore/apps.py`:

```python
from django.apps import AppConfig


class ExploreConfig(AppConfig):
    name = "explore"
```

`config/settings.py` 의 `INSTALLED_APPS` 끝에 `"explore",` 를 넣는다 (`"webapi",` 다음). 그리고 `CLOVA_API_KEY = …` 줄 다음에 빈 줄 하나를 두고 추가:

```python
# 탐색 그래프 (Neo4j, Postgres 의 읽기 전용 복사본). 비어 있으면 /api/explore/* 가 503
NEO4J_URI = env("NEO4J_URI", default="")
NEO4J_USER = env("NEO4J_USER", default="neo4j")
NEO4J_PASSWORD = env("NEO4J_PASSWORD", default="")
```

`config/settings_test.py` 끝에 빈 줄 하나를 두고 추가:

```python
# .env 에 Neo4j 접속 정보가 있어도 단위 테스트는 붙지 않는다 (통합 테스트는 NEO4J_TEST_URI)
NEO4J_URI = ""
```

`.env.example` 의 `CLOVA_API_KEY=` 줄 다음에 추가:

```
# 탐색 그래프 Neo4j. 비어 있으면 /api/explore/* 가 503. 로컬 compose 의 neo4j 는
# NEO4J_URI=bolt://localhost:7687, NEO4J_PASSWORD=localpassword (compose 기본값)
NEO4J_URI=
NEO4J_USER=neo4j
NEO4J_PASSWORD=
```

`compose.yaml` 전체를 다음으로 바꾼다 (`test-db` 는 그대로, 맨 위 주석과 `neo4j` · `volumes` 추가):

```yaml
# 로컬 테스트 DB 와 탐색 그래프 Neo4j. 앱 · Traefik · Redis 는 배포 세팅 때 추가한다.
# 단위 테스트는 test-db 만 있으면 된다: docker compose up -d --wait test-db
services:
  test-db:
    image: postgres:17
    environment:
      POSTGRES_PASSWORD: postgres
    ports:
      - "${TEST_DB_PORT:-5432}:5432"   # 5432 를 다른 DB 가 쓰면 TEST_DB_PORT=5433 으로 (테스트 설정도 같은 변수를 읽음)
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10

  # 탐색 그래프 (Postgres 의 읽기 전용 복사본). 포트는 이 컴퓨터에서만 연다 (스펙 §5-2)
  neo4j:
    image: neo4j:5-community
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-localpassword}
      NEO4J_server_memory_heap_initial__size: 512m
      NEO4J_server_memory_heap_max__size: 512m
    ports:
      - "127.0.0.1:7474:7474"
      - "127.0.0.1:7687:7687"
    volumes:
      - neo4j-data:/data
    healthcheck:
      test: ["CMD-SHELL", "wget -q --spider http://localhost:7474 || exit 1"]
      interval: 5s
      timeout: 5s
      retries: 30

volumes:
  neo4j-data:
```

- [ ] **Step 7: 설정 확인**

```bash
uv run python -c "import neo4j; print(neo4j.__version__)"
DJANGO_SETTINGS_MODULE=config.settings_test uv run python manage.py check
docker compose config --quiet && echo compose-ok
```

Expected: `5.28.x` · `System check identified no issues (0 silenced).` · `compose-ok`

- [ ] **Step 8: 전체 검사 후 Commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
git add pyproject.toml uv.lock config/settings.py config/settings_test.py compose.yaml .env.example \
  explore/__init__.py explore/apps.py db/schema.sql db/migrations/20261113_explore-v6.sql \
  tests/test_schema.py
git commit -m "feat: explore 앱 뼈대 · Neo4j 설정 · 탐색 v6 파생 표 4개

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 파생값 순수 계산 `derive.py`

**Files:**
- Create: `explore/derive.py`, `tests/explore_samples.py` (실측 상수만, 함수는 Task 4·6 에서 추가), `tests/test_explore_derive.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `TYPES = ("immersion", "networking", "local", "long_stay")` (동점 순서), `TYPE_KO`, `EASY_RAW = 80`, `COAST_KM = 1.0`, `LOCAL_LIFE_MIN = 0.7`, `WORK_HOURS = range(9, 18)`
  - `TypeScore(type: str, score: int, rank: int, reason: str)` (frozen dataclass)
  - `pct(vals, v) -> float`, `rank_of(vals, v, desc=True) -> int`
  - `easy_months(ease_raw: dict[int, float]) -> list[int]`, `quiet_months(ease_raw, n=3) -> list[int]`(달 순), `busiest_month(ease_raw) -> int | None`
  - `type_scores(dong_metrics, region_metrics, morning, region_of) -> dict[dong_id, list[TypeScore]]` (rank 순)
  - `hour_stat(driv: list[float | None], day_group: str) -> tuple[peak | None, quiet | None]`
  - `is_coastal(km) -> bool`, `life_tag(local_life) -> "local" | "tourist"`, `metric_vec(q, p, e, ll) -> tuple`
  - `similar_dongs(vecs, coastal, k=3) -> dict[dong_id, list[tuple[other_id, l2]]]`
  - `tests/explore_samples.py`: `GANGNEUNG_RAW`, `JEONJU_RAW`, `SOKCHO_RAW`, `JUMUNJIN_WEEKDAY`, `JUMUNJIN_WEEKEND`, `STAY`, `curve(peak, quiet)`

점수 반올림은 Python `round()`(반올림 짝수)다. 주문진읍 네트워킹형 0.5·39 + 0.5·62 = 50.5 → **50** 이 스펙 실측값과 맞는 이유다. 로컬살이형 60 · 몰입형 36 은 203동 분포가 있어야 나오므로 단위 테스트가 아니라 Task 11 수동 체크리스트로 확인한다.

- [ ] **Step 1: 공용 실측 상수 `tests/explore_samples.py`**

```python
"""탐색 v6 테스트 공용 재료. DB·Neo4j 없이 쓴다.

- sample_graph_input(): 시군구 3 · 동 4 (주문진읍·연곡면·노학동 바닷가, 금암동 내륙)
- jumunjin_raw(): queries.fetch_dong("5115025000") 이 돌려줄 값. 주문진읍 실측(2025)과
  graph_data/facts.json 으로 센 203동 기준 degree
"""

GANGNEUNG_RAW = dict(
    enumerate([80.3, 69.8, 78.4, 70.0, 81.9, 78.0, 89.4, 100.0, 70.5, 85.9, 72.5, 74.9], 1)
)
JEONJU_RAW = dict(
    enumerate([79.6, 74.8, 94.4, 84.3, 86.9, 82.1, 83.0, 79.1, 93.1, 100.0, 87.8, 86.0], 1)
)
SOKCHO_RAW = {m: (100.0 if m in (7, 8) else 70.0) for m in range(1, 13)}
JUMUNJIN_WEEKDAY = [
    637, 511, 478, 471, 497, 601, 782, 1129, 1576, 1573, 1712, 1969,
    2064, 2042, 2036, 2024, 1969, 1920, 1666, 1321, 1091, 941, 793, 680,
]  # fmt: skip
JUMUNJIN_WEEKEND = [
    790, 638, 593, 569, 580, 669, 828, 1078, 1408, 1769, 2203, 2623,
    2808, 2824, 2833, 2766, 2548, 2353, 2010, 1659, 1369, 1122, 912, 788,
]  # fmt: skip
STAY = "숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달)"


def curve(peak: int, quiet: int) -> list[float]:
    """peak 시각 200, quiet 시각 50, 나머지 100 인 24시간 값."""
    out = [100.0] * 24
    out[peak], out[quiet] = 200.0, 50.0
    return out
```

- [ ] **Step 2: 실패하는 테스트 `tests/test_explore_derive.py`**

```python
"""explore/derive.py 순수 계산. 손으로 계산 가능한 4동 + 강릉·주문진읍 실측 일부."""

import pytest

from explore import derive
from tests.explore_samples import GANGNEUNG_RAW, JUMUNJIN_WEEKDAY, JUMUNJIN_WEEKEND

# 원값 ≤ 80 인 달이 3개인 시군구 (1·2·3월)
THREE_EASY_RAW = {m: (70.0 if m <= 3 else 95.0) for m in range(1, 13)}


def four_dongs():
    """동 1·2 는 강릉(region 10), 3·4 는 한산한 달 3개인 시군구(region 20)."""
    dong_metrics = {
        1: {"quiet": 80, "peer": 20, "evening": 40, "local_life": 0.9},
        2: {"quiet": 40, "peer": 60, "evening": 80, "local_life": 0.5},
        3: {"quiet": 20, "peer": 90, "evening": 70, "local_life": 0.75},
        4: {"quiet": 60, "peer": 40, "evening": 20, "local_life": 0.6},
    }
    region_metrics = {
        10: {"overnight": 64, "ease_raw": GANGNEUNG_RAW},
        20: {"overnight": 30, "ease_raw": THREE_EASY_RAW},
    }
    morning = {1: 0.4, 2: 0.8, 3: 0.6, 4: 0.2}  # 백분위 50 · 100 · 75 · 25
    region_of = {1: 10, 2: 10, 3: 20, 4: 20}
    return dong_metrics, region_metrics, morning, region_of


def by_type(scores):
    return {s.type: s for s in scores}


def test_pct_counts_values_at_or_below():
    assert derive.pct([0.4, 0.8, 0.6, 0.2], 0.6) == 75
    assert derive.pct([1, 1, 2], 1) == pytest.approx(66.667, abs=1e-3)


def test_type_scores_four_dongs():
    out = derive.type_scores(*four_dongs())
    # 몰입형 = 0.6·quiet + 0.4·(100 − 오전 백분위), 네트워킹형 = 0.5·peer + 0.5·evening,
    # 로컬살이형 = 생활권 지수 백분위, 장기체류형 = 0.5·overnight + 0.5·(한산한 달 ÷ 12 × 100)
    one = {"immersion": 68, "networking": 30, "local": 100, "long_stay": 61}
    two = {"immersion": 24, "networking": 70, "local": 25, "long_stay": 61}
    assert {t: s.score for t, s in by_type(out[1]).items()} == one
    assert {t: s.score for t, s in by_type(out[2]).items()} == two
    # 0.5·30 + 0.5·25 = 27.5 → 28 (반올림 짝수)
    assert by_type(out[3])["long_stay"].score == 28
    assert [s.type for s in out[1]] == ["local", "immersion", "long_stay", "networking"]
    assert [s.type for s in out[3]] == ["networking", "local", "long_stay", "immersion"]
    assert [s.rank for s in out[4]] == [1, 2, 3, 4]
    assert [s.type for s in out[4]] == ["immersion", "local", "networking", "long_stay"]


def test_type_reasons():
    t = by_type(derive.type_scores(*four_dongs())[1])
    assert t["immersion"].reason == "조용함 80 · 오전 한산 50"
    assert t["networking"].reason == "또래 밀도 20 · 저녁 활기 40"
    assert t["local"].reason == "생활권 지수 0.90"
    assert t["long_stay"].reason == "숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달)"


def test_long_stay_matches_jumunjin():
    # 강릉 숙박 성향 64, 한산한 달 7개 → 0.5·64 + 0.5·7/12·100 = 61.17 → 61 (실측 주문진읍)
    out = derive.type_scores(*four_dongs())
    assert by_type(out[2])["long_stay"].score == 61


def test_networking_rounds_half_to_even():
    # 주문진읍 또래 39 · 저녁 62 → 50.5 → 50 (스펙 값)
    dong_metrics = {1: {"quiet": 31, "peer": 39, "evening": 62, "local_life": 0.7365}}
    region_metrics = {10: {"overnight": 64, "ease_raw": GANGNEUNG_RAW}}
    out = derive.type_scores(dong_metrics, region_metrics, {1: 0.5}, {1: 10})
    assert by_type(out[1])["networking"].score == 50


def test_ties_follow_type_order():
    # A: 몰입형 0.6·50 + 0.4·50 = 50, 네트워킹형 50, 로컬살이형 백분위 50 → 셋 동점, 장기체류형 0
    dong_metrics = {
        "A": {"quiet": 50, "peer": 50, "evening": 50, "local_life": 0.3},
        "B": {"quiet": 90, "peer": 90, "evening": 90, "local_life": 0.8},
    }
    no_easy = {m: 100.0 for m in range(1, 13)}
    region_metrics = {10: {"overnight": 0, "ease_raw": no_easy}}
    out = derive.type_scores(dong_metrics, region_metrics, {"A": 0.1, "B": 0.9}, {"A": 10, "B": 10})
    assert [(s.type, s.score, s.rank) for s in out["A"]] == [
        ("immersion", 50, 1),
        ("networking", 50, 2),
        ("local", 50, 3),
        ("long_stay", 0, 4),
    ]


def test_months_use_raw_values():
    assert derive.quiet_months(GANGNEUNG_RAW) == [2, 4, 9]
    assert derive.busiest_month(GANGNEUNG_RAW) == 8
    assert derive.easy_months(GANGNEUNG_RAW) == [2, 3, 4, 6, 9, 11, 12]


def test_quiet_months_tie_takes_earlier_month():
    raw = {m: 90.0 for m in range(1, 13)} | {11: 50.0, 5: 60.0, 7: 60.0, 3: 60.0}
    assert derive.quiet_months(raw) == [3, 5, 11]


def test_busiest_month_empty_is_none():
    assert derive.busiest_month({}) is None


def test_hour_stat_jumunjin():
    assert derive.hour_stat(JUMUNJIN_WEEKDAY, "weekday") == (12, 9)
    assert derive.hour_stat(JUMUNJIN_WEEKEND, "weekend") == (14, 3)


def test_hour_stat_weekday_quiet_stays_in_work_hours_and_ties_take_earlier():
    driv = [10.0] * 24
    driv[3] = 1.0  # 새벽은 평일 한산 후보가 아니다
    driv[11] = driv[15] = 5.0
    driv[20] = 50.0
    driv[21] = 50.0
    assert derive.hour_stat(driv, "weekday") == (20, 11)
    assert derive.hour_stat(driv, "weekend") == (20, 3)


def test_hour_stat_skips_missing_hours():
    driv = [None] * 24
    driv[10], driv[13] = 7.0, 3.0
    assert derive.hour_stat(driv, "weekday") == (10, 13)
    assert derive.hour_stat([None] * 24, "weekend") == (None, None)


def test_coastal_and_life():
    assert derive.is_coastal(0.0) and derive.is_coastal(1.0)
    assert not derive.is_coastal(1.01) and not derive.is_coastal(None)
    assert derive.life_tag(0.7) == "local" and derive.life_tag(0.6999) == "tourist"


def test_metric_vec_matches_dong_feature_definition():
    assert derive.metric_vec(31, 39, 62, 0.73654) == (0.31, 0.39, 0.62, 0.7365)


def test_similar_dongs_same_coast_only_three_and_not_self():
    vecs = {
        1: (0.0, 0.0, 0.0, 0.0),
        2: (0.1, 0.0, 0.0, 0.0),
        3: (0.2, 0.0, 0.0, 0.0),
        4: (0.05, 0.0, 0.0, 0.0),  # 내륙 — 1 과 가장 가깝지만 후보 아님
        5: (0.3, 0.0, 0.0, 0.0),
        6: (0.1, 0.0, 0.0, 0.0),  # 2 와 거리 같음 → id 순
    }
    coastal = {1: True, 2: True, 3: True, 4: False, 5: True, 6: True}
    out = derive.similar_dongs(vecs, coastal)
    assert [o for o, _ in out[1]] == [2, 6, 3]
    assert out[1][0][1] == pytest.approx(0.1)
    assert all(o != d and coastal[o] == coastal[d] for d, rows in out.items() for o, _ in rows)
    assert out[4] == []  # 같은 바닷가 여부의 다른 동이 없으면 비어 있다
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_derive.py -v`
Expected: FAIL — `ImportError: cannot import name 'derive' from 'explore'`

- [ ] **Step 4: 구현 `explore/derive.py`**

```python
"""탐색 v6 파생값 (순수 함수). DB 를 모른다. 입력은 explore/sources.py 가 읽어 온 값이다.

식과 기준은 스펙 §4, 실측 재료는 _notes/2026-11-11-explore-graphrag/graph_data/build.py 와 같다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 동점이면 이 순서가 앞선다 (스펙 §4-1 표 순서)
TYPES = ("immersion", "networking", "local", "long_stay")
TYPE_KO = {
    "immersion": "몰입형",
    "networking": "네트워킹형",
    "local": "로컬살이형",
    "long_stay": "장기체류형",
}
EASY_RAW = 80  # 한산한 달 = ease 원값 ≤ 80 (연중 최대 달보다 방문 20% 이상 적은 달)
COAST_KM = 1.0
LOCAL_LIFE_MIN = 0.7
WORK_HOURS = range(9, 18)  # 평일 한산 시각은 9~17시 안에서


@dataclass(frozen=True)
class TypeScore:
    type: str
    score: int
    rank: int
    reason: str


def pct(vals: list[float], v: float) -> float:
    """v 이하인 값의 비율 × 100 (build.py 와 같은 백분위)."""
    return 100 * sum(1 for x in vals if x <= v) / len(vals)


def rank_of(vals: list[float], v: float, desc: bool = True) -> int:
    """나보다 앞선 값 수 + 1. desc=False 면 낮을수록 1위."""
    return 1 + sum(1 for x in vals if (x > v if desc else x < v))


def easy_months(ease_raw: dict[int, float]) -> list[int]:
    return sorted(m for m, raw in ease_raw.items() if raw <= EASY_RAW)


def quiet_months(ease_raw: dict[int, float], n: int = 3) -> list[int]:
    """원값 낮은 n개 달(같으면 이른 달), 달 순으로."""
    return sorted(sorted(ease_raw, key=lambda m: (ease_raw[m], m))[:n])


def busiest_month(ease_raw: dict[int, float]) -> int | None:
    if not ease_raw:
        return None
    return min(ease_raw, key=lambda m: (-ease_raw[m], m))


def type_scores(
    dong_metrics: dict[int, dict[str, float]],
    region_metrics: dict[int, dict],
    morning: dict[int, float],
    region_of: dict[int, int],
) -> dict[int, list[TypeScore]]:
    """동마다 유형 4개 점수·순위·이유 (스펙 §4-1).

    dong_metrics: {dong_id: {"quiet", "peer", "evening": 점수, "local_life": 원값}}
    region_metrics: {region_id: {"overnight": 점수, "ease_raw": {1~12: 원값}}}
    morning: {dong_id: 평일 9~11시 보행 지수 평균 (0~1)}
    """
    mornings = [morning[d] for d in dong_metrics]
    lives = [m["local_life"] for m in dong_metrics.values()]
    out = {}
    for d, m in dong_metrics.items():
        region = region_metrics[region_of[d]]
        calm_am = 100 - pct(mornings, morning[d])
        n_easy = len(easy_months(region["ease_raw"]))
        raw = {
            "immersion": (
                0.6 * m["quiet"] + 0.4 * calm_am,
                f"조용함 {m['quiet']} · 오전 한산 {round(calm_am)}",
            ),
            "networking": (
                0.5 * m["peer"] + 0.5 * m["evening"],
                f"또래 밀도 {m['peer']} · 저녁 활기 {m['evening']}",
            ),
            "local": (pct(lives, m["local_life"]), f"생활권 지수 {m['local_life']:.2f}"),
            "long_stay": (
                0.5 * region["overnight"] + 0.5 * (n_easy / 12 * 100),
                f"숙박 성향 {region['overnight']} · 한산한 달 {n_easy}개"
                "(성수기보다 20% 이상 적은 달)",
            ),
        }
        # round() 는 반올림 짝수: 0.5·39 + 0.5·62 = 50.5 → 50 (실측 주문진읍 네트워킹형과 같음)
        scored = sorted(TYPES, key=lambda t: (-round(raw[t][0]), TYPES.index(t)))
        out[d] = [
            TypeScore(t, round(raw[t][0]), i, raw[t][1]) for i, t in enumerate(scored, start=1)
        ]
    return out


def hour_stat(driv: list[float | None], day_group: str) -> tuple[int | None, int | None]:
    """(가장 붐비는 시각, 가장 한산한 시각). 같은 값이면 이른 시각.
    평일 한산은 9~17시 안에서, 주말은 하루 전체에서 고른다. 값 없는 시각(None)은 건너뛴다."""
    hours = [h for h in range(24) if driv[h] is not None]
    quiet_pool = [h for h in hours if h in WORK_HOURS] if day_group == "weekday" else hours
    peak = min(hours, key=lambda h: (-driv[h], h)) if hours else None
    quiet = min(quiet_pool, key=lambda h: (driv[h], h)) if quiet_pool else None
    return peak, quiet


def is_coastal(km: float | None) -> bool:
    return km is not None and km <= COAST_KM


def life_tag(local_life: float) -> str:
    return "local" if local_life >= LOCAL_LIFE_MIN else "tourist"


def metric_vec(quiet: int, peer: int, evening: int, local_life: float) -> tuple[float, ...]:
    """db/04_dong_feature.sql 의 metric_vec 와 같은 정의."""
    return (quiet / 100, peer / 100, evening / 100, round(local_life, 4))


def similar_dongs(
    vecs: dict[int, tuple[float, ...]], coastal: dict[int, bool], k: int = 3
) -> dict[int, list[tuple[int, float]]]:
    """바닷가 여부가 같은 동 중 L2 가까운 k개 [(other_id, l2)]. 같은 거리면 id 순.

    지금은 전수 비교(203동). 동이 수천을 넘으면 이 함수만 pgvector HNSW 조회로 바꾼다 (스펙 §4-3).
    """
    out = {}
    for d, v in vecs.items():
        pool = [(math.dist(v, vecs[o]), o) for o in vecs if o != d and coastal[o] == coastal[d]]
        out[d] = [(o, dist) for dist, o in sorted(pool)[:k]]
    return out
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_explore_derive.py -v`
Expected: 15 passed

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add explore/derive.py tests/explore_samples.py tests/test_explore_derive.py
git commit -m "feat: 탐색 v6 파생값 계산 (유형 점수 · 한산한 달 원값 · 차량 시각 · 비슷한 동)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Postgres 읽기·쓰기 `sources.py` + `manage.py build_explore`

**Files:**
- Create: `explore/sources.py`, `explore/management/__init__.py`, `explore/management/commands/__init__.py` (빈 파일 둘), `explore/management/commands/build_explore.py`
- Test: `tests/test_explore_build.py` (테스트 DB)

**Interfaces:**
- Consumes: Task 2 `derive.*`, `TypeScore`; Task 1 표
- Produces:
  - `DONG_OK` (집계 제외·코드 없는 동 거르는 SQL 조건), `_rows(sql, params=())`, `_float(v)`
  - `data_year() -> int` (= `max(population_hourly.year)`, 지금 2025)
  - `DeriveInput(dong_metrics, region_metrics, morning, region_of, coast_km)`, `load_derive_input() -> DeriveInput`
  - `load_hourly(year) -> {dong_id: {"weekday" | "weekend": [24 × float | None]}}` (`avg(driv_avg)`)
  - `write_explore(types, hours, similar)` — 한 트랜잭션 DELETE → INSERT 3표. `hours = {dong_id: {day: (driv, peak, quiet)}}`
  - 명령 `build_explore` 출력: `2025년 · 동 N → dong_type_score 4N · dong_hour_stat M · dong_similar K`

- [ ] **Step 1: 실패하는 테스트 `tests/test_explore_build.py`**

```python
"""manage.py build_explore · explore/sources.py (테스트 DB).
테스트 DB 에는 PostGIS 가 없어 동 중심 좌표(load_centers)만 바꿔 끼운다."""

import math
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection

from explore import derive, sources
from metrics.models import Metric, MetricDef
from regions.models import Dong, DongHourly, Region
from tests.explore_samples import (
    GANGNEUNG_RAW,
    JEONJU_RAW,
    JUMUNJIN_WEEKDAY,
    JUMUNJIN_WEEKEND,
    curve,
)

KEYS = ("quiet", "peer", "evening", "local_life", "overnight", "ease")
STAY = "숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달)"
# dong_id: (region_id, 이름, adm_code, 해안 km, 태그, (quiet, peer, evening) 각 (점수, 등급, 원값),
#           local_life 원값, 평일 9~11시 보행 지수 index_100)
DONGS = {
    116: (1, "주문진읍", "5115025000", 0, ["beach", "cafe", "food", "coast"],
          ((31, "B", 0.8721), (39, "B", 0.3735), (62, "A", 0.278)), 0.7365, 40),
    112: (1, "연곡면", "5115037000", 0, ["beach", "coast"],
          ((55, "A", 0.80), (20, "C", 0.30), (40, "B", 0.20)), 0.65, 80),
    901: (9, "금암동", "5211357500", None, [],
          ((21, "C", 0.95), (97, "S", 0.60), (94, "S", 0.45)), 0.7218, 60),
    902: (9, "노송동", "5211160500", None, ["market"],
          ((6, "C", 0.99), (92, "S", 0.58), (95, "S", 0.46)), 0.70, 20),
}  # fmt: skip
HOURS = {  # dong_id: (평일, 주말) 차량 이동 인구 24칸
    116: (JUMUNJIN_WEEKDAY, JUMUNJIN_WEEKEND),
    112: (curve(13, 9), curve(15, 3)),
    901: (curve(17, 10), curve(13, 5)),
    902: (curve(18, 11), curve(12, 6)),
}
# ease 점수: 점수 기준으로 고르면 한산한 달이 1·4·10월 (db/05_dong_doc.sql 버그와 같은 상황)
EASE_SCORE = {4: 95, 10: 90, 1: 85}


def time_band(h):
    return "night" if h < 6 else "morning" if h < 12 else "day" if h < 18 else "evening"


def insert_hourly():
    rows = []
    for dong_id, (weekday, weekend) in HOURS.items():
        for h in range(24):
            # 평일은 두 달(1·2월) 값의 평균이 weekday[h] 가 되도록 ±1
            rows.append((dong_id, 2025, 1, 1, h, "weekday", time_band(h), 4, weekday[h] - 1))
            rows.append((dong_id, 2025, 2, 1, h, "weekday", time_band(h), 4, weekday[h] + 1))
            rows.append((dong_id, 2025, 1, 6, h, "weekend", time_band(h), 4, weekend[h]))
    rows.append((116, 2024, 1, 1, 0, "weekday", "night", 4, 99999.0))  # 데이터 연도가 아님
    with connection.cursor() as cur:
        cur.executemany(
            "INSERT INTO population_hourly (dong_id, year, month, dow, hour, day_group,"
            " time_band, weekday_count, driv_avg) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            rows,
        )


@pytest.fixture
def explore_data(db):
    for key in KEYS:
        MetricDef.objects.create(metric_key=key, name_ko=key, higher_is="good")
    Region.objects.create(
        region_id=1, sido_name="강원특별자치도", sigungu_name="강릉시", sigungu_code="51150"
    )
    Region.objects.create(
        region_id=9, sido_name="전북특별자치도", sigungu_name="전주시", sigungu_code="52110"
    )
    Dong.objects.create(dong_id=199, region_id=1, dong_name="수동면", is_excluded=True, tags=[])
    metrics, curves = [], []
    for dong_id, (region_id, name, code, km, tags, grades, life, am) in DONGS.items():
        Dong.objects.create(
            dong_id=dong_id,
            region_id=region_id,
            dong_name=name,
            adm_code=code,
            coast_dist_km=km,
            tags=tags,
        )
        for key, (score, grade, raw) in zip(("quiet", "peer", "evening"), grades, strict=True):
            metrics.append(
                Metric(scope="dong", unit_id=dong_id, month=0, metric_def_id=key,
                       score=score, grade=grade, raw_value=raw)
            )  # fmt: skip
        metrics.append(
            Metric(
                scope="dong", unit_id=dong_id, month=0, metric_def_id="local_life", raw_value=life
            )
        )
        curves += [
            DongHourly(dong_id=dong_id, month=0, day_group="weekday", hour=h, index_100=am)
            for h in (9, 10, 11)
        ]
    for region_id, overnight, raws in ((1, 64, GANGNEUNG_RAW), (9, 27, JEONJU_RAW)):
        metrics.append(
            Metric(scope="region", unit_id=region_id, month=0, metric_def_id="overnight",
                   score=overnight, grade="A", raw_value=0.8)
        )  # fmt: skip
        for m, raw in raws.items():
            metrics.append(
                Metric(scope="region", unit_id=region_id, month=m, metric_def_id="ease",
                       score=EASE_SCORE.get(m, 10), raw_value=raw)
            )  # fmt: skip
    Metric.objects.bulk_create(metrics)
    DongHourly.objects.bulk_create(curves)
    insert_hourly()


def build():
    out = StringIO()
    call_command("build_explore", stdout=out)
    return out.getvalue()


def fetch(sql):
    with connection.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def test_build_explore_counts(explore_data):
    assert "2025년 · 동 4 → dong_type_score 16 · dong_hour_stat 8 · dong_similar 4" in build()


def test_type_scores_written(explore_data):
    build()
    rows = fetch(
        "SELECT type, score, rank, reason FROM dong_type_score WHERE dong_id = 116 ORDER BY rank"
    )
    assert rows == [
        ("local", 100, 1, "생활권 지수 0.74"),
        ("long_stay", 61, 2, STAY),
        ("networking", 50, 3, "또래 밀도 39 · 저녁 활기 62"),
        ("immersion", 39, 4, "조용함 31 · 오전 한산 50"),
    ]
    # 전주 원값 ≤ 80 인 달 3개 → 0.5·27 + 0.5·25 = 26
    assert fetch(
        "SELECT score FROM dong_type_score WHERE dong_id = 901 AND type = 'long_stay'"
    ) == [(26,)]


def test_hour_stat_written(explore_data):
    build()
    rows = {
        (d, g): (driv, peak, quiet)
        for d, g, driv, peak, quiet in fetch(
            "SELECT dong_id, day_group, driv, peak_hour, quiet_hour FROM dong_hour_stat"
        )
    }
    driv, peak, quiet = rows[(116, "weekday")]
    assert (peak, quiet) == (12, 9)
    assert driv[12] == 2064 and driv[0] == 637  # 두 달 평균, 2024년 행 제외
    assert rows[(116, "weekend")][1:] == (14, 3)
    assert rows[(902, "weekday")][1:] == (18, 11)


def test_similar_written_same_coast_only(explore_data):
    build()
    rows = {d: (other, l2) for d, rank, other, l2 in fetch("SELECT * FROM dong_similar")}
    assert {d: other for d, (other, _) in rows.items()} == {116: 112, 112: 116, 901: 902, 902: 901}
    expected = math.dist((0.31, 0.39, 0.62, 0.7365), (0.55, 0.20, 0.40, 0.65))
    assert rows[116][1] == pytest.approx(expected, rel=1e-6)


def test_build_explore_rerun_replaces_rows(explore_data):
    build()
    build()
    assert fetch("SELECT count(*) FROM dong_type_score") == [(16,)]
    assert fetch("SELECT count(*) FROM dong_similar") == [(4,)]


def test_ease_quiet_months_use_raw_not_score(explore_data):
    ease = sources.load_derive_input().region_metrics[1]["ease_raw"]
    assert derive.quiet_months(ease) == [2, 4, 9]  # 점수 기준이면 1·4·10월
```

손 계산 (테스트 기대값의 근거):
- 오전 지수 0.4 · 0.8 · 0.6 · 0.2 → 백분위 50 · 100 · 75 · 25 → 오전 한산 50 · 0 · 25 · 75.
- 주문진읍 몰입형 0.6·31 + 0.4·50 = 38.6 → 39, 네트워킹형 50.5 → 50, 로컬살이형 = 생활권 지수 백분위(4동 중 최대) 100, 장기체류형 0.5·64 + 0.5·7/12·100 = 61.2 → 61.
- 비슷한 동: 바닷가 2동(주문진·연곡)끼리, 내륙 2동(금암·노송)끼리라 각 1개.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_build.py -v`
Expected: FAIL — `ImportError: cannot import name 'sources' from 'explore'`

- [ ] **Step 3: 구현 `explore/sources.py`**

```python
"""탐색 v6 Postgres 읽기·쓰기. 이 앱의 SQL 은 이 파일에만 둔다.

테스트 DB 는 db/schema.sql 만 실행해 PostGIS(dong.geom)·pgvector(dong_feature)가 없다.
그래서 metric_vec 는 metric 표에서 직접 만들고(derive.metric_vec), 동 중심 좌표만
load_centers() 하나에서 PostGIS 를 쓴다 (테스트에서는 바꿔 끼운다).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.db import connection, transaction

from explore.derive import TypeScore

# 집계 제외 동(수동면)과 경계·코드 없는 동은 그래프·API 에서 뺀다
DONG_OK = "d.is_excluded = false AND d.adm_code IS NOT NULL"


def _rows(sql: str, params: list | tuple = ()) -> list[tuple]:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _float(v) -> float | None:
    return None if v is None else float(v)


def data_year() -> int:
    return _rows("SELECT max(year) FROM population_hourly")[0][0]


@dataclass(frozen=True)
class DeriveInput:
    dong_metrics: dict[int, dict[str, float]]  # derive.type_scores 입력
    region_metrics: dict[int, dict]
    morning: dict[int, float]
    region_of: dict[int, int]
    coast_km: dict[int, float | None]


def load_derive_input() -> DeriveInput:
    """연간(month 0) 지표가 모두 있는 동만. 시군구는 숙박 성향(연간)과 ease 원값(1~12월)."""
    metrics: dict[int, dict] = defaultdict(dict)
    for dong_id, key, score, raw in _rows(
        f"""SELECT m.unit_id, m.metric_key, m.score, m.raw_value
              FROM metric m JOIN dong d ON d.dong_id = m.unit_id
             WHERE m.scope = 'dong' AND m.month = 0 AND {DONG_OK}
               AND m.metric_key IN ('quiet', 'peer', 'evening', 'local_life')"""
    ):
        value = raw if key == "local_life" else score
        if value is not None:
            metrics[dong_id][key] = value
    morning = {
        dong_id: float(v)
        for dong_id, v in _rows(
            """SELECT dong_id, avg(index_100) / 100.0 FROM dong_hourly
                WHERE month = 0 AND day_group = 'weekday' AND hour BETWEEN 9 AND 11
                  AND index_100 IS NOT NULL
                GROUP BY dong_id"""
        )
    }
    regions: dict[int, dict] = defaultdict(lambda: {"ease_raw": {}})
    for region_id, month, key, score, raw in _rows(
        """SELECT unit_id, month, metric_key, score, raw_value FROM metric
            WHERE scope = 'region'
              AND ((metric_key = 'overnight' AND month = 0)
                   OR (metric_key = 'ease' AND month BETWEEN 1 AND 12))"""
    ):
        if key == "overnight" and score is not None:
            regions[region_id]["overnight"] = score
        elif key == "ease" and raw is not None:
            regions[region_id]["ease_raw"][month] = raw
    region_metrics = {r: v for r, v in regions.items() if "overnight" in v}

    region_of, coast = {}, {}
    for dong_id, region_id, km in _rows(
        f"SELECT dong_id, region_id, coast_dist_km FROM dong d WHERE {DONG_OK}"
    ):
        region_of[dong_id], coast[dong_id] = region_id, _float(km)
    complete = {
        d: m
        for d, m in metrics.items()
        if len(m) == 4 and d in morning and region_of.get(d) in region_metrics
    }
    return DeriveInput(complete, region_metrics, morning, region_of, coast)


def load_hourly(year: int) -> dict[int, dict[str, list[float | None]]]:
    """동 → day_group → 0~23시 차량 이동 인구 평균 (그 해 월·요일 평균)."""
    out: dict[int, dict[str, list]] = defaultdict(
        lambda: {"weekday": [None] * 24, "weekend": [None] * 24}
    )
    for dong_id, day, hour, v in _rows(
        """SELECT dong_id, day_group, hour, avg(driv_avg) FROM population_hourly
            WHERE year = %s GROUP BY dong_id, day_group, hour""",
        [year],
    ):
        out[dong_id][day][hour] = v
    return dict(out)


@transaction.atomic
def write_explore(
    types: dict[int, list[TypeScore]],
    hours: dict[int, dict[str, tuple[list, int | None, int | None]]],
    similar: dict[int, list[tuple[int, float]]],
) -> None:
    """세 파생 표를 한 트랜잭션에서 지우고 다시 넣는다 (스펙 §5-1)."""
    with connection.cursor() as cur:
        for table in ("dong_similar", "dong_hour_stat", "dong_type_score"):
            cur.execute(f"DELETE FROM {table}")
        cur.executemany(
            "INSERT INTO dong_type_score (dong_id, type, score, rank, reason)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(d, t.type, t.score, t.rank, t.reason) for d, ts in types.items() for t in ts],
        )
        cur.executemany(
            "INSERT INTO dong_hour_stat (dong_id, day_group, driv, peak_hour, quiet_hour)"
            " VALUES (%s, %s, %s, %s, %s)",
            [(d, day, *row) for d, days in hours.items() for day, row in days.items()],
        )
        cur.executemany(
            "INSERT INTO dong_similar (dong_id, rank, other_id, l2) VALUES (%s, %s, %s, %s)",
            [
                (d, rank, other, l2)
                for d, rows in similar.items()
                for rank, (other, l2) in enumerate(rows, start=1)
            ],
        )
```

빈 파일 `explore/management/__init__.py`, `explore/management/commands/__init__.py` 를 만든다.

`explore/management/commands/build_explore.py`:

```python
"""탐색 v6 파생 표 만들기 (월 배치 ②). 지표 적재(03_metrics.sql) 다음에 실행한다.

uv run python manage.py build_explore
"""

from django.core.management.base import BaseCommand

from explore import derive, sources


class Command(BaseCommand):
    help = "dong_type_score · dong_hour_stat · dong_similar 를 지우고 다시 계산 (한 트랜잭션)"

    def handle(self, *args, **options):
        inp = sources.load_derive_input()
        types = derive.type_scores(inp.dong_metrics, inp.region_metrics, inp.morning, inp.region_of)
        year = sources.data_year()
        hours = {}
        for dong_id, days in sources.load_hourly(year).items():
            if dong_id not in inp.dong_metrics:
                continue
            hours[dong_id] = {
                day: (driv, *derive.hour_stat(driv, day))
                for day, driv in days.items()
                if any(v is not None for v in driv)
            }
        vecs = {
            d: derive.metric_vec(m["quiet"], m["peer"], m["evening"], m["local_life"])
            for d, m in inp.dong_metrics.items()
        }
        coastal = {d: derive.is_coastal(inp.coast_km.get(d)) for d in vecs}
        similar = derive.similar_dongs(vecs, coastal)
        sources.write_explore(types, hours, similar)
        n_hours = sum(len(days) for days in hours.values())
        n_similar = sum(len(rows) for rows in similar.values())
        self.stdout.write(
            f"{year}년 · 동 {len(types)} → dong_type_score {4 * len(types)}"
            f" · dong_hour_stat {n_hours} · dong_similar {n_similar}"
        )
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_explore_build.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add explore/sources.py explore/management tests/test_explore_build.py
git commit -m "feat: manage.py build_explore — 유형 점수 · 24시간 차량 · 비슷한 동 파생 표

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 그래프 모양 `graph.py` (순수) + `load_graph_input`

**Files:**
- Create: `explore/graph.py`, `tests/test_explore_graph.py`
- Modify: `explore/sources.py` (`load_centers`, `load_graph_input`), `tests/explore_samples.py` (`sample_graph_input`), `tests/test_explore_build.py` (`test_load_graph_input`)

**Interfaces:**
- Consumes: Task 2 `derive.*`; Task 3 `sources._rows`, `DONG_OK`, `_float`, `data_year`; `webapi.data.TAG_KO` (태그 한국어 이름, 기존)
- Produces:
  - `Node(label, id, props)`, `Rel(type, src, dst, props={}, key=())`, `Graph(nodes, rels, meta)`
  - `RegionRow(region_id, code, name, sido)`, `DongRow(dong_id, code, name, region_id, lat, lng, coast_km, tags, local_life)`
  - `GraphInput(regions, dongs, grades, types, hours, similar, ease, data_year)` — grades `{dong: {metric: (grade, score, raw)}}`, hours `{dong: {day: (driv, peak, quiet)}}`, similar `{dong: [(other, l2)]}`(rank 순), ease `{region_id: {month: raw}}`
  - `build_graph(inp) -> Graph`, `expected_counts(graph) -> {"nodes": {label: n}, "rels": {type: n}}` (Meta 1 포함), `label_of(node_id) -> str`
  - 상수 `POI_TAGS`(12개, food 없음), `METRICS`, `METRIC_KO`, `GRADES`, `DAYS`, `LIFE_KO`, `PREFIX_LABEL`, `LABELS`
  - `Rel.key` 는 MERGE 기준 속성 이름 (`QUIET_AT` · `PEAK_AT` 은 `("day",)`)
  - 사실 노드 `degree` = 이어진 동 수: Grade · Type(FITS rank 1 만) · Place · Tag · Region. Hour 는 `degree_weekday` · `degree_weekend` (QUIET_AT 의 day 별)
  - `Graph.meta = {"data_year", "dong_count", "region_count"}`
  - `sources.load_centers() -> {dong_id: (lat, lng)}`, `sources.load_graph_input() -> GraphInput`

- [ ] **Step 1: 공용 재료 — `tests/explore_samples.py`**

맨 위 docstring 다음에 import 를 넣는다:

```python
from explore.derive import TypeScore
from explore.graph import DongRow, GraphInput, RegionRow
```

파일 끝에 추가:

```python
def sample_graph_input() -> GraphInput:
    regions = [
        RegionRow(1, "51150", "강릉시", "강원특별자치도"),
        RegionRow(4, "51210", "속초시", "강원특별자치도"),
        RegionRow(9, "52110", "전주시", "전북특별자치도"),
    ]
    dongs = [
        DongRow(
            116, "5115025000", "주문진읍", 1, 37.88818, 128.75354, 0.0,
            ["beach", "cafe", "food", "market", "park", "resort", "coast"], 0.7365,
        ),
        DongRow(
            112, "5115037000", "연곡면", 1, 37.85, 128.8, 0.0,
            ["beach", "cafe", "food", "coast"], 0.65,
        ),
        DongRow(
            403, "5121057000", "노학동", 4, 38.18, 128.55, 0.887,
            ["cafe", "food", "park", "resort", "coast"], 0.75,
        ),
        DongRow(901, "5211357500", "금암동", 9, 35.84, 127.13, None, [], 0.7218),
    ]  # fmt: skip
    grades = {
        116: {"quiet": ("B", 31, 0.8721), "peer": ("B", 39, 0.3735), "evening": ("A", 62, 0.278)},
        112: {"quiet": ("A", 55, 0.80), "peer": ("C", 20, 0.30), "evening": ("B", 40, 0.20)},
        403: {"quiet": ("B", 39, 0.85), "peer": ("A", 52, 0.40), "evening": ("B", 47, 0.22)},
        901: {"quiet": ("C", 21, 0.95), "peer": ("S", 97, 0.60), "evening": ("S", 94, 0.45)},
    }
    types = {
        116: [
            TypeScore("long_stay", 61, 1, STAY),
            TypeScore("local", 60, 2, "생활권 지수 0.74"),
            TypeScore("networking", 50, 3, "또래 밀도 39 · 저녁 활기 62"),
            TypeScore("immersion", 36, 4, "조용함 31 · 오전 한산 44"),
        ],
        112: [
            TypeScore("long_stay", 61, 1, STAY),
            TypeScore("immersion", 50, 2, "조용함 55 · 오전 한산 42"),
            TypeScore("local", 40, 3, "생활권 지수 0.65"),
            TypeScore("networking", 30, 4, "또래 밀도 20 · 저녁 활기 40"),
        ],
        403: [
            TypeScore(
                "long_stay", 70, 1, "숙박 성향 56 · 한산한 달 10개(성수기보다 20% 이상 적은 달)"
            ),
            TypeScore("local", 65, 2, "생활권 지수 0.75"),
            TypeScore("networking", 50, 3, "또래 밀도 52 · 저녁 활기 47"),
            TypeScore("immersion", 40, 4, "조용함 39 · 오전 한산 41"),
        ],
        901: [
            TypeScore("networking", 96, 1, "또래 밀도 97 · 저녁 활기 94"),
            TypeScore("local", 53, 2, "생활권 지수 0.72"),
            TypeScore(
                "long_stay", 26, 3, "숙박 성향 27 · 한산한 달 3개(성수기보다 20% 이상 적은 달)"
            ),
            TypeScore("immersion", 18, 4, "조용함 21 · 오전 한산 15"),
        ],
    }
    geumam_weekday = curve(17, 10)
    geumam_weekday[0] = None  # 값 없는 시각 → Neo4j 에는 -1
    hours = {
        116: {
            "weekday": (JUMUNJIN_WEEKDAY, 12, 9),
            "weekend": (JUMUNJIN_WEEKEND, 14, 3),
        },
        112: {"weekday": (curve(13, 9), 13, 9), "weekend": (curve(15, 3), 15, 3)},
        403: {"weekday": (curve(17, 9), 17, 9), "weekend": (curve(14, 3), 14, 3)},
        901: {"weekday": (geumam_weekday, 17, 10), "weekend": (curve(13, 5), 13, 5)},
    }
    similar = {
        116: [(403, 0.2141), (112, 0.3)],
        112: [(116, 0.3), (403, 0.31)],
        403: [(116, 0.2141), (112, 0.31)],
        901: [],
    }
    ease = {1: GANGNEUNG_RAW, 4: SOKCHO_RAW, 9: JEONJU_RAW}
    return GraphInput(regions, dongs, grades, types, hours, similar, ease, 2025)
```

- [ ] **Step 2: 실패하는 테스트 `tests/test_explore_graph.py`**

```python
"""explore/graph.py build_graph (순수, Neo4j 없이)."""

from explore import graph
from tests.explore_samples import sample_graph_input


def build():
    return graph.build_graph(sample_graph_input())


def nodes_by_id(g):
    return {n.id: n for n in g.nodes}


def rels_of(g, type_, src=None):
    return [r for r in g.rels if r.type == type_ and (src is None or r.src == src)]


def test_label_counts():
    assert graph.expected_counts(build()) == {
        "nodes": {
            "Sido": 2, "Region": 3, "Dong": 4, "Grade": 12, "Type": 4, "Hour": 24,
            "Month": 12, "Place": 1, "Tag": 2, "PoiTag": 12, "Meta": 1,
        },
        "rels": {
            "IN": 7, "EASE": 36, "HAS_GRADE": 12, "FITS": 16, "QUIET_AT": 8, "PEAK_AT": 8,
            "NEAR": 3, "LIFE": 4, "HAS_TAG": 13, "SIMILAR": 6,
        },
    }  # fmt: skip


def test_label_of():
    assert graph.label_of("dong:5115025000") == "Dong"
    assert graph.label_of("life:local") == "Tag"
    assert graph.label_of("poi:cafe") == "PoiTag"
    assert graph.label_of("grade:evening:A") == "Grade"


def test_dong_props():
    n = nodes_by_id(build())["dong:5115025000"]
    assert n.label == "Dong"
    assert n.props["code"] == "5115025000" and n.props["name"] == "주문진읍"
    assert (n.props["lat"], n.props["lng"]) == (37.88818, 128.75354)
    assert n.props["coast_km"] == 0.0 and n.props["pg_id"] == 116
    assert n.props["driv_weekday"][12] == 2064 and len(n.props["driv_weekend"]) == 24
    # 값 없는 시각은 -1 (Neo4j 리스트에 null 불가)
    assert nodes_by_id(build())["dong:5211357500"].props["driv_weekday"][0] == -1


def test_fits_four_with_rank():
    fits = rels_of(build(), "FITS", "dong:5115025000")
    assert [(r.dst, r.props["score"], r.props["rank"]) for r in fits] == [
        ("type:long_stay", 61, 1),
        ("type:local", 60, 2),
        ("type:networking", 50, 3),
        ("type:immersion", 36, 4),
    ]
    assert fits[0].props["reason"].startswith("숙박 성향 64")


def test_near_only_coastal_dongs():
    near = rels_of(build(), "NEAR")
    assert {r.src for r in near} == {"dong:5115025000", "dong:5115037000", "dong:5121057000"}
    assert next(r for r in near if r.src == "dong:5121057000").props == {"km": 0.887}


def test_poi_tags_skip_food_and_keep_order():
    g = build()
    tags = [r.dst for r in rels_of(g, "HAS_TAG", "dong:5115025000")]
    assert tags == ["poi:coast", "poi:cafe", "poi:resort", "poi:beach", "poi:park", "poi:market"]
    assert "poi:food" not in nodes_by_id(g)
    cafe = nodes_by_id(g)["poi:cafe"].props
    assert cafe == {"code": "cafe", "name": "카페 많음", "order": 2}


def test_grade_rel_ranks_use_raw_values():
    g = build()
    jumunjin = {r.dst: r.props for r in rels_of(g, "HAS_GRADE", "dong:5115025000")}
    # 저녁 활기 원값 0.278: 전국 4동 중 2위(금암동 0.45 다음), 강릉 2동 중 1위
    assert jumunjin["grade:evening:A"] == {
        "score": 62, "rank": 2, "of": 4, "rank_sgg": 1, "of_sgg": 2,
    }  # fmt: skip
    # 조용함은 원값이 낮을수록 1위: 0.80(연곡) 0.85(노학) 0.8721(주문진) 0.95(금암)
    assert jumunjin["grade:quiet:B"]["rank"] == 3
    assert jumunjin["grade:quiet:B"]["rank_sgg"] == 2


def test_hour_rels_keyed_by_day():
    g = build()
    quiet = rels_of(g, "QUIET_AT", "dong:5115025000")
    assert [(r.dst, r.props, r.key) for r in quiet] == [
        ("hour:9", {"day": "weekday"}, ("day",)),
        ("hour:3", {"day": "weekend"}, ("day",)),
    ]
    peak = rels_of(g, "PEAK_AT", "dong:5115025000")
    assert [(r.dst, r.props["day"]) for r in peak] == [
        ("hour:12", "weekday"),
        ("hour:14", "weekend"),
    ]


def test_degree_counts_connected_dongs():
    n = nodes_by_id(build())
    assert n["grade:quiet:B"].props["degree"] == 2  # 주문진읍·노학동
    assert n["type:long_stay"].props["degree"] == 3  # FITS rank 1 만 센다
    assert n["type:local"].props["degree"] == 0  # 2위로만 이어져 있음
    assert n["place:coast"].props["degree"] == 3
    assert n["life:local"].props["degree"] == 3 and n["life:tourist"].props["degree"] == 1
    assert n["region:51150"].props["degree"] == 2
    assert n["hour:9"].props == {"h": 9, "degree_weekday": 3, "degree_weekend": 0}
    assert n["hour:3"].props["degree_weekend"] == 3


def test_ease_quiet_months_are_raw_bottom_three():
    ease = {int(r.dst.split(":")[1]): r.props for r in rels_of(build(), "EASE", "region:51150")}
    assert sorted(m for m, p in ease.items() if p["quiet"]) == [2, 4, 9]
    assert ease[8]["raw"] == 100.0 and ease[1]["raw"] == 80.3


def test_similar_rank_and_region_links():
    g = build()
    sim = rels_of(g, "SIMILAR", "dong:5115025000")
    assert [(r.dst, r.props) for r in sim] == [
        ("dong:5121057000", {"rank": 1, "l2": 0.2141}),
        ("dong:5115037000", {"rank": 2, "l2": 0.3}),
    ]
    ins = {(r.src, r.dst) for r in rels_of(g, "IN")}
    assert ("region:51150", "sido:51") in ins and ("dong:5211357500", "region:52110") in ins
    assert nodes_by_id(g)["sido:52"].props == {"name": "전북특별자치도"}


def test_meta():
    assert build().meta == {"data_year": 2025, "dong_count": 4, "region_count": 3}
```

`tests/test_explore_build.py` 의 `from explore import derive, sources` 를 `from explore import derive, graph, sources` 로 바꾸고 파일 끝에 추가:

```python
def test_load_graph_input(explore_data, monkeypatch):
    build()
    monkeypatch.setattr(sources, "load_centers", lambda: {116: (37.88818, 128.75354)})
    inp = sources.load_graph_input()
    assert [r.code for r in inp.regions] == ["51150", "52110"]
    assert [d.dong_id for d in inp.dongs] == [112, 116, 901, 902]  # 집계 제외 수동면 없음
    jumunjin = next(d for d in inp.dongs if d.dong_id == 116)
    assert (jumunjin.lat, jumunjin.lng, jumunjin.coast_km) == (37.88818, 128.75354, 0.0)
    assert jumunjin.tags == ["beach", "cafe", "food", "coast"] and jumunjin.local_life == 0.7365
    assert next(d for d in inp.dongs if d.dong_id == 901).coast_km is None
    assert inp.grades[116]["evening"] == ("A", 62, 0.278)
    assert inp.types[116][0] == derive.TypeScore("local", 100, 1, "생활권 지수 0.74")
    assert inp.hours[116]["weekday"][1:] == (12, 9)
    assert [o for o, _ in inp.similar[116]] == [112]
    assert inp.ease[1][2] == 69.8 and inp.data_year == 2025
    counts = graph.expected_counts(graph.build_graph(inp))
    assert counts["nodes"]["Dong"] == 4 and counts["rels"]["SIMILAR"] == 4
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_graph.py tests/test_explore_build.py -v`
Expected: FAIL — `ImportError: cannot import name 'DongRow' from 'explore.graph'` 류 (모듈 없음)

- [ ] **Step 4: 구현 `explore/graph.py`**

```python
"""탐색 v6 그래프 모양 (순수). Postgres 에서 읽은 GraphInput → Neo4j 노드·관계 목록 (스펙 §3).

Neo4j 를 모른다. 쓰기는 explore/graph_write.py, 읽기는 explore/queries.py.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from explore import derive
from webapi.data import TAG_KO

# 핀 아이콘 태그 순서 (v5 §3). food 는 넣지 않는다
POI_TAGS = (
    "coast", "city", "cafe", "library", "long_stay", "resort",
    "beach", "park", "mountain", "island", "surf", "market",
)  # fmt: skip
METRICS = ("quiet", "peer", "evening")
METRIC_KO = {"quiet": "조용함", "peer": "또래 밀도", "evening": "저녁 활기"}
GRADES = ("S", "A", "B", "C")
DAYS = ("weekday", "weekend")
LIFE_KO = {"local": "생활권", "tourist": "관광지"}
# 노드 id 앞부분 → 라벨
PREFIX_LABEL = {
    "sido": "Sido",
    "region": "Region",
    "dong": "Dong",
    "grade": "Grade",
    "type": "Type",
    "hour": "Hour",
    "month": "Month",
    "place": "Place",
    "life": "Tag",
    "poi": "PoiTag",
    "meta": "Meta",
}
LABELS = tuple(PREFIX_LABEL.values())


def label_of(node_id: str) -> str:
    return PREFIX_LABEL[node_id.split(":", 1)[0]]


@dataclass(frozen=True)
class Node:
    label: str
    id: str
    props: dict


@dataclass(frozen=True)
class Rel:
    type: str
    src: str
    dst: str
    props: dict = field(default_factory=dict)
    key: tuple[str, ...] = ()  # MERGE 기준 속성. 같은 두 노드 사이에 day 별로 따로 둘 때


@dataclass(frozen=True)
class Graph:
    nodes: list[Node]
    rels: list[Rel]
    meta: dict  # Meta 노드 속성 (synced_at 은 쓰기 때 붙인다)


@dataclass(frozen=True)
class RegionRow:
    region_id: int
    code: str  # sigungu_code 5자리
    name: str
    sido: str


@dataclass(frozen=True)
class DongRow:
    dong_id: int
    code: str  # adm_code 10자리
    name: str
    region_id: int
    lat: float | None
    lng: float | None
    coast_km: float | None
    tags: list[str]
    local_life: float


@dataclass(frozen=True)
class GraphInput:
    regions: list[RegionRow]
    dongs: list[DongRow]
    grades: dict[int, dict[str, tuple[str, int, float]]]  # 동 → 지표 → (등급, 점수, 원값)
    types: dict[int, list[derive.TypeScore]]  # dong_type_score, rank 순
    hours: dict[
        int, dict[str, tuple[list, int | None, int | None]]
    ]  # 동 → day → (driv, peak, quiet)
    similar: dict[int, list[tuple[int, float]]]  # dong_similar, rank 순 [(other_id, l2)]
    ease: dict[int, dict[int, float]]  # 시군구 → 1~12월 → ease 원값
    data_year: int


def _driv_prop(driv: list | None) -> list[int]:
    """Neo4j 리스트에는 null 을 못 넣는다 → 반올림 정수, 값 없음 = -1."""
    if not driv:
        return [-1] * 24
    return [-1 if v is None else round(v) for v in driv]


def _grade_ranks(inp: GraphInput, dongs: list[DongRow]) -> dict[tuple[int, str], dict]:
    """원값 기준 전국·시군구 안 순위. quiet 는 낮을수록 1위."""
    region_of = {d.dong_id: d.region_id for d in dongs}
    out = {}
    for m in METRICS:
        raws = {d: g[m][2] for d, g in inp.grades.items() if m in g and d in region_of}
        desc = m != "quiet"
        for d, v in raws.items():
            same = [x for o, x in raws.items() if region_of[o] == region_of[d]]
            out[(d, m)] = {
                "rank": derive.rank_of(list(raws.values()), v, desc),
                "of": len(raws),
                "rank_sgg": derive.rank_of(same, v, desc),
                "of_sgg": len(same),
            }
    return out


def build_graph(inp: GraphInput) -> Graph:
    regions = {r.region_id: r for r in inp.regions}
    dongs = sorted((d for d in inp.dongs if d.region_id in regions), key=lambda d: d.dong_id)
    dong_key = {d.dong_id: f"dong:{d.code}" for d in dongs}
    ranks = _grade_ranks(inp, dongs)
    nodes: list[Node] = []
    rels: list[Rel] = []

    for code, name in sorted({(r.code[:2], r.sido) for r in regions.values()}):
        nodes.append(Node("Sido", f"sido:{code}", {"name": name}))
    for r in sorted(regions.values(), key=lambda r: r.code):
        rels.append(Rel("IN", f"region:{r.code}", f"sido:{r.code[:2]}"))
        quiet = set(derive.quiet_months(inp.ease.get(r.region_id, {})))
        for m, raw in sorted(inp.ease.get(r.region_id, {}).items()):
            rels.append(
                Rel(
                    "EASE",
                    f"region:{r.code}",
                    f"month:{m}",
                    {"raw": round(raw, 1), "quiet": m in quiet},
                )
            )

    for d in dongs:
        me = dong_key[d.dong_id]
        hours = inp.hours.get(d.dong_id, {})
        nodes.append(
            Node(
                "Dong",
                me,
                {
                    "code": d.code,
                    "name": d.name,
                    "lat": d.lat,
                    "lng": d.lng,
                    "coast_km": d.coast_km,
                    "driv_weekday": _driv_prop(hours.get("weekday", (None,))[0]),
                    "driv_weekend": _driv_prop(hours.get("weekend", (None,))[0]),
                    "local_life": round(d.local_life, 4),
                    "pg_id": d.dong_id,
                },
            )
        )
        rels.append(Rel("IN", me, f"region:{regions[d.region_id].code}"))
        for m in METRICS:
            g = inp.grades.get(d.dong_id, {}).get(m)
            if g is not None:
                props = {"score": g[1], **ranks[(d.dong_id, m)]}
                rels.append(Rel("HAS_GRADE", me, f"grade:{m}:{g[0]}", props))
        for t in inp.types.get(d.dong_id, []):
            props = {"score": t.score, "rank": t.rank, "reason": t.reason}
            rels.append(Rel("FITS", me, f"type:{t.type}", props))
        for day in DAYS:
            _, peak, quiet = hours.get(day, (None, None, None))
            if quiet is not None:
                rels.append(Rel("QUIET_AT", me, f"hour:{quiet}", {"day": day}, ("day",)))
            if peak is not None:
                rels.append(Rel("PEAK_AT", me, f"hour:{peak}", {"day": day}, ("day",)))
        if derive.is_coastal(d.coast_km):
            rels.append(Rel("NEAR", me, "place:coast", {"km": d.coast_km}))
        life = derive.life_tag(d.local_life)
        rels.append(Rel("LIFE", me, f"life:{life}", {"value": round(d.local_life, 4)}))
        for t in POI_TAGS:
            if t in d.tags:
                rels.append(Rel("HAS_TAG", me, f"poi:{t}"))
        for rank, (other, l2) in enumerate(inp.similar.get(d.dong_id, []), start=1):
            if other in dong_key:
                props = {"rank": rank, "l2": round(l2, 4)}
                rels.append(Rel("SIMILAR", me, dong_key[other], props))

    # 사실 노드의 degree = 이어진 동 수. 공통점 정렬(스펙 §6-3)과 브리핑 흔함에 쓴다
    degree: Counter[str] = Counter()
    for r in rels:
        if not r.src.startswith("dong:"):
            continue
        if r.type in ("IN", "HAS_GRADE", "NEAR", "LIFE") or (
            r.type == "FITS" and r.props["rank"] == 1
        ):
            degree[r.dst] += 1
        elif r.type == "QUIET_AT":
            degree[f"{r.dst}:{r.props['day']}"] += 1

    for r in sorted(regions.values(), key=lambda r: r.code):
        rid = f"region:{r.code}"
        nodes.append(Node("Region", rid, {"code": r.code, "name": r.name, "degree": degree[rid]}))
    for m in METRICS:
        for g in GRADES:
            gid = f"grade:{m}:{g}"
            props = {"metric": m, "grade": g, "label": f"{METRIC_KO[m]} {g}", "degree": degree[gid]}
            nodes.append(Node("Grade", gid, props))
    for t in derive.TYPES:
        tid = f"type:{t}"
        nodes.append(Node("Type", tid, {"name": derive.TYPE_KO[t], "degree": degree[tid]}))
    for h in range(24):
        hid = f"hour:{h}"
        props = {f"degree_{day}": degree[f"{hid}:{day}"] for day in DAYS}
        nodes.append(Node("Hour", hid, {"h": h, **props}))
    for m in range(1, 13):
        nodes.append(Node("Month", f"month:{m}", {"m": m}))
    nodes.append(Node("Place", "place:coast", {"kind": "바닷가", "degree": degree["place:coast"]}))
    for key, name in LIFE_KO.items():
        lid = f"life:{key}"
        nodes.append(Node("Tag", lid, {"name": name, "degree": degree[lid]}))
    for order, t in enumerate(POI_TAGS):
        nodes.append(Node("PoiTag", f"poi:{t}", {"code": t, "name": TAG_KO[t], "order": order}))

    meta = {"data_year": inp.data_year, "dong_count": len(dongs), "region_count": len(regions)}
    return Graph(nodes, rels, meta)


def expected_counts(graph: Graph) -> dict[str, dict[str, int]]:
    """sync_graph 가 Neo4j 개수와 비교할 기대값. Meta 1개를 더한다."""
    nodes = Counter(n.label for n in graph.nodes)
    nodes["Meta"] = 1
    return {"nodes": dict(nodes), "rels": dict(Counter(r.type for r in graph.rels))}
```

- [ ] **Step 5: `explore/sources.py` 에 추가**

import 블록의 `from explore.derive import TypeScore` 다음 줄에:

```python
from explore.graph import DongRow, GraphInput, RegionRow
```

`write_explore` 다음(파일 끝)에:

```python
def load_centers() -> dict[int, tuple[float, float]]:
    """동 경계 안의 한 점 (lat, lng). 웹 public/geo 의 labelLat/labelLng 와 같은 값 (PostGIS)."""
    rows = _rows(
        """SELECT dong_id, extensions.st_y(p), extensions.st_x(p)
             FROM (SELECT dong_id, extensions.st_pointonsurface(geom) AS p
                     FROM dong WHERE geom IS NOT NULL) s"""
    )
    return {d: (round(lat, 5), round(lng, 5)) for d, lat, lng in rows}


def load_graph_input() -> GraphInput:
    """sync_graph 재료. build_explore 가 만든 파생 표 + 지표 원천."""
    regions = [
        RegionRow(*r)
        for r in _rows(
            """SELECT region_id, sigungu_code, sigungu_name, sido_name FROM region
                WHERE sigungu_code IS NOT NULL ORDER BY region_id"""
        )
    ]
    types: dict[int, list[TypeScore]] = defaultdict(list)
    for d, t, score, rank, reason in _rows(
        "SELECT dong_id, type, score, rank, reason FROM dong_type_score ORDER BY dong_id, rank"
    ):
        types[d].append(TypeScore(t, score, rank, reason))
    grades: dict[int, dict] = defaultdict(dict)
    local_life = {}
    for d, key, grade, score, raw in _rows(
        """SELECT unit_id, metric_key, grade, score, raw_value FROM metric
            WHERE scope = 'dong' AND month = 0
              AND metric_key IN ('quiet', 'peer', 'evening', 'local_life')"""
    ):
        if key == "local_life":
            local_life[d] = raw
        elif grade is not None and raw is not None:
            grades[d][key] = (grade, score, raw)
    centers = load_centers()
    dongs = []
    for d, code, name, region_id, km, tags in _rows(
        f"""SELECT dong_id, adm_code, dong_name, region_id, coast_dist_km, tags
              FROM dong d WHERE {DONG_OK} ORDER BY dong_id"""
    ):
        if d in types and local_life.get(d) is not None:
            lat, lng = centers.get(d, (None, None))
            dongs.append(
                DongRow(d, code, name, region_id, lat, lng, _float(km), list(tags), local_life[d])
            )
    hours: dict[int, dict] = defaultdict(dict)
    for d, day, driv, peak, quiet in _rows(
        "SELECT dong_id, day_group, driv, peak_hour, quiet_hour FROM dong_hour_stat"
    ):
        hours[d][day] = (list(driv), peak, quiet)
    similar: dict[int, list] = defaultdict(list)
    for d, other, l2 in _rows(
        "SELECT dong_id, other_id, l2 FROM dong_similar ORDER BY dong_id, rank"
    ):
        similar[d].append((other, l2))
    ease: dict[int, dict[int, float]] = defaultdict(dict)
    for r, month, raw in _rows(
        """SELECT unit_id, month, raw_value FROM metric
            WHERE scope = 'region' AND metric_key = 'ease' AND month BETWEEN 1 AND 12
              AND raw_value IS NOT NULL"""
    ):
        ease[r][month] = raw
    return GraphInput(
        regions,
        dongs,
        dict(grades),
        dict(types),
        dict(hours),
        dict(similar),
        dict(ease),
        data_year(),
    )
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_explore_graph.py tests/test_explore_build.py -v`
Expected: 19 passed (graph 12 · build 7)

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add explore/graph.py explore/sources.py tests/explore_samples.py tests/test_explore_graph.py \
  tests/test_explore_build.py
git commit -m "feat: 탐색 v6 그래프 모양 build_graph (노드 · 관계 · degree) + Postgres 그래프 재료

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Neo4j 접속 · 쓰기 · `manage.py sync_graph`

**Files:**
- Create: `explore/graphdb.py`, `explore/graph_write.py`, `explore/management/commands/sync_graph.py`
- Test: `tests/test_explore_graph_write.py` (가짜 드라이버)

**Interfaces:**
- Consumes: Task 4 `Graph`, `LABELS`, `label_of`, `build_graph`, `expected_counts`; Task 4 `sources.load_graph_input`
- Produces:
  - `graphdb.GraphUnavailable(Exception)`, `graphdb.get_driver()` (설정 없으면 `GraphUnavailable`, 프로세스마다 드라이버 재사용), `graphdb.read(work, **params)` → `work(tx, **params)` 를 `session.execute_read` 로. 드라이버 오류는 `GraphUnavailable`
  - `graph_write.BATCH = 1000`, `node_query(label)`, `rel_query(type, src_label, dst_label, key)`, `COUNT_NODES`, `COUNT_RELS`, `DELETE_OLD_RELS`, `DELETE_OLD_NODES`, `MERGE_META`
  - `ensure_constraints(driver)` — 라벨마다 `CREATE CONSTRAINT {label}_id IF NOT EXISTS … REQUIRE n.id IS UNIQUE` (별도 `session.run`)
  - `write_graph(driver, graph, sync_id, synced_at)` — 한 write 트랜잭션: 라벨별 노드 MERGE → 관계 MERGE → 이번 sync_id 아닌 관계 삭제 → Meta 제외 노드 DETACH DELETE → Meta MERGE
  - `count_graph(driver) -> {"nodes": {label: n}, "rels": {type: n}}`, `compare_counts(expected, actual) -> list[str]`
  - 명령 `sync_graph`: 개수가 다르면 `CommandError`(종료 코드 1)와 차이 목록

- [ ] **Step 1: 실패하는 테스트 `tests/test_explore_graph_write.py`**

```python
"""explore/graph_write.py · sync_graph 명령. 실제 Neo4j 대신 쿼리를 기록하는 가짜 드라이버."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from neo4j.exceptions import ServiceUnavailable

from explore import graph, graph_write, graphdb, sources
from tests.explore_samples import sample_graph_input


class FakeResult(list):
    def consume(self):
        return None


class FakeTx:
    def __init__(self, driver):
        self.driver = driver

    def run(self, query, **params):
        self.driver.log.append((query, params))
        if query in (graph_write.COUNT_NODES, graph_write.COUNT_RELS):
            return FakeResult(self.driver.answers.pop(0))
        return FakeResult()


class FakeSession:
    def __init__(self, driver):
        self.driver = driver

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.driver.schema.append(query)
        return FakeResult()

    def execute_write(self, work):
        if self.driver.fail:
            raise self.driver.fail
        return work(FakeTx(self.driver))

    def execute_read(self, work, **params):
        return work(FakeTx(self.driver), **params)


class FakeDriver:
    """개수 쿼리(COUNT_NODES · COUNT_RELS)는 answers 를 앞에서부터 하나씩 돌려준다."""

    def __init__(self, answers=None, fail=None):
        self.log, self.schema, self.answers, self.fail = [], [], list(answers or []), fail

    def session(self):
        return FakeSession(self)


def counts_as_rows(counts):
    return [
        [{"key": k, "cnt": n} for k, n in counts["nodes"].items()],
        [{"key": k, "cnt": n} for k, n in counts["rels"].items()],
    ]


def test_write_graph_query_order():
    g = graph.build_graph(sample_graph_input())
    driver = FakeDriver()
    graph_write.write_graph(driver, g, "sid1", "2026-11-13T10:00:00+09:00")
    queries = [q for q, _ in driver.log]
    first_rel = next(i for i, q in enumerate(queries) if "MERGE (a)-" in q)
    assert all("MERGE (n:" in q for q in queries[:first_rel])
    assert queries[-3:] == [
        graph_write.DELETE_OLD_RELS,
        graph_write.DELETE_OLD_NODES,
        graph_write.MERGE_META,
    ]
    assert all(p["sid"] == "sid1" for _, p in driver.log)
    meta = driver.log[-1][1]
    assert meta["synced_at"] == "2026-11-13T10:00:00+09:00"
    assert (meta["dong_count"], meta["data_year"], meta["region_count"]) == (4, 2025, 3)


def test_write_graph_rows_and_keys():
    g = graph.build_graph(sample_graph_input())
    driver = FakeDriver()
    graph_write.write_graph(driver, g, "sid1", "t")
    dong_q = graph_write.node_query("Dong")
    rows = next(p["rows"] for q, p in driver.log if q == dong_q)
    assert {r["id"] for r in rows} == {
        "dong:5115025000", "dong:5115037000", "dong:5121057000", "dong:5211357500",
    }  # fmt: skip
    quiet_q = graph_write.rel_query("QUIET_AT", "Dong", "Hour", ("day",))
    assert "MERGE (a)-[r:QUIET_AT {day: row.props.day}]->(b)" in quiet_q
    quiet_rows = next(p["rows"] for q, p in driver.log if q == quiet_q)
    assert {"src": "dong:5115025000", "dst": "hour:3", "props": {"day": "weekend"}} in quiet_rows


def test_write_graph_batches_by_1000(monkeypatch):
    monkeypatch.setattr(graph_write, "BATCH", 5)
    g = graph.build_graph(sample_graph_input())
    driver = FakeDriver()
    graph_write.write_graph(driver, g, "sid1", "t")
    hour_q = graph_write.node_query("Hour")
    assert [len(p["rows"]) for q, p in driver.log if q == hour_q] == [5, 5, 5, 5, 4]


def test_ensure_constraints_one_per_label():
    driver = FakeDriver()
    graph_write.ensure_constraints(driver)
    assert len(driver.schema) == len(graph.LABELS)
    assert (
        "CREATE CONSTRAINT dong_id IF NOT EXISTS FOR (n:Dong) REQUIRE n.id IS UNIQUE"
        in driver.schema
    )


def test_compare_counts_lists_differences():
    expected = {"nodes": {"Dong": 203, "Meta": 1}, "rels": {"IN": 215}}
    actual = {"nodes": {"Dong": 202, "Meta": 1}, "rels": {"IN": 215, "OLD": 3}}
    assert graph_write.compare_counts(expected, actual) == [
        "노드 Dong: 기대 203, 실제 202",
        "관계 OLD: 기대 0, 실제 3",
    ]


def test_sync_graph_command_ok(monkeypatch):
    g = graph.build_graph(sample_graph_input())
    driver = FakeDriver(answers=counts_as_rows(graph.expected_counts(g)))
    monkeypatch.setattr(sources, "load_graph_input", sample_graph_input)
    monkeypatch.setattr(graphdb, "get_driver", lambda: driver)
    out = StringIO()
    call_command("sync_graph", stdout=out)
    text = out.getvalue()
    assert "Dong 4" in text and "SIMILAR 6" in text and "개수 일치" in text
    assert driver.schema  # 제약 먼저


def test_sync_graph_command_count_mismatch_fails(monkeypatch):
    g = graph.build_graph(sample_graph_input())
    counts = graph.expected_counts(g)
    counts["nodes"]["Dong"] = 3
    driver = FakeDriver(answers=counts_as_rows(counts))
    monkeypatch.setattr(sources, "load_graph_input", sample_graph_input)
    monkeypatch.setattr(graphdb, "get_driver", lambda: driver)
    with pytest.raises(CommandError, match="노드 Dong: 기대 4, 실제 3"):
        call_command("sync_graph", stdout=StringIO())


def test_sync_graph_command_neo4j_down(monkeypatch):
    driver = FakeDriver(fail=ServiceUnavailable("refused"))
    monkeypatch.setattr(sources, "load_graph_input", sample_graph_input)
    monkeypatch.setattr(graphdb, "get_driver", lambda: driver)
    with pytest.raises(CommandError, match="Neo4j 쓰기 실패"):
        call_command("sync_graph", stdout=StringIO())


def test_get_driver_without_uri(settings):
    settings.NEO4J_URI = ""
    with pytest.raises(graphdb.GraphUnavailable, match="NEO4J_URI"):
        graphdb.get_driver()


def test_read_wraps_driver_errors(monkeypatch):
    class DownSession(FakeSession):
        def execute_read(self, work, **params):
            raise ServiceUnavailable("refused")

    class DownDriver(FakeDriver):
        def session(self):
            return DownSession(self)

    monkeypatch.setattr(graphdb, "get_driver", lambda: DownDriver())
    with pytest.raises(graphdb.GraphUnavailable, match="refused"):
        graphdb.read(lambda tx: None)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_graph_write.py -v`
Expected: FAIL — `ImportError: cannot import name 'graph_write' from 'explore'`

- [ ] **Step 3: `explore/graphdb.py`**

```python
"""Neo4j 접속. 서비스 코드(API·브리핑 조회)는 read() 만 쓴다 (읽기 트랜잭션).

커뮤니티판은 읽기 전용 계정이 없어서 쓰기는 관리 명령(explore/graph_write.py)에만 둔다 (스펙 §5-2).
"""

from __future__ import annotations

from django.conf import settings
from neo4j import GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError

_drivers: dict[tuple[str, str], object] = {}


class GraphUnavailable(Exception):
    """NEO4J_URI 미설정 · 연결 실패 · 동기화 전(Meta 없음). API 는 마지막 응답 또는 503."""


def get_driver():
    """프로세스마다 드라이버 하나를 재사용한다 (드라이버가 연결 풀을 가진다)."""
    if not settings.NEO4J_URI:
        raise GraphUnavailable("NEO4J_URI 미설정")
    key = (settings.NEO4J_URI, settings.NEO4J_USER)
    if key not in _drivers:
        _drivers[key] = GraphDatabase.driver(
            settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
        )
    return _drivers[key]


def read(work, **params):
    """work(tx, **params) 를 읽기 트랜잭션 하나에서 실행한다. 드라이버 오류는 GraphUnavailable."""
    try:
        with get_driver().session() as session:
            return session.execute_read(work, **params)
    except (DriverError, Neo4jError) as ex:
        raise GraphUnavailable(str(ex)) from ex
```

- [ ] **Step 4: `explore/graph_write.py`**

```python
"""Neo4j 쓰기 (manage.py sync_graph 전용). 서비스 코드에서 import 하지 않는다.

한 쓰기 트랜잭션에서 MERGE → 이번 sync_id 가 아닌 관계·노드 삭제 → Meta 갱신.
중간에 실패하면 트랜잭션이 되돌아가 이전 그래프가 그대로 남는다 (스펙 §5-3).
"""

from __future__ import annotations

from collections import defaultdict

from explore.graph import LABELS, Graph, label_of

BATCH = 1000

COUNT_NODES = "MATCH (n) RETURN labels(n)[0] AS key, count(*) AS cnt"
COUNT_RELS = "MATCH ()-[r]->() RETURN type(r) AS key, count(*) AS cnt"
DELETE_OLD_RELS = "MATCH ()-[r]->() WHERE coalesce(r.sync_id, '') <> $sid DELETE r"
DELETE_OLD_NODES = "MATCH (n) WHERE NOT n:Meta AND coalesce(n.sync_id, '') <> $sid DETACH DELETE n"
MERGE_META = (
    "MERGE (m:Meta {id: 'meta'})"
    " SET m.synced_at = $synced_at, m.data_year = $data_year,"
    " m.dong_count = $dong_count, m.region_count = $region_count, m.sync_id = $sid"
)


def _chunks(rows: list):
    for i in range(0, len(rows), BATCH):
        yield rows[i : i + BATCH]


def node_query(label: str) -> str:
    return f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n = row, n.sync_id = $sid"


def rel_query(rel_type: str, src_label: str, dst_label: str, key: tuple[str, ...]) -> str:
    match_props = ", ".join(f"{k}: row.props.{k}" for k in key)
    rel = f"[r:{rel_type} {{{match_props}}}]" if key else f"[r:{rel_type}]"
    return (
        "UNWIND $rows AS row"
        f" MATCH (a:{src_label} {{id: row.src}})"
        f" MATCH (b:{dst_label} {{id: row.dst}})"
        f" MERGE (a)-{rel}->(b)"
        " SET r = row.props, r.sync_id = $sid"
    )


def ensure_constraints(driver) -> None:
    """라벨마다 id 유일 제약. 스키마 변경이라 데이터 쓰기 트랜잭션과 따로 실행한다."""
    with driver.session() as session:
        for label in LABELS:
            session.run(
                f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS"
                f" FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            ).consume()


def write_graph(driver, graph: Graph, sync_id: str, synced_at: str) -> None:
    nodes: dict[str, list[dict]] = defaultdict(list)
    for n in graph.nodes:
        nodes[n.label].append({"id": n.id, **n.props})
    rels: dict[tuple, list[dict]] = defaultdict(list)
    for r in graph.rels:
        group = (r.type, label_of(r.src), label_of(r.dst), r.key)
        rels[group].append({"src": r.src, "dst": r.dst, "props": r.props})

    def work(tx):
        for label, rows in nodes.items():
            for chunk in _chunks(rows):
                tx.run(node_query(label), rows=chunk, sid=sync_id).consume()
        for group, rows in rels.items():
            for chunk in _chunks(rows):
                tx.run(rel_query(*group), rows=chunk, sid=sync_id).consume()
        tx.run(DELETE_OLD_RELS, sid=sync_id).consume()
        tx.run(DELETE_OLD_NODES, sid=sync_id).consume()
        tx.run(MERGE_META, sid=sync_id, synced_at=synced_at, **graph.meta).consume()

    with driver.session() as session:
        session.execute_write(work)


def count_graph(driver) -> dict[str, dict[str, int]]:
    def work(tx):
        nodes = {r["key"]: r["cnt"] for r in tx.run(COUNT_NODES)}
        rels = {r["key"]: r["cnt"] for r in tx.run(COUNT_RELS)}
        return {"nodes": nodes, "rels": rels}

    with driver.session() as session:
        return session.execute_read(work)


def compare_counts(expected: dict, actual: dict) -> list[str]:
    """다른 항목만 '노드 Dong: 기대 203, 실제 202' 형식으로."""
    out = []
    for kind, ko in (("nodes", "노드"), ("rels", "관계")):
        for key in sorted(set(expected[kind]) | set(actual[kind])):
            exp, act = expected[kind].get(key, 0), actual[kind].get(key, 0)
            if exp != act:
                out.append(f"{ko} {key}: 기대 {exp}, 실제 {act}")
    return out
```

- [ ] **Step 5: `explore/management/commands/sync_graph.py`**

```python
"""Postgres → Neo4j 탐색 그래프 동기화 (월 배치 ③). build_explore 다음에 실행한다.

uv run python manage.py sync_graph
"""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from neo4j.exceptions import DriverError, Neo4jError

from explore import graph, graph_write, graphdb, sources


class Command(BaseCommand):
    help = "Postgres 파생 표 → Neo4j 그래프 (MERGE 후 지난 동기화분 삭제, 개수 비교)"

    def handle(self, *args, **options):
        g = graph.build_graph(sources.load_graph_input())
        sync_id = uuid.uuid4().hex
        synced_at = timezone.localtime().isoformat(timespec="seconds")
        try:
            driver = graphdb.get_driver()
            graph_write.ensure_constraints(driver)
            graph_write.write_graph(driver, g, sync_id, synced_at)
            actual = graph_write.count_graph(driver)
        except (graphdb.GraphUnavailable, DriverError, Neo4jError) as ex:
            raise CommandError(f"Neo4j 쓰기 실패: {ex}") from ex
        expected = graph.expected_counts(g)
        self.stdout.write(f"동기화 {synced_at} (sync_id {sync_id})")
        for kind in ("nodes", "rels"):
            for key, n in sorted(actual[kind].items()):
                self.stdout.write(f"  {key} {n}")
        diffs = graph_write.compare_counts(expected, actual)
        if diffs:
            raise CommandError("Postgres 기준 개수와 다름:\n  " + "\n  ".join(diffs))
        self.stdout.write(self.style.SUCCESS("개수 일치"))
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_explore_graph_write.py -v`
Expected: 10 passed

- [ ] **Step 7: Commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add explore/graphdb.py explore/graph_write.py explore/management/commands/sync_graph.py \
  tests/test_explore_graph_write.py
git commit -m "feat: manage.py sync_graph — Neo4j MERGE · 지난 동기화분 삭제 · 개수 비교

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Neo4j 읽기와 응답 모양 `queries.py`

**Files:**
- Create: `explore/queries.py`, `tests/test_explore_queries.py`
- Modify: `tests/explore_samples.py` (`jumunjin_raw`)

**Interfaces:**
- Consumes: Task 4 `METRICS`, `METRIC_KO`; Task 5 `graphdb.read`, `GraphUnavailable`
- Produces:
  - Cypher 상수 `META`, `PINS`, `DONG`, `FACTS` (모두 패턴 컴프리헨션으로 한 쿼리씩, 노드는 `id` 유일 제약 인덱스로 찾는다)
  - `fetch_pins(code) -> dict | None` (§6-1 응답, 없는 시군구 None), `fetch_dong(code) -> dict | None` (raw, 위 "raw 모양"). Meta 없으면 `GraphUnavailable`
  - `shape_pins(rec)`, `shape_dong(raw, briefing) -> dict` (§6-2 응답)
  - `fact_list(row) -> {key: (문구, degree)}`, `common_facts(mine, other) -> [(key, 문구)]` (degree, key 오름차순), `scores_of(row)`, `score_diff(mine, other)` (상대 − 이 동, 없으면 None)
  - `tests/explore_samples.py`: `jumunjin_raw()` — 주문진읍 실측, 비슷한 동 노학동(공통 6) · 대정읍(4) · 영천동(5). degree 는 `graph_data/facts.json` 203동으로 센 값(조용함 B 50 · 저녁 활기 A 51 · 1위 장기체류형 58 · 평일 9시 108 · 주말 3시 102 · 바닷가 97 · 생활권 107 · 강릉시 21)

- [ ] **Step 1: 공용 재료 — `tests/explore_samples.py` 끝에 추가**

```python
def _facts_row(code, scores, grades, top, quiet, coastal, life, region):
    """FACTS Cypher 한 행. scores = (quiet, peer, evening) 점수, grades 는 (등급, degree) 3개."""
    return {
        "code": code,
        "grades": [
            {"metric": m, "grade": g, "score": s, "degree": deg}
            for m, s, (g, deg) in zip(("quiet", "peer", "evening"), scores, grades, strict=True)
        ],
        "top": [{"id": f"type:{top[0]}", "name": top[1], "degree": top[2]}],
        "quiet": [{"day": day, "h": h, "degree": deg} for day, h, deg in quiet],
        "near": [{"id": "place:coast", "kind": "바닷가", "degree": 97}] if coastal else [],
        "life": [{"id": f"life:{life[0]}", "name": life[1], "degree": life[2]}],
        "region": {"id": f"region:{region[0]}", "name": region[1], "degree": region[2]},
    }


def jumunjin_raw() -> dict:
    """주문진읍 실측. 비슷한 동 공통 수는 노학동 6 · 대정읍 4 · 영천동 5 (스펙 §6-3 규칙)."""
    stay_top = ("long_stay", "장기체류형", 58)
    local = ("local", "생활권", 107)
    coast_quiet = [("weekday", 9, 108), ("weekend", 3, 102)]
    return {
        "rec": {
            "dong": {
                "code": "5115025000",
                "name": "주문진읍",
                "coast_km": 0.0,
                "driv_weekday": JUMUNJIN_WEEKDAY,
                "driv_weekend": JUMUNJIN_WEEKEND,
            },
            "region": {"code": "51150", "name": "강릉시"},
            "sido": "강원특별자치도",
            "types": [
                [1, "장기체류형", 61, STAY],
                [2, "로컬살이형", 60, "생활권 지수 0.74"],
                [3, "네트워킹형", 50, "또래 밀도 39 · 저녁 활기 62"],
                [4, "몰입형", 36, "조용함 31 · 오전 한산 44"],
            ],
            "grades": [
                ["quiet", "B", 31, 140, 203, 12, 21],
                ["peer", "B", 39, 124, 203, 15, 21],
                ["evening", "A", 62, 78, 203, 1, 21],
            ],
            "hours": [
                ["QUIET_AT", "weekday", 9],
                ["PEAK_AT", "weekday", 12],
                ["QUIET_AT", "weekend", 3],
                ["PEAK_AT", "weekend", 14],
            ],
            "near": [0.0],
            "months": [[m, raw, m in (2, 4, 9)] for m, raw in sorted(GANGNEUNG_RAW.items())],
            "similar": [
                [1, "5121057000", "노학동", "속초시"],
                [2, "5013025000", "대정읍", "서귀포시"],
                [3, "5013056000", "영천동", "서귀포시"],
            ],
        },
        "facts": {
            "5115025000": _facts_row(
                "5115025000",
                (31, 39, 62),
                [("B", 50), ("B", 50), ("A", 51)],
                stay_top,
                coast_quiet,
                True,
                local,
                ("51150", "강릉시", 21),
            ),
            "5121057000": _facts_row(
                "5121057000",
                (39, 52, 47),
                [("B", 50), ("A", 51), ("B", 50)],
                stay_top,
                coast_quiet,
                True,
                local,
                ("51210", "속초시", 8),
            ),
            "5013025000": _facts_row(
                "5013025000",
                (50, 50, 59),
                [("A", 51), ("A", 51), ("A", 51)],
                ("networking", "네트워킹형", 49),
                coast_quiet,
                True,
                ("tourist", "관광지", 96),
                ("50130", "서귀포시", 17),
            ),
            "5013056000": _facts_row(
                "5013056000",
                (41, 59, 67),
                [("B", 50), ("A", 51), ("A", 51)],
                ("networking", "네트워킹형", 49),
                coast_quiet,
                True,
                ("tourist", "관광지", 96),
                ("50130", "서귀포시", 17),
            ),
        },
        "synced_at": "2026-11-13T10:00:00+09:00",
        "dong_count": 203,
        "region_count": 12,
    }
```

- [ ] **Step 2: 실패하는 테스트 `tests/test_explore_queries.py`**

```python
"""explore/queries.py 응답 모양 (순수) + 읽기 트랜잭션 흐름 (가짜 tx)."""

import pytest

from explore import queries
from explore.graphdb import GraphUnavailable
from tests.explore_samples import GANGNEUNG_RAW, JUMUNJIN_WEEKDAY, jumunjin_raw

PINS_REC = {  # PINS Cypher 결과 한 행
    "code": "51150",
    "dongs": [
        {
            "code": "5115037000", "name": "연곡면", "lat": 37.85, "lng": 128.8,
            "tags": [[6, "beach"], [0, "coast"], [2, "cafe"]],
            "top": [["장기체류형", 61]], "near": ["place:coast"], "life": ["관광지"],
            "grades": [["quiet", "A"], ["peer", "C"], ["evening", "B"]],
        },
        {
            "code": "5115025000", "name": "주문진읍", "lat": 37.88818, "lng": 128.75354,
            "tags": [[11, "market"], [0, "coast"], [5, "resort"], [2, "cafe"], [6, "beach"],
                     [7, "park"]],
            "top": [["장기체류형", 61]], "near": ["place:coast"], "life": ["생활권"],
            "grades": [["quiet", "B"], ["peer", "B"], ["evening", "A"]],
        },
        {
            "code": "5115099000", "name": "가상동", "lat": None, "lng": None,
            "tags": [], "top": [], "near": [], "life": [], "grades": [["peer", "S"]],
        },
    ],
}  # fmt: skip


def test_shape_pins():
    body = queries.shape_pins(PINS_REC)
    assert body["sigunguId"] == "51150"
    assert [d["name"] for d in body["dongs"]] == ["가상동", "연곡면", "주문진읍"]
    jumunjin = body["dongs"][2]
    assert jumunjin == {
        "id": "5115025000",
        "name": "주문진읍",
        "center": {"lat": 37.88818, "lng": 128.75354},
        "tags": ["coast", "cafe", "resort", "beach", "park", "market"],
        "topType": "장기체류형",
        "topScore": 61,
        "coastal": True,
        "life": "생활권",
        "grades": {"quiet": "B", "evening": "A"},
    }


def test_shape_pins_missing_values():
    empty = queries.shape_pins(PINS_REC)["dongs"][0]
    assert empty["tags"] == [] and empty["coastal"] is False
    assert (empty["topType"], empty["topScore"], empty["life"]) == (None, None, None)
    assert empty["grades"] == {"quiet": None, "evening": None}


def test_fact_list_labels_and_degrees():
    facts = queries.fact_list(jumunjin_raw()["facts"]["5115025000"])
    assert facts == {
        "grade:quiet:B": ("조용함 B", 50),
        "grade:peer:B": ("또래 밀도 B", 50),
        "grade:evening:A": ("저녁 활기 A", 51),
        "type:long_stay": ("1위 장기체류형", 58),
        "quiet:weekday:9": ("평일 9시 한산", 108),
        "quiet:weekend:3": ("주말 3시 한산", 102),
        "place:coast": ("바닷가", 97),
        "life:local": ("생활권", 107),
        "region:51150": ("같은 강릉시", 21),
    }


def test_common_facts_rare_first():
    raw = jumunjin_raw()
    mine = queries.fact_list(raw["facts"]["5115025000"])
    nohak = queries.fact_list(raw["facts"]["5121057000"])
    assert [label for _, label in queries.common_facts(mine, nohak)] == [
        "조용함 B", "1위 장기체류형", "바닷가", "주말 3시 한산", "생활권", "평일 9시 한산",
    ]  # fmt: skip


def test_score_diff_other_minus_mine_and_null():
    assert queries.score_diff({"quiet": 31, "peer": 39, "evening": 62}, {
        "quiet": 39, "peer": 52, "evening": 47,
    }) == {"quiet": 8, "peer": 13, "evening": -15}  # fmt: skip
    assert queries.score_diff({"quiet": 31}, {"quiet": 39, "peer": 52}) == {
        "quiet": 8, "peer": None, "evening": None,
    }  # fmt: skip


def test_shape_dong_jumunjin():
    body = queries.shape_dong(jumunjin_raw(), ["문장 1", "문장 2", "문장 3"])
    assert body["id"] == "5115025000" and body["name"] == "주문진읍"
    assert body["sigungu"] == {"id": "51150", "name": "강릉시"}
    assert body["sido"] == "강원특별자치도" and body["coastKm"] == 0.0
    assert [(t["name"], t["score"]) for t in body["types"]] == [
        ("장기체류형", 61), ("로컬살이형", 60), ("네트워킹형", 50), ("몰입형", 36),
    ]  # fmt: skip
    assert body["types"][0]["reason"] == "숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달)"
    assert body["grades"] == {
        "quiet": {"grade": "B", "score": 31},
        "peer": {"grade": "B", "score": 39},
        "evening": {"grade": "A", "score": 62},
    }
    assert body["hourly"]["weekday"] == {"driv": JUMUNJIN_WEEKDAY, "peakHour": 12, "quietHour": 9}
    assert (body["hourly"]["weekend"]["peakHour"], body["hourly"]["weekend"]["quietHour"]) == (
        14,
        3,
    )
    assert body["months"] == {
        "raw": [GANGNEUNG_RAW[m] for m in range(1, 13)],
        "quiet": [2, 4, 9],
        "basis": "강릉시",
    }
    assert body["briefing"] == ["문장 1", "문장 2", "문장 3"]
    assert body["syncedAt"] == "2026-11-13T10:00:00+09:00"


def test_shape_dong_similar():
    similar = queries.shape_dong(jumunjin_raw(), [])["similar"]
    assert [(s["name"], s["sigungu"], s["common"]) for s in similar] == [
        ("노학동", "속초시", 6), ("대정읍", "서귀포시", 4), ("영천동", "서귀포시", 5),
    ]  # fmt: skip
    assert similar[0]["id"] == "5121057000"
    assert similar[0]["commonTop"] == ["조용함 B", "1위 장기체류형", "바닷가"]
    assert similar[0]["diff"] == {"quiet": 8, "peer": 13, "evening": -15}
    assert similar[1]["commonTop"] == ["저녁 활기 A", "바닷가", "주말 3시 한산"]
    assert similar[1]["diff"] == {"quiet": 19, "peer": 11, "evening": -3}
    assert similar[2]["commonTop"] == ["조용함 B", "저녁 활기 A", "바닷가"]
    assert similar[2]["diff"] == {"quiet": 10, "peer": 20, "evening": 5}


def test_shape_dong_missing_values():
    raw = jumunjin_raw()
    raw["rec"]["grades"] = [g for g in raw["rec"]["grades"] if g[0] != "peer"]
    raw["rec"]["months"] = [row for row in raw["rec"]["months"] if row[0] != 5]
    raw["rec"]["dong"]["driv_weekend"] = [-1] * 23 + [788]
    raw["rec"]["similar"] = []
    body = queries.shape_dong(raw, [])
    assert body["grades"]["peer"] is None
    assert body["months"]["raw"][4] is None
    assert body["hourly"]["weekend"]["driv"] == [None] * 23 + [788]
    assert body["similar"] == [] and body["briefing"] == []


class FakeRecord(dict):
    def data(self):
        return dict(self)


class FakeResult(list):
    def single(self):
        return self[0] if self else None


class FakeTx:
    """Cypher 문자열 → 돌려줄 레코드 목록."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def run(self, query, **params):
        self.calls.append((query, params))
        return FakeResult(FakeRecord(r) for r in self.answers.get(query, []))


META_ROW = {"synced_at": "2026-11-13T10:00:00+09:00", "dong_count": 203, "region_count": 12}


def fake_read(tx):
    return lambda work, **params: work(tx, **params)


def test_fetch_dong_reads_self_and_similar_facts(monkeypatch):
    raw = jumunjin_raw()
    tx = FakeTx(
        {
            queries.META: [META_ROW],
            queries.DONG: [raw["rec"]],
            queries.FACTS: list(raw["facts"].values()),
        }
    )
    monkeypatch.setattr(queries, "read", fake_read(tx))
    assert queries.fetch_dong("5115025000") == raw
    facts_call = next(p for q, p in tx.calls if q == queries.FACTS)
    assert facts_call["codes"] == ["5115025000", "5121057000", "5013025000", "5013056000"]


def test_fetch_dong_unknown_is_none(monkeypatch):
    monkeypatch.setattr(queries, "read", fake_read(FakeTx({queries.META: [META_ROW]})))
    assert queries.fetch_dong("0000000000") is None


def test_fetch_pins(monkeypatch):
    tx = FakeTx({queries.META: [META_ROW], queries.PINS: [PINS_REC]})
    monkeypatch.setattr(queries, "read", fake_read(tx))
    assert queries.fetch_pins("51150")["dongs"][2]["id"] == "5115025000"
    monkeypatch.setattr(queries, "read", fake_read(FakeTx({queries.META: [META_ROW]})))
    assert queries.fetch_pins("99999") is None


def test_no_meta_is_unavailable(monkeypatch):
    monkeypatch.setattr(queries, "read", fake_read(FakeTx({})))
    with pytest.raises(GraphUnavailable, match="sync_graph"):
        queries.fetch_dong("5115025000")
    with pytest.raises(GraphUnavailable):
        queries.fetch_pins("51150")
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_queries.py -v`
Expected: FAIL — `ImportError: cannot import name 'queries' from 'explore'`

- [ ] **Step 4: 구현 `explore/queries.py`**

```python
"""Neo4j 읽기와 API 응답 모양 (스펙 §6). 읽기는 graphdb.read 한 트랜잭션에서 Cypher 몇 개.

fetch_* 는 Neo4j 레코드를 dict 로 돌려주고, shape_* 는 그 dict 를 웹 계약 모양으로 바꾼다(순수).
"""

from __future__ import annotations

from explore.graph import METRIC_KO, METRICS
from explore.graphdb import GraphUnavailable, read

EMPTY = "Neo4j 에 탐색 그래프가 없음: uv run python manage.py sync_graph 실행 필요"

META = (
    "MATCH (m:Meta) RETURN m.synced_at AS synced_at,"
    " m.dong_count AS dong_count, m.region_count AS region_count"
)

PINS = """
MATCH (r:Region {id: 'region:' + $code})
OPTIONAL MATCH (d:Dong)-[:IN]->(r)
RETURN r.code AS code, collect(d {
  .code, .name, .lat, .lng,
  tags: [(d)-[:HAS_TAG]->(t:PoiTag) | [t.order, t.code]],
  top: [(d)-[f:FITS {rank: 1}]->(ty:Type) | [ty.name, f.score]],
  near: [(d)-[:NEAR]->(p:Place) | p.id],
  life: [(d)-[:LIFE]->(t:Tag) | t.name],
  grades: [(d)-[:HAS_GRADE]->(g:Grade) | [g.metric, g.grade]]
}) AS dongs
"""

DONG = """
MATCH (d:Dong {id: 'dong:' + $code})-[:IN]->(r:Region)-[:IN]->(s:Sido)
RETURN d {.code, .name, .coast_km, .driv_weekday, .driv_weekend} AS dong,
  r {.code, .name} AS region, s.name AS sido,
  [(d)-[f:FITS]->(t:Type) | [f.rank, t.name, f.score, f.reason]] AS types,
  [(d)-[g:HAS_GRADE]->(x:Grade) |
    [x.metric, x.grade, g.score, g.rank, g.of, g.rank_sgg, g.of_sgg]] AS grades,
  [(d)-[h:QUIET_AT|PEAK_AT]->(x:Hour) | [type(h), h.day, x.h]] AS hours,
  [(d)-[n:NEAR]->(:Place) | n.km] AS near,
  [(r)-[e:EASE]->(m:Month) | [m.m, e.raw, e.quiet]] AS months,
  [(d)-[s2:SIMILAR]->(o:Dong)-[:IN]->(o_r:Region) | [s2.rank, o.code, o.name, o_r.name]]
    AS similar
"""

# 공통점이 되는 사실 노드 (스펙 §6-3): 등급 · 1위 유형 · 한산 시각(day 별) · 바닷가 · 생활 · 시군구
FACTS = """
UNWIND $codes AS c
MATCH (d:Dong {id: 'dong:' + c})-[:IN]->(r:Region)
RETURN d.code AS code,
  [(d)-[g:HAS_GRADE]->(x:Grade) |
    {metric: x.metric, grade: x.grade, score: g.score, degree: x.degree}] AS grades,
  [(d)-[:FITS {rank: 1}]->(t:Type) | {id: t.id, name: t.name, degree: t.degree}] AS top,
  [(d)-[q:QUIET_AT]->(h:Hour) | {day: q.day, h: h.h,
    degree: CASE q.day WHEN 'weekday' THEN h.degree_weekday ELSE h.degree_weekend END}] AS quiet,
  [(d)-[:NEAR]->(p:Place) | {id: p.id, kind: p.kind, degree: p.degree}] AS near,
  [(d)-[:LIFE]->(t:Tag) | {id: t.id, name: t.name, degree: t.degree}] AS life,
  r {.id, .name, .degree} AS region
"""

DAY_KO = {"weekday": "평일", "weekend": "주말"}


def _meta(tx) -> dict:
    meta = tx.run(META).single()
    if meta is None:
        raise GraphUnavailable(EMPTY)
    return meta.data()


def _pins_work(tx, code):
    _meta(tx)
    rec = tx.run(PINS, code=code).single()
    return None if rec is None else rec.data()


def _dong_work(tx, code):
    meta = _meta(tx)
    rec = tx.run(DONG, code=code).single()
    if rec is None:
        return None
    rec = rec.data()
    codes = [code] + [row[1] for row in rec["similar"]]
    facts = {r["code"]: r.data() for r in tx.run(FACTS, codes=codes)}
    return {"rec": rec, "facts": facts, **meta}


def fetch_pins(code: str) -> dict | None:
    """시군구 핀 응답. 없는 시군구면 None. Neo4j 문제는 GraphUnavailable."""
    rec = read(_pins_work, code=code)
    return None if rec is None else shape_pins(rec)


def fetch_dong(code: str) -> dict | None:
    """동 상세 원재료 {"rec", "facts": {code: row}, "synced_at", "dong_count", "region_count"}.

    없는 동이면 None. 응답 모양은 shape_dong, 브리핑 사실은 brief.collect_facts 가 만든다."""
    return read(_dong_work, code=code)


def shape_pins(rec: dict) -> dict:
    dongs = []
    for d in sorted(rec["dongs"], key=lambda d: d["name"]):
        grades = dict(d["grades"])
        top = d["top"][0] if d["top"] else [None, None]
        dongs.append(
            {
                "id": d["code"],
                "name": d["name"],
                "center": {"lat": d["lat"], "lng": d["lng"]},
                "tags": [code for _, code in sorted(d["tags"])],
                "topType": top[0],
                "topScore": top[1],
                "coastal": bool(d["near"]),
                "life": d["life"][0] if d["life"] else None,
                "grades": {"quiet": grades.get("quiet"), "evening": grades.get("evening")},
            }
        )
    return {"sigunguId": rec["code"], "dongs": dongs}


def fact_list(row: dict) -> dict[str, tuple[str, int]]:
    """FACTS 한 행 → {사실 key: (문구, 이어진 동 수)}. 문구는 웹 commonTop 에 그대로 나간다."""
    out = {}
    for g in row["grades"]:
        out[f"grade:{g['metric']}:{g['grade']}"] = (
            f"{METRIC_KO[g['metric']]} {g['grade']}",
            g["degree"],
        )
    for t in row["top"]:
        out[t["id"]] = (f"1위 {t['name']}", t["degree"])
    for q in row["quiet"]:
        out[f"quiet:{q['day']}:{q['h']}"] = (f"{DAY_KO[q['day']]} {q['h']}시 한산", q["degree"])
    for p in row["near"]:
        out[p["id"]] = (p["kind"], p["degree"])
    for t in row["life"]:
        out[t["id"]] = (t["name"], t["degree"])
    r = row["region"]
    out[r["id"]] = (f"같은 {r['name']}", r["degree"])
    return out


def common_facts(mine: dict, other: dict) -> list[tuple[str, str]]:
    """두 동이 함께 가진 사실 [(key, 문구)]. 이어진 동이 적은(드문) 사실부터, 같으면 key 순."""
    keys = sorted(set(mine) & set(other), key=lambda k: (mine[k][1], k))
    return [(k, mine[k][0]) for k in keys]


def scores_of(row: dict) -> dict[str, int]:
    return {g["metric"]: g["score"] for g in row["grades"]}


def score_diff(mine: dict[str, int], other: dict[str, int]) -> dict[str, int | None]:
    """상대 − 이 동. 한쪽이라도 없으면 null."""
    return {m: (other[m] - mine[m]) if m in mine and m in other else None for m in METRICS}


def _driv(values: list | None) -> list[int | None]:
    if not values:
        return [None] * 24
    return [None if v < 0 else v for v in values]


def shape_dong(raw: dict, briefing: list[str]) -> dict:
    rec = raw["rec"]
    d, region = rec["dong"], rec["region"]
    me = raw["facts"][d["code"]]
    mine = fact_list(me)
    hours = {(rel, day): h for rel, day, h in rec["hours"]}
    grades = {g[0]: {"grade": g[1], "score": g[2]} for g in rec["grades"]}
    month_raw = {m: v for m, v, _ in rec["months"]}
    similar = []
    for _, code, name, sigungu in sorted(rec["similar"]):
        other = raw["facts"].get(code)
        if other is None:
            continue
        common = common_facts(mine, fact_list(other))
        similar.append(
            {
                "id": code,
                "name": name,
                "sigungu": sigungu,
                "common": len(common),
                "commonTop": [label for _, label in common[:3]],
                "diff": score_diff(scores_of(me), scores_of(other)),
            }
        )
    return {
        "id": d["code"],
        "name": d["name"],
        "sigungu": {"id": region["code"], "name": region["name"]},
        "sido": rec["sido"],
        "coastKm": d["coast_km"],
        "types": [
            {"name": name, "score": score, "reason": reason}
            for _, name, score, reason in sorted(rec["types"])
        ],
        "grades": {m: grades.get(m) for m in METRICS},
        "hourly": {
            day: {
                "driv": _driv(d[f"driv_{day}"]),
                "peakHour": hours.get(("PEAK_AT", day)),
                "quietHour": hours.get(("QUIET_AT", day)),
            }
            for day in ("weekday", "weekend")
        },
        "months": {
            "raw": [month_raw.get(m) for m in range(1, 13)],
            "quiet": sorted(m for m, _, quiet in rec["months"] if quiet),
            "basis": region["name"],
        },
        "briefing": briefing,
        "similar": similar,
        "syncedAt": raw["synced_at"],
    }
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_explore_queries.py -v`
Expected: 12 passed

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add explore/queries.py tests/explore_samples.py tests/test_explore_queries.py
git commit -m "feat: 탐색 v6 Neo4j 조회 (핀 · 동 상세 · 비슷한 동 공통점)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: API 두 개 · 캐시 · gzip · 브리핑 행

**Files:**
- Create: `explore/views.py`, `explore/urls.py`, `tests/test_explore_api.py`
- Modify: `config/urls.py`, `config/settings.py` (`MIDDLEWARE`), `explore/sources.py` (브리핑 행 4개 함수), `tests/test_explore_build.py` (브리핑 행 테스트 2개)

**Interfaces:**
- Consumes: Task 6 `fetch_pins`, `fetch_dong`, `shape_pins`, `shape_dong`, `PINS_REC`(테스트); Task 5 `GraphUnavailable`; `core.errors.error`
- Produces:
  - `GET /api/explore/sigungu/<sigungu_id>/pins` (name `explore-pins`), `GET /api/explore/dongs/<dong_id>` (name `explore-dong`)
  - `views._serve(key, load, nf_code, nf_msg)` — 캐시 1시간, `{key}:last` 만료 없음, `GraphUnavailable` → last 또는 503, `None` → 404 (캐시 안 함)
  - `sources.load_brief_targets() -> [(dong_id, adm_code)]`, `load_brief_hashes() -> {dong_id: hash}`, `save_brief(dong_id, sentences, source, model, facts_hash)` (upsert), `fetch_briefing(adm_code) -> list[str]` (`DatabaseError` → `[]`)

- [ ] **Step 1: 실패하는 테스트 `tests/test_explore_api.py`**

```python
"""GET /api/explore/sigungu/{id}/pins · /api/explore/dongs/{id}.
queries · sources 함수를 바꿔 끼워 Neo4j · DB 없이 본다."""

import pytest
from django.core.cache import cache

from explore import queries, sources
from explore.graphdb import GraphUnavailable
from tests.explore_samples import jumunjin_raw
from tests.test_explore_queries import PINS_REC

PINS = queries.shape_pins(PINS_REC)
UNAVAILABLE = {
    "error": {"code": "GRAPH_UNAVAILABLE", "message": "탐색 데이터를 불러오지 못했어요."}
}


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def no_briefing(monkeypatch):
    monkeypatch.setattr(sources, "fetch_briefing", lambda code: [])


def down(*args):
    raise GraphUnavailable("connection refused")


def test_pins(client, monkeypatch):
    monkeypatch.setattr(queries, "fetch_pins", lambda code: PINS if code == "51150" else None)
    res = client.get("/api/explore/sigungu/51150/pins")
    assert res.status_code == 200
    jumunjin = next(d for d in res.json()["dongs"] if d["id"] == "5115025000")
    assert (jumunjin["topType"], jumunjin["topScore"], jumunjin["coastal"]) == (
        "장기체류형",
        61,
        True,
    )
    assert jumunjin["life"] == "생활권" and jumunjin["grades"] == {"quiet": "B", "evening": "A"}


def test_pins_not_found_is_not_cached(client, monkeypatch):
    calls = []
    monkeypatch.setattr(queries, "fetch_pins", lambda code: calls.append(code))
    res = client.get("/api/explore/sigungu/99999/pins")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "SIGUNGU_NOT_FOUND"
    client.get("/api/explore/sigungu/99999/pins")
    assert calls == ["99999", "99999"]


def test_dong_detail_with_briefing(client, monkeypatch):
    monkeypatch.setattr(queries, "fetch_dong", lambda code: jumunjin_raw())
    monkeypatch.setattr(sources, "fetch_briefing", lambda code: ["가", "나", "다"])
    body = client.get("/api/explore/dongs/5115025000").json()
    assert body["name"] == "주문진읍" and body["briefing"] == ["가", "나", "다"]
    assert body["types"][0] == {
        "name": "장기체류형",
        "score": 61,
        "reason": "숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달)",
    }
    assert body["months"]["quiet"] == [2, 4, 9]
    assert body["similar"][0]["commonTop"] == ["조용함 B", "1위 장기체류형", "바닷가"]


def test_dong_without_briefing_row(client, monkeypatch, no_briefing):
    monkeypatch.setattr(queries, "fetch_dong", lambda code: jumunjin_raw())
    assert client.get("/api/explore/dongs/5115025000").json()["briefing"] == []


def test_dong_not_found(client, monkeypatch, no_briefing):
    monkeypatch.setattr(queries, "fetch_dong", lambda code: None)
    res = client.get("/api/explore/dongs/0000000000")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DONG_NOT_FOUND"


def test_cache_hit_skips_neo4j(client, monkeypatch, no_briefing):
    calls = []
    monkeypatch.setattr(queries, "fetch_dong", lambda code: calls.append(code) or jumunjin_raw())
    client.get("/api/explore/dongs/5115025000")
    client.get("/api/explore/dongs/5115025000")
    assert calls == ["5115025000"]


def test_neo4j_down_serves_last_response(client, monkeypatch):
    monkeypatch.setattr(queries, "fetch_pins", lambda code: PINS)
    client.get("/api/explore/sigungu/51150/pins")
    cache.delete("explore:pins:51150")  # 1시간 캐시가 만료된 상황
    monkeypatch.setattr(queries, "fetch_pins", down)
    res = client.get("/api/explore/sigungu/51150/pins")
    assert res.status_code == 200 and res.json() == PINS


def test_neo4j_down_without_last_is_503(client, monkeypatch, no_briefing):
    monkeypatch.setattr(queries, "fetch_pins", down)
    monkeypatch.setattr(queries, "fetch_dong", down)
    for url in ("/api/explore/sigungu/51150/pins", "/api/explore/dongs/5115025000"):
        res = client.get(url)
        assert res.status_code == 503 and res.json() == UNAVAILABLE


def test_response_is_gzipped(client, monkeypatch, no_briefing):
    monkeypatch.setattr(queries, "fetch_dong", lambda code: jumunjin_raw())
    res = client.get("/api/explore/dongs/5115025000", HTTP_ACCEPT_ENCODING="gzip")
    assert res["Content-Encoding"] == "gzip"
```

`tests/test_explore_build.py` 의 `from django.db import connection` 을 `from django.db import DatabaseError, connection` 으로 바꾸고 파일 끝에 추가:

```python
def test_brief_rows(explore_data):
    build()
    assert sources.load_brief_targets() == [
        (112, "5115037000"), (116, "5115025000"), (901, "5211357500"), (902, "5211160500"),
    ]  # fmt: skip
    sources.save_brief(116, ["가", "나", "다"], "mixed", "HCX-005", "a" * 64)
    sources.save_brief(116, ["라", "마", "바"], "rule", None, "b" * 64)  # upsert
    assert sources.load_brief_hashes() == {116: "b" * 64}
    assert sources.fetch_briefing("5115025000") == ["라", "마", "바"]
    assert sources.fetch_briefing("5115037000") == []


def test_fetch_briefing_db_error_is_empty(monkeypatch):
    class Broken:
        def cursor(self):
            raise DatabaseError('relation "dong_brief" does not exist')

    monkeypatch.setattr(sources, "connection", Broken())
    assert sources.fetch_briefing("5115025000") == []
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_api.py tests/test_explore_build.py -v`
Expected: FAIL — API 는 404(경로 없음), `AttributeError: module 'explore.sources' has no attribute 'fetch_briefing'`

- [ ] **Step 3: `explore/sources.py` 에 브리핑 행 함수**

import 블록을 다음으로 바꾼다 (`json` · `logging` · `DatabaseError` · `logger` 추가. `# 집계 제외 …` 주석과 `DONG_OK` 는 그 아래 그대로):

```python
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass

from django.db import DatabaseError, connection, transaction

from explore.derive import TypeScore
from explore.graph import DongRow, GraphInput, RegionRow

logger = logging.getLogger(__name__)
```

파일 끝에 추가:

```python
def load_brief_targets() -> list[tuple[int, str]]:
    """브리핑을 만들 동 (dong_id, adm_code). build_explore 가 유형 점수를 만든 동."""
    return _rows(
        f"""SELECT DISTINCT d.dong_id, d.adm_code FROM dong d
              JOIN dong_type_score t ON t.dong_id = d.dong_id
             WHERE {DONG_OK} ORDER BY d.dong_id"""
    )


def load_brief_hashes() -> dict[int, str]:
    return dict(_rows("SELECT dong_id, facts_hash FROM dong_brief"))


def save_brief(
    dong_id: int, sentences: list[str], source: str, model: str | None, facts_hash: str
) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO dong_brief (dong_id, sentences, source, model, facts_hash, generated_at)
               VALUES (%s, %s::jsonb, %s, %s, %s, now())
               ON CONFLICT (dong_id) DO UPDATE SET
                 sentences = EXCLUDED.sentences, source = EXCLUDED.source,
                 model = EXCLUDED.model, facts_hash = EXCLUDED.facts_hash,
                 generated_at = EXCLUDED.generated_at""",
            [dong_id, json.dumps(sentences, ensure_ascii=False), source, model, facts_hash],
        )


def fetch_briefing(adm_code: str) -> list[str]:
    """API 용. 행이 없거나 조회가 실패하면 [] (나머지 응답은 정상, 스펙 §6-4)."""
    try:
        with connection.cursor() as cur:
            cur.execute(
                """SELECT b.sentences FROM dong_brief b JOIN dong d ON d.dong_id = b.dong_id
                    WHERE d.adm_code = %s""",
                [adm_code],
            )
            row = cur.fetchone()
    except DatabaseError as ex:
        logger.warning("dong_brief 조회 실패: %s", ex)
        return []
    return list(row[0]) if row else []
```

- [ ] **Step 4: 뷰 · URL**

`explore/views.py`:

```python
"""탐색 v6 API (스펙 §6). 응답 1시간 캐시, Neo4j 가 안 되면 마지막 응답 또는 503."""

import logging

from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import error
from explore import queries, sources
from explore.graphdb import GraphUnavailable

logger = logging.getLogger(__name__)

TTL = 60 * 60


def _serve(key: str, load, nf_code: str, nf_msg: str) -> Response:
    """캐시 → load() → 실패 시 `{key}:last`(만료 없음) → 503. 없는 id(None)는 캐시하지 않는다."""
    body = cache.get(key)
    if body is not None:
        return Response(body)
    try:
        body = load()
    except GraphUnavailable as ex:
        logger.warning("탐색 그래프 조회 실패 %s: %s", key, ex)
        last = cache.get(f"{key}:last")
        if last is None:
            return error(503, "GRAPH_UNAVAILABLE", "탐색 데이터를 불러오지 못했어요.")
        return Response(last)
    if body is None:
        return error(404, nf_code, nf_msg)
    cache.set(key, body, TTL)
    cache.set(f"{key}:last", body, None)
    return Response(body)


@api_view(["GET"])
def sigungu_pins(request, sigungu_id: str):
    return _serve(
        f"explore:pins:{sigungu_id}",
        lambda: queries.fetch_pins(sigungu_id),
        "SIGUNGU_NOT_FOUND",
        "시군구를 찾을 수 없어요.",
    )


@api_view(["GET"])
def dong_detail(request, dong_id: str):
    def load():
        raw = queries.fetch_dong(dong_id)
        return None if raw is None else queries.shape_dong(raw, sources.fetch_briefing(dong_id))

    return _serve(f"explore:dong:{dong_id}", load, "DONG_NOT_FOUND", "동네를 찾을 수 없어요.")
```

`explore/urls.py`:

```python
"""탐색 v6 API (접두사 /api/explore/). 계약: 백엔드·웹 v6 스펙 §6"""

from django.urls import path

from explore import views

urlpatterns = [
    path("sigungu/<str:sigungu_id>/pins", views.sigungu_pins, name="explore-pins"),
    path("dongs/<str:dong_id>", views.dong_detail, name="explore-dong"),
]
```

`config/urls.py` 의 `path("api/", include("webapi.urls")),` 바로 위(그 줄의 주석 위)에 추가:

```text
    # 탐색 v6 (Neo4j 동네 근거 그래프). webapi 의 api/ 보다 먼저 둔다
    path("api/explore/", include("explore.urls")),
```

`config/settings.py` 의 `MIDDLEWARE` 를 다음으로 바꾼다 (CorsMiddleware 바로 다음에 GZip):

```python
MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    # 200바이트 이상 JSON 을 gzip (Accept-Encoding: gzip 일 때). 탐색 동 상세 응답이 크다
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_explore_api.py tests/test_explore_build.py -v`
Expected: 18 passed (api 9 · build 9)

- [ ] **Step 6: 전체 검사 (GZip 이 기존 API 를 깨지 않는지)**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
Expected: 전부 PASS. 테스트 클라이언트는 기본으로 Accept-Encoding 을 보내지 않아 기존 응답은 그대로다.

- [ ] **Step 7: Commit**

```bash
git add explore/views.py explore/urls.py explore/sources.py config/urls.py config/settings.py \
  tests/test_explore_api.py tests/test_explore_build.py
git commit -m "feat: GET /api/explore/sigungu/{id}/pins · /api/explore/dongs/{id} — 캐시 · 마지막 응답 · gzip

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 브리핑 사실 · 검증 · 규칙 문장 `brief.py` (순수)

**Files:**
- Create: `explore/brief.py`, `tests/test_explore_brief.py`

**Interfaces:**
- Consumes: Task 6 raw 모양(`jumunjin_raw`); Task 2 `derive.busiest_month`; Task 4 `GRADES`, `METRIC_KO`, `METRICS`; `core.clova.CHAT_MODEL`(`"HCX-005"`), `core.clova.ClovaError`
- Produces:
  - `Fact(id, text, values: frozenset[str], common: float)`, `Brief(sentences, source, model)`
  - 사실 id: `F1-{metric}`, `F1-{metric}-sgg`(시군구 1위이고 시군구 동이 2개 이상일 때), `F2-1`(평일), `F2-2`(주말), `F3-1`(한산한 달·붐비는 달), `F4-{rank}`, `F5-1`(바닷가), `F6-{n}`(비슷한 동)
  - `collect_facts(raw) -> list[Fact]` — 정렬 `(common > 0.3, common, id)`
  - `facts_hash(facts) -> str` (SHA-256 64자)
  - `validate_sentence(text, cited, facts_by_id) -> list[str]` (빈 목록 = 통과). 숫자 `\d+(?:\.\d+)?`(천 단위 쉼표는 먼저 지움), 등급 `(?<![A-Za-z])[SABC](?![A-Za-z])`, 60자, 금지어 `추천 · 최고 · 가장 좋 · 최적 · 강추 · 핫플`, 인용 id 존재
  - `josa(word, pair="은/는")`, `rule_sentences(raw, facts) -> [3문장]`, `build_prompt(raw, facts) -> (system, user)`, `parse_reply(reply) -> [(text, ids)] × 3` (아니면 `ValueError`), `compose(items, facts, rules) -> (sentences, source)`
  - `generate(raw, facts, chat) -> Brief` — `chat(system, user) -> str` 또는 None(키 없음). `ClovaError` 는 3번 다시 시도(최대 4번 호출), JSON 파싱 실패 → 전부 규칙, 문장별 검증 실패 → 그 자리만 규칙. `source` llm/mixed/rule, `model` 은 rule 이면 None

주문진읍 규칙 문장 기대값 (스펙 §5-4):
1. `평일 차량은 12시에 가장 많고, 업무시간엔 9시가 가장 한산해요.`
2. `강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요.`
3. `장기체류형 61점이고, 강릉시 안 저녁 활기 1위예요.` (시군구 1위 사실이 없으면 `{1위 유형} {점수}점으로, {가장 높은 등급 지표} {등급}({점수}점)이에요.`)

- [ ] **Step 1: 실패하는 테스트 `tests/test_explore_brief.py`**

```python
"""explore/brief.py 사실 · 검증 · 규칙 문장 · 생성 (순수, 가짜 채팅 함수)."""

import json

import pytest

from core.clova import ClovaError
from explore import brief
from tests.explore_samples import jumunjin_raw

RULES = [
    "평일 차량은 12시에 가장 많고, 업무시간엔 9시가 가장 한산해요.",
    "강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요.",
    "장기체류형 61점이고, 강릉시 안 저녁 활기 1위예요.",
]
GOOD = [
    ("평일엔 12시에 차가 가장 많고 9시가 가장 한산해요.", ["F2-1"]),
    ("강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요.", ["F3-1"]),
    ("저녁 활기는 강릉시 21동 중 1위예요.", ["F1-evening-sgg"]),
]


def facts():
    return brief.collect_facts(jumunjin_raw())


def by_id():
    return {f.id: f for f in facts()}


def reply(items):
    return json.dumps(
        {"sentences": [{"text": t, "facts": c} for t, c in items]}, ensure_ascii=False
    )


def test_collect_facts_rare_first():
    assert [f.id for f in facts()] == [
        "F1-evening-sgg", "F3-1", "F1-peer", "F1-quiet", "F1-evening", "F4-1",
        "F5-1", "F2-2", "F2-1", "F4-2", "F4-3", "F4-4", "F6-1", "F6-2", "F6-3",
    ]  # fmt: skip


def test_fact_texts_and_values():
    f = by_id()
    assert f["F1-evening"].text == "저녁 활기 A등급 62점, 전국 203동 중 78위"
    assert f["F1-evening"].values == {"A", "62", "203", "78"}
    assert f["F1-evening-sgg"].text == "강릉시 21동 중 저녁 활기 1위"
    assert f["F1-evening-sgg"].common == pytest.approx(12 / 203)
    assert f["F2-1"].text == (
        "평일 차량 이동 인구: 12시 2064명으로 가장 많고, "
        "업무시간(9~17시) 중 9시 1573명으로 가장 적음"
    )
    assert f["F2-2"].values == {"14", "3", "2833", "569"}
    assert f["F3-1"].text == "강릉시 월별 방문: 가장 한산한 달 2·4·9월, 가장 붐비는 달 8월"
    assert f["F3-1"].values == {"2", "4", "9", "8"}
    assert f["F4-1"].text == "장기체류형 61점 (유형 1위)"
    assert f["F5-1"].text == "바닷가 (해안선까지 0.0km)"
    assert f["F6-1"].text == "비슷한 동 1: 속초시 노학동" and f["F6-1"].values == frozenset()
    # 시군구 1위가 아닌 지표는 -sgg 사실이 없다
    assert "F1-quiet-sgg" not in f and "F1-peer-sgg" not in f


def test_validate_passes_good_sentences():
    for text, cited in GOOD:
        assert brief.validate_sentence(text, cited, by_id()) == []


def test_validate_rejects_months_not_in_facts():
    # 점수 기준 한산한 달(4·10월)은 사실 목록에 없다
    problems = brief.validate_sentence("강릉시는 4·10월이 가장 한산해요.", ["F3-1"], by_id())
    assert problems == ["사실에 없는 값: ['10']"]


def test_validate_rejects_grade_and_number_not_cited():
    f = by_id()
    assert brief.validate_sentence("저녁 활기 S등급이에요.", ["F1-evening"], f) == [
        "사실에 없는 값: ['S']"
    ]
    # 62점은 F1-evening 값이라 F1-evening-sgg 만 인용하면 실패
    assert brief.validate_sentence("저녁 활기 62점, 강릉 1위예요.", ["F1-evening-sgg"], f) == [
        "사실에 없는 값: ['62']"
    ]


def test_validate_thousands_separator():
    text = "평일 12시엔 차량이 2,064명으로 가장 많아요."
    assert brief.validate_sentence(text, ["F2-1"], by_id()) == []


def test_validate_length_banned_and_ids():
    f = by_id()
    long = "가" * 61
    assert brief.validate_sentence(long, ["F3-1"], f) == ["60자 초과"]
    assert brief.validate_sentence("8월 여행지로 추천해요.", ["F3-1"], f) == ["금지어: ['추천']"]
    assert brief.validate_sentence("8월이 붐벼요.", ["F9-1"], f) == [
        "인용 id 없음: ['F9-1']",
        "사실에 없는 값: ['8']",
    ]
    assert brief.validate_sentence("한산해요.", [], f) == ["인용 id 없음: 빈 목록"]


def test_rule_sentences_jumunjin():
    assert brief.rule_sentences(jumunjin_raw(), facts()) == RULES


def test_rule_sentences_pass_validation():
    cited = [["F2-1"], ["F3-1"], ["F4-1", "F1-evening-sgg"]]
    for text, ids in zip(RULES, cited, strict=True):
        assert brief.validate_sentence(text, ids, by_id()) == []


def test_rule_third_without_sigungu_first_uses_best_grade():
    raw = jumunjin_raw()
    raw["rec"]["grades"][2][5] = 2  # 저녁 활기 강릉 2위로
    third = brief.rule_sentences(raw, brief.collect_facts(raw))[2]
    assert third == "장기체류형 61점으로, 저녁 활기 A(62점)이에요."


def test_josa():
    assert brief.josa("강릉시") == "강릉시는"
    assert brief.josa("고성군") == "고성군은"
    assert brief.josa("서귀포시") == "서귀포시는"


def test_facts_hash_stable_and_sensitive():
    assert brief.facts_hash(facts()) == brief.facts_hash(facts())
    raw = jumunjin_raw()
    raw["rec"]["grades"][2][2] = 63
    assert brief.facts_hash(brief.collect_facts(raw)) != brief.facts_hash(facts())


def test_parse_reply_with_code_fence():
    text = "```json\n" + reply(GOOD) + "\n```"
    assert brief.parse_reply(text) == GOOD


@pytest.mark.parametrize("text", ["문장만 있어요", '{"sentences": []}', '{"x": 1}', "{bad"])
def test_parse_reply_rejects(text):
    with pytest.raises(ValueError):
        brief.parse_reply(text)


class FakeChat:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, system, user):
        self.calls += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_generate_llm():
    chat = FakeChat(reply(GOOD))
    result = brief.generate(jumunjin_raw(), facts(), chat)
    assert result == brief.Brief([t for t, _ in GOOD], "llm", "HCX-005")
    assert chat.calls == 1


def test_generate_mixed_replaces_only_bad_sentence():
    items = [GOOD[0], ("강릉시는 4·10월이 가장 한산해요.", ["F3-1"]), GOOD[2]]
    result = brief.generate(jumunjin_raw(), facts(), FakeChat(reply(items)))
    assert result.sentences == [GOOD[0][0], RULES[1], GOOD[2][0]]
    assert (result.source, result.model) == ("mixed", "HCX-005")


def test_generate_bad_json_is_all_rule():
    result = brief.generate(jumunjin_raw(), facts(), FakeChat("죄송해요"))
    assert result == brief.Brief(RULES, "rule", None)


def test_generate_retries_three_times_then_rule():
    chat = FakeChat(ClovaError("timeout"))
    result = brief.generate(jumunjin_raw(), facts(), chat)
    assert chat.calls == 4 and result.source == "rule"


def test_generate_recovers_after_failures():
    chat = FakeChat(ClovaError("429"), ClovaError("timeout"), reply(GOOD))
    assert brief.generate(jumunjin_raw(), facts(), chat).source == "llm"
    assert chat.calls == 3


def test_generate_without_chat_is_rule():
    assert brief.generate(jumunjin_raw(), facts(), None) == brief.Brief(RULES, "rule", None)


def test_prompt_lists_facts_rare_first():
    system, user = brief.build_prompt(jumunjin_raw(), facts())
    assert "60자" in system and '"sentences"' in system
    assert user.startswith(
        "동네: 강원특별자치도 강릉시 주문진읍\n사실 (드문 것부터):\nF1-evening-sgg: "
    )
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_brief.py -v`
Expected: FAIL — `ImportError: cannot import name 'brief' from 'explore'`

- [ ] **Step 3: 구현 `explore/brief.py`**

```python
"""동네 브리핑 3문장 (월 배치 ④, 스펙 §5-4). 순수 함수 — 채팅 함수는 인자로 받는다.

사실 모으기 → 프롬프트 → 응답 파싱 → 문장마다 코드 검증 → 실패한 자리만 규칙 문장.
사실 id(F1~F6)는 프롬프트·검증용 내부 값이고 화면에는 나가지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from core.clova import CHAT_MODEL, ClovaError
from explore import derive
from explore.graph import GRADES, METRIC_KO, METRICS

logger = logging.getLogger(__name__)

MAX_LEN = 60
RETRIES = 3  # 첫 호출이 실패하면 3번 더
COMMON_LATER = 0.3  # 이보다 흔한 사실은 목록 뒤로
BANNED = ("추천", "최고", "가장 좋", "최적", "강추", "핫플")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
GRADE = re.compile(r"(?<![A-Za-z])[SABC](?![A-Za-z])")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3})")

SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 브리핑 작성자다. 사용자가 준 사실 목록만 근거로 쓴다.",
        "- 정확히 3문장. 문장마다 60자 이내, 존댓말 해요체.",
        "- 1문장: 하루 흐름(시간대). 2문장: 1년 흐름(한산한 달).",
        "- 3문장: 이 동의 강점(유형·등급). 목록 앞쪽의 드문 사실을 하나 이상 쓴다.",
        "- 목록에 없는 숫자·달·시각·등급·장소 이름·추측을 쓰지 않는다.",
        "- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.",
        "- 문장마다 근거로 쓴 사실 id 를 facts 에 적는다.",
        '- JSON 하나만 출력한다: {"sentences": [{"text": "...", "facts": ["F2-1"]}, ...]}',
    ]
)


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    values: frozenset[str]  # 이 사실을 인용한 문장에 나와도 되는 숫자·등급
    common: float  # 같은 사실을 가진 동 비율 (0~1)


@dataclass(frozen=True)
class Brief:
    sentences: list[str]
    source: str  # llm · mixed · rule
    model: str | None


def _vals(*xs) -> frozenset[str]:
    return frozenset(str(x) for x in xs)


def _hours(rec: dict) -> dict[tuple[str, str], int]:
    return {(rel, day): h for rel, day, h in rec["hours"]}


def collect_facts(raw: dict) -> list[Fact]:
    """queries.fetch_dong 결과 → 사실 목록.
    드문 사실이 앞: 흔함 0.3 이하 먼저, 그 안에서 흔함 오름차순, 같으면 id 순."""
    rec, n = raw["rec"], raw["dong_count"]
    d, sgg = rec["dong"], rec["region"]["name"]
    me = raw["facts"][d["code"]]
    deg = {f"grade:{g['metric']}": g["degree"] for g in me["grades"]}
    deg |= {f"quiet:{q['day']}": q["degree"] for q in me["quiet"]}
    facts = []

    for metric, grade, score, rank, of, rank_sgg, of_sgg in rec["grades"]:
        ko = METRIC_KO[metric]
        facts.append(
            Fact(
                f"F1-{metric}",
                f"{ko} {grade}등급 {score}점, 전국 {of}동 중 {rank}위",
                _vals(grade, score, of, rank),
                deg[f"grade:{metric}"] / n,
            )
        )
        if rank_sgg == 1 and of_sgg > 1:
            facts.append(
                Fact(
                    f"F1-{metric}-sgg",
                    f"{sgg} {of_sgg}동 중 {ko} 1위",
                    _vals(1, of_sgg),
                    raw["region_count"] / n,
                )
            )

    hours = _hours(rec)
    for i, day in enumerate(("weekday", "weekend"), start=1):
        peak, quiet = hours.get(("PEAK_AT", day)), hours.get(("QUIET_AT", day))
        if peak is None or quiet is None:
            continue
        driv = d[f"driv_{day}"]
        if day == "weekday":
            text = (
                f"평일 차량 이동 인구: {peak}시 {driv[peak]}명으로 가장 많고, "
                f"업무시간(9~17시) 중 {quiet}시 {driv[quiet]}명으로 가장 적음"
            )
            values = _vals(peak, quiet, driv[peak], driv[quiet], 9, 17)
        else:
            text = (
                f"주말 차량 이동 인구: {peak}시 {driv[peak]}명으로 가장 많고, "
                f"{quiet}시 {driv[quiet]}명으로 가장 적음"
            )
            values = _vals(peak, quiet, driv[peak], driv[quiet])
        facts.append(Fact(f"F2-{i}", text, values, deg[f"quiet:{day}"] / n))

    month_raw = {m: v for m, v, _ in rec["months"] if v is not None}
    quiet_m = sorted(m for m, _, q in rec["months"] if q)
    busiest = derive.busiest_month(month_raw)
    if quiet_m and busiest is not None:
        facts.append(
            Fact(
                "F3-1",
                f"{sgg} 월별 방문: 가장 한산한 달 {'·'.join(map(str, quiet_m))}월, "
                f"가장 붐비는 달 {busiest}월",
                _vals(*quiet_m, busiest),
                me["region"]["degree"] / n,
            )
        )

    top_degree = me["top"][0]["degree"] if me["top"] else n
    for rank, name, score, _ in sorted(rec["types"]):
        common = top_degree / n if rank == 1 else 1.0  # 1위가 아닌 유형은 강점이 아니므로 뒤로
        facts.append(
            Fact(f"F4-{rank}", f"{name} {score}점 (유형 {rank}위)", _vals(score, rank), common)
        )

    if rec["near"]:
        km = f"{rec['near'][0]:.1f}"
        facts.append(
            Fact("F5-1", f"바닷가 (해안선까지 {km}km)", _vals(km), me["near"][0]["degree"] / n)
        )

    for rank, _, name, sigungu in sorted(rec["similar"]):
        facts.append(Fact(f"F6-{rank}", f"비슷한 동 {rank}: {sigungu} {name}", frozenset(), 1.0))

    return sorted(facts, key=lambda f: (f.common > COMMON_LATER, f.common, f.id))


def facts_hash(facts: list[Fact]) -> str:
    """사실 목록이 같으면 같은 값. 같으면 브리핑을 다시 만들지 않는다."""
    body = [[f.id, f.text, sorted(f.values)] for f in facts]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()


def validate_sentence(text: str, cited: list[str], facts: dict[str, Fact]) -> list[str]:
    """문제 목록. 비어 있으면 통과."""
    problems = []
    if len(text) > MAX_LEN:
        problems.append(f"{MAX_LEN}자 초과")
    unknown = [c for c in cited if c not in facts]
    if not cited or unknown:
        problems.append(f"인용 id 없음: {unknown or '빈 목록'}")
    banned = [w for w in BANNED if w in text]
    if banned:
        problems.append(f"금지어: {banned}")
    allowed = set().union(*(facts[c].values for c in cited if c in facts))
    plain = THOUSANDS.sub("", text)
    stray = [x for x in NUMBER.findall(plain) + GRADE.findall(plain) if x not in allowed]
    if stray:
        problems.append(f"사실에 없는 값: {stray}")
    return problems


def josa(word: str, pair: str = "은/는") -> str:
    """받침이 있으면 앞, 없으면 뒤. josa('강릉시') → '강릉시는'."""
    with_final, without = pair.split("/")
    code = ord(word[-1]) - 0xAC00
    has_final = 0 <= code <= 11171 and code % 28 != 0
    return word + (with_final if has_final else without)


def rule_sentences(raw: dict, facts: list[Fact]) -> list[str]:
    """검증이 필요 없는 규칙 문장 3개 (스펙 §5-4 5번)."""
    rec = raw["rec"]
    sgg = rec["region"]["name"]
    hours = _hours(rec)
    peak, quiet = hours.get(("PEAK_AT", "weekday")), hours.get(("QUIET_AT", "weekday"))
    if peak is None or quiet is None:
        first = "평일 시간대별 차량 자료가 아직 없어요."
    else:
        first = f"평일 차량은 {peak}시에 가장 많고, 업무시간엔 {quiet}시가 가장 한산해요."

    month_raw = {m: v for m, v, _ in rec["months"] if v is not None}
    quiet_m = sorted(m for m, _, q in rec["months"] if q)
    busiest = derive.busiest_month(month_raw)
    if not quiet_m or busiest is None:
        second = f"{josa(sgg)} 월별 방문 자료가 아직 없어요."
    else:
        months = "·".join(map(str, quiet_m))
        second = f"{josa(sgg)} {months}월이 가장 한산하고 {busiest}월이 가장 붐벼요."

    _, top_name, top_score, _ = sorted(rec["types"])[0]
    sgg_first = [f.id.split("-")[1] for f in facts if f.id.endswith("-sgg")]
    grades = {g[0]: g for g in rec["grades"]}
    if sgg_first:
        metric = min(sgg_first, key=METRICS.index)
        third = f"{top_name} {top_score}점이고, {sgg} 안 {METRIC_KO[metric]} 1위예요."
    elif grades:
        best = min(grades.values(), key=lambda g: (GRADES.index(g[1]), -g[2], METRICS.index(g[0])))
        third = f"{top_name} {top_score}점으로, {METRIC_KO[best[0]]} {best[1]}({best[2]}점)이에요."
    else:
        third = f"{top_name} {top_score}점이에요."
    return [first, second, third]


def build_prompt(raw: dict, facts: list[Fact]) -> tuple[str, str]:
    rec = raw["rec"]
    lines = "\n".join(f"{f.id}: {f.text}" for f in facts)
    where = f"{rec['sido']} {rec['region']['name']} {rec['dong']['name']}"
    return SYSTEM, f"동네: {where}\n사실 (드문 것부터):\n{lines}"


def parse_reply(reply: str) -> list[tuple[str, list[str]]]:
    """{"sentences": [{"text", "facts"}] × 3} → [(문장, 사실 id 목록)]. 모양이 다르면 ValueError."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON 없음")
    try:
        items = json.loads(reply[start : end + 1])["sentences"]
        out = [(str(i["text"]).strip(), [str(x) for x in i["facts"]]) for i in items]
    except (json.JSONDecodeError, KeyError, TypeError) as ex:
        raise ValueError(f"JSON 모양 다름: {ex}") from ex
    if len(out) != 3:
        raise ValueError(f"문장 수 {len(out)}")
    return out


def compose(
    items: list[tuple[str, list[str]]], facts: list[Fact], rules: list[str]
) -> tuple[list[str], str]:
    """검증을 통과한 생성 문장은 그대로, 실패한 자리만 규칙 문장. (문장 3개, source)"""
    by_id = {f.id: f for f in facts}
    out, passed = [], 0
    for (text, cited), rule in zip(items, rules, strict=True):
        problems = validate_sentence(text, cited, by_id)
        if problems:
            logger.info("브리핑 문장 교체 %r: %s", text, problems)
            out.append(rule)
        else:
            out.append(text)
            passed += 1
    source = "llm" if passed == 3 else "rule" if passed == 0 else "mixed"
    return out, source


def generate(raw: dict, facts: list[Fact], chat: Callable[[str, str], str] | None) -> Brief:
    """chat(system, user) → 응답 문자열. None 이면(키 없음) 규칙 문장만."""
    rules = rule_sentences(raw, facts)
    if chat is None:
        return Brief(rules, "rule", None)
    system, user = build_prompt(raw, facts)
    reply = None
    for attempt in range(1 + RETRIES):
        try:
            reply = chat(system, user)
            break
        except ClovaError as ex:
            logger.warning("브리핑 생성 실패 (%d번째): %s", attempt + 1, ex)
    if reply is None:
        return Brief(rules, "rule", None)
    try:
        items = parse_reply(reply)
    except ValueError as ex:
        logger.warning("브리핑 응답 파싱 실패: %s", ex)
        return Brief(rules, "rule", None)
    sentences, source = compose(items, facts, rules)
    return Brief(sentences, source, None if source == "rule" else CHAT_MODEL)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_explore_brief.py -v`
Expected: 24 passed

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add explore/brief.py tests/test_explore_brief.py
git commit -m "feat: 탐색 v6 브리핑 사실 · 숫자 검증 · 규칙 문장 대체

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `manage.py build_briefs` + CLOVA 시간 제한 인자

**Files:**
- Create: `explore/management/commands/build_briefs.py`, `tests/test_explore_briefs_command.py`
- Modify: `core/clova.py` (`chat` 에 `timeout` 인자)

**Interfaces:**
- Consumes: Task 6 `queries.fetch_dong`; Task 7 `sources.load_brief_targets`, `load_brief_hashes`, `save_brief`; Task 8 `brief.collect_facts`, `facts_hash`, `generate`; `core.clova.enabled`, `chat`
- Produces:
  - `core.clova.chat(system, user, max_tokens=300, temperature=0.0, timeout=15)` — 기존 호출(검색)은 그대로 15초
  - `build_briefs.chat(system, user)` — `max_tokens=400, temperature=0.3, timeout=20`
  - 명령 출력 `llm a · mixed b · rule c · 건너뜀 d`, rule 이 만든 수의 20% 초과면 경고. `--force`. `GraphUnavailable` → `CommandError`

- [ ] **Step 1: 실패하는 테스트 `tests/test_explore_briefs_command.py`**

```python
"""manage.py build_briefs. Neo4j · DB · CLOVA 대신 함수를 바꿔 끼운다."""

import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from core import clova
from explore import brief, queries, sources
from explore.graphdb import GraphUnavailable
from explore.management.commands import build_briefs
from tests.explore_samples import jumunjin_raw

GOOD = {
    "sentences": [
        {"text": "평일엔 12시에 차가 가장 많고 9시가 가장 한산해요.", "facts": ["F2-1"]},
        {"text": "강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요.", "facts": ["F3-1"]},
        {"text": "저녁 활기는 강릉시 21동 중 1위예요.", "facts": ["F1-evening-sgg"]},
    ]
}


@pytest.fixture
def saved(monkeypatch):
    rows = []
    monkeypatch.setattr(sources, "load_brief_targets", lambda: [(116, "5115025000")])
    monkeypatch.setattr(sources, "load_brief_hashes", lambda: {})
    monkeypatch.setattr(sources, "save_brief", lambda *args: rows.append(args))
    monkeypatch.setattr(queries, "fetch_dong", lambda code: jumunjin_raw())
    return rows


def run(*args):
    out = StringIO()
    call_command("build_briefs", *args, stdout=out)
    return out.getvalue()


def test_with_clova(settings, monkeypatch, saved):
    settings.CLOVA_API_KEY = "test-key"
    monkeypatch.setattr(build_briefs, "chat", lambda s, u: json.dumps(GOOD, ensure_ascii=False))
    text = run()
    assert "llm 1 · mixed 0 · rule 0" in text
    dong_id, sentences, source, model, digest = saved[0]
    assert (dong_id, source, model) == (116, "llm", "HCX-005")
    assert sentences[1] == "강릉시는 2·4·9월이 가장 한산하고 8월이 가장 붐벼요."
    assert digest == brief.facts_hash(brief.collect_facts(jumunjin_raw()))


def test_without_clova_key_rule_only_and_warns(settings, saved):
    settings.CLOVA_API_KEY = ""
    text = run()
    assert "CLOVA_API_KEY 없음" in text and "rule 1" in text
    assert "20% 초과" in text
    assert saved[0][2] == "rule" and saved[0][3] is None


def test_same_facts_hash_is_skipped(settings, monkeypatch, saved):
    settings.CLOVA_API_KEY = ""
    digest = brief.facts_hash(brief.collect_facts(jumunjin_raw()))
    monkeypatch.setattr(sources, "load_brief_hashes", lambda: {116: digest})
    assert "건너뜀 1" in run()
    assert saved == []
    run("--force")
    assert len(saved) == 1


def test_neo4j_down_fails(monkeypatch, saved):
    def down(code):
        raise GraphUnavailable("refused")

    monkeypatch.setattr(queries, "fetch_dong", down)
    with pytest.raises(CommandError, match="sync_graph"):
        run()


def test_chat_uses_20s_timeout(monkeypatch):
    seen = {}

    def fake_post(path, payload, timeout):
        seen.update(timeout=timeout, temperature=payload["temperature"])
        return {"message": {"content": "{}"}}

    monkeypatch.setattr(clova, "_post", fake_post)
    build_briefs.chat("s", "u")
    assert seen == {"timeout": 20, "temperature": 0.3}
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_briefs_command.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_briefs'`

- [ ] **Step 3: `core/clova.py` 의 `chat` 을 다음으로 바꾼다**

```python
def chat(
    system: str,
    user: str,
    max_tokens: int = 300,
    temperature: float = 0.0,
    timeout: float = 15,
) -> str:
    result = _post(
        f"/v3/chat-completions/{CHAT_MODEL}",
        {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "maxTokens": max_tokens,
            "temperature": temperature,
            "topP": 0.8,
        },
        timeout=timeout,
    )
    return result["message"]["content"]
```

- [ ] **Step 4: `explore/management/commands/build_briefs.py`**

```python
"""동네 브리핑 3문장 생성 (월 배치 ④). sync_graph 다음에 실행한다.

uv run python manage.py build_briefs [--force]
CLOVA_API_KEY 가 없으면 규칙 문장만 만든다(로컬 개발).
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from core import clova
from explore import brief, queries, sources
from explore.graphdb import GraphUnavailable

CHAT_TIMEOUT = 20  # 초 (스펙 §5-4)
RULE_WARN = 0.2


def chat(system: str, user: str) -> str:
    return clova.chat(system, user, max_tokens=400, temperature=0.3, timeout=CHAT_TIMEOUT)


class Command(BaseCommand):
    help = "Neo4j 사실 → HyperCLOVA 3문장 → 코드 검증 → dong_brief (사실이 같으면 건너뜀)"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="사실이 같아도 다시 만든다")

    def handle(self, *args, **options):
        use_chat = chat if clova.enabled() else None
        if use_chat is None:
            self.stdout.write(self.style.WARNING("CLOVA_API_KEY 없음: 규칙 문장만 만든다"))
        saved = sources.load_brief_hashes()
        counts: Counter[str] = Counter()
        for dong_id, code in sources.load_brief_targets():
            try:
                raw = queries.fetch_dong(code)
            except GraphUnavailable as ex:
                raise CommandError(f"Neo4j 조회 실패 (sync_graph 먼저): {ex}") from ex
            if raw is None:
                self.stdout.write(self.style.WARNING(f"그래프에 없는 동 {code} 건너뜀"))
                continue
            facts = brief.collect_facts(raw)
            digest = brief.facts_hash(facts)
            if saved.get(dong_id) == digest and not options["force"]:
                counts["skip"] += 1
                continue
            result = brief.generate(raw, facts, use_chat)
            sources.save_brief(dong_id, result.sentences, result.source, result.model, digest)
            counts[result.source] += 1
        made = counts["llm"] + counts["mixed"] + counts["rule"]
        self.stdout.write(
            f"llm {counts['llm']} · mixed {counts['mixed']} · rule {counts['rule']}"
            f" · 건너뜀 {counts['skip']}"
        )
        if made and counts["rule"] / made > RULE_WARN:
            self.stdout.write(
                self.style.WARNING(f"규칙 문장만 쓴 동이 {counts['rule']}/{made} (20% 초과)")
            )
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_explore_briefs_command.py -v && uv run pytest -q`
Expected: 5 passed, 이어서 전체 PASS (`chat` 의 기존 호출은 기본값 15초 그대로)

- [ ] **Step 6: Commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add core/clova.py explore/management/commands/build_briefs.py tests/test_explore_briefs_command.py
git commit -m "feat: manage.py build_briefs — HyperCLOVA 3문장 · 검증 · 사실 해시로 건너뛰기

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 한산한 달 버그 · 문서 · Neo4j 통합 테스트

**Files:**
- Modify: `db/05_dong_doc.sql:27-28`, `db/README.md`, `docs/개발-가이드.md`, `pyproject.toml` (pytest marker)
- Create: `tests/integration/__init__.py` (빈 파일), `tests/integration/test_neo4j_sync.py`

**Interfaces:**
- Consumes: Task 4 `build_graph`, `expected_counts`; Task 5 `get_driver`, `ensure_constraints`, `write_graph`, `count_graph`, `compare_counts`; Task 6 `fetch_pins`, `fetch_dong`, `shape_dong`; `tests.explore_samples.sample_graph_input`
- Produces: pytest marker `neo4j`. `uv run pytest` 기본 실행에서는 4개 skip.

- [ ] **Step 1: `db/05_dong_doc.sql` 의 `ez` CTE (스펙 §10)**

다음 세 줄을

```sql
ez AS (  -- 시군구 시기 여유: 한산한 달 3개, 붐비는 달 3개
  SELECT unit_id AS region_id,
         (SELECT string_agg(m::text, '·' ORDER BY m) FROM unnest((array_agg(month ORDER BY score DESC, month))[1:3]) m) || '월' AS calm_m,
         (SELECT string_agg(m::text, '·' ORDER BY m) FROM unnest((array_agg(month ORDER BY score, month))[1:3]) m) || '월' AS busy_m
```

다음으로 바꾼다 (ease **원값** = 그 달 방문 ÷ 연중 최대 월 × 100, 낮을수록 한산. 점수는 같은 달 12개 시군구 사이 백분위라 시군구 안 계절성이 아니다):

```sql
ez AS (  -- 시군구 월별 방문 원값: 한산한 달 3개(원값 낮은 순), 붐비는 달 3개(높은 순)
  SELECT unit_id AS region_id,
         (SELECT string_agg(m::text, '·' ORDER BY m) FROM unnest((array_agg(month ORDER BY raw_value, month))[1:3]) m) || '월' AS calm_m,
         (SELECT string_agg(m::text, '·' ORDER BY m) FROM unnest((array_agg(month ORDER BY raw_value DESC, month))[1:3]) m) || '월' AS busy_m
```

읽기 전용으로 결과 확인 (Supabase, 쓰기 없음):

```bash
.venv/bin/python - <<'EOF'
import environ, psycopg
env = environ.Env(); environ.Env.read_env(".env")
with psycopg.connect(host=env("DB_HOST"), port=env("DB_PORT"), dbname=env("DB_NAME"),
                     user=env("DB_USER"), password=env("DB_PASSWORD"),
                     sslmode=env("DB_SSLMODE", default="require"),
                     options="-c default_transaction_read_only=on") as c:
    print(c.execute("""
      SELECT r.sigungu_name,
             (SELECT string_agg(m::text, '·' ORDER BY m)
                FROM unnest((array_agg(month ORDER BY raw_value, month))[1:3]) m)
        FROM metric x JOIN region r ON r.region_id = x.unit_id
       WHERE x.scope = 'region' AND x.metric_key = 'ease' AND x.month BETWEEN 1 AND 12
       GROUP BY r.sigungu_name ORDER BY 1""").fetchall())
EOF
```

Expected: `('강릉시', '2·4·9')`, `('전주시', '1·2·8')` 이 들어 있다. (Supabase 에 05_dong_doc.sql 을 다시 실행하는 것은 Task 11.)

- [ ] **Step 2: `db/README.md`**

`## 적재 흐름` 의 코드 블록을 다음으로 바꾼다:

```
LG U+ CSV 2종 ──preprocess.py──▶ parquet 4개 ──load_db.py──▶ region, dong,
   (workation-data)                                            population_hourly, population_demographic,
                                                               dong_month_summary, dong_year_summary
                                                                       │
                                                        03_metrics.sql ──▶ metric_def, metric, dong_hourly
                                                                       │
                                     ② manage.py build_explore ──▶ dong_type_score, dong_hour_stat, dong_similar
                                     ③ manage.py sync_graph    ──▶ Neo4j 탐색 그래프 (읽기 전용 복사본)
                                     ④ manage.py build_briefs  ──▶ dong_brief (HyperCLOVA, 코드 검증)
```

그 아래 목록 끝에 추가:

```markdown
- 월 지표 적재(03_metrics.sql) 뒤 ②~④ 를 차례로 실행한다 (탐색 v6, `docs/superpowers/specs/2026-11-13-explore-graph-v6-design.md` §5). ②는 세 표를 한 트랜잭션에서 다시 만들고, ③은 `NEO4J_URI` 가 필요하며 개수가 Postgres 와 다르면 종료 코드 1, ④는 `CLOVA_API_KEY` 가 없으면 규칙 문장만 만든다.
```

`### 적용 이력` 표 끝에 두 줄 추가:

```markdown
| `migrations/20261113_explore-v6.sql` | 탐색 v6 파생 표 4개 (`dong_type_score` · `dong_hour_stat` · `dong_similar` · `dong_brief`) + RLS | 미적용 |
| `05_dong_doc.sql` 한산한 달 원값 기준 (2026-11-13) | `calm_m` · `busy_m` 을 ease 점수 → 원값 순으로. 문장이 바뀐 동만 임베딩을 다시 만든다 | 미적용 |
```

- [ ] **Step 3: `docs/개발-가이드.md`**

`## 커밋 전 검사` 코드 블록의 `docker compose up -d --wait` 를 `docker compose up -d --wait test-db` 로 바꾼다.

`## 구조` 표의 `search/` 줄 다음에 추가:

```markdown
| `explore/` | 탐색 v6: 파생값 `derive.py` · SQL `sources.py` · 그래프 `graph.py` · Neo4j 쓰기 `graph_write.py`(명령 전용) / 읽기 `graphdb.py` · 조회 `queries.py` · 브리핑 `brief.py` · 명령 `build_explore` · `sync_graph` · `build_briefs` |
```

`## 모델 규칙` 바로 위에 절을 추가:

````markdown
## 탐색 v6 (Neo4j)

| API | 화면 | 구현 |
| --- | --- | --- |
| `GET /api/explore/sigungu/{id}/pins` | W-09 ① 시군구 핀 · 지식그래프 | `explore/queries.py` `PINS` |
| `GET /api/explore/dongs/{id}` | W-09 ② 동 패널 | `explore/queries.py` `DONG` · `FACTS` + `dong_brief` |

- Neo4j 는 Postgres 파생 표의 읽기 전용 복사본이다. 서비스 코드는 `graphdb.read`(읽기 트랜잭션)만 쓴다.
- 응답은 1시간 캐시, Neo4j 가 안 되면 마지막 응답, 그것도 없으면 503 `GRAPH_UNAVAILABLE`.
- 로컬: `docker compose up -d --wait neo4j`, `.env` 에 `NEO4J_URI=bolt://localhost:7687` · `NEO4J_PASSWORD=localpassword` 후 `build_explore` → `sync_graph` → `build_briefs`.
- 통합 테스트: `NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest -m neo4j -v` (그 Neo4j 의 그래프를 테스트 그래프로 바꾼다).
````

- [ ] **Step 4: pytest marker — `pyproject.toml` 의 `[tool.pytest.ini_options]` 끝에 추가**

```toml
markers = ["neo4j: 실제 Neo4j 가 필요한 통합 테스트 (NEO4J_TEST_URI 가 있을 때만 실행)"]
```

- [ ] **Step 5: 통합 테스트 `tests/integration/test_neo4j_sync.py`** (빈 `tests/integration/__init__.py` 도 만든다)

```python
"""실제 Neo4j 에 그래프를 쓰고 Cypher 로 읽어 본다. NEO4J_TEST_URI 가 없으면 건너뛴다.

docker compose up -d --wait neo4j
NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest -m neo4j -v

주의: 그 Neo4j 의 그래프를 테스트 그래프로 바꾼다.
개발 그래프가 필요하면 끝난 뒤 sync_graph 를 다시 실행한다.
"""

import dataclasses
import os

import pytest

from explore import graph, graph_write, graphdb, queries
from tests.explore_samples import sample_graph_input

URI = os.environ.get("NEO4J_TEST_URI", "")
pytestmark = [pytest.mark.neo4j, pytest.mark.skipif(not URI, reason="NEO4J_TEST_URI 없음")]


@pytest.fixture
def driver(settings):
    settings.NEO4J_URI = URI
    settings.NEO4J_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
    settings.NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "localpassword")
    d = graphdb.get_driver()
    graph_write.ensure_constraints(d)
    return d


def sync(driver, inp, sync_id):
    g = graph.build_graph(inp)
    graph_write.write_graph(driver, g, sync_id, "2026-11-13T10:00:00+09:00")
    return g


def test_sync_twice_same_counts(driver):
    g = sync(driver, sample_graph_input(), "first")
    first = graph_write.count_graph(driver)
    assert graph_write.compare_counts(graph.expected_counts(g), first) == []
    sync(driver, sample_graph_input(), "second")
    assert graph_write.count_graph(driver) == first


def test_removed_dong_disappears(driver):
    inp = sample_graph_input()
    sync(driver, inp, "first")
    fewer = dataclasses.replace(inp, dongs=[d for d in inp.dongs if d.dong_id != 112])
    g = sync(driver, fewer, "second")
    counts = graph_write.count_graph(driver)
    assert counts["nodes"]["Dong"] == 3
    assert graph_write.compare_counts(graph.expected_counts(g), counts) == []
    assert queries.fetch_dong("5115037000") is None


def test_pins_with_real_cypher(driver):
    sync(driver, sample_graph_input(), "first")
    pins = queries.fetch_pins("51150")
    assert [d["name"] for d in pins["dongs"]] == ["연곡면", "주문진읍"]
    jumunjin = pins["dongs"][1]
    assert jumunjin["tags"] == ["coast", "cafe", "resort", "beach", "park", "market"]
    assert (jumunjin["topType"], jumunjin["topScore"]) == ("장기체류형", 61)
    assert (jumunjin["coastal"], jumunjin["life"]) == (True, "생활권")
    assert jumunjin["grades"] == {"quiet": "B", "evening": "A"}
    assert jumunjin["center"] == {"lat": 37.88818, "lng": 128.75354}
    assert queries.fetch_pins("99999") is None


def test_dong_with_real_cypher(driver):
    sync(driver, sample_graph_input(), "first")
    raw = queries.fetch_dong("5115025000")
    assert (raw["dong_count"], raw["region_count"]) == (4, 3)
    body = queries.shape_dong(raw, [])
    assert body["sido"] == "강원특별자치도" and body["coastKm"] == 0.0
    assert [t["name"] for t in body["types"]] == [
        "장기체류형",
        "로컬살이형",
        "네트워킹형",
        "몰입형",
    ]
    assert body["grades"]["evening"] == {"grade": "A", "score": 62}
    assert body["hourly"]["weekday"]["peakHour"] == 12
    assert body["hourly"]["weekend"]["quietHour"] == 3
    assert body["months"]["quiet"] == [2, 4, 9] and body["months"]["raw"][7] == 100.0
    nohak, yeongok = body["similar"]
    # 노학동: 조용함 B · 1위 장기체류형 · 평일 9시 · 주말 3시 · 바닷가 · 생활권
    assert (nohak["name"], nohak["common"]) == ("노학동", 6)
    assert nohak["commonTop"] == ["조용함 B", "생활권", "바닷가"]  # degree 2 → 3(key 순)
    assert nohak["diff"] == {"quiet": 8, "peer": 13, "evening": -15}
    # 연곡면: 1위 장기체류형 · 평일 9시 · 주말 3시 · 바닷가 · 같은 강릉시
    assert (yeongok["name"], yeongok["common"]) == ("연곡면", 5)
    assert queries.fetch_dong("0000000000") is None
```

기대값 근거(샘플 4동): 노학동과 주문진읍은 조용함 B · 1위 장기체류형 · 평일 9시 · 주말 3시 · 바닷가 · 생활권이 같다(6). degree 는 조용함 B 2, 나머지 3이라 `commonTop` = 조용함 B → (3 중 key 순) `life:local` · `place:coast`.

- [ ] **Step 6: 기본 실행에서는 건너뛰는지 확인**

Run: `uv run pytest tests/integration -v`
Expected: 4 skipped (`NEO4J_TEST_URI 없음`)

- [ ] **Step 7: 실제 Neo4j 로 통합 테스트**

```bash
docker compose up -d --wait neo4j
NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest -m neo4j -v
```

Expected: 4 passed. 실패하면 Cypher 문법 · 속성 이름을 `explore/queries.py` · `graph_write.py` 에서 고치고 단위 테스트를 다시 돌린다.

- [ ] **Step 8: 전체 검사 후 Commit**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest -q
git add db/05_dong_doc.sql db/README.md docs/개발-가이드.md pyproject.toml tests/integration
git commit -m "fix: 소개문 한산한 달을 ease 원값 기준으로 · 탐색 v6 문서와 Neo4j 통합 테스트

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: 운영 반영과 수동 검증 (사람이 실행 — Supabase 쓰기 · CLOVA 호출)

에이전트는 이 Task 를 실행하지 않는다. PR 설명에 체크리스트로 옮긴다.

- [ ] **Step 1: Supabase 에 `db/migrations/20261113_explore-v6.sql` 실행** (SQL Editor). `db/README.md` 적용 이력의 "미적용" 을 날짜로 바꾼다.
- [ ] **Step 2: Supabase 에 `db/05_dong_doc.sql` 다시 실행 → `db/tools/build_embeddings.py doc`** 로 문장이 바뀐 동만 임베딩을 채운다 (스펙 §10. 강릉 등 한산한 달이 바뀐 시군구의 동). 적용 이력 날짜 기입.
- [ ] **Step 3: 로컬 Neo4j + Supabase 로 배치 세 개**

```bash
docker compose up -d --wait neo4j
uv run python manage.py build_explore
uv run python manage.py sync_graph
uv run python manage.py sync_graph   # 두 번째도 같은 개수 (MERGE 멱등)
uv run python manage.py build_briefs
```

Expected: `build_explore` 가 `2025년 · 동 203 → dong_type_score 812 · dong_hour_stat 406 · dong_similar 609`. `sync_graph` 노드: Sido 4 · Region 12 · Dong 203 · Grade 12 · Type 4 · Hour 24 · Month 12 · Place 1 · Tag 2 · PoiTag 12 · Meta 1, 관계: IN 215 · HAS_GRADE 609 · FITS 812 · QUIET_AT 406 · PEAK_AT 406 · NEAR 97 · LIFE 203 · SIMILAR 609 · EASE 144 · HAS_TAG (dong.tags 중 food 제외 합), "개수 일치".

- [ ] **Step 4: PR 체크리스트 (스펙 §9 수동 검증)**
  - [ ] `build_explore` 후 주문진읍(dong_id 116) 유형 장기체류형 61 · 로컬살이형 60 · 네트워킹형 50 · 몰입형 36(이유 `조용함 31 · 오전 한산 44`), 평일 (12시, 9시) · 주말 (14시, 3시). 금암동 네트워킹형 96 · 로컬살이형 53 · 장기체류형 26 · 몰입형 18. `graph_data/examples.md` 와 같음
  - [ ] 비슷한 동: 주문진읍 → 속초시 노학동 · 서귀포시 대정읍 · 서귀포시 영천동, 금암동 → 전주시 중앙동 · 노송동 · 풍남동 (pgvector `dong_feature` 결과와 같은 순서인지 `db/README.md` 의 비슷한 동네 SQL 로 비교)
  - [ ] `GET /api/explore/dongs/5115025000` 의 `similar` 공통 수 6 · 4 · 5, `commonTop` 첫 행 `["조용함 B", "1위 장기체류형", "바닷가"]`, `months.quiet` `[2, 4, 9]`
  - [ ] `GET /api/explore/sigungu/51150/pins` 동 21개, 연곡면 `topType` 장기체류형 61
  - [ ] Neo4j Browser(`http://localhost:7474`)에서 `MATCH (d:Dong {code: '5115025000'})-[r]-(x) RETURN d, r, x` 캡처 (웹 패널 그래프와 비교용)
  - [ ] `build_briefs` 출력 llm / mixed / rule 수. 무작위 10동 문장 읽기(반복 문장, 어색한 조사)
  - [ ] 두 API 응답 크기와 응답 시간 (캐시 없음 · 있음, `curl -s -o /dev/null -w '%{size_download} %{time_total}\n'`)

---

## Self-Review

**1. 스펙 대비:**
- §3 노드·관계 → Task 4 (`build_graph`), §4 파생 표 → Task 1(DDL) · Task 2(계산) · Task 3(쓰기), §4-5 한산한 달 원값 → Task 2 `quiet_months` · Task 3 테스트(점수 기준과 다름 확인)
- §5-1 `build_explore` → Task 3, §5-2 접속·읽기 전용 → Task 5 `graphdb`, §5-3 `sync_graph`(MERGE · sync_id 삭제 · Meta · 제약 · 개수 비교) → Task 5, §5-4 `build_briefs` → Task 8 · 9
- §6-1 · §6-2 · §6-3 · §6-4 → Task 6(조회·모양·공통점) · Task 7(뷰·캐시·503·404·gzip·브리핑 실패 시 `[]`)
- §7 설정 → Task 1, §9 테스트 → 각 Task + Task 10 통합 테스트 + Task 11 체크리스트, §10 버그 → Task 10 Step 1 · Task 11 Step 2
- 남는 것: §8 HNSW 전환은 동이 수천을 넘을 때(`derive.similar_dongs` 한 곳). §11 범위 밖.

**2. 자리표시:** 모든 코드 단계에 전체 코드가 있다. db/README 적용 이력의 "미적용" 은 Task 11 에서 날짜로 바꾸는 실제 상태 값이다.

**3. 이름·타입 일관성:** `TypeScore(type, score, rank, reason)` (Task 2 → 3 · 4 · 테스트), `GraphInput.hours[dong][day] = (driv, peak, quiet)` (Task 3 `write_explore` 와 같은 모양), `Rel.key`(Task 4 → Task 5 `rel_query`), `graphdb.read(work, **params)` (Task 5 → Task 6), raw 모양(Task 6 → 8 · 9), `save_brief(dong_id, sentences, source, model, facts_hash)` (Task 7 → 9).

**검증 기록 (계획 작성 시):** Task 2 · 4 · 5 · 6 · 7(API) · 8 · 9 의 순수·가짜 드라이버 테스트 87개를 scratch 복사본에서 실행해 통과(neo4j 5.28.8), 통합 테스트 4개는 skip. ruff check · format 통과. Docker 가 꺼져 있어 **테스트 DB 테스트(Task 1 `test_schema`, Task 3 · 4 · 7 의 `test_explore_build.py`)와 실제 Neo4j Cypher(Task 10 Step 7)는 실행하지 못했다** — 통합 테스트 기대값은 Cypher 를 파이썬으로 흉내 내 확인했다.
