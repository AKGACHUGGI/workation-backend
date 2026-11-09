# 탐색 지식그래프 (백엔드) 설계

- 작성일: 2026-11-09 · 개정: 2026-11-09 (응답 형식을 웹 W-09 §6 에 맞춤)
- 상태: 승인됨
- 관련
  - 웹 짝 문서: workation-web `docs/superpowers/specs/2026-11-09-W-09-지도탐색-design.md` (브랜치 `docs/W-09-explore-spec`, 커밋 9d5bf85). **응답 형식은 웹 §6 이 기준이고 백엔드가 맞춘다** (사용자 결정)
  - `2026-11-07-W-04-동네프로필-design.md` (행정동 경계 정적 GeoJSON), `db/README.md`
- 브랜치: `docs/explore-graph-spec`

## 1. 목표

웹 `/explore`(W-09 지도 탐색)에서 태그를 고르고 지도를 움직이며 동네·태그·장소의 연결을 탐색할 수 있도록, 백엔드가 지식그래프를 만들어 한 번에 내려준다.

완료 기준:

- `place` 테이블에 관광공사 장소가 저장되고, 동 태그(`dong.tags`)는 이 테이블에서 계산된다.
- `uv run python manage.py sync_graph` 를 실행하면 Neo4j 에 노드·관계가 채워진다 (전체 삭제 후 재적재).
- `GET /api/explore/graph` 가 §5 형식(웹 W-09 §6 과 같음)으로 돌려준다. 압축 응답 100KB 이하(목표, 실측).
- `uv run pytest` 가 Neo4j 없이 통과한다.

## 2. 결정 요약

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 저장소 | **Postgres = 원본, Neo4j(AuraDB Free) = 읽기 전용 복사본** | 탐색용 그래프 질의·시연. 원본은 기존 파이프라인 유지 |
| 쓰기 | 서비스는 Neo4j 에 쓰지 않는다 | 두 DB 간 트랜잭션·동기화 문제 회피 |
| 동기화 | 적재 후 `manage.py sync_graph` 수동 실행, 전체 삭제 후 재적재 | 데이터는 배치로만 바뀌고 규모가 작다(수 초) |
| 전달 방식 | 진입 시 그래프 전체 1회 전달, 이후 상호작용은 브라우저에서 | 노드 약 1,100개라 작다. 태그 클릭 등 즉시 반응 |
| 응답 형식 | 웹 W-09 §6 구조(`tags · provinces · sigungu · dongs · places`). Neo4j 의 노드·관계를 API 에서 이 형식으로 펼친다 | 웹이 형식을 정하고 백엔드가 맞춘다 |
| 태그 | 12개 (`food` 제외). 묶음 `trait`(coast · city) · `work` · `stay` · `rest` · `life`(market). `display`: coast · city · mountain = `badge`, 나머지 `branch` | 웹 W-09 §3-0 |
| 장소 범위 | `branch` 태그의 근거 장소만. **음식점·산·숲 근거 장소는 Neo4j 에서 제외**(Postgres 에는 저장) | 음식점은 워케이션 판단과 거리가 멀고, 배지 태그는 가지·핀을 그리지 않는다 |
| 집계 제외 동 | 응답에서 뺀다 | 유일한 제외 동(고성군 수동면)은 경계·`adm_code`·중심점이 없어 id·center·bbox 를 만들 수 없다. 형식의 `excluded` 필드는 남겨 두고 경계 있는 제외 동이 생기면 넣는다 |
| 이미지 | `http://` 를 `https://` 로 바꿔 내려준다. 그 밖은 null | 2026-11-09 확인: `tong.visitkorea.or.kr` 는 https 로 200, http 는 https 로 301 |
| 지표 | 노드가 아니라 동 노드의 속성(연간 등급) | 월별 관계 약 8천 개를 늘릴 만큼 탐색에 기여하지 않음 |
| Neo4j 장애 | 1시간 캐시 → 마지막 그래프 → 없으면 503. Postgres 대체 경로는 만들지 않음 | Aura Free 72시간 일시정지는 시연 전 재개로 대응 |

넣지 않는 것: 표현(`tag_vocab`) 노드, 인접 동 관계(지도에서 보임), 월별 지표, 화면 범위별 조회 API, 서비스 실행 중 TourAPI 호출, 해안선 좌표.

## 3. 그래프 모델 (Neo4j)

### 노드

