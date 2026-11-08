# 탐색 지식그래프 (백엔드) 설계

- 작성일: 2026-11-09
- 상태: 승인됨 (브레인스토밍 결과). 화면 동작(프론트)은 UI 확정 후 별도 설계
- 관련: workation-web `docs/superpowers/specs/2026-11-07-W-01-탐색홈-design.md` (홈의 전체 폭 지도안은 폐기 → 탐색은 별도 경로 `/explore`), `2026-11-07-W-04-동네프로필-design.md` (행정동 경계 정적 GeoJSON), `db/README.md`
- 브랜치: `docs/explore-graph-spec`

## 1. 목표

웹 `/explore` 페이지에서 사용자가 지도를 끌고 확대·축소하며 **도 → 시군구 → 행정동 → 장소**로 이어진 관계를 탐색할 수 있도록, 백엔드가 지식그래프를 만들어 한 번에 내려준다.

완료 기준:

- `place` 테이블에 관광공사 장소가 저장되고, 동 태그(`dong.tags`)는 이 테이블에서 계산된다.
- `db/tools/sync_graph.py` 를 실행하면 Neo4j 에 노드·관계가 채워진다 (전체 삭제 후 재적재).
- `GET /api/explore/graph` 가 그래프 전체를 §5 형식으로 돌려준다. 압축 응답 100KB 이하(목표, 실측).
- `uv run pytest` 가 Neo4j 없이 통과한다.

## 2. 결정 요약

| 항목 | 결정 | 이유 |
| --- | --- | --- |
| 저장소 | **Postgres = 원본, Neo4j(AuraDB Free) = 읽기 전용 복사본** | 탐색용 그래프 질의·시연. 원본은 기존 파이프라인 유지 |
| 쓰기 | 서비스는 Neo4j 에 쓰지 않는다 | 두 DB 간 트랜잭션·동기화 문제 회피 |
| 동기화 | 적재 후 `sync_graph.py` 수동 실행, 전체 삭제 후 재적재 | 데이터는 배치로만 바뀌고 규모가 작다(수 초) |
| 전달 방식 | 진입 시 그래프 전체 1회 전달, 이후 상호작용은 브라우저에서 | 노드 약 1,230개라 작다. 태그 클릭 등 즉시 반응 |
| 장소 범위 | 태그 근거 장소만. **음식점은 Neo4j 에서 제외**(Postgres 에는 저장) | 워케이션 판단과 거리가 멀고 장소의 54% 를 차지. `food` 태그 계산에는 필요 |
| 지표 | 노드가 아니라 동 노드의 속성(연간 등급) | 월별 관계 약 8천 개를 늘릴 만큼 탐색에 기여하지 않음 |
| Neo4j 장애 | 캐시 응답 → 없으면 503. Postgres 대체 경로는 만들지 않음 | Aura Free 72시간 일시정지는 시연 전 재개로 대응 |

넣지 않는 것: 표현(`tag_vocab`) 노드, 인접 동 관계(지도에서 보임), 월별 지표, 화면 범위별 조회 API, 서비스 실행 중 TourAPI 호출.

## 3. 그래프 모델

### 노드

| 라벨 | 수 | id | 속성 |
| --- | --- | --- | --- |
| `Sido` | 4 | `region.sido_name` | name |
| `Region` | 12 | `sigungu_code` (웹 API 와 같은 5자리) | name, lat, lng (경계 중심) |
| `Dong` | 203 (`is_excluded`·`adm_code` 없는 동 제외) | `adm_code` (웹 API 와 같은 10자리) | name, lat, lng, grades `{quiet, peer, evening}` (연간 등급 S/A/B/C, 없으면 null) |
| `Tag` | 13 | `dong.tags` 코드 | label (`regions/views.py` `TAG_LABELS`), group |
| `Place` | 약 1,000 (2026-11-09 조회 기준 음식점 제외 1,057곳, 동 경계 밖 제외 전) | TourAPI `contentid` | name, lat, lng, image (TourAPI 대표 이미지 URL, 없으면 null) |

태그 묶음(`group`): `work` = cafe · library, `stay` = long_stay · resort, `rest` = beach · park · mountain · island · surf, `life` = market · food, `terrain` = coast · city. 화면 기본 표시는 work · stay (프론트 설계에서 확정).

### 관계

| 관계 | 수(추정) | 출처 |
| --- | --- | --- |
| `(Region)-[:IN]->(Sido)` | 12 | `region.sido_name` |
| `(Dong)-[:IN]->(Region)` | 203 | `dong.region_id` |
| `(Dong)-[:HAS_TAG]->(Tag)` | 약 560 | `dong.tags` |
| `(Place)-[:LOCATED_IN]->(Dong)` | 약 1,000 | `place.dong_id` |
| `(Place)-[:EVIDENCE_OF]->(Tag)` | 약 1,000 | `place.tag` (mountain 은 태그 시트 제안 근거 장소) |
| `(Dong)-[:SIMILAR {rank, dist}]->(Dong)` | 약 1,000 | `dong_feature` 연간 `metric_vec` 거리 상위 5. `webapi/data.py` `similar_dongs` 재사용 |

