# 탐색 동네 근거 그래프 v6 (백엔드) 설계

- 작성일: 2026-11-13
- 상태: 승인됨 (브레인스토밍 결과, 2026-11-11~13)
- 대체 문서: [v5 설계](./2026-11-09-explore-graph-design.md) — 태그·장소 그래프, `GET /api/explore/graph` 한 번에 전체. **v6가 우선**한다. 저장 구조(Postgres 원천 + Neo4j 읽기 전용)와 오류 규칙은 v5에서 이어받는다
- 웹 짝 문서: workation-web `docs/superpowers/specs/2026-11-13-W-09-지도탐색-v6-design.md` (브랜치 `docs/W-09-explore-spec`). 응답 형식은 두 문서 §6이 같다
- 결정 기록: `_notes/2026-11-11-explore-graphrag/HANDOFF.md`, 그래프 재료 정의·실측: 같은 폴더 `graph_data/README.md`, `examples.md`
- 브랜치: `docs/explore-graph-spec`

## 1. 목표

웹 `/explore` 오른쪽 패널에 **한 동네와 이어진 사실들**(유형·등급·시간대·한산한 달·바닷가·비슷한 동)과 그 사실만으로 쓴 3문장 브리핑을 내려준다. 사실은 LG U+ 유동인구 지표에서만 만든다.

완료 기준:

- 월 적재 후 명령 세 개(§5)를 차례로 실행하면 Postgres 파생 테이블, Neo4j 그래프, 브리핑이 모두 갱신된다.
- `GET /api/explore/sigungu/{id}/pins`, `GET /api/explore/dongs/{id}`가 §6 형식으로 돌려준다.
- 브리핑의 숫자·등급·달·시각이 모두 사실 목록과 일치한다(코드 검증, §5-4).
- 서비스 실행 중 LLM 호출 0회.
- `uv run pytest`가 Neo4j 없이 통과한다(Neo4j 통합 테스트는 따로, §9).

## 2. 결정 요약

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 저장소 | **Postgres = 원천, Neo4j = 읽기 전용 복사본** (v5와 같음) | 원천은 기존 파이프라인. 그래프는 동 주변 탐색·시각화용 읽기 모델 |
| 판단 기준 | 현재 203동이 아니라 데이터가 늘었을 때 기준 | 사용자 결정. 동·지표·사용자 팁이 늘어도 구조를 바꾸지 않게 |
| 호스팅 | **자체 Neo4j 컨테이너**(`compose.yaml`, Postgres와 함께). AuraDB Free는 쓰지 않음 | Free는 미사용 시 일시정지 → 시연 사고 위험. 접속은 `NEO4J_URI`만 바꾸면 되게 |
| 범주 값 | 노드 (등급·유형·시각·달·바닷가·생활권) | 같은 노드에 이어진 동 = 공통점. 비슷한 동의 이유를 그래프에서 바로 센다 |
| 연속 숫자 | 속성 (24시간 차량 수, 월 원값, 점수) | 노드로 만들면 의미 없는 공통점이 생긴다 |
| 비슷한 동 | 연간 `metric_vec` L2 상위 3, **같은 바닷가 여부 안**에서. 이유는 공통 노드 수 + 드문 순 | 지표 거리로 고르고 공통 노드로 설명한다. 두 점수를 섞지 않는다 |
| 한산한 달 | 시군구 ease **원값**(raw) 하위 3 | ease 점수는 같은 달 12개 시군구 백분위라 "그 시군구 안 한산한 달"이 아니다 |
| 24시간 | `population_hourly.driv_avg`(차량). 평일·주말 | 보행은 업무시간 변화가 작다(중앙값 14%), 차량은 하루 6.3배 |
| 브리핑 | 월 배치에서 HyperCLOVA로 미리 생성 → 코드 검증 → 실패 문장만 규칙 문장 → Postgres 저장 | 서비스 중 LLM 0회, 틀린 숫자 0 |
| API | 시군구 핀 + 동 상세 2개. v5 `GET /api/explore/graph` 삭제 | 데이터가 늘어도 응답 크기가 일정 |
| 시군구 화면 | 핀 API가 동마다 1위 유형·점수·바닷가·생활권/관광지·조용함·저녁 활기 등급을 같이 준다. 웹이 시군구 지식그래프(힘 기반 배치)를 그린다 | 새 API 없이 같은 조회 한 번 |
| 관광공사 | 동 태그(`dong.tags`)만 핀 아이콘용으로 그래프에 넣는다. 장소(`Place`)·`place` 테이블은 v6 범위 밖 | 1차 화면에 장소 핀이 없다. 공공데이터는 보조 |

