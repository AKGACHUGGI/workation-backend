# 탐색 지식그래프 (백엔드) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관광공사 장소를 `place` 테이블에 저장하고, Postgres 데이터를 Neo4j 지식그래프로 동기화해 `GET /api/explore/graph` 로 한 번에 내려준다.

**Architecture:** Postgres 가 원본이고 Neo4j(AuraDB Free)는 읽기 전용 복사본이다. `webapi/graph.py` 가 Postgres 읽기(`load_rows`) → 순수 변환(`build_graph`) → Neo4j 쓰기(`write_graph`) / 읽기(`fetch_graph`)를 맡고, Django 명령 `sync_graph` 와 API 뷰가 이를 쓴다. API 는 응답을 1시간 캐시하고, Neo4j 가 안 되면 마지막 응답으로, 그것도 없으면 503 으로 답한다.

**Tech Stack:** Django 5.2 · DRF 3.18 · PostgreSQL 17 (PostGIS · pgvector는 Supabase 에만) · Neo4j 공식 Python 드라이버 `neo4j` · pytest-django · ruff

**Spec:** `docs/superpowers/specs/2026-11-09-explore-graph-design.md`

## Global Constraints

- 브랜치: `docs/explore-graph-spec` 에서 `feat/explore-graph` 를 만들어 작업한다. `main` 에 직접 커밋하지 않는다.
- 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` 를 붙인다. 메시지는 기존처럼 한국어 `feat:` / `chore:` / `docs:`.
- 커밋 전 검사(CI 와 같음): `uv run ruff check .` · `uv run ruff format --check .` · `docker compose up -d --wait` · `uv run pytest`. ruff 는 `db/` 를 검사하지 않는다.
- 줄 길이 100 (ruff). Python 하한 3.12.
- 테스트는 Neo4j · TourAPI · Supabase 에 붙지 않는다. 테스트 DB 는 `db/schema.sql` 만 실행되어 PostGIS(`dong.geom`)와 pgvector(`dong_feature`)가 없다.
- id 규칙: 시군구 `sigungu_code`(5자리), 행정동 `adm_code`(10자리). 그래프 노드 id 는 `sido:<시도명>` · `region:<sigungu_code>` · `dong:<adm_code>` · `tag:<태그>` · `place:<contentid>`.
- 에러 응답 형식: `{"error": {"code", "message"}}` (`core/errors.error`). 503 코드는 `GRAPH_UNAVAILABLE`, 메시지 `탐색 데이터를 불러오지 못했어요.`
- 그래프에서 음식점(`food`) 장소는 뺀다. `place` 테이블에는 저장한다.
- 비슷한 동은 연간(`month = 0`) `metric_vec` 거리 상위 5, 집계 제외 동 제외.
- 장소 출처 표기는 `한국관광공사`.

## File Structure

| 파일 | 역할 |
| --- | --- |
| `db/schema.sql` (수정) | `place` 테이블 DDL + RLS (테스트 DB 도 이걸로 만든다) |
| `db/migrations/20261109_place.sql` (신규) | Supabase 에 `place` 추가 |
| `db/tools/build_poi_tags_sql.py` (수정) | TourAPI 200건 단위 · 재시도, 장소를 `place` 에 저장, 태그를 `place` 에서 계산 |
| `webapi/graph.py` (신규) | 그래프 읽기 · 변환 · Neo4j 쓰기/읽기. 이 기능의 핵심 모듈 |
| `webapi/management/commands/sync_graph.py` (신규) | `manage.py sync_graph` |
| `webapi/views.py`, `webapi/urls.py` (수정) | `GET /api/explore/graph` |
| `config/settings.py`, `config/settings_test.py`, `.env.example`, `pyproject.toml` (수정) | Neo4j 설정 · GZip · 의존성 |
| `tests/test_poi_tags_tool.py` | 장소 수집 스크립트 |
| `tests/test_graph_build.py` | `build_graph` · 속성 변환 · `load_rows` |
| `tests/test_graph_sync.py` | `write_graph` · `fetch_graph` · `sync_graph` 명령 (가짜 드라이버) |
| `tests/test_explore_api.py` | API · 캐시 · 503 · gzip |
| `db/README.md`, `docs/개발-가이드.md` (수정) | 적재 흐름 · 구조 · API 표 |

---

### Task 0: 작업 브랜치

- [ ] **Step 1: 브랜치 만들기**

```bash
git checkout docs/explore-graph-spec
git checkout -b feat/explore-graph
docker compose up -d --wait
uv run pytest -q
```

Expected: 기존 테스트 전부 PASS.

---

### Task 1: `place` 테이블

**Files:**
- Modify: `db/schema.sql` (`region_link` 테이블 정의 바로 뒤, 그리고 파일 끝 RLS 목록)
- Create: `db/migrations/20261109_place.sql`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: 테이블 `place(place_id BIGINT PK, name, tag, content_type, category, lng, lat, dong_id SMALLINT FK→dong, image_url, fetched_at)`. Task 2 의 SQL 과 Task 4 의 `load_rows` 가 쓴다.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_schema.py` 의 `EXPECTED_TABLES` 에 `"place",` 를 `"nl_search_log",` 다음 줄에 추가한다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_schema.py -v`
Expected: FAIL (`assert EXPECTED_TABLES <= tables`, `place` 없음)

- [ ] **Step 3: `db/schema.sql` 에 DDL 추가**

`COMMENT ON COLUMN region_link.kind IS 'support, stay, tour';` 줄 바로 뒤(빈 줄 하나 두고)에 넣는다.

```sql
CREATE TABLE place (
  place_id      BIGINT           PRIMARY KEY,
  name          VARCHAR(200)     NOT NULL,
  tag           VARCHAR(20)      NOT NULL,
  content_type  VARCHAR(4),
  category      VARCHAR(10),
  lng           DOUBLE PRECISION NOT NULL,
  lat           DOUBLE PRECISION NOT NULL,
  dong_id       SMALLINT         NOT NULL REFERENCES dong(dong_id),
  image_url     TEXT,
  fetched_at    TIMESTAMPTZ      NOT NULL DEFAULT now()
);
COMMENT ON TABLE  place              IS '관광공사 TourAPI 장소 중 동 태그의 근거가 되는 것 (db/tools/build_poi_tags_sql.py 가 통째로 다시 채움). 탐색 지식그래프의 장소 노드 원본';
COMMENT ON COLUMN place.place_id     IS 'TourAPI contentid';
COMMENT ON COLUMN place.tag          IS '태그 코드 하나 (cafe · food · library · long_stay · park · market · resort · beach · island · surf · mountain). mountain 은 태그 시트 제안 근거';
COMMENT ON COLUMN place.content_type IS 'TourAPI contenttypeid';
COMMENT ON COLUMN place.category     IS 'TourAPI 신분류 3단계 lclsSystm3';
COMMENT ON COLUMN place.dong_id      IS '좌표가 들어 있는 행정동 (ST_Contains). 경계 밖 장소는 저장하지 않음';
COMMENT ON COLUMN place.image_url    IS 'TourAPI firstimage. 화면에 출처 "한국관광공사" 표기';

CREATE INDEX place_dong_idx ON place (dong_id);
```

파일 끝 RLS 목록의 `ALTER TABLE nl_search_log          ENABLE ROW LEVEL SECURITY;` 다음 줄에 추가:

```sql
ALTER TABLE place                  ENABLE ROW LEVEL SECURITY;
```

- [ ] **Step 4: migration 파일 작성**

`db/migrations/20261109_place.sql`:

```sql
-- 관광공사 장소 테이블 · 2026-11-09
-- 기준: migrations/20261107_text-embedding.sql 까지 적용된 DB. 두 번 실행해도 안전.
--
-- 바뀌는 것
--   1. place 추가. 지금까지 build_poi_tags_sql.py 는 TourAPI 장소를 임시 테이블에서 동별로 세고 버렸다.
--      앞으로는 태그 근거 장소를 저장하고 태그도 이 테이블에서 계산한다. 탐색 지식그래프(/explore) 장소 노드의 원본
--   값은 db/tools/build_poi_tags_sql.py apply 출력으로 채운다.

BEGIN;