모든 노드는 `:Node` 라벨과 `id`, `kind` 속성을 함께 가진다. Neo4j 속성에는 맵을 넣을 수 없어 동 등급은 `grade_quiet` 처럼 펼쳐 저장한다. null 속성은 저장되지 않는다.

| 라벨 | 수 | id | 속성 |
| --- | --- | --- | --- |
| `Sido` | 4 | `sido:<시군구 코드 앞 2자리>` (강원 51 · 전북 52 · 전남 46 · 제주 50) | name, lat, lng (소속 동 경계 합집합의 내부 점) |
| `Region` | 12 | `region:<sigungu_code>` | name, lat, lng |
| `Dong` | 203 (`is_excluded`·`adm_code` 없는 동 제외) | `dong:<adm_code>` | name, lat, lng (`ST_PointOnSurface`), bbox `[w, s, e, n]`, coast_km, grade_quiet · grade_peer · grade_evening (연간 S/A/B/C) |
| `Tag` | 12 | `tag:<코드>` | name (`regions/views.py` `TAG_LABELS`), group, display, order |
| `Place` | 약 870 (2026-11-09 조회: 음식점 제외 1,057곳 − 산·숲 근거 186곳, 동 경계 밖 제외 전) | `place:<contentid>` | name, lat, lng, tag, image (https 또는 없음) |

태그 순서(웹 태그 바 순서)와 묶음:

| 순서 | 태그 | group | display |
| --- | --- | --- | --- |
| 1 · 2 | coast · city | trait | badge |
| 3 · 4 | cafe · library | work | branch |
| 5 · 6 | long_stay · resort | stay | branch |
| 7 · 8 · 9 · 10 · 11 | beach · park · mountain · island · surf | rest | branch (mountain 만 badge) |
| 12 | market | life | branch |

### 관계

| 관계 | 수(추정) | 출처 |
| --- | --- | --- |
| `(Region)-[:IN]->(Sido)` | 12 | `region.sigungu_code` 앞 2자리 |
| `(Dong)-[:IN]->(Region)` | 203 | `dong.region_id` |
| `(Dong)-[:HAS_TAG]->(Tag)` | 약 520 | `dong.tags` (food 제외) |
| `(Place)-[:LOCATED_IN]->(Dong)` | 약 870 | `place.dong_id` |
| `(Place)-[:EVIDENCE_OF]->(Tag)` | 약 870 | `place.tag` |
| `(Dong)-[:SIMILAR {rank, dist}]->(Dong)` | 약 1,000 | `dong_feature` 연간 `metric_vec` 거리 상위 5. `similar_dongs` 와 같은 거리식, 한 번의 쿼리. 집계 제외 동은 후보에서 뺀다 |

## 4. 데이터 준비

```
① TourAPI ──(build_poi_tags_sql.py, 로컬)──▶ Postgres place + dong.tags · region.tags
② Postgres ──(manage.py sync_graph, 로컬)──▶ Neo4j
```

### 4-1. `place` 테이블 (migration `db/migrations/20261109_place.sql`)

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `place_id` | `BIGINT PK` | TourAPI `contentid` |
| `name` | `VARCHAR(200)` | 장소명 |
| `tag` | `VARCHAR(20)` | 태그 코드. 장소의 분류 코드가 하나라 태그도 하나다(`tag_of` 기준. `mountain_hint` 는 `mountain` 으로 저장) |
| `content_type` | `VARCHAR(4)` | TourAPI `contenttypeid` |
| `category` | `VARCHAR(10)` | TourAPI 신분류 3단계 `lclsSystm3` |
| `lng`, `lat` | `DOUBLE PRECISION` | 좌표 |
| `dong_id` | `SMALLINT FK → dong` | `ST_Contains(dong.geom, 좌표)`. 경계 밖이면 저장하지 않음 |
| `image_url` | `TEXT NULL` | `firstimage` 원본 (http). https 변환은 그래프 동기화 때 |
| `fetched_at` | `TIMESTAMPTZ` | 수집 시각 |

RLS ENABLE, 정책 없음 (다른 테이블과 같음). 음식점·산·숲 근거 장소도 저장한다(`food` 태그 계산, mountain 태그 시트 근거).

### 4-2. `build_poi_tags_sql.py` 변경