넣지 않는 것: 사용자 팁·투표 노드(2차, §11), 관광공사 장소 노드, 서비스 중 Neo4j 쓰기, 서비스 중 LLM 호출, 외부 후기 수집.

## 3. 그래프 모델 (Neo4j)

모든 노드는 `id` 속성(유일 제약)을 가진다. 동기화 실행마다 `sync_id`를 붙인다(§5-3).

### 노드

| 라벨 | 수 | id | 속성 |
| --- | --- | --- | --- |
| `Sido` | 4 | `sido:<시군구 코드 앞 2자리>` | name |
| `Region` | 12 | `region:<sigungu_code>` | name |
| `Dong` | 203 | `dong:<adm_code>` | name, lat, lng, coast_km, driv_weekday[24], driv_weekend[24], local_life |
| `Grade` | 12 | `grade:<metric>:<S\|A\|B\|C>` (metric = quiet · peer · evening) | metric, grade, label (`저녁 활기 A`) |
| `Type` | 4 | `type:<immersion\|networking\|local\|long_stay>` | name (`몰입형` …) |
| `Hour` | 24 | `hour:<0~23>` | h |
| `Month` | 12 | `month:<1~12>` | m |
| `Place` | 1 | `place:coast` | kind `바닷가` |
| `Tag` | 2 | `life:local`, `life:tourist` | name (`생활권`, `관광지`) |
| `PoiTag` | 12 | `poi:<태그 코드>` | name, order (v5 §3 태그 순서, `food` 제외) |
| `Meta` | 1 | `meta` | synced_at, data_year |

### 관계

| 관계 | 동마다 | 속성 | 출처 |
| --- | --- | --- | --- |
| `(Region)-[:IN]->(Sido)` | — | | `region.sigungu_code` |
| `(Dong)-[:IN]->(Region)` | 1 | | `dong.region_id` |
| `(Dong)-[:HAS_GRADE]->(Grade)` | 3 | score | `metric`(dong, month 0) |
| `(Dong)-[:FITS]->(Type)` | 4 | score, rank, reason | `dong_type_score` (§4) |
| `(Dong)-[:QUIET_AT {day}]->(Hour)` | 2 | day = weekday · weekend | `dong_hour_stat` (§4) |
| `(Dong)-[:PEAK_AT {day}]->(Hour)` | 2 | day | 같음 |
| `(Dong)-[:NEAR]->(Place)` | 0~1 | km | `dong.coast_dist_km ≤ 1.0` |
| `(Dong)-[:LIFE]->(Tag)` | 1 | value | `local_life ≥ 0.7` → 생활권, 아니면 관광지 |
| `(Dong)-[:HAS_TAG]->(PoiTag)` | 0~n | | `dong.tags` (`food` 제외) |
| `(Dong)-[:SIMILAR]->(Dong)` | 3 | rank, l2 | `dong_similar` (§4) |
| `(Region)-[:EASE]->(Month)` | 시군구마다 12 | raw, quiet (bool, 하위 3) | `metric`(region, ease, month 1~12) raw |

- 브레인스토밍 중 그렸던 `QUIET_WINDOW → HourWindow`(2시간 창)는 `QUIET_AT → Hour`(한 시각)로 바꿨다. 화면이 "평일 9시"처럼 한 시각을 보여주기 때문이다.
- `FITS`는 4개 모두 넣고 `rank`를 둔다. 공통점 계산에는 `rank = 1`만 쓴다(§6-3). 4개를 다 세면 모든 동이 공통이 된다.

