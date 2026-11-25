# 또래 밀도 연령대 개인화 설계

- 작성일: 2026-11-23
- 상태: 구현 완료 (2026-11-23, 최종 리뷰 G3 통과. 통합 SHA 는 `docs/task-integration-ledger.tsv`)
- 범위 저장소: workation-backend (이 문서), workation-web
- 구현 계획: `docs/superpowers/plans/2026-11-23-peer-personalization.md`

## 설계 전제

2026-11-23 사용자 확인.

| 항목 | 내용 |
|---|---|
| 사용자 | 로그인한 방문자는 자기 연령대 기준의 또래 밀도로 점수·등급·순위를 본다. 로그인하지 않은 방문자는 지금처럼 20~40대 기준으로 본다. |
| 드러남 | 온보딩 모달의 연령대 휠(10대~90대, 필수)만 보인다. 개인화는 표시 없이 숫자만 바뀐다. "30대 기준" 같은 라벨은 넣지 않는다. |
| 범위 밖 | 마이페이지에서 연령대 수정 화면, 기존 가입자에게 연령을 다시 묻는 것, 또래 밀도 외 지표의 개인화. |
| 성공 지표 | 연령대가 다른 더미 계정으로 같은 화면 API 를 부르면 또래 밀도 점수가 그 연령대 방문 비율의 백분위(`metric` 의 `peer_<구간>` 점수)와 모든 단위에서 일치한다. 비로그인과 연령대 없는 계정의 응답은 지금(`peer`)과 같다. API 테스트와 공유 DB 더미 계정 실측 두 가지로 확인한다. |
| 더미 데이터 | 연령대가 다른 가짜 계정을 공유 Supabase 에 넣는다. 지울 수 있게 `provider = 'dummy'` 로 표시한다. |

## 1. 목표와 비범위

목표
1. 첫 로그인 온보딩에서 연령대(10대~90대 9개)를 필수로 받아 `app_user.age_group` 에 저장한다.
2. 로그인 사용자의 연령대가 있으면 웹 화면 API 의 또래 밀도(`peer`) 점수·등급·원값을 그 연령대 구간 값으로 바꿔 내려준다. 웹의 종합 점수·등급·순위는 웹이 응답의 `scores` 로 계산하므로(`src/features/popular/useWeights.ts`, `sort.ts`, 티어 재계산) 웹 계산 코드는 바꾸지 않아도 바뀐 값을 따른다.
3. 연령대별 더미 계정 10개(9개 연령대 + 연령대 없음 1개)를 공유 Supabase 에 만들고, 실서버에서 2번이 맞는지 자동으로 검증한다.

비범위
- 마이페이지 연령대 수정 화면. API(`PATCH /api/me`)는 `ageGroup` 변경을 받지만 화면은 만들지 않는다.
- 이미 약관에 동의한 기존 사용자에게 연령대를 다시 묻는 것. 이들은 `age_group = NULL` 이고 비로그인과 같은 20~40대 기준으로 본다.
- 앱용 이전 API(`metrics/views.py` 의 `/api/map/scores`·`/api/tiers`, `regions/views.py` 의 `/api/regions/<id>`·`/api/dongs/<id>`). 웹이 쓰지 않으므로 기본 기준 그대로 둔다. 그래서 `scoring.region_values`(이 API 들만 씀)도 바꾸지 않는다.
- 탐색 채팅·탐색 그래프(`explore/`)와 비슷한 동네(`webapi/data.py` `_load_similar`). Neo4j 복사본과 임베딩을 쓰며 사람마다 다르게 만들지 않는다.
- 또래 밀도 외 지표, 성별 개인화.
- 운영 서버 배포. 아직 서버가 없다(Traefik 설계 1단계 범위 밖). 이번 작업은 로컬 서버 + 공유 Supabase 기준이다.

## 2. 현재 구조 (2026-11-23 코드 기준)

- 또래 밀도 원값은 월 배치 SQL `db/03_metrics.sql` 이 `vist_2040 ÷ vist_known` 으로 행정동·시군구 × 월(0~12)마다 계산하고, 같은 scope·월 안의 백분위를 점수(0~100), 사분위를 등급으로 `metric` 표에 저장한다.
- 요청 시 `metrics/scoring.py` `unit_scores(scope, month)` 가 `metric` 을 읽어 `{unit_id: {metric_key: {"score","grade","raw"}}}` 를 만들고 `core/cache.py` `static` 캐시(`STATIC_TTL` 10분, Redis 또는 LocMem)에 `scores:{scope}:{m}` 키로 둔다.
- `dong_month_summary` 에 연령대별 방문 인구 `vist_age_under20`, `vist_age_20s`, `vist_age_30s`, `vist_age_40s`, `vist_age_50s`, `vist_age_60plus` 와 분모 `vist_known` 이 이미 있다(모두 NULL 허용).
- 웹이 부르는 점수 API: `webapi/views.py` 의 `tier`(`/api/tier`), `popular`(`/api/regions/popular`), `region_detail`(`/api/regions/<sgg>`), `dong_detail`(`/api/dongs/<dong>`), `webapi/me.py` `FavoritesView`(`/api/me/favorites`, 내부에서 `webapi/community.py` `favorites`).
- 인증: `accounts/authentication.py` `SupabaseJWTAuthentication.authenticate` 가 `(AppUser | None, claims)` 를 돌려준다. `AppUser` 행이 있으면 provider 를 다시 보지 않는다.
- 웹: `lib/api.ts` 가 Supabase 세션 토큰이 있으면 모든 요청에 `Authorization: Bearer` 를 붙인다(`me` 를 보지 않는다). 점수 응답은 TanStack Query 에 캐시된다. 로그인은 OAuth 리다이렉트라 페이지 전체를 다시 불러온다.