CREATE TABLE IF NOT EXISTS place (
  place_id      BIGINT           PRIMARY KEY,
  name          VARCHAR(200)     NOT NULL,
  tag           VARCHAR(20)      NOT NULL,
  content_type  VARCHAR(4),
  category      VARCHAR(10),
  lng           DOUBLE PRECISION NOT NULL,
  lat           DOUBLE PRECISION NOT NULL,
  dong_id       SMALLINT         NOT NULL REFERENCES dong(dong_id),
  image_url     TEXT,
  fetched_at    TIMESTAMPTZ      NOT NULL DEFAULT now()
);
COMMENT ON TABLE  place              IS '관광공사 TourAPI 장소 중 동 태그의 근거가 되는 것 (db/tools/build_poi_tags_sql.py 가 통째로 다시 채움). 탐색 지식그래프의 장소 노드 원본';
COMMENT ON COLUMN place.place_id     IS 'TourAPI contentid';
COMMENT ON COLUMN place.tag          IS '태그 코드 하나 (cafe · food · library · long_stay · park · market · resort · beach · island · surf · mountain). mountain 은 태그 시트 제안 근거';
COMMENT ON COLUMN place.content_type IS 'TourAPI contenttypeid';
COMMENT ON COLUMN place.category     IS 'TourAPI 신분류 3단계 lclsSystm3';
COMMENT ON COLUMN place.dong_id      IS '좌표가 들어 있는 행정동 (ST_Contains). 경계 밖 장소는 저장하지 않음';
COMMENT ON COLUMN place.image_url    IS 'TourAPI firstimage. 화면에 출처 "한국관광공사" 표기';

CREATE INDEX IF NOT EXISTS place_dong_idx ON place (dong_id);

ALTER TABLE place ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON place FROM anon, authenticated;

COMMIT;
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add db/schema.sql db/migrations/20261109_place.sql tests/test_schema.py
git commit -m "chore: 관광공사 장소 테이블 place 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 장소 수집 스크립트가 `place` 에 저장

**Files:**
- Modify: `db/tools/build_poi_tags_sql.py`
- Test: `tests/test_poi_tags_tool.py`

**Interfaces:**
- Consumes: Task 1 의 `place` 테이블.
- Produces: 스크립트 함수 `is_last_page(page: int, total: int, rows: int = ROWS) -> bool`, `sql_str(s: str | None) -> str`, `place_tag(it: dict) -> str | None`, `build(mode: str, places: dict[str, dict]) -> str`. 상수 `ROWS = 200`, `RETRY = 3`.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_poi_tags_tool.py`:

```python
"""db/tools/build_poi_tags_sql.py (표준 라이브러리 스크립트라 파일 경로로 불러온다). TourAPI 는 부르지 않는다."""

import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "db" / "tools" / "build_poi_tags_sql.py"
_spec = importlib.util.spec_from_file_location("build_poi_tags_sql", TOOL)
poi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(poi)


def place(cid, title, code, ctype="39", x=128.9, y=37.8, image=""):
    return {
        "contentid": cid,
        "title": title,
        "lclsSystm3": code,
        "contenttypeid": ctype,
        "mapx": str(x),
        "mapy": str(y),
        "firstimage": image,
    }


def test_tag_of_classifies_cafe_food_mountain():
    assert poi.tag_of(place("1", "카페", "FD050100")) == ["cafe"]
    assert poi.tag_of(place("2", "식당", "FD010100")) == ["food"]
    assert poi.tag_of(place("3", "산", "NA010100", ctype="12")) == ["mountain_hint"]
    assert poi.tag_of(place("4", "박물관", "VE070100", ctype="14")) == []


def test_place_tag_stores_mountain_hint_as_mountain():
    assert poi.place_tag(place("3", "산", "NA010100", ctype="12")) == "mountain"
    assert poi.place_tag(place("1", "카페", "FD050100")) == "cafe"
    assert poi.place_tag(place("4", "박물관", "VE070100", ctype="14")) is None


def test_page_size_is_200_and_last_page():
    assert poi.ROWS == 200
    assert poi.is_last_page(1, 150)
    assert not poi.is_last_page(1, 201)
    assert poi.is_last_page(2, 400)
    assert poi.is_last_page(1, 0)


def test_sql_str_escapes_quotes_and_nulls():
    assert poi.sql_str("It's") == "'It''s'"
    assert poi.sql_str("") == "NULL"
    assert poi.sql_str(None) == "NULL"


def test_build_apply_writes_place_then_tags_from_place():
    places = {
        "11": place("11", "카페 'A'", "FD050100", image="http://img/a.jpg"),
        "12": place("12", "박물관", "VE070100", ctype="14"),
    }
    sql = poi.build("apply", places)
    assert "(11, '카페 ''A''', 'cafe', '39', 'FD050100', 128.900000, 37.800000, 'http://img/a.jpg')" in sql
    assert "(12," not in sql  # 태그 없는 장소는 넣지 않는다
    assert sql.index("TRUNCATE place;") < sql.index("INSERT INTO place")
    assert "FROM place GROUP BY dong_id, tag" in sql
    assert sql.rstrip().endswith("COMMIT;")


def test_build_preview_does_not_touch_place():
    sql = poi.build("preview", {"11": place("11", "카페", "FD050100")})
    assert "TRUNCATE place" not in sql and "INSERT INTO place" not in sql
    assert sql.rstrip().endswith("ORDER BY 1, 2;")
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_poi_tags_tool.py -v`
Expected: FAIL (`AttributeError: module 'build_poi_tags_sql' has no attribute 'place_tag'` 등)

- [ ] **Step 3: 스크립트 수정**

`db/tools/build_poi_tags_sql.py` 의 모듈 docstring 을 다음으로 바꾼다.

```python
"""TourAPI 장소 → place 테이블과 행정동 장소 태그 SQL 생성

장소: 한국관광공사 국문 관광정보(KorService2/areaBasedList2)의 12개 시군구 전체(약 5천 곳).
분류: 관광공사 신분류 3단계 코드(lclsSystm3). 장소 좌표가 행정동 경계 안에 있으면 그 동에 센다.
저장: 태그 근거가 되는 장소(2026-11-09 기준 약 2,300곳, 음식점 포함)를 place 에 통째로 다시 넣는다.
  탐색 지식그래프(/explore)의 장소 노드 원본이다. 경계 밖(바다 위 등) 좌표는 버린다.
태그: 아래 TAGS 의 코드에 해당하는 장소가 기준 개수 이상인 동에 붙인다. 개수는 place 에서 센다.
  cafe 와 food 는 "많은 곳"이라 기준이 3 · 10 이다. 장소가 있는 동 가운데 상위 약 20% (2026-11-07 분포).
  나머지는 1곳 이상.
mountain 은 이 스크립트가 태그로 붙이지 않는다. 태그 시트(build_tags_sql.py)로 팀이 확정하고,
  MOUNTAIN_HINT 장소는 place 에 tag = mountain 으로 저장해 근거로만 쓴다.
시군구 태그는 소속 행정동 태그의 합집합으로 다시 계산한다.
TourAPI 는 2026-11-09 기준 한 번에 1,000건을 요청하면 응답하지 않아 200건씩 받고 3번까지 다시 시도한다.
  끝까지 실패하면 SQL 파일을 만들지 않는다(반쪽 반영 방지).

사용 (표준 라이브러리만. 키는 TOURAPI_KEY 환경변수, 없으면 현재 폴더 .env 의 TOURAPI_KEY)
  python3 db/tools/build_poi_tags_sql.py preview <출력.sql>   # 동별 태그 개수 SELECT (DB 변경 없음)
  python3 db/tools/build_poi_tags_sql.py apply <출력.sql>     # place · dong.tags · region.tags 갱신
  migrations/20261109_place.sql 을 먼저 적용해야 한다. 출력 SQL 은 커밋하지 않는다.
"""
```

`MANAGED = [t for t, _, _ in TAGS]` 줄 다음에 상수를 추가한다.

```python
URL = "https://apis.data.go.kr/B551011/KorService2/areaBasedList2"
ROWS = 200  # 1,000 이면 응답이 없다 (2026-11-09)
RETRY = 3
```

`fetch_places` 를 다음 세 함수로 바꾼다.

```python
def is_last_page(page: int, total: int, rows: int = ROWS) -> bool:
    return page * rows >= total