## 4. 데이터 준비

```
① TourAPI ──(build_poi_tags_sql.py, 로컬)──▶ Postgres place + dong.tags · region.tags
② Postgres ──(sync_graph.py, 로컬)──▶ Neo4j
```

### 4-1. `place` 테이블 (migration `db/migrations/<적용일 YYYYMMDD>_place.sql`, 기존 이름 규칙)

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `place_id` | `BIGINT PK` | TourAPI `contentid` |
| `name` | `VARCHAR(200)` | 장소명 |
| `tag` | `VARCHAR(20)` | 태그 코드. 장소의 분류 코드가 하나라 태그도 하나다(`tag_of` 기준. `mountain_hint` 는 `mountain` 으로 저장) |
| `content_type` | `VARCHAR(4)` | TourAPI `contenttypeid` |
| `category` | `VARCHAR(10)` | TourAPI 신분류 3단계 `lclsSystm3` |
| `lng`, `lat` | `DOUBLE PRECISION` | 좌표 |
| `dong_id` | `SMALLINT FK → dong` | `ST_Contains(dong.geom, 좌표)`. 경계 밖이면 저장하지 않음 |
| `image_url` | `TEXT NULL` | `firstimage` |
| `fetched_at` | `TIMESTAMPTZ` | 수집 시각 |

RLS ENABLE, 정책 없음 (다른 테이블과 같음). 출처 표기는 "한국관광공사".

### 4-2. `build_poi_tags_sql.py` 변경

- 생성 SQL 이 임시 테이블 대신 `place` 를 **TRUNCATE 후 INSERT** 하고, 태그 계산(`dong.tags`, `region.tags`)은 `place` 에서 한다. 태그 기준(카페 3곳 · 음식점 10곳 · 나머지 1곳)은 그대로.
- 페이지 크기 1,000 → **200**, 요청당 3회 재시도. (2026-11-09 확인: 1,000건 요청은 60초 넘게 무응답, 200건은 정상. 호출 약 31회, 개발계정 일일 1,000회 안)
- 받기에 실패하면 SQL 파일을 만들지 않는다 (반쪽 반영 방지). 생성 SQL 은 지금처럼 한 트랜잭션.
- `preview` 모드는 유지 (DB 변경 없이 동별 개수).

### 4-3. `db/tools/sync_graph.py` (신규)

- Postgres(`DATABASE_URL`)에서 읽고 Neo4j(`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`)에 쓴다.
- 행 → 노드·관계 변환은 **순수 함수** (`build_graph(rows) -> (nodes, edges)`)로 분리해 테스트한다. 같은 함수를 API 테스트의 가짜 그래프 생성에도 쓴다.
- Neo4j 쓰기는 한 트랜잭션: 전체 삭제 → 노드 → 관계 → `(:Meta {synced_at})`. 실패하면 이전 그래프가 남는다.
- 끝나면 라벨별 노드 수와 관계 수를 출력한다.
- 실행 순서는 `db/README.md` 적재 흐름 끝에 추가한다: 지표·태그·장소 반영 후 `sync_graph.py`.

## 5. API — `GET /api/explore/graph`

`webapi/` 에 추가 (웹 화면 계약 API 와 같은 위치·규칙). 파라미터 없음.

```json
{
  "syncedAt": "2026-11-09T10:00:00+09:00",
  "nodes": [
    {"id": "sido:강원특별자치도", "kind": "sido", "name": "강원특별자치도"},
    {"id": "region:51150", "kind": "region", "name": "강릉시", "lat": 37.75, "lng": 128.88},
    {"id": "dong:5115025000", "kind": "dong", "name": "주문진읍", "lat": 37.89, "lng": 128.82,
     "grades": {"quiet": "S", "peer": "A", "evening": "B"}},
    {"id": "tag:cafe", "kind": "tag", "name": "카페 많음", "group": "work"},
    {"id": "place:2868839", "kind": "place", "name": "○○카페", "lat": 37.9, "lng": 128.8,
     "tag": "cafe", "image": "http://tong.visitkorea.or.kr/…", "credit": "한국관광공사"}
  ],
  "edges": [
    {"from": "region:51150", "to": "sido:강원특별자치도", "type": "IN"},
    {"from": "dong:5115025000", "to": "region:51150", "type": "IN"},
    {"from": "dong:5115025000", "to": "tag:cafe", "type": "HAS_TAG"},
    {"from": "place:2868839", "to": "dong:5115025000", "type": "LOCATED_IN"},
    {"from": "place:2868839", "to": "tag:cafe", "type": "EVIDENCE_OF"},
    {"from": "dong:5115025000", "to": "dong:5183031000", "type": "SIMILAR", "rank": 1}
  ]
}
```