## 4. 파생값 (Postgres, 원천)

원천은 Postgres에 둔다. Neo4j는 이 표를 복사만 한다. migration `db/migrations/20261113_explore-v6.sql`.

### 4-1. `dong_type_score`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `dong_id` | `SMALLINT FK → dong` | PK 1 |
| `type` | `VARCHAR(12)` | `immersion` · `networking` · `local` · `long_stay`, PK 2 |
| `score` | `SMALLINT` | 0~100 |
| `rank` | `SMALLINT` | 그 동 안 순위 1~4 |
| `reason` | `VARCHAR(80)` | 화면 이유 문구 |

점수식(`graph_data/README.md` F4, 확정):

| 유형 | 점수 | 이유 문구 |
| --- | --- | --- |
| 몰입형 | 0.6·quiet + 0.4·(100 − 오전 블록 백분위) | `조용함 {quiet} · 오전 한산 {100−백분위}` |
| 네트워킹형 | 0.5·peer + 0.5·evening | `또래 밀도 {peer} · 저녁 활기 {evening}` |
| 로컬살이형 | local_life 백분위 | `생활권 지수 {local_life 소수 둘째 자리}` |
| 장기체류형 | 0.5·overnight + 0.5·(한산한 달 수 ÷ 12 × 100), 한산한 달 = 시군구 ease raw ≤ 80 | `숙박 성향 {overnight} · 한산한 달 {n}개(성수기보다 20% 이상 적은 달)` |

- 오전 블록 = `dong_feature.curve_vec` 평일 9~11시. 백분위는 203동 기준.
- 동점이면 표의 순서(몰입형 → 장기체류형)로 순위를 매긴다.

### 4-2. `dong_hour_stat`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `dong_id` | `SMALLINT FK` | PK 1 |
| `day_group` | `VARCHAR(7)` | `weekday` · `weekend`, PK 2 |
| `driv` | `REAL[24]` | 0~23시 차량 이동 인구, 데이터 연도의 월 평균 |
| `peak_hour` | `SMALLINT` | 하루 전체 최대 |
| `quiet_hour` | `SMALLINT` | 평일 = 9~17시 중 최소, 주말 = 하루 전체 중 최소 |

- 계산: `population_hourly`(year = 데이터 연도)에서 `dong_id, day_group, hour`별 `avg(driv_avg)`. 같은 값이면 이른 시각.

### 4-3. `dong_similar`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `dong_id` | `SMALLINT FK` | PK 1 |
| `rank` | `SMALLINT` | 1~3, PK 2 |
| `other_id` | `SMALLINT FK` | 비슷한 동 |
| `l2` | `REAL` | 연간 `metric_vec` 거리 |

- 후보: 집계 제외가 아닌 동 중 **바닷가 여부가 같은 동**(바닷가 = `coast_dist_km ≤ 1.0`, NULL은 바닷가 아님). 자기 자신 제외. 다른 시군구 포함.
- 지표가 백분위라 동 하나만 바뀌어도 전체 점수가 바뀐다 → 월 배치마다 **전체 다시 계산**.
- 지금은 pgvector `<->` 전수 비교 한 쿼리. 동 수가 수천을 넘으면 `dong_feature.metric_vec`에 HNSW 인덱스를 두고 바닷가 여부로 거른 뒤 상위 k를 뽑는다. 계산은 `explore/derive.py`의 함수 하나에 모아 바꿀 때 한 곳만 고친다.

### 4-4. `dong_brief`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `dong_id` | `SMALLINT PK FK` | |
| `sentences` | `JSONB` | 문자열 3개 |
| `source` | `VARCHAR(5)` | `llm`(3개 모두 생성 통과) · `mixed`(일부 규칙 문장) · `rule`(전부 규칙 문장) |
| `model` | `VARCHAR(20) NULL` | `HCX-005` 등 |
| `facts_hash` | `CHAR(64)` | 사실 목록 SHA-256. 같으면 다시 만들지 않는다 |
| `generated_at` | `TIMESTAMPTZ` | |