def get_page(key: str, regn: str, code: str, page: int) -> dict:
    q = urllib.parse.urlencode({
        "serviceKey": key, "MobileOS": "ETC", "MobileApp": "workation", "_type": "json",
        "numOfRows": ROWS, "pageNo": page, "lDongRegnCd": regn, "lDongSignguCd": code})
    for attempt in range(1, RETRY + 1):
        try:
            return json.loads(urllib.request.urlopen(f"{URL}?{q}", timeout=40).read())["response"]["body"]
        except (OSError, ValueError, KeyError) as ex:  # 시간 초과 · 연결 끊김 · 응답 깨짐
            if attempt == RETRY:
                sys.exit(f"TourAPI 응답 없음 ({regn}-{code} {page}쪽): {ex} — SQL 파일을 만들지 않았다")
            print(f"  다시 시도 {attempt}/{RETRY - 1} ({regn}-{code} {page}쪽): {ex}")
            time.sleep(2 * attempt)


def fetch_places(key: str) -> dict[str, dict]:
    places: dict[str, dict] = {}
    for name, regn, codes in TARGETS:
        for code in codes:
            page = 1
            while True:
                body = get_page(key, regn, code, page)
                items = body["items"]["item"] if body.get("items") else []
                for it in [items] if isinstance(items, dict) else items:
                    if it.get("mapx") and it.get("mapy"):
                        places[it["contentid"]] = it
                if is_last_page(page, int(body.get("totalCount") or 0)):
                    break
                page += 1
                time.sleep(0.3)
        print(f"  {name} 누적 {len(places)}곳")
    return places
```

`tag_of` 는 그대로 두고, 그 아래에 추가한다.

```python
def place_tag(it: dict) -> str | None:
    """place.tag 에 넣을 태그 하나. 분류 코드가 하나라 tag_of 결과도 하나다. mountain_hint → mountain."""
    tags = tag_of(it)
    if not tags:
        return None
    return "mountain" if tags[0] == "mountain_hint" else tags[0]


def sql_str(s: str | None) -> str:
    return "NULL" if not s else "'" + s.replace("'", "''") + "'"
```

`build` 함수 전체를 다음으로 바꾼다.

```python
def build(mode: str, places: dict[str, dict]) -> str:
    rows = []
    for cid, it in places.items():
        tag = place_tag(it)
        if tag:
            rows.append(
                f"({int(cid)}, {sql_str(it.get('title') or '이름 없음')}, '{tag}', "
                f"{sql_str(it.get('contenttypeid'))}, {sql_str(it.get('lclsSystm3'))}, "
                f"{float(it['mapx']):.6f}, {float(it['mapy']):.6f}, {sql_str(it.get('firstimage'))})")
    in_dong = "JOIN dong d ON ST_Contains(d.geom, ST_SetSRID(ST_MakePoint(p.x, p.y), 4326))"
    head = [
        "-- 장소 · 장소 태그 (db/tools/build_poi_tags_sql.py 로 생성, 직접 고치지 말 것)",
        f"-- 장소: 한국관광공사 TourAPI {len(places)}곳 중 태그 대상 {len(rows)}곳",
        "BEGIN;",
        "SET LOCAL search_path = public, extensions;",
        "CREATE TEMP TABLE poi (place_id bigint, name text, tag text, content_type text, category text,",
        "  x float8, y float8, image text) ON COMMIT DROP;",
        "INSERT INTO poi VALUES\n" + ",\n".join(rows) + ";",
    ]
    if mode == "preview":  # 이 API 는 마지막 문장 결과만 돌려주므로 SELECT 로 끝낸다. 임시 테이블뿐이라 남는 것 없음
        return "\n".join(head + [
            f"SELECT d.dong_id, p.tag, count(*) AS n FROM poi p {in_dong}",
            "  GROUP BY 1, 2 ORDER BY 1, 2;",
        ]) + "\n"
    managed = "ARRAY[" + ", ".join(f"'{t}'" for t in MANAGED) + "]"
    need = "CASE c.tag " + " ".join(f"WHEN '{t}' THEN {k}" for t, k, _ in TAGS) + " END"
    body = [
        "TRUNCATE place;",
        "INSERT INTO place (place_id, name, tag, content_type, category, lng, lat, dong_id, image_url)",
        "SELECT DISTINCT ON (p.place_id) p.place_id, p.name, p.tag, p.content_type, p.category,",
        f"       p.x, p.y, d.dong_id, p.image FROM poi p {in_dong}",
        "ORDER BY p.place_id, d.dong_id;",
        "CREATE TEMP TABLE cnt ON COMMIT DROP AS",
        "SELECT dong_id, tag, count(*) AS n FROM place GROUP BY dong_id, tag;",
        f"UPDATE dong SET tags = coalesce((SELECT array_agg(t) FROM unnest(tags) t WHERE t <> ALL({managed})), '{{}}');",
        "UPDATE dong d SET tags = d.tags || ARRAY(",
        f"  SELECT c.tag FROM cnt c WHERE c.dong_id = d.dong_id AND c.tag = ANY({managed}) AND c.n >= {need}",
        "  ORDER BY c.tag);",
        "-- 시군구 태그 = 소속 행정동 태그의 합집합",
        "UPDATE region r SET tags = coalesce((",
        "  SELECT array_agg(DISTINCT t ORDER BY t) FROM dong d, unnest(d.tags) t WHERE d.region_id = r.region_id",
        "), '{}');",
        "COMMIT;",
    ]
    return "\n".join(head + body) + "\n"
```

`main()` 은 그대로 둔다 (`fetch_places` 가 끝까지 성공해야 `out.write_text` 에 도달한다).

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_poi_tags_tool.py -v`
Expected: 6 passed

- [ ] **Step 5: 실제 API 로 preview 생성 확인 (TOURAPI_KEY 가 `.env` 에 있을 때)**

Run: `python3 db/tools/build_poi_tags_sql.py preview /tmp/poi_preview.sql && head -2 /tmp/poi_preview.sql`
Expected: `  강릉시 누적 …곳` … `  제주시 누적 …곳` 출력 후 두 번째 줄이 `-- 장소: 한국관광공사 TourAPI 4989곳 중 태그 대상 2291곳` 근처 (2026-11-09 조회값. 관광공사 데이터 변경에 따라 조금 다를 수 있음). DB 는 바꾸지 않는다. 키가 없으면 이 단계는 건너뛰고 PR 에 적는다.

- [ ] **Step 6: Commit**

```bash
git add db/tools/build_poi_tags_sql.py tests/test_poi_tags_tool.py
git commit -m "feat: 장소 수집 스크립트가 place 에 저장 · 200건 단위 재시도

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 그래프 변환 (순수 함수)

**Files:**
- Create: `webapi/graph.py`
- Test: `tests/test_graph_build.py`

**Interfaces:**
- Consumes: `regions.views.TAG_LABELS` (태그 13개 → 한글 이름).
- Produces (later tasks rely on these exact names):
  - `GraphRows` dataclass: `regions: list[dict]` (키 `region_id, sido_name, sigungu_name, sigungu_code, lat, lng`), `dongs: list[dict]` (키 `dong_id, region_id, dong_name, adm_code, is_excluded, tags, lat, lng, grades`), `places: list[dict]` (키 `place_id, name, tag, lat, lng, dong_id, image_url`), `similar: list[tuple[int, int, int, float]]` (`dong_id, other_dong_id, rank, dist`)
  - `build_graph(rows: GraphRows) -> dict` → `{"nodes": [...], "edges": [...]}`. 노드 키는 `FIELDS[kind]` 순서 그대로, 동은 `grades` 추가. 선은 `{"from", "to", "type"}` + SIMILAR 만 `rank`, `dist`
  - `node_props(node: dict) -> dict`, `node_from_props(props: dict) -> dict`, `edge_props(edge: dict) -> dict`
  - 상수 `GRADE_KEYS`, `SIMILAR_TOP`, `HIDDEN_PLACE_TAGS`, `TAG_GROUP`, `FIELDS`, `LABEL`, `CREDIT`

- [ ] **Step 1: 실패하는 테스트**

`tests/test_graph_build.py`:

```python
"""탐색 지식그래프 변환 (webapi/graph.py). DB · Neo4j 없이 도는 순수 함수 테스트."""

from regions.views import TAG_LABELS
from webapi import graph
from webapi.graph import GraphRows, build_graph