## 3. 접근

| 방법 | 내용 | 판단 |
|---|---|---|
| 1. 인자로 명시 전달 | 배치에서 구간별 또래 밀도를 미리 계산하고, `unit_scores(scope, month, basis)` 가 basis 를 받아 `peer` 자리에 구간 값을 끼운다. 웹 점수 API 가 요청에서 basis 를 꺼내 넘긴다. | **채택** (사용자 결정). 어느 API 가 개인화되는지 코드에 드러나고 테스트가 쉽다. |
| 2. 요청 컨텍스트 암묵 전달 | 미들웨어가 basis 를 contextvar 에 넣고 `unit_scores` 가 읽는다. | 기각. 개인화 지점이 코드에서 안 보이고, 캐시·백그라운드 작업에 다른 사람 값이 섞일 위험. |
| 3. 요청 때 원데이터로 계산 / 브라우저 재계산 | | 기각. 요청마다 무거운 집계, 또는 백분위 로직이 웹과 서버에 중복. |

세부 결정과 이유
- 구간 지표는 별도 SQL 파일이 아니라 `03_metrics.sql` 안에서 계산한다. 원천을 다시 적재한 뒤 03 만 돌려도 `peer` 와 구간 값이 같은 세대로 함께 바뀌어야 하기 때문이다(별도 파일이면 한쪽만 돌렸을 때 개인화 사용자만 옛 값을 조용히 본다).
- 개인화 결과는 캐시하지 않는다. 원값 캐시(`scores:{scope}:{m}`, 지금 키 그대로) 위에서 요청마다 단위 수백 개짜리 dict 를 복사하는 비용이 작고, 캐시 항목이 basis 수만큼 늘거나 basis 마다 다른 세대의 값이 보이는 일이 없다.

## 4. 데이터

### 4-1. 연령대와 데이터 구간

사용자 연령대 9개를 데이터 구간 6개로 대응한다. 데이터가 60대 이상을 한 구간으로만 갖고 있기 때문이다.

| `age_group` (저장값) | 화면 라벨 | basis (데이터 구간) | 지표 키 |
|---|---|---|---|
| `10s` | 10대 | `under20` | `peer_under20` |
| `20s` | 20대 | `20s` | `peer_20s` |
| `30s` | 30대 | `30s` | `peer_30s` |
| `40s` | 40대 | `40s` | `peer_40s` |
| `50s` | 50대 | `50s` | `peer_50s` |
| `60s` `70s` `80s` `90s` | 60대 … 90대 | `60plus` | `peer_60plus` |
| `NULL` | (없음) | 없음 | `peer` (기본 20~40대) |

### 4-2. 마이그레이션 `db/migrations/20261123_peer-personalization.sql`

공유 DB 와 `db/schema.sql` 양쪽에 같은 내용을 반영한다. 두 번 실행해도 안전하다.

```sql
-- 또래 밀도 개인화: app_user 연령대 · 더미 provider · 2026-11-23
-- 설계: workation-backend docs/superpowers/specs/2026-11-23-peer-personalization-design.md 4-2절
-- 실행 전 확인: SELECT conname FROM pg_constraint WHERE conrelid = 'app_user'::regclass AND contype = 'c';
--   provider CHECK 이름이 app_user_provider_check 가 아니면 이 파일을 실행하지 않는다.
BEGIN;

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS age_group VARCHAR(3);
ALTER TABLE app_user DROP CONSTRAINT IF EXISTS app_user_age_group_check;
ALTER TABLE app_user ADD CONSTRAINT app_user_age_group_check
  CHECK (age_group IN ('10s','20s','30s','40s','50s','60s','70s','80s','90s'));
ALTER TABLE app_user DROP CONSTRAINT IF EXISTS app_user_provider_check;
ALTER TABLE app_user ADD CONSTRAINT app_user_provider_check
  CHECK (provider IN ('kakao', 'google', 'apple', 'dummy'));

COMMENT ON COLUMN app_user.age_group IS '연령대 10s~90s. 또래 밀도 개인화 기준. NULL 이면 기본(20~40대)';
COMMENT ON COLUMN app_user.provider  IS 'kakao, google, apple, dummy(검증용 더미 계정, 지워도 됨)';

COMMIT;
```

`db/schema.sql` 의 `CREATE TABLE app_user` 에는 `age_group VARCHAR(3)` 칸(`agreed_at` 다음)과 이름 붙인 두 CHECK(`CONSTRAINT app_user_provider_check CHECK (...)`, `CONSTRAINT app_user_age_group_check CHECK (...)`)를 넣고, 위 두 COMMENT 를 반영한다. Postgres 는 이름 없는 칼럼 CHECK 에 `<표>_<칼럼>_check` 이름을 붙이므로 공유 DB 의 기존 provider CHECK 는 `app_user_provider_check` 일 것으로 본다. 실행 전 확인 쿼리로 이 가정을 검증한다.