모든 표: RLS ENABLE, 정책 없음(다른 표와 같음).

### 4-5. 한산한 달 (Neo4j `EASE.quiet`, API `months.quiet`)

- 시군구별 `metric`(ease, month 1~12)의 `raw_value` 낮은 순 3개. 같은 값이면 이른 달. 응답에서는 달 순으로 정렬.
- 주문진읍(강릉) 기대값: 2·4·9월(raw 70), 가장 붐비는 달 8월(100).

## 5. 월 배치 흐름

```
① 지표 적재 (기존 파이프라인, db/README.md)
② manage.py build_explore      → dong_type_score · dong_hour_stat · dong_similar (Postgres)
③ manage.py sync_graph         → Neo4j (MERGE)
④ manage.py build_briefs       → dong_brief (Postgres)
```

`db/README.md` 적재 흐름 끝에 ②~④를 추가한다. 코드는 새 앱 `explore/`(`derive.py`, `graph.py`, `brief.py`, `views.py`, `management/commands/`).

### 5-1. `build_explore`

- 세 표를 한 트랜잭션에서 지우고 다시 넣는다(동 수 × 소수 행, 원천이 바뀌면 전부 바뀜).
- 계산은 SQL 또는 순수 함수(`derive.py`)로 하고 테스트한다.

### 5-2. Neo4j 접속