def sample_rows() -> GraphRows:
    return GraphRows(
        regions=[
            {"region_id": 1, "sido_name": "강원특별자치도", "sigungu_name": "고성군",
             "sigungu_code": "51820", "lat": 38.38, "lng": 128.46},
            {"region_id": 2, "sido_name": "강원특별자치도", "sigungu_name": "강릉시",
             "sigungu_code": "51150", "lat": 37.75, "lng": 128.88},
        ],
        dongs=[
            {"dong_id": 101, "region_id": 1, "dong_name": "거진읍", "adm_code": "5182025300",
             "is_excluded": False, "tags": ["coast", "cafe"], "lat": 38.44, "lng": 128.45,
             "grades": {"quiet": "S", "peer": "B"}},
            {"dong_id": 102, "region_id": 1, "dong_name": "수동면", "adm_code": None,
             "is_excluded": True, "tags": [], "lat": None, "lng": None, "grades": {}},
            {"dong_id": 201, "region_id": 2, "dong_name": "경포동", "adm_code": "5115060500",
             "is_excluded": False, "tags": ["cafe"], "lat": 37.79, "lng": 128.9, "grades": {}},
        ],
        places=[
            {"place_id": 11, "name": "카페A", "tag": "cafe", "lat": 38.441, "lng": 128.451,
             "dong_id": 101, "image_url": "http://img/a.jpg"},
            {"place_id": 12, "name": "식당B", "tag": "food", "lat": 38.442, "lng": 128.452,
             "dong_id": 101, "image_url": None},
            {"place_id": 13, "name": "카페C", "tag": "cafe", "lat": 38.2, "lng": 128.3,
             "dong_id": 102, "image_url": None},
        ],
        similar=[(101, 201, 1, 0.12341), (201, 101, 1, 0.12341), (101, 201, 6, 0.9)],
    )


def ids(g, kind):
    return {n["id"] for n in g["nodes"] if n["kind"] == kind}


def edges(g, type_):
    return [(e["from"], e["to"]) for e in g["edges"] if e["type"] == type_]


def test_hierarchy_sido_region_dong():
    g = build_graph(sample_rows())
    assert ids(g, "sido") == {"sido:강원특별자치도"}
    assert ids(g, "region") == {"region:51820", "region:51150"}
    assert ("region:51820", "sido:강원특별자치도") in edges(g, "IN")
    assert ("dong:5182025300", "region:51820") in edges(g, "IN")


def test_excluded_dong_and_its_places_are_dropped():
    g = build_graph(sample_rows())
    assert ids(g, "dong") == {"dong:5182025300", "dong:5115060500"}
    assert "place:13" not in ids(g, "place")


def test_food_place_hidden_but_food_tag_node_kept():
    g = build_graph(sample_rows())
    assert ids(g, "place") == {"place:11"}
    assert "tag:food" in ids(g, "tag")
    assert len(ids(g, "tag")) == len(TAG_LABELS)


def test_place_edges_and_fields():
    g = build_graph(sample_rows())
    assert edges(g, "LOCATED_IN") == [("place:11", "dong:5182025300")]
    assert edges(g, "EVIDENCE_OF") == [("place:11", "tag:cafe")]
    p = next(n for n in g["nodes"] if n["id"] == "place:11")
    assert p == {"id": "place:11", "kind": "place", "name": "카페A", "lat": 38.441, "lng": 128.451,
                 "tag": "cafe", "image": "http://img/a.jpg", "credit": "한국관광공사"}


def test_dong_tags_and_grades_with_nulls():
    g = build_graph(sample_rows())
    assert set(edges(g, "HAS_TAG")) == {
        ("dong:5182025300", "tag:coast"), ("dong:5182025300", "tag:cafe"),
        ("dong:5115060500", "tag:cafe"),
    }
    d = next(n for n in g["nodes"] if n["id"] == "dong:5115060500")
    assert d["grades"] == {"quiet": None, "peer": None, "evening": None}


def test_similar_keeps_top5_and_rounds_dist():
    g = build_graph(sample_rows())
    sim = [e for e in g["edges"] if e["type"] == "SIMILAR"]
    assert len(sim) == 2
    assert sim[0] == {"from": "dong:5182025300", "to": "dong:5115060500", "type": "SIMILAR",
                      "rank": 1, "dist": 0.1234}


def test_every_tag_has_group():
    assert set(graph.TAG_GROUP) == set(TAG_LABELS)