### 4-3. 구간별 또래 밀도: `db/03_metrics.sql` 에 추가

03 의 기존 규칙(2025년, 집계 제외 행정동 제외, 연간 = 12개월 재료 평균에 같은 식, 시군구 = 소속 행정동 분자 합 ÷ 분모 합, 점수 = 같은 scope·월·지표 안의 백분위, 등급 = 점수 사분위)을 그대로 쓴다. 바꾸는 곳은 여섯 군데다.

1. 파일 머리 주석 "공통 규칙" 끝에 한 줄: `--   peer_<구간>(peer_under20 … peer_60plus) = 그 연령 구간 방문인구 ÷ vist_known. 연령대 개인화용(2026-11-23). 점수·등급은 peer 와 같은 식.`
2. `1. 지표 정의` INSERT 에 6행 추가 (`is_core = false`):

```sql
  ('peer_under20', '또래 밀도(10대)',      '20세 미만 방문인구 ÷ 성별·연령이 확인된 방문인구. 연령대 개인화용',   'good', false),
  ('peer_20s',     '또래 밀도(20대)',      '20대 방문인구 ÷ 성별·연령이 확인된 방문인구. 연령대 개인화용',       'good', false),
  ('peer_30s',     '또래 밀도(30대)',      '30대 방문인구 ÷ 성별·연령이 확인된 방문인구. 연령대 개인화용',       'good', false),
  ('peer_40s',     '또래 밀도(40대)',      '40대 방문인구 ÷ 성별·연령이 확인된 방문인구. 연령대 개인화용',       'good', false),
  ('peer_50s',     '또래 밀도(50대)',      '50대 방문인구 ÷ 성별·연령이 확인된 방문인구. 연령대 개인화용',       'good', false),
  ('peer_60plus',  '또래 밀도(60대 이상)', '60세 이상 방문인구 ÷ 성별·연령이 확인된 방문인구. 연령대 개인화용', 'good', false)
```

3. `DELETE FROM metric WHERE metric_key IN (...)` 목록에 `'peer_under20','peer_20s','peer_30s','peer_40s','peer_50s','peer_60plus'` 추가.
4. `m_base` 의 `monthly` SELECT 끝(`s.resd_total, s.wrk_total` 다음)에 `, s.vist_age_under20, s.vist_age_20s, s.vist_age_30s, s.vist_age_40s, s.vist_age_50s, s.vist_age_60plus` 를, 연간 SELECT 끝(`avg(resd_total), avg(wrk_total)` 다음)에 `, avg(vist_age_under20), avg(vist_age_20s), avg(vist_age_30s), avg(vist_age_40s), avg(vist_age_50s), avg(vist_age_60plus)` 를 같은 순서로 넣는다(UNION ALL 칼럼 순서가 맞아야 한다).
5. `3-1. 행정동` VALUES 에 6행, `3-2. 시군구` 의 안쪽 SELECT 에 합 6개와 VALUES 6행. VALUES 행은 각 목록의 **기존 마지막 행 앞**(3-1 은 `('local_life', …)` 앞, 3-2 는 `('weekend_gap', …)` 앞)에 넣는다. 아래 행들이 모두 쉼표로 끝나기 때문이다:

```sql
-- 3-1 VALUES 에 추가
    ('peer_under20', vist_age_under20 / NULLIF(vist_known, 0)),
    ('peer_20s',     vist_age_20s     / NULLIF(vist_known, 0)),
    ('peer_30s',     vist_age_30s     / NULLIF(vist_known, 0)),
    ('peer_40s',     vist_age_40s     / NULLIF(vist_known, 0)),
    ('peer_50s',     vist_age_50s     / NULLIF(vist_known, 0)),
    ('peer_60plus',  vist_age_60plus  / NULLIF(vist_known, 0)),

-- 3-2 안쪽 SELECT 의 sum(vist_known) vknown 뒤에 추가
               , sum(vist_age_under20) au20, sum(vist_age_20s) a20, sum(vist_age_30s) a30,
               sum(vist_age_40s) a40, sum(vist_age_50s) a50, sum(vist_age_60plus) a60

-- 3-2 VALUES 에 추가
    ('peer_under20', au20 / NULLIF(vknown, 0)),
    ('peer_20s',     a20  / NULLIF(vknown, 0)),
    ('peer_30s',     a30  / NULLIF(vknown, 0)),
    ('peer_40s',     a40  / NULLIF(vknown, 0)),
    ('peer_50s',     a50  / NULLIF(vknown, 0)),
    ('peer_60plus',  a60  / NULLIF(vknown, 0)),
```

6. `4. 점수·등급` 의 두 키 목록: 점수를 매기는 `metric_key IN ('ease','quiet','peer','evening','overnight')` 에 구간 6개를 추가한다. 역순 목록 `IN ('ease','quiet')` 은 그대로 둔다(구간 값은 높을수록 좋다).