- `.env`: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`. `compose.yaml`에 `neo4j:5` 커뮤니티 컨테이너(볼륨, 7687 포트는 내부망만).
- 커뮤니티판은 역할별 권한(읽기 전용 계정)이 없다. 그래서 **서비스 코드는 읽기 트랜잭션(`session.execute_read`)만 쓰고**, 쓰기 함수는 관리 명령에만 둔다. 외부에 포트를 열지 않는다. 권한 분리가 필요해지면 Enterprise/유료 Aura로 옮긴다.

### 5-3. `sync_graph` (v5 명령을 바꿈)

- Postgres 읽기 → 순수 함수 `build_graph(rows)`로 노드·관계 목록 → Neo4j 쓰기.
- 쓰기는 한 트랜잭션:
  1. 노드·관계를 `MERGE`하고 속성을 덮어쓴다. 모두 이번 `sync_id`를 받는다.
  2. 이번 `sync_id`가 아닌 노드·관계를 지운다(사라진 동, 바뀐 비슷한 동 등).
  3. `Meta.synced_at` 갱신.
  - 실패하면 트랜잭션이 되돌아가 이전 그래프가 남는다.
- 처음 한 번 `id` 유일 제약을 만든다(`CREATE CONSTRAINT … IF NOT EXISTS`).
- 끝나면 Postgres 기준 기대 개수(동, 라벨별 노드, 관계 유형별)와 Neo4j 개수를 비교한다. 다르면 0이 아닌 종료 코드와 차이를 출력한다.

### 5-4. `build_briefs`

동마다:

1. **사실 모으기** (Neo4j local search): 동 주변 1단계 + 시군구 → 달. 사실마다 내부 id(F1~F6 분류, 화면에 안 나감), 문장용 값, **흔함**(같은 사실을 가진 동 비율)을 붙인다.
   - 쓰는 사실: 등급·점수·전국 순위·시군구 안 순위(F1), 평일·주말 최대·최소 시각과 차량 수(F2), 시군구 한산한 달·붐비는 달(F3), 유형 점수·순위(F4), 바닷가(F5), 비슷한 동 이름(F6).
   - 흔함 30% 넘는 사실(예: 평일 9시 한산 108동)은 뒤로 미루고, 흔함이 낮은 사실(예: 저녁 활기 강릉 1위)을 앞에 둔다.
2. `facts_hash`가 저장된 것과 같으면 건너뛴다.
3. **생성**: `core/clova.py` 채팅(HCX-005, temperature 0.3). 프롬프트는 사실 목록만 주고 다음을 지시한다.
   - 3문장, 문장당 60자 이내, 존댓말 해요체
   - 1문장 하루 흐름(시간대), 2문장 1년 흐름(한산한 달), 3문장 이 동의 강점(유형·등급, 흔함 낮은 사실 하나 이상)
   - 목록에 없는 숫자·달·시각·등급·장소·추측 금지
   - 출력 JSON `{"sentences": [{"text": "...", "facts": ["F2-1", ...]}]}`
4. **검증** (`brief.py`, 순수 함수): 문장마다
   - 숫자(정수·소수·`n시`·`n월`·`n점`·`n대`·`n위`)와 등급 글자(S/A/B/C)를 뽑아, 그 문장이 인용한 사실 값에 모두 있어야 한다.
   - 인용한 사실 id가 목록에 있어야 한다. 길이 60자 이내.
   - 금지어(`추천`, `최고`, `가장 좋`, 외부 장소명 등 목록)가 없어야 한다.
5. 실패한 문장만 같은 자리의 **규칙 문장**으로 바꾼다.
   - 1: `평일 차량은 {peak}시에 가장 많고, 업무시간엔 {quiet}시가 가장 한산해요.`
   - 2: `{시군구}는 {m1}·{m2}·{m3}월이 가장 한산하고 {busiest}월이 가장 붐벼요.`
   - 3: `{1위 유형} {점수}점으로, {가장 높은 등급 지표} {등급}({점수}점)이에요.` 흔함 낮은 사실이 있으면 그것을 이어 붙인다.
6. 호출 실패·시간 초과(20초)는 3번 다시 시도(분당 60회 한도는 `core/clova.py` 처리 그대로). 그래도 실패하면 세 문장 모두 규칙 문장, `source = rule`.
7. `dong_brief` upsert.
- 끝나면 `llm / mixed / rule` 동 수를 출력한다. `rule`이 20%를 넘으면 경고.
- `CLOVA_API_KEY`가 없으면 전부 규칙 문장으로 만든다(로컬 개발).

## 6. API

`explore/urls.py`를 `/api/explore/`에 연결한다. v5 `GET /api/explore/graph`는 만들지 않는다(v5 계획 미구현분 폐기).

### 6-1. `GET /api/explore/sigungu/{sigunguId}/pins`

```json
{
  "sigunguId": "51150",
  "dongs": [
    {
      "id": "5115025000", "name": "주문진읍", "center": {"lat": 37.89, "lng": 128.82}, "tags": ["beach", "cafe"],
      "topType": "장기체류형", "topScore": 61, "coastal": true,
      "life": "생활권", "grades": {"quiet": "B", "evening": "A"}
    }
  ]
}
```

- Cypher 1개: `Region` ← `Dong` → `PoiTag`(order 순), `Dong-[:FITS {rank: 1}]->Type`, `Dong-[:NEAR]->Place` 여부, `LIFE`, `HAS_GRADE`(quiet·evening). `tags`는 `food` 제외, 없으면 `[]`.
- `topType`·`topScore`: 1위 유형(`FITS rank 1`). `coastal`: `NEAR` 관계가 있으면 true. `life`: `LIFE` 관계의 `Tag` 이름. `grades`: `HAS_GRADE`의 quiet·evening 등급.
- 웹 ① 시군구 지식그래프가 이 값들로 동과 사실 노드를 그린다(시군구의 동 이웃 1단계를 펼친 것과 같다). 공통 사실 계산도 웹에서 한다.
- 404 `SIGUNGU_NOT_FOUND`.

### 6-2. `GET /api/explore/dongs/{dongId}`

웹 짝 문서 §6-2와 같은 형식(주문진읍 예시는 그 문서 참고). 필드별 출처:

| 필드 | 출처 |
| --- | --- |
| id, name, coastKm, sigungu, sido | `Dong` · `Region` · `Sido` |
| types[] | `FITS` 4개, score 내림차순(rank 순) — name, score, reason |
| grades | `HAS_GRADE` → `{grade, score}`, 없으면 그 지표 null |
| hourly.weekday/weekend | `Dong.driv_*` + `QUIET_AT` · `PEAK_AT` (day별) |
| months | `Region-[:EASE]->Month`: raw[12](없으면 null), quiet = `EASE.quiet` 달 순, basis = 시군구 이름 |
| briefing | Postgres `dong_brief.sentences`, 행이 없으면 `[]` |
| similar[] | `SIMILAR` rank 순 + §6-3 |
| syncedAt | `Meta.synced_at` |

- Neo4j 읽기 트랜잭션 하나에서 Cypher 3개(동·이웃, 비슷한 동·공통점, 시군구 달). Postgres 쿼리 1개.
- 404 `DONG_NOT_FOUND`.

### 6-3. 비슷한 동의 공통점

공통점이 되는 노드: `Grade`, `Type`(`FITS.rank = 1`만), `Hour`(`QUIET_AT`, day까지 같아야), `Place`(바닷가), `Tag`(생활권/관광지), `Region`(같은 시군구).

- `common` = 두 동이 함께 이어진 위 노드 수.
- `commonTop` = 그중 **이어진 동 수가 적은 노드부터** 최대 3개의 문구. 문구: `저녁 활기 A`, `1위 장기체류형`, `평일 9시 한산`, `바닷가`, `생활권`, `같은 강릉시`.
- `diff` = 상대 점수 − 이 동 점수(quiet · peer · evening), 한쪽이 없으면 그 값 null.
- 노드 연결 수는 `sync_graph`가 `degree` 속성으로 저장해 두고 그 값으로 정렬한다(조회 때 다시 세지 않음).

### 6-4. 캐시와 오류 (v5 규칙 이어받음)

- 응답마다 Django 캐시 **1시간**(키: `explore:pins:{id}`, `explore:dong:{id}`), 마지막 성공 응답을 만료 없이 따로 보관.
- `GZipMiddleware` 압축.

| 상황 | 동작 |
| --- | --- |
| 캐시 있음 | Neo4j를 부르지 않고 200 |
| Neo4j 연결 실패 / `Meta` 없음 / `NEO4J_URI` 미설정, 마지막 응답 있음 | 마지막 응답 200 |
| 위와 같고 마지막 응답 없음 | 503 `GRAPH_UNAVAILABLE` "탐색 데이터를 불러오지 못했어요." (`core/errors.error`) |
| 없는 id | 404 (`SIGUNGU_NOT_FOUND`, `DONG_NOT_FOUND`) — 캐시하지 않음 |
| `dong_brief` 조회 실패 | `briefing: []`로 200 (나머지는 정상) |

## 7. 설정

- `pyproject.toml`: `neo4j` 추가(v5와 같음).
- `.env.example`: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` (값 없이). `CLOVA_API_KEY`는 이미 있다.
- `compose.yaml`: `neo4j` 서비스 추가(`neo4j:5` 커뮤니티, 데이터 볼륨, 7474·7687 포트는 로컬에서만, 메모리 설정). 배포 세팅 때 앱과 같은 네트워크에 둔다.
- `settings.INSTALLED_APPS`에 `explore`.