def test_neo4j_props_round_trip():
    g = build_graph(sample_rows())
    for n in g["nodes"]:
        props = graph.node_props(n)
        assert all(not isinstance(v, dict) for v in props.values())  # Neo4j 속성에 맵 불가
        assert None not in props.values()
        assert graph.node_from_props(props) == n
    dong = next(n for n in g["nodes"] if n["id"] == "dong:5182025300")
    assert graph.node_props(dong)["grade_quiet"] == "S"
    sim = next(e for e in g["edges"] if e["type"] == "SIMILAR")
    assert graph.edge_props(sim) == {"rank": 1, "dist": 0.1234}
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_graph_build.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'webapi.graph'`)

- [ ] **Step 3: `webapi/graph.py` 작성 (변환 부분)**

```python
"""탐색 지식그래프 (/explore). 설계: docs/superpowers/specs/2026-11-09-explore-graph-design.md

Postgres 가 원본이고 Neo4j 는 읽기 전용 복사본이다.
  load_rows()    Postgres → GraphRows                 (manage.py sync_graph)
  build_graph()  GraphRows → {nodes, edges}            순수 함수. API 응답과 같은 모양
  write_graph()  {nodes, edges} → Neo4j               전체 삭제 후 재적재, 한 트랜잭션
  fetch_graph()  Neo4j → {syncedAt, nodes, edges}     GET /api/explore/graph
Neo4j 속성에는 맵을 넣을 수 없어 동 등급은 grade_quiet 처럼 펼쳐 저장하고 읽을 때 되돌린다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from regions.views import TAG_LABELS

logger = logging.getLogger(__name__)

GRADE_KEYS = ("quiet", "peer", "evening")
SIMILAR_TOP = 5
HIDDEN_PLACE_TAGS = {"food"}  # 관광 맛집이라 워케이션 판단과 거리가 멀다. place 테이블에는 있다
CREDIT = "한국관광공사"
TAG_GROUP = {
    "cafe": "work",
    "library": "work",
    "long_stay": "stay",
    "resort": "stay",
    "beach": "rest",
    "park": "rest",
    "mountain": "rest",
    "island": "rest",
    "surf": "rest",
    "market": "life",
    "food": "life",
    "coast": "terrain",
    "city": "terrain",
}
# 종류별 노드 키 (API 응답 순서). Neo4j 는 null 속성을 저장하지 않아 읽을 때 이 목록으로 채운다
FIELDS = {
    "sido": ("id", "kind", "name"),
    "region": ("id", "kind", "name", "lat", "lng"),
    "dong": ("id", "kind", "name", "lat", "lng"),
    "tag": ("id", "kind", "name", "group"),
    "place": ("id", "kind", "name", "lat", "lng", "tag", "image", "credit"),
}
LABEL = {"sido": "Sido", "region": "Region", "dong": "Dong", "tag": "Tag", "place": "Place"}


@dataclass
class GraphRows:
    regions: list[dict] = field(default_factory=list)
    dongs: list[dict] = field(default_factory=list)
    places: list[dict] = field(default_factory=list)
    similar: list[tuple[int, int, int, float]] = field(default_factory=list)


def build_graph(rows: GraphRows) -> dict:
    nodes: list[dict] = []
    edges: list[dict] = []
    for sido in sorted({r["sido_name"] for r in rows.regions}):
        nodes.append({"id": f"sido:{sido}", "kind": "sido", "name": sido})

    region_key: dict[int, str] = {}
    for r in rows.regions:
        if not r["sigungu_code"]:
            continue
        rid = f"region:{r['sigungu_code']}"
        region_key[r["region_id"]] = rid
        nodes.append(
            {"id": rid, "kind": "region", "name": r["sigungu_name"], "lat": r["lat"], "lng": r["lng"]}
        )
        edges.append({"from": rid, "to": f"sido:{r['sido_name']}", "type": "IN"})

    for key, label in TAG_LABELS.items():
        nodes.append({"id": f"tag:{key}", "kind": "tag", "name": label, "group": TAG_GROUP[key]})

    dong_key: dict[int, str] = {}
    for d in rows.dongs:
        if d["is_excluded"] or not d["adm_code"] or d["region_id"] not in region_key:
            continue
        did = f"dong:{d['adm_code']}"
        dong_key[d["dong_id"]] = did
        nodes.append(
            {
                "id": did,
                "kind": "dong",
                "name": d["dong_name"],
                "lat": d["lat"],
                "lng": d["lng"],
                "grades": {k: d["grades"].get(k) for k in GRADE_KEYS},
            }
        )
        edges.append({"from": did, "to": region_key[d["region_id"]], "type": "IN"})
        for t in d["tags"]:
            if t in TAG_LABELS:
                edges.append({"from": did, "to": f"tag:{t}", "type": "HAS_TAG"})

    for p in rows.places:
        if p["tag"] in HIDDEN_PLACE_TAGS or p["tag"] not in TAG_LABELS:
            continue
        if p["dong_id"] not in dong_key:
            continue
        pid = f"place:{p['place_id']}"
        nodes.append(
            {
                "id": pid,
                "kind": "place",
                "name": p["name"],
                "lat": p["lat"],
                "lng": p["lng"],
                "tag": p["tag"],
                "image": p["image_url"],
                "credit": CREDIT,
            }
        )
        edges.append({"from": pid, "to": dong_key[p["dong_id"]], "type": "LOCATED_IN"})
        edges.append({"from": pid, "to": f"tag:{p['tag']}", "type": "EVIDENCE_OF"})

    for a, b, rank, dist in rows.similar:
        if rank <= SIMILAR_TOP and a in dong_key and b in dong_key:
            edges.append(
                {
                    "from": dong_key[a],
                    "to": dong_key[b],
                    "type": "SIMILAR",
                    "rank": rank,
                    "dist": round(dist, 4),
                }
            )
    return {"nodes": nodes, "edges": edges}


def node_props(node: dict) -> dict:
    """Neo4j 노드 속성. null 은 빼고, 동 등급 맵은 grade_<지표> 로 펼친다."""
    props = {k: v for k, v in node.items() if k != "grades" and v is not None}
    for key, grade in (node.get("grades") or {}).items():
        if grade is not None:
            props[f"grade_{key}"] = grade
    return props


def node_from_props(props: dict) -> dict:
    node = {k: props.get(k) for k in FIELDS[props["kind"]]}
    if props["kind"] == "dong":
        node["grades"] = {k: props.get(f"grade_{k}") for k in GRADE_KEYS}
    return node


def edge_props(edge: dict) -> dict:
    return {k: v for k, v in edge.items() if k not in ("from", "to", "type")}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_graph_build.py -v`
Expected: 8 passed

- [ ] **Step 5: 린트 · 포맷**

Run: `uv run ruff check webapi tests && uv run ruff format webapi tests`
Expected: `All checks passed!` (format 이 테스트 파일의 dict 줄바꿈을 고칠 수 있다. 그대로 둔다)

- [ ] **Step 6: Commit**

```bash
git add webapi/graph.py tests/test_graph_build.py
git commit -m "feat: 탐색 지식그래프 변환 build_graph (도 · 시군구 · 동 · 태그 · 장소 · 비슷한 동)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Postgres 에서 그래프 재료 읽기 (`load_rows`)

**Files:**
- Modify: `webapi/graph.py`
- Test: `tests/test_graph_build.py` (끝에 추가)

**Interfaces:**
- Consumes: Task 1 `place`, Task 3 `GraphRows`, `GRADE_KEYS`, `SIMILAR_TOP`. 모델 `regions.models.Region`, `regions.models.Dong`, `metrics.models.Metric` (`metric_def_id` 가 지표 키).
- Produces: `load_rows() -> GraphRows`. 좌표는 PostGIS `ST_PointOnSurface`, 비슷한 동은 pgvector. 둘 다 없으면(테스트 DB) 좌표 `None`, `similar == []`.

- [ ] **Step 1: 실패하는 테스트**

`tests/test_graph_build.py` 맨 위 import 를 다음으로 바꾼다.

```python
from django.db import connection

from metrics.models import Metric, MetricDef
from regions.models import Dong, Region
from regions.views import TAG_LABELS
from webapi import graph
from webapi.graph import GraphRows, build_graph
```

그리고 파일 끝에 추가:

```python
def test_load_rows_reads_postgres_without_postgis(db):
    for key in ("quiet", "peer", "evening"):
        MetricDef.objects.create(metric_key=key, name_ko=key, higher_is="good")
    goseong = Region.objects.create(
        region_id=1, sido_name="강원특별자치도", sigungu_name="고성군", sigungu_code="51820"
    )
    Dong.objects.create(
        dong_id=101, region=goseong, dong_name="거진읍", adm_code="5182025300", tags=["cafe"]
    )
    Metric.objects.create(scope="dong", unit_id=101, month=0, metric_def_id="quiet", grade="S")
    Metric.objects.create(scope="dong", unit_id=101, month=10, metric_def_id="peer", grade="A")
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO place (place_id, name, tag, lng, lat, dong_id, image_url)"
            " VALUES (11, '카페A', 'cafe', 128.45, 38.44, 101, NULL)"
        )

    rows = graph.load_rows()

    assert rows.regions == [
        {"region_id": 1, "sido_name": "강원특별자치도", "sigungu_name": "고성군",
         "sigungu_code": "51820", "lat": None, "lng": None}
    ]
    dong = rows.dongs[0]
    assert dong["grades"] == {"quiet": "S"}  # 연간(month 0) 등급만
    assert dong["lat"] is None and dong["tags"] == ["cafe"] and dong["is_excluded"] is False
    assert rows.places == [
        {"place_id": 11, "name": "카페A", "tag": "cafe", "lat": 38.44, "lng": 128.45,
         "dong_id": 101, "image_url": None}
    ]
    assert rows.similar == []  # 테스트 DB 에는 dong_feature 가 없다
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_graph_build.py::test_load_rows_reads_postgres_without_postgis -v`
Expected: FAIL (`AttributeError: module 'webapi.graph' has no attribute 'load_rows'`)

- [ ] **Step 3: `webapi/graph.py` 에 구현 추가**

import 블록을 다음으로 바꾼다.

```python
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

from django.db import DatabaseError, connection, transaction

from metrics.models import Metric
from regions.models import Dong, Region
from regions.views import TAG_LABELS
```

`edge_props` 아래에 추가:

```python
# ───────────────────────── Postgres 읽기 ─────────────────────────

DONG_POINT_SQL = """
    SELECT dong_id, ST_Y(p), ST_X(p)
      FROM (SELECT dong_id, ST_PointOnSurface(geom) AS p FROM dong WHERE geom IS NOT NULL) s"""
REGION_POINT_SQL = """
    SELECT region_id, ST_Y(p), ST_X(p)
      FROM (SELECT region_id, ST_PointOnSurface(ST_Union(geom)) AS p
              FROM dong WHERE geom IS NOT NULL GROUP BY region_id) s"""
# 동마다 similar_dongs 를 부르지 않고 같은 거리식(연간 metric_vec <->)으로 한 번에 상위 N
SIMILAR_SQL = """
    SELECT a.dong_id, s.dong_id, s.rank::int, s.dist
      FROM dong_feature a
      JOIN dong da ON da.dong_id = a.dong_id AND NOT da.is_excluded
      CROSS JOIN LATERAL (
        SELECT b.dong_id, (b.metric_vec <-> a.metric_vec)::float AS dist,
               row_number() OVER (ORDER BY b.metric_vec <-> a.metric_vec) AS rank
          FROM dong_feature b
          JOIN dong dn ON dn.dong_id = b.dong_id AND NOT dn.is_excluded
         WHERE b.month = 0 AND b.dong_id <> a.dong_id
         ORDER BY b.metric_vec <-> a.metric_vec
         LIMIT %s) s
     WHERE a.month = 0"""
PLACE_SQL = "SELECT place_id, name, tag, lat, lng, dong_id, image_url FROM place ORDER BY place_id"


def _optional(sql: str, params: list | None = None) -> list[tuple]:
    """PostGIS · pgvector 가 필요한 조회. 테스트 DB 처럼 없으면 빈 목록."""
    try:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute(sql, params or [])
            return cur.fetchall()
    except DatabaseError:
        logger.warning("PostGIS · pgvector 없음: 좌표 또는 비슷한 동을 비운다")
        return []


def _points(sql: str) -> dict[int, tuple[float, float]]:
    return {k: (round(lat, 5), round(lng, 5)) for k, lat, lng in _optional(sql)}


def load_rows() -> GraphRows:
    region_pt = _points(REGION_POINT_SQL)
    dong_pt = _points(DONG_POINT_SQL)
    grades: dict[int, dict[str, str]] = defaultdict(dict)
    for unit_id, key, grade in Metric.objects.filter(
        scope="dong", month=0, metric_def_id__in=GRADE_KEYS, grade__isnull=False
    ).values_list("unit_id", "metric_def_id", "grade"):
        grades[unit_id][key] = grade

    rows = GraphRows()
    for r in Region.objects.order_by("region_id").values(
        "region_id", "sido_name", "sigungu_name", "sigungu_code"
    ):
        lat, lng = region_pt.get(r["region_id"], (None, None))
        rows.regions.append({**r, "lat": lat, "lng": lng})
    for d in Dong.objects.order_by("dong_id").values(
        "dong_id", "region_id", "dong_name", "adm_code", "is_excluded", "tags"
    ):
        lat, lng = dong_pt.get(d["dong_id"], (None, None))
        rows.dongs.append({**d, "lat": lat, "lng": lng, "grades": grades.get(d["dong_id"], {})})
    with connection.cursor() as cur:
        cur.execute(PLACE_SQL)
        cols = [c.name for c in cur.description]
        rows.places = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    rows.similar = [tuple(r) for r in _optional(SIMILAR_SQL, [SIMILAR_TOP])]
    return rows
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_graph_build.py -v`
Expected: 9 passed

- [ ] **Step 5: 린트 · 포맷 후 Commit**

```bash
uv run ruff check . && uv run ruff format .
git add webapi/graph.py tests/test_graph_build.py
git commit -m "feat: 탐색 그래프 재료를 Postgres 에서 읽는 load_rows

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Neo4j 쓰기 · 읽기와 `sync_graph` 명령

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add neo4j`), `config/settings.py`, `config/settings_test.py`, `.env.example`, `webapi/graph.py`
- Create: `webapi/management/__init__.py`, `webapi/management/commands/__init__.py`, `webapi/management/commands/sync_graph.py`
- Test: `tests/test_graph_sync.py`

**Interfaces:**
- Consumes: Task 3 `build_graph`, `node_props`, `node_from_props`, `edge_props`, `LABEL`; Task 4 `load_rows`.
- Produces:
  - `class GraphUnavailable(Exception)`
  - `open_driver()` → neo4j `Driver` (설정 없으면 `GraphUnavailable`)
  - `write_graph(graph: dict, driver, synced_at: str) -> None`
  - `records_to_graph(synced_at: str, node_rows: list[dict], rel_rows: list[tuple[str, str, str, dict]]) -> dict`
  - `fetch_graph() -> dict` → `{"syncedAt", "nodes", "edges"}`. 실패는 전부 `GraphUnavailable`
  - 설정 `settings.NEO4J_URI`, `settings.NEO4J_USER`, `settings.NEO4J_PASSWORD`

- [ ] **Step 1: 의존성과 설정**

Run: `uv add neo4j`
Expected: `pyproject.toml` dependencies 에 `"neo4j>=…"` 추가, `uv.lock` 갱신.

`config/settings.py` 의 `CLOVA_API_KEY = …` 줄 다음에 추가:

```python

# 탐색 지식그래프 (Neo4j AuraDB, 읽기 전용 복사본). 비어 있으면 /api/explore/graph 가 503
NEO4J_URI = env("NEO4J_URI", default="")
NEO4J_USER = env("NEO4J_USER", default="neo4j")
NEO4J_PASSWORD = env("NEO4J_PASSWORD", default="")
```

`config/settings_test.py` 끝에 추가:

```python

# .env 에 Aura 접속 정보가 있어도 테스트는 Neo4j 에 붙지 않는다
NEO4J_URI = ""
```

`.env.example` 의 `CLOVA_API_KEY=` 줄 다음에 추가:

```
# 탐색 지식그래프 Neo4j AuraDB (팀 인스턴스). 없으면 /api/explore/graph 가 503
NEO4J_URI=
NEO4J_USER=neo4j
NEO4J_PASSWORD=

# 관광공사 TourAPI (db/tools/build_poi_tags_sql.py 용, 서버는 쓰지 않음). data.go.kr "한국관광공사_국문 관광정보 서비스_GW" 일반 인증키(Decoding)
TOURAPI_KEY=
```

- [ ] **Step 2: 실패하는 테스트**

`tests/test_graph_sync.py`:

```python
"""Neo4j 쓰기 · 읽기 · sync_graph 명령. 실제 Neo4j 대신 쿼리를 기록하는 가짜 드라이버를 쓴다."""

from io import StringIO

import pytest
from django.core.management import call_command
from neo4j.exceptions import ServiceUnavailable

from tests.test_graph_build import sample_rows
from webapi import graph


class FakeTx:
    def __init__(self, log):
        self.log = log

    def run(self, query, **params):
        self.log.append((query, params))
        return self

    def consume(self):
        return None


class FakeSession:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_write(self, work):
        return work(FakeTx(self.log))


class FakeDriver:
    """execute_query 는 answers 의 앞에서부터 하나씩 돌려준다."""

    def __init__(self, answers=None, fail=None):
        self.log, self.answers, self.fail = [], list(answers or []), fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_query(self, query, **params):
        if self.fail:
            raise self.fail
        self.log.append((query, params))
        return (self.answers.pop(0) if self.answers else []), None, None

    def session(self):
        return FakeSession(self.log)


def test_write_graph_replaces_everything_in_order():
    g = graph.build_graph(sample_rows())
    driver = FakeDriver()
    graph.write_graph(g, driver, "2026-11-09T10:00:00+09:00")
    queries = [q for q, _ in driver.log]
    assert "CREATE INDEX node_id IF NOT EXISTS" in queries[0]
    assert queries[1] == "MATCH (n) DETACH DELETE n"
    assert queries[-1] == "CREATE (:Meta {synced_at: $at})"
    dong_q, dong_p = next((q, p) for q, p in driver.log if ":Node:Dong" in q)
    assert {r["id"] for r in dong_p["rows"]} == {"dong:5182025300", "dong:5115060500"}
    assert next(r for r in dong_p["rows"] if r["id"] == "dong:5182025300")["grade_quiet"] == "S"
    sim_q, sim_p = next((q, p) for q, p in driver.log if "[r:SIMILAR]" in q)
    assert sim_p["rows"][0] == {
        "src": "dong:5182025300",
        "dst": "dong:5115060500",
        "props": {"rank": 1, "dist": 0.1234},
    }


def test_records_to_graph_restores_api_shape():
    out = graph.records_to_graph(
        "2026-11-09T10:00:00+09:00",
        [{"id": "dong:1", "kind": "dong", "name": "가", "grade_peer": "A"}],
        [("dong:1", "dong:2", "SIMILAR", {"rank": 1, "dist": 0.1})],
    )
    assert out == {
        "syncedAt": "2026-11-09T10:00:00+09:00",
        "nodes": [
            {"id": "dong:1", "kind": "dong", "name": "가", "lat": None, "lng": None,
             "grades": {"quiet": None, "peer": "A", "evening": None}}
        ],
        "edges": [{"from": "dong:1", "to": "dong:2", "type": "SIMILAR", "rank": 1, "dist": 0.1}],
    }


def test_fetch_graph_reads_meta_nodes_and_relations(monkeypatch):
    driver = FakeDriver(
        answers=[
            [{"at": "2026-11-09T10:00:00+09:00"}],
            [{"p": {"id": "tag:cafe", "kind": "tag", "name": "카페 많음", "group": "work"}}],
            [{"src": "dong:1", "dst": "tag:cafe", "type": "HAS_TAG", "p": {}}],
        ]
    )
    monkeypatch.setattr(graph, "open_driver", lambda: driver)
    out = graph.fetch_graph()
    assert out["syncedAt"] == "2026-11-09T10:00:00+09:00"
    assert out["nodes"] == [{"id": "tag:cafe", "kind": "tag", "name": "카페 많음", "group": "work"}]
    assert out["edges"] == [{"from": "dong:1", "to": "tag:cafe", "type": "HAS_TAG"}]


def test_fetch_graph_empty_neo4j_is_unavailable(monkeypatch):
    monkeypatch.setattr(graph, "open_driver", lambda: FakeDriver(answers=[[]]))
    with pytest.raises(graph.GraphUnavailable, match="sync_graph"):
        graph.fetch_graph()


def test_fetch_graph_connection_error_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        graph, "open_driver", lambda: FakeDriver(fail=ServiceUnavailable("paused"))
    )
    with pytest.raises(graph.GraphUnavailable):
        graph.fetch_graph()


def test_open_driver_without_uri_is_unavailable(settings):
    settings.NEO4J_URI = ""
    with pytest.raises(graph.GraphUnavailable, match="NEO4J_URI"):
        graph.open_driver()


def test_sync_graph_command(monkeypatch):
    driver = FakeDriver()
    monkeypatch.setattr(graph, "load_rows", sample_rows)
    monkeypatch.setattr(graph, "open_driver", lambda: driver)
    out = StringIO()
    call_command("sync_graph", stdout=out)
    text = out.getvalue()
    assert "Dong 2" in text and "Place 1" in text and "SIMILAR 2" in text
    assert any(q == "CREATE (:Meta {synced_at: $at})" for q, _ in driver.log)
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_graph_sync.py -v`
Expected: FAIL (`AttributeError: module 'webapi.graph' has no attribute 'write_graph'`, 명령 없음 `Unknown command: 'sync_graph'`)

- [ ] **Step 4: `webapi/graph.py` 에 Neo4j 부분 추가**

import 블록에 추가 (알파벳 순서는 `ruff format`/`ruff check --fix` 가 맞춘다):

```python
from django.conf import settings
from neo4j import GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError
```

`LABEL = …` 아래에 추가:

```python
UNAVAILABLE_EMPTY = "Neo4j 가 비어 있음: uv run python manage.py sync_graph 실행 필요"


class GraphUnavailable(Exception):
    """Neo4j 미설정 · 연결 실패 · 동기화 전. API 는 503 으로 바꾼다."""
```

파일 끝에 추가:

```python
# ───────────────────────── Neo4j ─────────────────────────


def open_driver():
    if not settings.NEO4J_URI:
        raise GraphUnavailable("NEO4J_URI 미설정")
    return GraphDatabase.driver(
        settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )


def write_graph(graph: dict, driver, synced_at: str) -> None:
    """전체 삭제 후 재적재. 한 트랜잭션이라 중간에 실패하면 이전 그래프가 남는다."""
    driver.execute_query("CREATE INDEX node_id IF NOT EXISTS FOR (n:Node) ON (n.id)")
    nodes: dict[str, list[dict]] = defaultdict(list)
    for n in graph["nodes"]:
        nodes[n["kind"]].append(node_props(n))
    rels: dict[str, list[dict]] = defaultdict(list)
    for e in graph["edges"]:
        rels[e["type"]].append({"src": e["from"], "dst": e["to"], "props": edge_props(e)})

    def work(tx):
        tx.run("MATCH (n) DETACH DELETE n").consume()
        for kind, rows in nodes.items():
            tx.run(
                f"UNWIND $rows AS row CREATE (n:Node:{LABEL[kind]}) SET n = row", rows=rows
            ).consume()
        for rel, rows in rels.items():
            tx.run(
                "UNWIND $rows AS row"
                " MATCH (a:Node {id: row.src}), (b:Node {id: row.dst})"
                f" CREATE (a)-[r:{rel}]->(b) SET r = row.props",
                rows=rows,
            ).consume()
        tx.run("CREATE (:Meta {synced_at: $at})", at=synced_at).consume()

    with driver.session() as session:
        session.execute_write(work)


def records_to_graph(
    synced_at: str, node_rows: list[dict], rel_rows: list[tuple[str, str, str, dict]]
) -> dict:
    nodes = sorted((node_from_props(p) for p in node_rows), key=lambda n: n["id"])
    edges = [{"from": s, "to": d, "type": t, **p} for s, d, t, p in rel_rows]
    return {"syncedAt": synced_at, "nodes": nodes, "edges": edges}


def fetch_graph() -> dict:
    try:
        with open_driver() as driver:
            meta, _, _ = driver.execute_query("MATCH (m:Meta) RETURN m.synced_at AS at")
            if not meta:
                raise GraphUnavailable(UNAVAILABLE_EMPTY)
            nodes, _, _ = driver.execute_query("MATCH (n:Node) RETURN properties(n) AS p")
            rels, _, _ = driver.execute_query(
                "MATCH (a:Node)-[r]->(b:Node)"
                " RETURN a.id AS src, b.id AS dst, type(r) AS type, properties(r) AS p"
            )
    except (DriverError, Neo4jError) as ex:  # 일시정지 · 인증 실패 · 주소 오류
        raise GraphUnavailable(str(ex)) from ex
    return records_to_graph(
        meta[0]["at"],
        [r["p"] for r in nodes],
        [(r["src"], r["dst"], r["type"], r["p"]) for r in rels],
    )
```

- [ ] **Step 5: 명령 작성**

빈 파일 `webapi/management/__init__.py`, `webapi/management/commands/__init__.py` 를 만든다.

`webapi/management/commands/sync_graph.py`:

```python
"""Postgres → Neo4j 탐색 지식그래프 동기화. 지표 · 태그 · 장소를 다시 적재한 뒤 실행한다.