이 변경 뒤 03 을 공유 DB 에서 한 번 다시 실행한다. 03 은 지표 8개 전체와 `dong_hourly` 를 지우고 다시 만들므로, 원천(`dong_month_summary`, `population_hourly`)이 마지막 03 실행 때와 같아야 기존 값이 그대로다. 이것은 가정이므로 아래 (라)로 재실행 전후를 비교해 확인한다.

`db/README.md` 도 함께 고친다(03 을 설명하는 사본). 구현 때 고칠 곳: 파일 표의 `03_metrics.sql` 행("`metric_def` 시드 8행" → 14행), 지표 표에 `peer_<구간>` 한 행(지표 "또래 밀도 연령 구간 (개인화용)", 원값 "구간 방문 ÷ 성별·연령 확인 방문", 재료 `vist_age_under20`~`vist_age_60plus`, `vist_known`, scope "dong + region 집계", 점수 "백분위"). 공유 DB 적용 뒤에 고칠 곳(실환경 검증 단계): "현재 상태" 절의 `03_metrics.sql` 적재 줄(행 수·날짜)과 적용 이력 표의 `20261123_peer-personalization.sql` 행.

적재 후 점검 쿼리 (구현 계획 실환경 검증에서 쓴다. 기대값은 모두 0):

```sql
-- (가) NULL 여부 일치: 모든 scope·단위·월에서 peer 와 구간 6개의 NULL 여부가 같다
SELECT count(*) AS null_mismatch
  FROM metric p
  JOIN metric b ON (b.scope, b.unit_id, b.month) = (p.scope, p.unit_id, p.month)
              AND b.metric_key IN ('peer_under20','peer_20s','peer_30s','peer_40s','peer_50s','peer_60plus')
 WHERE p.metric_key = 'peer' AND (p.raw_value IS NULL) <> (b.raw_value IS NULL);

-- (나) 구간 행 누락: peer 행이 있는데 구간 행이 6개가 아닌 단위
SELECT count(*) AS missing_bucket_rows
  FROM (SELECT p.scope, p.unit_id, p.month, count(b.metric_key) n
          FROM metric p
          LEFT JOIN metric b ON (b.scope, b.unit_id, b.month) = (p.scope, p.unit_id, p.month)
                            AND b.metric_key LIKE 'peer\_%'
         WHERE p.metric_key = 'peer'
         GROUP BY p.scope, p.unit_id, p.month) t
 WHERE n <> 6;

-- (다) 합 일치: 모든 scope(행정동·시군구)에서 peer = peer_20s + peer_30s + peer_40s (원값, 1e-9 안)
SELECT count(*) AS sum_mismatch
  FROM metric p
  JOIN metric a ON (a.scope, a.unit_id, a.month, a.metric_key) = (p.scope, p.unit_id, p.month, 'peer_20s')
  JOIN metric b ON (b.scope, b.unit_id, b.month, b.metric_key) = (p.scope, p.unit_id, p.month, 'peer_30s')
  JOIN metric c ON (c.scope, c.unit_id, c.month, c.metric_key) = (p.scope, p.unit_id, p.month, 'peer_40s')
 WHERE p.metric_key = 'peer' AND p.raw_value IS NOT NULL
   AND abs(p.raw_value - (a.raw_value + b.raw_value + c.raw_value)) > 1e-9;
```

(가)가 0 이 아니면 `vist_2040` 이 연령 칸의 합이라는 가정(스키마 주석)이 깨진 것이므로 백엔드 배포를 멈추고 사용자에게 보고한다. (다)는 같은 가정을 값으로 확인한다.

(라) 재실행 전후 불변: 재실행 **전**에 아래 두 결과를 떠 두고, 재실행 **후** 같은 쿼리 결과와 비교한다. 키·`score`·`grade` 와 `dong_hourly` 두 값은 완전히 같아야 하고, `raw_value` 는 차이가 1e-9 안이어야 한다(float8 합의 마지막 자리 차이는 허용). 다르면 원천이 그사이 다시 적재된 것이므로 멈추고, `db/README.md` 의 03 이후 단계(탐색 데이터 재생성) 실행 여부를 사용자에게 묻는다.

```sql
SELECT scope, unit_id, month, metric_key, round(raw_value::numeric, 9) AS raw_value, score, grade
  FROM metric WHERE metric_key NOT LIKE 'peer\_%' ORDER BY 1, 2, 3, 4;
SELECT count(*), sum(index_100) FROM dong_hourly;
```

## 5. 백엔드

### 5-1. 점수 계산 `metrics/scoring.py`

공유 계약. 시그니처와 동작을 그대로 구현한다.