## 8. 규모가 커질 때

| 늘어나는 것 | 지금 구조 | 바꿀 곳 |
| --- | --- | --- |
| 동 수 (전국 3,500동대) | 핀은 시군구 단위, 동 상세는 동 단위라 응답 크기 그대로 | `dong_similar` 계산을 HNSW로(§4-3). `sync_graph`는 배치 단위 UNWIND |
| 월별 데이터 | 연간(month 0) 기준 | `Month` 노드·`EASE` 관계가 이미 있어 동 월별 관계를 더하면 됨 |
| 사용자 팁(2차) | 없음 | `(:User)-[:WROTE]->(:Tip)-[:ABOUT]->(:Dong)` 추가, 브리핑 사실에 팁 요약 추가 |
| 브리핑 비용 | `facts_hash`가 바뀐 동만 다시 생성 | 그대로 |

## 9. 테스트

- `tests/test_explore_derive.py` (테스트 DB)
  - 유형 점수·순위·이유 문구. 주문진읍 실측을 픽스처로: 장기체류형 61, 로컬살이형 60, 네트워킹형 50, 몰입형 36
  - 한산한 달 = 원값 하위 3(강릉 2·4·9월), 점수 기준이 아님을 확인(점수 기준이면 4·10월)
  - `quiet_hour`: 평일은 9~17시 안, 주말은 하루 전체. 같은 값이면 이른 시각
  - 비슷한 동: 바닷가 동끼리만, 3개, 자기 자신 없음