uv run python manage.py sync_graph
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from neo4j.exceptions import DriverError, Neo4jError

from webapi import graph


class Command(BaseCommand):
    help = "Postgres → Neo4j 탐색 지식그래프 동기화 (전체 삭제 후 재적재)"

    def handle(self, *args, **options):
        g = graph.build_graph(graph.load_rows())
        synced_at = timezone.localtime().isoformat(timespec="seconds")
        try:
            with graph.open_driver() as driver:
                graph.write_graph(g, driver, synced_at)
        except (graph.GraphUnavailable, DriverError, Neo4jError) as ex:
            raise CommandError(f"Neo4j 쓰기 실패: {ex}") from ex
        self.stdout.write(f"동기화 {synced_at}")
        for kind, n in Counter(n["kind"] for n in g["nodes"]).items():
            self.stdout.write(f"  {graph.LABEL[kind]} {n}")
        for rel, n in Counter(e["type"] for e in g["edges"]).items():
            self.stdout.write(f"  {rel} {n}")
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_graph_sync.py tests/test_graph_build.py -v`
Expected: 16 passed

- [ ] **Step 7: 린트 · 포맷 후 Commit**

```bash
uv run ruff check . && uv run ruff format .
git add pyproject.toml uv.lock config/settings.py config/settings_test.py .env.example \
  webapi/graph.py webapi/management tests/test_graph_sync.py