```python
AGE_GROUPS = ("10s", "20s", "30s", "40s", "50s", "60s", "70s", "80s", "90s")
PEER_BASIS = {
    "10s": "under20",
    "20s": "20s",
    "30s": "30s",
    "40s": "40s",
    "50s": "50s",
    "60s": "60plus",
    "70s": "60plus",
    "80s": "60plus",
    "90s": "60plus",
}
PEER_BASES = ("under20", "20s", "30s", "40s", "50s", "60plus")


def peer_basis(age_group: str | None) -> str | None:
    """사용자 연령대 → 또래 밀도 데이터 구간. 없거나 모르는 값이면 None (기본 20~40대)."""
    return PEER_BASIS.get(age_group) if age_group else None


def unit_scores(
    scope: str, month: int | None, basis: str | None = None
) -> dict[int, dict[str, dict]]:
    """{unit_id: {metric_key: {"score","grade","raw"}}}. 그 달 값, overnight 는 연간 값.
    basis 가 있으면 "peer" 를 그 구간 값으로 바꾼다. 결과에 peer_<구간> 키는 나오지 않는다.
    원값은 정적 데이터 캐시(scores:{scope}:{m})에 두고, basis 적용은 요청마다 한다(캐시하지 않는다)."""
    if basis is not None and basis not in PEER_BASES:
        raise ValueError(f"unknown peer basis: {basis}")
    m = month or 0
    raw = static(f"scores:{scope}:{m}", lambda: _load_unit_scores(scope, m))
    return _personalize(raw, basis)


def _personalize(raw: dict[int, dict[str, dict]], basis: str | None) -> dict[int, dict[str, dict]]:
    key = f"peer_{basis}" if basis else None
    out = {}
    for unit_id, s in raw.items():
        row = {k: v for k, v in s.items() if not k.startswith("peer_")}
        if key is not None:
            row.pop("peer", None)
            if key in s:
                row["peer"] = s[key]
        out[unit_id] = row
    return out
```

- `_load_unit_scores` 는 그대로 둔다(모든 지표 키를 읽는다). 캐시 키도 지금 그대로다.
- `_personalize` 는 원값 dict 를 바꾸지 않고 새 dict 를 만든다(캐시된 객체를 오염시키지 않는다).
- `region_values`, `dong_kind`, `with_region_metrics` 는 바꾸지 않는다.

### 5-2. 보는 사람의 basis `webapi/views.py`

```python
def viewer_basis(request) -> str | None:
    """로그인 사용자의 또래 밀도 구간. 토큰이 없거나 틀리거나 연령대가 없으면 None (읽기는 막지 않는다)."""
    try:
        found = SupabaseJWTAuthentication().authenticate(request)
    except AuthenticationFailed:
        return None
    user = found[0] if found else None
    return scoring.peer_basis(user.age_group) if user is not None else None
```

### 5-3. 개인화하는 API

| API | 핸들러 | basis 출처 |
|---|---|---|
| `GET /api/tier` | `webapi/views.py` `tier` | `viewer_basis(request)` |
| `GET /api/regions/popular` | `webapi/views.py` `popular` | `viewer_basis(request)` |
| `GET /api/regions/<sgg>` | `webapi/views.py` `region_detail` | `viewer_basis(request)` |
| `GET /api/dongs/<dong>` | `webapi/views.py` `dong_detail` | `viewer_basis(request)` |
| `GET /api/me/favorites` | `webapi/me.py` `FavoritesView` → `webapi/community.py` `favorites(user_id, month, basis)` | `scoring.peer_basis(request.user.age_group)` |

규칙
- 핸들러 안에서 그 달 점수를 읽는 `unit_scores(scope, month)` 호출에는 모두 같은 basis 를 넘긴다.
- 연간 동 점수를 `local_life`·`dong_kind` 용으로 읽는 호출(`unit_scores("dong", None)`, `dong_detail` 과 `region_detail`·`favorites` 의 `annual = … if month else dong_scores`)은 지금 코드 그대로 둔다. `_personalize` 는 `local_life` 를 건드리지 않으므로 basis 가 섞여도 결과가 같다.

API 별로 개인화가 드러나는 응답 칸 (2026-11-23 코드 기준):

| API | 바뀌는 칸 |
|---|---|
| `/api/tier` (sigungu·dong) | 각 항목 `scores.peer` |
| `/api/regions/popular` | 각 항목 `scores.peer` |
| `/api/regions/<sgg>` | `stats.peerShare`(시군구 `peer` 원값 × 100), `peerOffpeakMonths`. 이 핸들러는 `data.region_monthly(rid, ("ease", "peer"))` 의 월별 또래 원값으로 `peerOffpeakMonths` 를 고른다. basis 가 있으면 `("ease", f"peer_{basis}")` 로 읽고 그 키의 값을 `peer_by_month` 재료로 쓴다. `bestMonths` 는 혼잡도만 보므로 바뀌지 않는다. |
| `/api/dongs/<dong>` | `stats.peerShare`(동 `peer` 원값 × 100), `scores.peer` |
| `/api/me/favorites` | `regions[].scores.peer`, `dongs[].scores.peer` |

### 5-4. 계정 API `accounts/`

- `AppUser.age_group = models.CharField(max_length=3, null=True)` (managed=False, 마이그레이션은 4-2 SQL).
- `GET /api/me` 응답에 `"ageGroup": user.age_group` (문자열 또는 null). `needsOnboarding` 판정은 바꾸지 않는다(`agreed_at is None`).
- `PATCH /api/me`:
  - 오류는 `core/errors.py` `error(status, code, message)` 형식(`{"error": {"code", "message"}}`).
  - 검사 순서: ① `nickname`(지금 규칙, `INVALID_NICKNAME`) → ② `ageGroup` 형식 → ③ 필수 규칙. 첫 실패 하나만 반환하고, 어떤 칸도 저장하지 않는다. 모든 검사를 통과한 뒤 한 번에 저장한다.
  - ② `ageGroup` 키가 본문에 있는데 값이 `AGE_GROUPS` 의 문자열이 아니면 `error(400, "INVALID_AGE_GROUP", "연령대를 선택해 주세요")`.
  - ③ `body.get("agreedAt")` 이 truthy(지금 코드와 같은 판정)이고 이번 요청의 `ageGroup` 과 기존 `age_group` 이 모두 비어 있으면 `error(400, "AGE_GROUP_REQUIRED", "연령대를 선택해 주세요")`. 이미 `age_group` 이 있는 사용자가 `agreedAt` 만 다시 보내는 것은 허용한다.
  - 성공 응답은 지금처럼 갱신된 Me 전체.