(값은 형식 예시다.) 프론트는 이 응답만으로 확대 단계별 표시, 태그 강조, 클릭 중심 그래프를 브라우저에서 처리한다. 행정동 경계는 웹의 정적 GeoJSON(`public/geo/{sggId}.json`)을 쓰므로 응답에 넣지 않는다.

### 구현

- Neo4j 조회는 `webapi/graph.py` 의 `fetch_graph() -> dict` 하나에 모은다 (공식 `neo4j` Python 드라이버, Cypher 2개: 노드 전체 · 관계 전체). 뷰는 이 함수만 부른다.
- 응답을 Django 캐시(기본 LocMem)에 **1시간** 보관한다. 캐시가 있으면 Neo4j 를 부르지 않는다.
- 응답은 gzip (Django `GZipMiddleware` 또는 Gunicorn 앞단)으로 압축한다.

### 오류

| 상황 | 동작 |
| --- | --- |
| Neo4j 연결 실패, 캐시 있음 | 캐시로 200 |
| Neo4j 연결 실패, 캐시 없음 | 503 `{"error": {"code": "GRAPH_UNAVAILABLE", "message": "탐색 데이터를 불러오지 못했어요."}}` (`core/errors.error`) |
| Neo4j 가 비어 있음 (`Meta` 없음) | 위와 같은 503. 로그에 "sync_graph 실행 필요" |
| `NEO4J_URI` 미설정 | 위와 같은 503 (로컬 개발에서 Neo4j 없이도 서버가 뜬다) |

## 6. 설정

- `pyproject.toml` 에 `neo4j` 추가.
- `.env.example` 에 `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `TOURAPI_KEY` 추가 (값 없이).
- Aura Free 인스턴스는 팀 계정으로 하나 만든다. 72시간 사용이 없으면 일시정지되고, 일시정지 30일이면 삭제된다(2026-11 Neo4j 문서). 삭제돼도 `sync_graph.py` 로 복구한다. **시연 전날과 당일 콘솔에서 상태 확인**.

## 7. 테스트

- `tests/test_graph_build.py`: `build_graph` 순수 함수. 음식점 제외, 경계 밖 장소 제외, 도 노드 생성, SIMILAR 상위 5, 집계 제외 동 제외, 등급 없는 동은 null.
- `tests/test_explore_api.py`: `fetch_graph` 를 가짜로 바꿔 응답 형식, 캐시 적중 시 재호출 없음, 연결 실패·빈 그래프·미설정 시 503.
- `build_poi_tags_sql.py`: `tag_of` 분류와 페이지 처리(200건 단위, 마지막 페이지 판정) 단위 테스트. 실제 API 호출은 테스트하지 않는다.
- CI 에 Neo4j 컨테이너를 추가하지 않는다.

### 수동 검증 (PR 체크리스트)

- [ ] `place` 저장 수, 동 경계 밖으로 빠진 수 기록
- [ ] 태그 재계산 후 태그별 동 수가 `db/README.md` 기록(2026-11-07)과 크게 다르지 않음. 다르면 차이를 README 에 기록
- [ ] `sync_graph.py` 출력의 라벨별 노드 수 · 관계 수
- [ ] `/api/explore/graph` 응답 크기(원본 · gzip)와 응답 시간(캐시 없음 · 있음)

## 8. 프론트로 넘기는 것 (UI 확정 후 설계)

이 스펙에서 정하지 않고 workation-web 설계로 넘긴다. 브레인스토밍에서 나온 초안이다.

- 화면: 지도 주인공 + 오른쪽 그래프 패널(클릭한 노드 중심). 지표 등급은 패널에 글자로.
- 확대 단계: 도 → 시군구 → 행정동(장소 수 표시) → 장소 핀(줌 14 이상).
- 장소 핀: 2026-11-09 데이터 기준 한 화면 최대 약 100개(강릉 시내, 줌 14, 음식점 제외 전부 켬) · 기본 표시(work · stay) 83개. 클러스터링(`@googlemaps/markerclusterer`)과 한 화면 200개 상한.
- 태그 클릭 → 해당 동 즉시 강조, 비슷한 동 클릭 → 지도 이동, 동 → W-04 동네 프로필 / 시군구 → W-03 지역 상세 링크.
- 그래프 패널 후보 라이브러리: `react-force-graph-2d`.

## 9. 남은 확인 사항

| 항목 | 확인 방법 | 실패 시 |
| --- | --- | --- |
| 동 경계 밖으로 빠지는 장소 수 | 4-2 실행 후 집계 | 많으면(10% 이상) 가장 가까운 동에 붙이는 규칙 검토 |
| TourAPI 이미지 URL 이 `http://` | 웹이 https 라 혼합 콘텐츠로 막힐 수 있음. 실측 | `https://` 로 바꿔 내려주거나 이미지 생략 |
| Aura Free 리전과 지연 | 인스턴스 생성 시 확인 | 캐시로 대부분 가려짐. 첫 요청만 영향 |
| 공공누리 · 이용허락 표기 | TourAPI 이용허락범위 "제한 없음"(2026-11-09 신청 화면). 출처 "한국관광공사" 표기 | — |