- 생성 SQL 이 임시 테이블 대신 `place` 를 **TRUNCATE 후 INSERT** 하고, 태그 계산(`dong.tags`, `region.tags`)은 `place` 에서 한다. 태그 기준(카페 3곳 · 음식점 10곳 · 나머지 1곳)은 그대로.
- 페이지 크기 1,000 → **200**, 요청당 3회 재시도. (2026-11-09 확인: 1,000건 요청은 60초 넘게 무응답, 200건은 정상. 호출 약 31회, 개발계정 일일 1,000회 안)
- 받기에 실패하면 SQL 파일을 만들지 않는다 (반쪽 반영 방지). 생성 SQL 은 지금처럼 한 트랜잭션.
- `preview` 모드는 유지 (DB 변경 없이 동별 개수).

### 4-3. `webapi/management/commands/sync_graph.py` (신규 Django 명령)

- Django DB 설정(`.env` 의 `DB_*`)으로 Postgres 를 읽고 Neo4j(`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`)에 쓴다. 읽기 · 변환 · 쓰기 함수는 `webapi/graph.py` 에 두고 API 와 같이 쓴다.
- 행 → 노드·관계 변환은 **순수 함수** `build_graph(rows)` 로 분리해 테스트한다.
- Neo4j 쓰기는 한 트랜잭션: 전체 삭제 → 노드 → 관계 → `(:Meta {synced_at})`. 실패하면 이전 그래프가 남는다.
- 끝나면 라벨별 노드 수와 관계 수를 출력한다.
- 실행 순서는 `db/README.md` 적재 흐름 끝에 추가한다: 지표·태그·장소 반영 후 `manage.py sync_graph`.

## 5. API — `GET /api/explore/graph`

`webapi/` 에 추가 (웹 화면 계약 API 와 같은 위치·규칙). 파라미터 없음. 형식은 웹 W-09 §6 `ExploreGraphResponse` 와 같다.

```json
{
  "syncedAt": "2026-11-09T10:00:00+09:00",
  "tags": [
    {"id": "coast", "label": "바닷가", "group": "trait", "display": "badge"},
    {"id": "cafe", "label": "카페 많음", "group": "work", "display": "branch"}
  ],
  "provinces": [{"id": "51", "name": "강원특별자치도", "center": {"lat": 37.8, "lng": 128.3}}],
  "sigungu": [{"id": "51150", "name": "강릉시", "provinceId": "51", "center": {"lat": 37.75, "lng": 128.88}}],
  "dongs": [
    {
      "id": "5115025000", "name": "주문진읍", "sigunguId": "51150",
      "center": {"lat": 37.89, "lng": 128.82}, "bbox": [128.75, 37.85, 128.84, 37.93],
      "grades": {"quiet": "S", "peer": "A", "evening": "B"},
      "tags": [{"tagId": "coast", "placeCount": null}, {"tagId": "cafe", "placeCount": 7}],
      "coastKm": 0.0,
      "similar": ["5183031000", "5121051000"]
    }
  ],
  "places": [
    {"id": "2868839", "name": "○○카페", "dongId": "5115025000", "lat": 37.9, "lng": 128.8,
     "tag": "cafe", "image": "https://tong.visitkorea.or.kr/cms/resource/…"}
  ]
}
```

(값은 형식 예시다.) 규칙:

- `tags` 는 12개, §3 순서. `food` 태그와 음식점 장소는 없다.
- `provinces[].id` 는 시군구 코드 앞 2자리, `sigungu[].id` 는 `sigungu_code`, `dongs[].id` 는 `adm_code`, `places[].id` 는 `contentid` (모두 문자열).
- `center` 는 경계 내부 점(`ST_PointOnSurface`). 경계가 없으면 null (지금은 해당 동 없음).
- `dongs[].grades`: 연간 등급. 세 지표가 모두 없으면 null, 일부만 없으면 그 값만 null.
- `dongs[].tags`: §3 순서, `food` 제외. `placeCount` = 같은 응답 `places` 중 그 동·그 태그 수. `badge` 태그는 null.
- `dongs[].coastKm`: `dong.coast_dist_km`, 내륙(약 18km 초과)·경계 없음은 null.
- `dongs[].similar`: 비슷한 동 id, 순위순 최대 5, 다른 시군구 포함.
- `dongs[].excluded`: 형식에만 있다. 지금은 넣는 동이 없다(§2).
- `places`: `branch` 태그 근거 장소만. `image` 는 https 또는 null. 출처 "한국관광공사" 표기는 웹이 고정 문구로 한다.
- 행정동 경계는 웹의 정적 GeoJSON(`public/geo/{sggId}.json`)을 쓰므로 응답에 넣지 않는다.