- `tests/test_explore_graph.py` (Neo4j 없이)
  - `build_graph`: 라벨별 개수, `FITS` 4개와 rank, 바닷가 동만 `NEAR`, `food` 태그 제외, `degree` 계산
  - 가짜 드라이버로 `write_graph` 쿼리 순서(MERGE → 오래된 것 삭제 → Meta), 개수 비교 실패 시 종료 코드
  - 공통점: rank 1 유형만 셈, 드문 순 정렬, 문구
- `tests/test_explore_brief.py`
  - 검증기: 사실에 없는 숫자·달·등급 문장 실패("강릉 4·10월 한산" → 실패), 맞는 문장 통과, 60자 초과 실패
  - 규칙 문장 대체, `source` 값(llm/mixed/rule), `facts_hash` 같으면 건너뜀
  - `core/clova.py`를 가짜로 바꿔 호출 실패·잘못된 JSON 처리
- `tests/test_explore_api.py`
  - 두 API 응답 형식(§6), 핀의 `topType`·`topScore`·`coastal`, 404, 캐시 적중 시 재조회 없음, Neo4j 실패 시 마지막 응답, 없으면 503, 브리핑 행 없음 → `[]`
- `tests/integration/test_neo4j_sync.py` — `@pytest.mark.neo4j`, `NEO4J_TEST_URI`가 있을 때만 실행. compose의 neo4j 컨테이너에 `sync_graph`를 두 번 돌려 개수가 같은지(MERGE 멱등), 동 하나를 지우고 다시 돌리면 사라지는지 확인. 기본 CI에서는 건너뛴다.

### 수동 검증 (PR 체크리스트)

- [ ] `build_explore` 후 주문진읍·금암동 값이 `graph_data/examples.md`와 같음
- [ ] `sync_graph` 출력의 라벨별 노드 수·관계 수, 두 번 실행해도 같음
- [ ] Neo4j Browser에서 주문진읍 주변 1단계 그림 캡처(웹 패널 그래프와 비교용)
- [ ] `build_briefs` 출력 llm / mixed / rule 수, 무작위 10동 문장 읽어보기(반복 문장 여부)
- [ ] 두 API 응답 크기와 응답 시간(캐시 없음·있음)

## 10. 같이 고칠 버그

- `db/05_dong_doc.sql:27`의 한산한 달(`calm_m`)이 ease **점수** 기준으로 뽑힌다. 원값 기준으로 바꾼다(`regions/views.py:139`의 추천 달은 원값 기준이라 맞음). `dong_doc` 임베딩을 다시 만들어야 하는지 확인한다.

## 11. 범위 밖 (2차)

- 팁·체감 투표 노드와 쓰기 API, 브리핑에 팁 요약 붙이기(근거 팁 번호는 붙이지 않음)
- 사실 노드로 같은 사실을 가진 동 찾기 API(노드 클릭 지도 검색)
- 자연어 검색(F-09)에서 그래프 이웃 활용
- 관광공사 장소 노드·`place` 테이블(v5 Task 1~2)
- 동 월별 지표 관계