git commit -m "feat: Neo4j 쓰기 · 읽기와 manage.py sync_graph

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `GET /api/explore/graph` (캐시 · 503 · gzip)

**Files:**
- Modify: `webapi/views.py`, `webapi/urls.py`, `config/settings.py`
- Test: `tests/test_explore_api.py`

**Interfaces:**
- Consumes: Task 5 `graph.fetch_graph()`, `graph.GraphUnavailable`; `core.errors.error(status, code, message)`.
- Produces: URL name `web-explore-graph`, 경로 `/api/explore/graph`. 캐시 키 `explore_graph`(1시간), `explore_graph_stale`(만료 없음).

- [ ] **Step 1: 실패하는 테스트**

`tests/test_explore_api.py`:

```python
"""GET /api/explore/graph. fetch_graph 를 바꿔 끼워 Neo4j 없이 본다."""

import pytest
from django.core.cache import cache

from webapi import graph

SAMPLE = {
    "syncedAt": "2026-11-09T10:00:00+09:00",
    "nodes": [
        {"id": f"tag:t{i}", "kind": "tag", "name": f"태그{i}", "group": "work"} for i in range(20)
    ],
    "edges": [],
}


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def unavailable():
    raise graph.GraphUnavailable("paused")


def test_returns_graph(client, monkeypatch):
    monkeypatch.setattr(graph, "fetch_graph", lambda: SAMPLE)
    res = client.get("/api/explore/graph")
    assert res.status_code == 200
    assert res.json() == SAMPLE


def test_second_call_uses_cache(client, monkeypatch):
    calls = []
    monkeypatch.setattr(graph, "fetch_graph", lambda: calls.append(1) or SAMPLE)
    client.get("/api/explore/graph")
    client.get("/api/explore/graph")
    assert calls == [1]


def test_falls_back_to_last_graph_when_neo4j_down(client, monkeypatch):
    monkeypatch.setattr(graph, "fetch_graph", lambda: SAMPLE)
    client.get("/api/explore/graph")
    cache.delete("explore_graph")  # 1시간 캐시 만료와 같은 상황
    monkeypatch.setattr(graph, "fetch_graph", unavailable)
    res = client.get("/api/explore/graph")
    assert res.status_code == 200 and res.json() == SAMPLE


def test_503_when_neo4j_down_and_no_cache(client, monkeypatch):
    monkeypatch.setattr(graph, "fetch_graph", unavailable)
    res = client.get("/api/explore/graph")
    assert res.status_code == 503
    assert res.json() == {
        "error": {"code": "GRAPH_UNAVAILABLE", "message": "탐색 데이터를 불러오지 못했어요."}
    }


def test_response_is_gzipped(client, monkeypatch):
    monkeypatch.setattr(graph, "fetch_graph", lambda: SAMPLE)
    res = client.get("/api/explore/graph", HTTP_ACCEPT_ENCODING="gzip")
    assert res["Content-Encoding"] == "gzip"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_api.py -v`