### 구현

- Neo4j 조회는 `webapi/graph.py` 의 `fetch_graph() -> dict` 하나에 모은다 (공식 `neo4j` Python 드라이버, Cypher 3개: Meta · 노드 전체 · 관계 전체). 노드·관계를 읽은 뒤 순수 함수 `to_response()` 가 위 형식으로 펼친다. 뷰는 `fetch_graph` 만 부른다.
- 응답을 Django 캐시(기본 LocMem)에 **1시간** 보관하고, 마지막 응답을 만료 없이 따로 보관한다.
- 응답은 Django `GZipMiddleware` 로 압축한다.

### 오류

| 상황 | 동작 |
| --- | --- |
| 캐시 있음 | Neo4j 를 부르지 않고 200 |
| Neo4j 연결 실패, 마지막 응답 있음 | 마지막 응답으로 200 |
| Neo4j 연결 실패, 마지막 응답 없음 | 503 `{"error": {"code": "GRAPH_UNAVAILABLE", "message": "탐색 데이터를 불러오지 못했어요."}}` (`core/errors.error`) |
| Neo4j 가 비어 있음 (`Meta` 없음) | 위와 같은 처리. 로그에 "manage.py sync_graph 실행 필요" |
| `NEO4J_URI` 미설정 | 위와 같은 처리 (로컬 개발에서 Neo4j 없이도 서버가 뜬다) |

## 6. 설정

- `pyproject.toml` 에 `neo4j` 추가.
- `.env.example` 에 `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `TOURAPI_KEY` 추가 (값 없이).
- Aura Free 인스턴스는 팀 계정으로 하나 만든다. 72시간 사용이 없으면 일시정지되고, 일시정지 30일이면 삭제된다(2026-11 Neo4j 문서). 삭제돼도 `manage.py sync_graph` 로 복구한다. **시연 전날과 당일 콘솔에서 상태 확인**.

## 7. 테스트

- `tests/test_graph_build.py`: `build_graph` (도 노드, 음식점·산·숲 장소 제외, food 태그 제외, 집계 제외 동 제외, SIMILAR 상위 5, https 변환), Neo4j 속성 왕복, `to_response` (웹 형식, 태그 순서, placeCount, badge null, grades null), `load_rows` (테스트 DB, PostGIS·pgvector 없음).
- `tests/test_graph_sync.py`: 가짜 드라이버로 `write_graph` 쿼리 순서, `fetch_graph` 성공·빈 그래프·연결 실패, `sync_graph` 명령.
- `tests/test_explore_api.py`: `fetch_graph` 를 가짜로 바꿔 응답, 캐시 적중 시 재호출 없음, 마지막 응답 대체, 503, gzip.
- `tests/test_poi_tags_tool.py`: `tag_of` 분류와 페이지 처리(200건 단위, 마지막 페이지 판정), 생성 SQL. 실제 API 호출은 테스트하지 않는다.
- CI 에 Neo4j 컨테이너를 추가하지 않는다.

### 수동 검증 (PR 체크리스트)

- [ ] `place` 저장 수, 동 경계 밖으로 빠진 수 기록
- [ ] 태그 재계산 후 태그별 동 수가 `db/README.md` 기록(2026-11-07)과 크게 다르지 않음. 다르면 차이를 README 에 기록
- [ ] `manage.py sync_graph` 출력의 라벨별 노드 수 · 관계 수
- [ ] `/api/explore/graph` 응답 크기(원본 · gzip)와 응답 시간(캐시 없음 · 있음)
- [ ] 응답 `image` 몇 개를 브라우저에서 열어 https 로 뜨는지

## 8. 화면

웹 W-09 스펙이 기준이다. 백엔드는 §5 형식만 책임진다.

## 9. 남은 확인 사항

| 항목 | 확인 방법 | 실패 시 |
| --- | --- | --- |
| 동 경계 밖으로 빠지는 장소 수 | 4-2 실행 후 집계 | 많으면(10% 이상) 가장 가까운 동에 붙이는 규칙 검토 |
| Aura Free 리전과 지연 | 인스턴스 생성 시 확인 | 캐시로 대부분 가려짐. 첫 요청만 영향 |
| 관광공사 이미지 저작권 유형 | TourAPI 이미지 조회의 저작권 구분 필드 확인 (미확인). 활용신청 화면 이용허락범위는 "제한 없음" | 출처 "한국관광공사" 표기, 이미지 변형 금지를 가정 |