### 5-5. 더미 계정 `accounts/management/commands/dummy_users.py`

`uv run python manage.py dummy_users create|delete|verify`.

설정 출처: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` 는 `django.conf.settings` 에서(이미 settings 에 있다), `SUPABASE_ANON_KEY`, `DUMMY_USER_PASSWORD` 는 `os.environ` 에서 읽는다. 필요한 값이 비면 그 이름을 적은 `CommandError` 를 낸다(종료 코드 1). 필요한 값: create = URL·service role·password, delete = URL·service role, verify = URL·anon·password.

전제: Supabase Auth 의 Email 로그인이 켜져 있어야 verify 의 비밀번호 로그인이 된다. 2026-11-23 `GET {SUPABASE_URL}/auth/v1/settings` 에서 `external.email = true` 를 확인했다. 실환경 검증 프리플라이트에서 다시 확인한다. 더미 계정의 JWT `app_metadata.provider` 는 `email` 이지만, create 가 `app_user` 행을 미리 넣으므로 인증(`authenticate`)과 `GET /api/me` 는 provider 를 다시 보지 않고 통과한다.

계정 10개. 이메일 `dummy-<age>@workcation.test`, `<age>` 는 `10s` … `90s` 와 `none`.

Admin API 호출은 테스트에서 바꿔 끼울 수 있게 모듈 함수로 둔다.
- `admin_create_or_get(email: str, password: str) -> str`: `POST {URL}/auth/v1/admin/users` (`{"email", "password", "email_confirm": true}`). 422(이미 있음)이면 `admin_find(email)` 로 id 를 찾는다.
- `admin_find(email: str) -> str | None`: `GET {URL}/auth/v1/admin/users?per_page=1000` 에서 이메일이 같은 사용자 id.
- 삭제는 기존 `accounts/services.py` `delete_auth_user(user_id)` 를 재사용한다.

동작
- `create`: 계정마다 `uid = admin_create_or_get(...)`. 그다음 한 트랜잭션에서 `DELETE FROM app_user WHERE provider='dummy' AND provider_uid=%s AND user_id<>%s`(Supabase 쪽에서 다시 만들어져 id 가 바뀐 경우), 이어서 `INSERT INTO app_user (user_id, provider, provider_uid, nickname, agreed_at, age_group) VALUES (%s,'dummy',%s,%s,now(),%s) ON CONFLICT (user_id) DO UPDATE SET age_group = EXCLUDED.age_group, agreed_at = EXCLUDED.agreed_at, nickname = EXCLUDED.nickname`. `provider_uid` = 이메일, `nickname` = `더미<라벨>`(예: `더미30대`, `더미없음`), `none` 은 `age_group` NULL. 두 번 실행해도 행이 늘지 않는다.
- `delete`: 계정마다 `uid = admin_find(email)`. 있으면 `delete_auth_user(uid)` 를 먼저 하고, 성공하면 그 `app_user` 행을 지운다. `admin_find` 가 None 이면(Supabase 쪽만 먼저 지워진 경우) `DELETE FROM app_user WHERE provider='dummy' AND provider_uid=<이메일>` 로 그 행만 지운다. Supabase 삭제가 실패하면 `app_user` 를 남기고 계속한 뒤 마지막에 실패 목록을 출력하고 종료 코드 1(재실행 가능). 실패한 계정의 행은 재시도 근거로 남긴다.
- `verify --base-url <url> --month <YYYY-MM>`: 각 계정으로 `POST {URL}/auth/v1/token?grant_type=password` (`apikey: <anon>`) 로 토큰을 받아 `GET {base}/api/tier?month=<m>&scope=sigungu` 와 `scope=dong` 를 부른다. 같은 두 요청을 토큰 없이도 부른다.
  - 기대값은 DB 에서 직접 읽는다: `metric` 에서 `month = <m 의 월>` 인 `peer` 와 `peer_<구간>` 의 `score`. 응답 id 와 잇는 방법은 시군구 `region.sigungu_code` ↔ `metric.unit_id = region.region_id`(scope `region`), 행정동 `dong.adm_code` ↔ `metric.unit_id = dong.dong_id`(scope `dong`).
  - 연령대 → 구간 대응은 `scoring` 을 import 하지 않고 이 명령 안에 4-1절 표를 그대로 적는다(서버 상수가 틀렸을 때 검증도 함께 틀리지 않게).
  - 검사 1: 연령대 계정마다 응답 모든 항목의 `scores.peer` 가 그 구간 `peer_<구간>` 의 `score`(없으면 0)와 같다.
  - 검사 2: `none` 계정과 토큰 없는 요청의 `scores.peer` 가 `peer` 의 `score`(없으면 0)와 같다.
  - 검사 3: 구간 6개의 응답이 모든 쌍(15쌍)마다 적어도 한 단위에서 `scores.peer` 가 다르다.
  - 결과를 계정 × scope 표(일치 단위 수 / 전체)로 출력한다. 하나라도 어긋나면 어긋난 단위 id·기대·실제를 출력하고 종료 코드 1.

응답의 `scores.peer` 는 `webapi/data.py` `web_scores` 가 만드는 웹 키다(`WEB_TO_DB["peer"] == "peer"`, 2026-11-23 확인). 점수가 없으면 `web_scores` 가 0 을 낸다.

## 6. 웹

### 6-1. 계약

- `src/features/auth/ageGroup.ts`: `AGE_GROUPS = ['10s','20s','30s','40s','50s','60s','70s','80s','90s'] as const`, `type AgeGroup`, `AGE_GROUP_LABEL: Record<AgeGroup, string>` (`'10s' → '10대'` … `'90s' → '90대'`).
- `Me` 타입에 `ageGroup: AgeGroup | null`. `PatchMeBody` 에 `ageGroup?: AgeGroup`.
- mock(`src/mocks/auth/me.ts` 의 `DUI` 등, `src/features/auth/api.ts` 의 mock `patchMe`)도 `ageGroup` 을 다룬다: mock 첫 로그인은 `null`, mock `patchMe` 는 `body.ageGroup ?? cur.ageGroup` 을 저장.

### 6-2. 온보딩 화면 (화면 초안, 세부는 사용자 확인으로 정한다)

요구사항
- 닉네임과 약관 사이에 연령대 휠을 **모달 안에 바로**(별도 모달 없이) 둔다. 휠은 `src/components/ui/MonthPicker.tsx` 의 내부 함수 `MonthWheel` 을 export 해 재사용한다. 이때 선택 prop 세 개를 더한다: `ariaLabel?: string`(기본 `'월'`, listbox 접근성 이름), `idPrefix?: string`(기본 `'month-option'`, 항목 id 접두어), `selected?: boolean`(기본 `true`. false 면 어떤 항목도 `aria-selected` 가 아니고 가운데 강조도 하지 않는다). 기본값이 지금 동작과 같으므로 `MonthPicker` 호출부와 동작은 바뀌지 않는다. 온보딩에서는 `ariaLabel='연령대'`, `idPrefix='age-option'`, `selected={ageGroup !== null}` 로 쓴다. 휠을 또 다른 모달로 띄우지 않는 이유: 온보딩 모달과 휠 모달이 겹치면 `useFocusTrap` 의 document keydown 리스너가 둘 다 실행돼, 휠에서 Esc 를 누를 때 온보딩 쪽 닫기(로그아웃)까지 불린다.
- 선택 상태는 `AgeGroup | null`, 처음 `null`. 휠은 처음에 `30대` 를 가운데에 두되 선택 표시는 없다(`selected=false`). 사용자가 휠을 움직이거나 항목을 눌러 `onIndex` 가 불려야 값이 정해지고 선택 표시가 생긴다. 가운데 항목을 눌러도 `onIndex` 가 불린다(지금 `scrollTo` 동작).
- 연령대가 `null` 이면 "시작하기" 가 비활성이다.
- 제출하면 `PATCH /api/me` 본문에 `nickname`, `agreedAt`, `ageGroup` 이 함께 간다.
- 서버가 `AGE_GROUP_REQUIRED` 를 돌려주면 지금 저장 실패 토스트와 같은 방식으로 알린다(로그아웃하지 않는다).
- 배치·문구·스타일은 실행자 재량이며 사용자 확인 루프에서 바뀐다.

### 6-3. 보는 사람이 바뀌면 점수 다시 받기

점수 응답이 보는 사람에 따라 달라지므로, 같은 페이지 안에서 보는 사람의 기준이 바뀌면 점수 쿼리만 무효화한다.

- `src/features/auth/useInvalidateOnViewerChange.ts`: `useInvalidateOnViewerChange(me: Me | null | undefined)`.
  - 비교 키: `me === undefined` 이면 비교하지 않는다(첫 조회 중. 기준을 갱신하지도, 무효화하지도 않는다). 그 밖에는 `me ? `${me.id}:${me.ageGroup ?? ''}` : 'anon'`.
  - 처음 확정된 키를 기준으로 삼고 그때는 무효화하지 않는다. 그 뒤 키가 바뀔 때마다 아래 predicate 로 `invalidateQueries` 를 한 번 부르고 기준을 바꾼다.
  - 첫 로드에서 다시 받지 않는 이유: 점수 요청의 basis 는 `me` 가 아니라 `lib/api.ts` 가 붙이는 토큰으로 정해지므로 첫 로드 응답은 이미 개인화돼 있다.
- predicate (점수 쿼리 화이트리스트, 키 팩토리 2026-11-23 기준):

```ts
export function isScoreQuery(key: readonly unknown[]): boolean {
  const [head, second, third] = key
  if (head === 'tier' || head === 'popular') return true // tierKeys.list, popularKeys.list
  if ((head === 'region' || head === 'dong') && key.length === 3 && typeof third === 'object')
    return true // regionKeys.detail, dongKeys.detail (notes·poll 은 3번째가 문자열이라 제외)
  if (head === 'me' && second === 'favorites' && typeof third === 'object') return true // meKeys.favorites
  return false
}
```

  채팅 기록(`exploreKeys.chatThread`, 서버 요청 없는 로컬 상태), `geo`, notes, poll, `['auth','me']` 는 무효화하지 않는다.
- `AuthProvider` 가 자기 `me` 로 이 훅을 부른다.
- 실제로 이 훅이 동작하는 경우: 온보딩 완료(`ageGroup` null → 값), 로그아웃(사용자 → 익명), 계정 삭제. OAuth 로그인은 페이지를 새로 불러오므로 첫 확정 값이 곧 로그인 상태다.

## 7. 실패 경로와 적용 순서

| 상황 | 동작 |
|---|---|
| 토큰 만료·위조 | `viewer_basis` 가 None, 기본 점수. 읽기는 막지 않는다. |
| 로그인했지만 `app_user` 행이 아직 없음(첫 `GET /api/me` 전) | None, 기본 점수. |
| 03 재실행 전이라 구간 지표가 없는 DB | `_personalize` 가 `peer` 를 빼고 `web_scores` 가 0 을 낸다. 그래서 아래 순서를 지킨다. |
| 03 재실행 직후 정적 캐시(10분)에 옛 원값이 남음 | 같은 증상(개인화 사용자 peer 0). 03 재실행 뒤 `static:*` 캐시를 지우고 서버를 다시 띄운 다음 검증한다. |
| `age_group` 칸 없는 DB 에 새 백엔드 | `AppUser` 조회가 없는 칸을 SELECT 해 로그인 요청이 500 (추정). 그래서 4-2 마이그레이션을 맨 먼저 한다. |
| 새 백엔드 + 옛 웹 | 옛 온보딩은 `ageGroup` 을 안 보내 `AGE_GROUP_REQUIRED` 400. 로컬에서 백엔드와 웹을 같은 때 바꾸므로 받아들인다. |
| Supabase Admin API 실패(create·delete) | 실패한 이메일과 HTTP 상태를 출력하고 종료 코드 1. 재실행 안전. |
| verify 로그인 400 (Email 로그인 꺼짐 등) | 그 계정과 응답 본문을 출력하고 종료 코드 1. 프리플라이트에서 Email 활성을 먼저 확인한다. |

적용 순서(공유 DB 와 로컬 서버): ① 4-2 마이그레이션 → ② (라) 전 결과 저장, 03 재실행, 점검 쿼리 (가)~(라) → ③ 캐시 삭제와 새 백엔드 기동 → ④ 새 웹 기동 → ⑤ 더미 계정 create·verify.

03 재실행이 Management API(`supabase db query --linked`) 시간 제한에 걸리면 세션 풀러로 직접 실행한다(사용자 실행): 백엔드 `.env` 의 `DB_HOST`·`DB_PORT`·`DB_NAME`·`DB_USER`·`DB_PASSWORD` 로 `psql` 을 열어 `-v ON_ERROR_STOP=1 -f db/03_metrics.sql` (명령 전문은 구현 계획 7절 3단계). 03 은 한 트랜잭션이라 중간 실패 시 원래 상태가 남는다.

## 8. 성능

- 원값 캐시는 지금과 같다(scope·월마다 1개). 개인화는 요청마다 단위 수백 개 dict 복사로, DB 쿼리 수는 늘지 않는다.
- 개인화 API 에서 요청당 JWT 검증 1회(`viewer_basis`)와 `app_user` 조회 1회가 는다.

## 9. 검증 기준 (완료 조건)

1. 백엔드 테스트(`TEST_DB_PORT=5439 uv run pytest`) 전체 통과, `uv run ruff check .` 와 `uv run ruff format --check .` 통과.
2. 새 테스트가 아래를 단언한다(구현 계획에 이름과 단언 전량).
   - `unit_scores` 가 basis 별로 `peer` 를 바꾸고, 어떤 basis 에서도 `peer_*` 키를 내보내지 않으며, 캐시된 원값을 오염시키지 않는다.
   - 5-3절 둘째 표의 칸이 연령대 있는 사용자에게 구간 값을, 비로그인·연령대 없는 사용자에게 기본 값을 준다.
   - `PATCH /api/me` 의 `ageGroup` 검증 순서와 필수 규칙.
   - `dummy_users` create·delete 의 행 수와 순서, Supabase 쪽만 지워진 행 정리(네트워크 없이).
   - 바뀐 `db/03_metrics.sql` 이 빈 스크래치 DB 에서 `schema.sql` 다음에 오류 없이 실행된다.
3. 웹 테스트(`pnpm vitest run`) 전체 통과, `pnpm tsc -b`·`pnpm lint` 오류 없음.
4. 공유 DB 에 4-2 를 적용하고 03 을 다시 실행한 뒤 4-3 점검 쿼리 (가)(나)(다)가 모두 0 이고 (라) 전후 결과가 같다.
5. `dummy_users create` 뒤 `dummy_users verify --base-url http://127.0.0.1:8000 --month 2025-10` 이 종료 코드 0.
6. 사용자가 온보딩 화면을 보고 확정한다.