Expected: FAIL (404 — 경로 없음)

- [ ] **Step 3: 뷰 · URL · GZip**

`webapi/views.py` import 블록을 다음과 같이 바꾼다 (`logging`, `cache`, `graph` 추가).

```python
from __future__ import annotations

import logging
import re
from datetime import date
from statistics import mean

from django.core.cache import cache
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import bad_request, error, not_found
from metrics import scoring
from regions.models import DayGroup, Dong, DongHourly, Region
from regions.photos import dong_photos, region_photos
from search import engine
from webapi import data, graph

logger = logging.getLogger(__name__)
```

(기존 파일에 `from webapi import data` 가 있으면 위처럼 `data, graph` 로 합친다. 다른 기존 import 는 그대로 둔다.)

파일 끝에 추가:

```python
# ───────────────────────── /explore 탐색 지식그래프 ─────────────────────────

GRAPH_CACHE = "explore_graph"
GRAPH_STALE = "explore_graph_stale"  # Neo4j 가 꺼졌을 때(Aura Free 일시정지) 내줄 마지막 그래프
GRAPH_TTL = 60 * 60


@api_view(["GET"])
def explore_graph(request):
    body = cache.get(GRAPH_CACHE)
    if body is None:
        try:
            body = graph.fetch_graph()
        except graph.GraphUnavailable as ex:
            logger.warning("탐색 그래프 조회 실패: %s", ex)
            body = cache.get(GRAPH_STALE)
            if body is None:
                return error(503, "GRAPH_UNAVAILABLE", "탐색 데이터를 불러오지 못했어요.")
            return Response(body)
        cache.set(GRAPH_CACHE, body, GRAPH_TTL)
        cache.set(GRAPH_STALE, body, None)
    return Response(body)
```

`webapi/urls.py` 의 `path("search/parse", …)` 다음 줄에 추가:

```python
    path("explore/graph", views.explore_graph, name="web-explore-graph"),
```

`config/settings.py` 의 `MIDDLEWARE` 맨 앞에 추가:

```python
MIDDLEWARE = [
    # 탐색 그래프처럼 큰 JSON 을 압축한다 (200바이트 이상, Accept-Encoding: gzip 일 때)
    "django.middleware.gzip.GZipMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_explore_api.py -v`
Expected: 5 passed

- [ ] **Step 5: 전체 검사**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`
Expected: 린트 통과, 전체 테스트 PASS (GZip 추가로 기존 테스트가 깨지지 않아야 한다. 테스트 클라이언트는 기본으로 Accept-Encoding 을 보내지 않는다)

- [ ] **Step 6: Commit**

```bash
git add webapi/views.py webapi/urls.py config/settings.py tests/test_explore_api.py
git commit -m "feat: GET /api/explore/graph — 1시간 캐시 · Neo4j 장애 시 마지막 그래프 · gzip

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 문서

**Files:**
- Modify: `db/README.md`, `docs/개발-가이드.md`

- [ ] **Step 1: `db/README.md`**

파일 표(`| 파일 | 용도 |`)의 `tools/build_poi_tags_sql.py` 행을 다음으로 바꾼다.

```markdown
| `tools/build_poi_tags_sql.py` | 관광공사 TourAPI 장소 → `place` 저장(태그 근거 장소, 경계 밖 제외) + 장소 태그(beach · island · surf · cafe · food · library · long_stay · park · market · resort) 갱신 SQL 생성. `TOURAPI_KEY` 필요. 200건씩 받고 3번 재시도 |
```

"적재 흐름" 코드 블록 바로 아래 목록 끝에 추가:

```markdown
- 탐색 지식그래프(Neo4j): 지표 · 태그 · 장소를 다시 넣은 뒤 백엔드에서 `uv run python manage.py sync_graph` 를 실행한다. Neo4j 는 읽기 전용 복사본이라 전부 지우고 다시 채운다(수 초). 설계: `docs/superpowers/specs/2026-11-09-explore-graph-design.md`
```

"적용 이력" 표 끝에 행을 추가한다 (Supabase 적용일 칸은 Task 8 에서 채운다).

```markdown
| `migrations/20261109_place.sql` + `build_poi_tags_sql.py apply` 출력 | `place` 테이블 추가, 태그 근거 장소 저장 · 태그를 `place` 에서 재계산 | (미적용) |
```

- [ ] **Step 2: `docs/개발-가이드.md`**

"구조" 표의 `| \`db/\` | …` 행 위에 추가:

```markdown
| `webapi/graph.py` | 탐색 지식그래프. Postgres 읽기 → 변환 → Neo4j 쓰기/읽기. 동기화는 `manage.py sync_graph` |
```

"화면 API" 표 아래 목록 끝에 추가:

```markdown
- 탐색 지식그래프 `GET /api/explore/graph` (`webapi/views.py`): Neo4j(AuraDB Free)에서 도 · 시군구 · 동 · 태그 · 장소 그래프를 한 번에 내려준다. 1시간 캐시, Neo4j 가 꺼지면 마지막 그래프, 그것도 없으면 503. `NEO4J_URI` 가 없으면 로컬에서도 503 이다. Aura Free 는 72시간 안 쓰면 일시정지되므로 시연 전 콘솔에서 재개한다.
```

- [ ] **Step 3: Commit**

```bash
git add db/README.md docs/개발-가이드.md
git commit -m "docs: 탐색 지식그래프 적재 순서 · 구조 · API 안내

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 운영 반영과 수동 검증 (사람이 실행 — Supabase · Aura 에 쓰는 작업)

에이전트는 이 Task 를 실행하지 않는다. 명령과 확인 항목을 PR 본문 체크리스트로 옮기고, 팀(데이터 담당 또는 사용자)이 실행한다.

- [ ] **Step 1: Supabase 에 migration 적용** — SQL Editor 에서 `db/migrations/20261109_place.sql` 실행.
- [ ] **Step 2: 장소 저장** — `python3 db/tools/build_poi_tags_sql.py apply /tmp/poi_apply.sql` → SQL Editor 에서 실행. 이어서 확인:

```sql
SELECT count(*) FROM place;                                   -- 태그 대상 2,291 중 경계 안 개수
SELECT tag, count(*) FROM place GROUP BY tag ORDER BY 2 DESC;
SELECT t, count(*) FROM dong, unnest(tags) t GROUP BY t ORDER BY 2 DESC;  -- README 2026-11-07 값과 비교
```

경계 밖으로 빠진 수(2,291 − place 수)와 태그별 동 수 변화를 `db/README.md` 에 기록하고, "적용 이력" 의 적용일을 채운다.
- [ ] **Step 3: Aura 인스턴스** — console.neo4j.io 에서 AuraDB Free 생성, 접속 정보를 `.env` 의 `NEO4J_URI` · `NEO4J_USER` · `NEO4J_PASSWORD` 에 넣는다 (팀에는 안전한 경로로만 공유).
- [ ] **Step 4: 동기화** — `uv run python manage.py sync_graph`. 출력의 라벨별 노드 수(Sido 4 · Region 12 · Dong 203 · Tag 13 · Place 약 1,000)와 관계 수를 PR 에 붙인다.
- [ ] **Step 5: API 실측**

```bash
uv run python manage.py runserver &
curl -s -o /tmp/g.json -w '원본 %{size_download}B %{time_total}s\n' localhost:8000/api/explore/graph
curl -s -o /dev/null -H 'Accept-Encoding: gzip' -w 'gzip %{size_download}B %{time_total}s (캐시)\n' localhost:8000/api/explore/graph
```

목표: gzip 100KB 이하. 두 번째 호출(캐시)은 첫 호출보다 빠르다. 결과를 PR 에 붙인다.
- [ ] **Step 6: 이미지 URL 확인** — `/tmp/g.json` 의 `image` 가 `http://` 인지 확인하고 PR 에 적는다 (웹 https 에서 혼합 콘텐츠로 막히면 후속 이슈).
