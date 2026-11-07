-- 워케이션맵 ERD v0.3 (PostgreSQL 15+ / Supabase)
-- 2026-11-06 · 팀 아카츠키 · 최종 갱신 2026-11-07 (migrations/20261107_metric-v0.3.sql,
--   20261107_metric-formulas-django.sql, 20261107_dong-tags-coast.sql 반영)
--
-- 지표 개편 (2026-11-07, 명세 v0.3 변경안 · migrations/20261107_metric-v0.3.sql)
--   - metric_def 키: ease·quiet·peer·overnight·evening + 보조 weekend_gap·peak_share·local_life.
--     종합 value 는 프리셋별로 달라 저장하지 않고 백엔드(Django)가 요청 시 계산.
--   - 식 확정 (migrations/20261107_metric-formulas-django.sql): evening = 저녁 보행 ÷ 저녁 체류,
--     overnight = 숙박 계수(동 고정효과 회귀 β, 연간만). 산출 SQL 은 db/03_metrics.sql.
--   - dong.is_excluded / excluded_reason 추가 (수동면 집계 제외 → 행정동 203곳 백분위).
--   - dong_hourly.index_100 원천 = 보행인구(population_hourly.walk_avg).
--
-- v0.2(2026-10-28) → v0.3 변경
--   - LG U+ 데이터에 행정동 코드가 없어 region/dong 키를 정수 대리키로 변경. 공식 코드는
--     region.sigungu_code / dong.adm_code 에 나중에 채운다.
--   - raw_flow / raw_service 를 전처리 결과(workation-data)와 1:1 로 맞춘
--     population_hourly / population_demographic 으로 교체.
--   - 전처리 요약 테이블 dong_month_summary / dong_year_summary 추가 (추천 점수 재료).
--   - 전주시는 U+ 데이터에 구(완산·덕진) 구분이 없어 시 단위 1개로 둔다 → region 12개.
--   - 모든 테이블 RLS 활성화. 프론트는 Supabase 를 직접 호출하지 않고 백엔드(Django)가 service role
--     또는 DB 커넥션으로만 접근하므로 anon/authenticated 정책은 만들지 않는다.
--
-- 영역
--   [마스터]    region, dong
--   [정제 데이터] population_hourly(D1), population_demographic(D2),
--                dong_month_summary, dong_year_summary          ← workation-data 가 적재
--   [산출]      metric_def, metric, dong_hourly, region_link    ← db/03_metrics.sql 이 계산 (종합 value 는 Django)
--   [사용자]    app_user, favorite
--   [콘텐츠]    photo, tip, tip_reaction, tip_reply, report, metric_vote
--   [추가 제안] share_card(F-16), nl_search_log(F-09)
--
-- 규칙
--   scope + unit_id 조합은 region(scope='region') 또는 dong(scope='dong') 을 가리키는
--   논리 참조다. 한 컬럼이 두 테이블을 가리키므로 물리 FK 는 걸지 않는다.
--   인구 값은 전부 "평균 인구(명)" 단위의 실수다 (단위 환산 규칙은 workation-data/README).

-- ───────────────────────── 마스터 ─────────────────────────

CREATE TABLE region (
  region_id     SMALLINT     PRIMARY KEY,
  sido_name     VARCHAR(20)  NOT NULL,
  sigungu_name  VARCHAR(20)  NOT NULL,
  sigungu_code  VARCHAR(5),
  area_km2      NUMERIC(8,2),
  tags          TEXT[],
  UNIQUE (sido_name, sigungu_name)
);
COMMENT ON TABLE  region              IS '시군구 (12개). region_id 는 적재 스크립트가 시도·시군구 정렬 순으로 부여';
COMMENT ON COLUMN region.region_id    IS '대리키 1~12';
COMMENT ON COLUMN region.sido_name    IS '시도명 (강원특별자치도, 전북특별자치도, 전라남도, 제주특별자치도)';
COMMENT ON COLUMN region.sigungu_name IS '시군구명 (전주시는 구 없이 시 단위)';
COMMENT ON COLUMN region.sigungu_code IS '행정표준 시군구 코드 5자리 (추후 매핑, NULL 허용)';
COMMENT ON COLUMN region.area_km2     IS '면적 km²';
COMMENT ON COLUMN region.tags         IS '속성 태그. 소속 행정동 태그의 합집합 (dong.tags 와 같은 코드)';

CREATE TABLE dong (
  dong_id       SMALLINT     PRIMARY KEY,
  region_id     SMALLINT     NOT NULL REFERENCES region(region_id),
  dong_name     VARCHAR(30)  NOT NULL,
  adm_code      VARCHAR(10),
  area_km2      NUMERIC(8,3),
  is_excluded   BOOLEAN      NOT NULL DEFAULT false,
  excluded_reason VARCHAR(100),
  tags          TEXT[]       NOT NULL DEFAULT '{}',
  coast_dist_km NUMERIC(7,3),
  UNIQUE (region_id, dong_name)
);
COMMENT ON TABLE  dong           IS '읍면동 (204개, 집계 대상 203개). dong_id = region_id*100 + 시군구 내 가나다 순번';
COMMENT ON COLUMN dong.dong_id   IS '대리키 (예: 101 = 강릉시 첫 번째 읍면동)';
COMMENT ON COLUMN dong.region_id IS '소속 시군구';
COMMENT ON COLUMN dong.dong_name IS '읍면동명. 시군구가 다르면 같은 이름 가능 (송정동·중앙동·서면)';
COMMENT ON COLUMN dong.adm_code  IS '행정동 코드 10자리 (U+ 미제공, 추후 매핑, NULL 허용)';
COMMENT ON COLUMN dong.area_km2  IS '면적 km²';
COMMENT ON COLUMN dong.is_excluded     IS 'true 면 지표·등급·지도·백분위에서 제외하고 화면에 "집계 제외" 표기. 데이터 자체는 적재. 수동면(고성군)';
COMMENT ON COLUMN dong.excluded_reason IS '제외 사유 (is_excluded=true 일 때). 예: 특수시설 패턴';
COMMENT ON COLUMN dong.tags          IS '속성 태그. 자동: coast(해안선 1 km 이내), beach · island · surf · library · long_stay · park · market · resort(관광공사 장소 1곳 이상), cafe(카페 3곳 이상), food(음식점 10곳 이상). 팀 입력: city · mountain';
COMMENT ON COLUMN dong.coast_dist_km IS '경계에서 가장 가까운 해안선까지 거리(km, OSM natural=coastline). 0 = 해안에 닿음, NULL = 내륙(약 18 km 이상) 또는 경계 없음';
-- 경계 폴리곤(geom) 은 PostGIS 활성화 후 02_postgis.sql 로 추가
-- is_excluded 값은 migrations/20261107_metric-v0.3.sql 의 UPDATE 로 넣는다 (load_db.py 는 dong_id 만 upsert 하므로 재적재해도 유지)

CREATE INDEX dong_region_idx ON dong (region_id);

-- ───────────────────────── 정제 데이터 (workation-data 적재) ─────────────────────────

CREATE TABLE population_hourly (
  dong_id       SMALLINT     NOT NULL REFERENCES dong(dong_id),
  year          SMALLINT     NOT NULL,
  month         SMALLINT     NOT NULL,
  dow           SMALLINT     NOT NULL,
  hour          SMALLINT     NOT NULL,
  day_group     VARCHAR(7)   NOT NULL,
  time_band     VARCHAR(7)   NOT NULL,
  weekday_count SMALLINT     NOT NULL,
  stay_sum      DOUBLE PRECISION,
  walk_sum      DOUBLE PRECISION,
  driv_sum      DOUBLE PRECISION,
  stay_avg      DOUBLE PRECISION,
  walk_avg      DOUBLE PRECISION,
  driv_avg      DOUBLE PRECISION,
  PRIMARY KEY (dong_id, year, month, dow, hour),
  CHECK (month BETWEEN 1 AND 12),
  CHECK (dow BETWEEN 0 AND 6),
  CHECK (hour BETWEEN 0 AND 23),
  CHECK (day_group IN ('weekday', 'weekend')),
  CHECK (time_band IN ('night', 'morning', 'day', 'evening'))
);
COMMENT ON TABLE  population_hourly               IS 'D1 유동인구 정제본 (읍면동 × 월 × 요일 × 시간대). 411,264행';
COMMENT ON COLUMN population_hourly.dow           IS '요일 0=월 … 6=일';
COMMENT ON COLUMN population_hourly.hour          IS '시간대 코드 0~23. 그 시각부터 1시간';
COMMENT ON COLUMN population_hourly.day_group     IS 'weekday(월~금), weekend(토·일)';
COMMENT ON COLUMN population_hourly.time_band     IS 'night(23~05), morning(06~09), day(10~16), evening(17~22)';
COMMENT ON COLUMN population_hourly.weekday_count IS '그 달에 해당 요일이 몇 번 있는지 (4 또는 5)';
COMMENT ON COLUMN population_hourly.stay_sum      IS '원본 값: 그 달 해당 요일들의 해당 시간대 체류 인구 합';
COMMENT ON COLUMN population_hourly.walk_sum      IS '원본 값: 도보 이동 인구 합';
COMMENT ON COLUMN population_hourly.driv_sum      IS '원본 값: 차량 이동 인구 합';
COMMENT ON COLUMN population_hourly.stay_avg      IS 'stay_sum ÷ weekday_count. 해당 요일·시간대 평균 체류 인구(명)';
COMMENT ON COLUMN population_hourly.walk_avg      IS '평균 도보 이동 인구(명)';
COMMENT ON COLUMN population_hourly.driv_avg      IS '평균 차량 이동 인구(명)';

CREATE INDEX population_hourly_month_idx ON population_hourly (year, month, day_group, time_band);

CREATE TABLE population_demographic (
  dong_id       SMALLINT     NOT NULL REFERENCES dong(dong_id),
  year          SMALLINT     NOT NULL,
  month         SMALLINT     NOT NULL,
  sex           VARCHAR(7)   NOT NULL,
  age_group     VARCHAR(7)   NOT NULL,
  is_unknown    BOOLEAN      NOT NULL DEFAULT false,
  days_in_month SMALLINT     NOT NULL,
  resd_sum      DOUBLE PRECISION,
  wrk_sum       DOUBLE PRECISION,
  vist_sum      DOUBLE PRECISION,
  resd_avg      DOUBLE PRECISION,
  wrk_avg       DOUBLE PRECISION,
  vist_avg      DOUBLE PRECISION,
  PRIMARY KEY (dong_id, year, month, sex, age_group),
  CHECK (month BETWEEN 1 AND 12),
  CHECK (sex IN ('male', 'female', 'unknown')),
  CHECK (age_group IN ('under20', '20s', '30s', '40s', '50s', '60plus', 'unknown'))
);
COMMENT ON TABLE  population_demographic               IS 'D2 서비스인구 정제본 (읍면동 × 월 × 성별 × 연령). 31,824행';
COMMENT ON COLUMN population_demographic.sex           IS 'male, female, unknown';
COMMENT ON COLUMN population_demographic.age_group     IS 'under20, 20s, 30s, 40s, 50s, 60plus, unknown';
COMMENT ON COLUMN population_demographic.is_unknown    IS '성별·연령 미상 행. 거주·직장은 항상 0, 방문만 값 있음. 총량에는 포함, 성별·연령 비율에서는 제외';
COMMENT ON COLUMN population_demographic.days_in_month IS '그 달 일수';
COMMENT ON COLUMN population_demographic.resd_sum      IS '원본 값: 시간별 추정치의 월 누적 (거주)';
COMMENT ON COLUMN population_demographic.wrk_sum       IS '원본 값 (직장)';
COMMENT ON COLUMN population_demographic.vist_sum      IS '원본 값 (방문)';
COMMENT ON COLUMN population_demographic.resd_avg      IS 'resd_sum ÷ (일수 × 24). 일평균 거주 인구(명)';
COMMENT ON COLUMN population_demographic.wrk_avg       IS '일평균 직장 인구(명)';
COMMENT ON COLUMN population_demographic.vist_avg      IS '일평균 방문 인구(명)';

CREATE TABLE dong_month_summary (
  dong_id       SMALLINT     NOT NULL REFERENCES dong(dong_id),
  year          SMALLINT     NOT NULL,
  month         SMALLINT     NOT NULL,
  -- 요일그룹 × 시간구간 평균 인구 (시간당, 명) — population_hourly 집계
  stay_weekday_night    DOUBLE PRECISION,
  stay_weekday_morning  DOUBLE PRECISION,
  stay_weekday_day      DOUBLE PRECISION,
  stay_weekday_evening  DOUBLE PRECISION,
  stay_weekend_night    DOUBLE PRECISION,
  stay_weekend_morning  DOUBLE PRECISION,
  stay_weekend_day      DOUBLE PRECISION,
  stay_weekend_evening  DOUBLE PRECISION,
  walk_weekday_night    DOUBLE PRECISION,
  walk_weekday_morning  DOUBLE PRECISION,
  walk_weekday_day      DOUBLE PRECISION,
  walk_weekday_evening  DOUBLE PRECISION,
  walk_weekend_night    DOUBLE PRECISION,
  walk_weekend_morning  DOUBLE PRECISION,
  walk_weekend_day      DOUBLE PRECISION,
  walk_weekend_evening  DOUBLE PRECISION,
  driv_weekday_night    DOUBLE PRECISION,
  driv_weekday_morning  DOUBLE PRECISION,
  driv_weekday_day      DOUBLE PRECISION,
  driv_weekday_evening  DOUBLE PRECISION,
  driv_weekend_night    DOUBLE PRECISION,
  driv_weekend_morning  DOUBLE PRECISION,
  driv_weekend_day      DOUBLE PRECISION,
  driv_weekend_evening  DOUBLE PRECISION,
  stay_weekday_all      DOUBLE PRECISION,
  stay_weekend_all      DOUBLE PRECISION,
  walk_weekday_all      DOUBLE PRECISION,
  walk_weekend_all      DOUBLE PRECISION,
  driv_weekday_all      DOUBLE PRECISION,
  driv_weekend_all      DOUBLE PRECISION,
  walk_share            DOUBLE PRECISION,
  -- 인구 합계·비율 (일평균, 명) — population_demographic 집계
  resd_total            DOUBLE PRECISION,
  wrk_total             DOUBLE PRECISION,
  vist_total            DOUBLE PRECISION,
  vist_known            DOUBLE PRECISION,
  vist_unknown          DOUBLE PRECISION,
  vist_age_under20      DOUBLE PRECISION,
  vist_age_20s          DOUBLE PRECISION,
  vist_age_30s          DOUBLE PRECISION,
  vist_age_40s          DOUBLE PRECISION,
  vist_age_50s          DOUBLE PRECISION,
  vist_age_60plus       DOUBLE PRECISION,
  vist_female           DOUBLE PRECISION,
  vist_male             DOUBLE PRECISION,
  vist_2040             DOUBLE PRECISION,
  vist_2040_share       DOUBLE PRECISION,
  vist_per_resd         DOUBLE PRECISION,
  PRIMARY KEY (dong_id, year, month),
  CHECK (month BETWEEN 1 AND 12)
);
COMMENT ON TABLE  dong_month_summary                 IS '읍면동 × 월 요약 (2,448행). 추천 점수 계산의 기본 재료';
COMMENT ON COLUMN dong_month_summary.stay_weekday_day IS '월~금 10:00~17:00 평균 체류 인구. 같은 패턴 {stay|walk|driv}_{weekday|weekend}_{night|morning|day|evening}';
COMMENT ON COLUMN dong_month_summary.stay_weekday_all IS '요일그룹 전체(24시간) 평균. {stay|walk|driv}_{weekday|weekend}_all';
COMMENT ON COLUMN dong_month_summary.walk_share       IS '주중 도보 ÷ (주중 도보 + 주중 차량). 1에 가까울수록 걸어 다니는 동네';
COMMENT ON COLUMN dong_month_summary.resd_total       IS '거주 인구 합계 (일평균)';
COMMENT ON COLUMN dong_month_summary.wrk_total        IS '직장 인구 합계 (일평균)';
COMMENT ON COLUMN dong_month_summary.vist_total       IS '방문 인구 합계, 미상 포함. 총량 지표(붐빔·피크)에 사용';
COMMENT ON COLUMN dong_month_summary.vist_known       IS '방문 인구 합계, 미상 제외. 성별·연령 비율의 분모';
COMMENT ON COLUMN dong_month_summary.vist_unknown     IS '성별·연령 미상 방문 인구';
COMMENT ON COLUMN dong_month_summary.vist_2040        IS '20·30·40대 방문 합';
COMMENT ON COLUMN dong_month_summary.vist_2040_share  IS 'vist_2040 ÷ vist_known';
COMMENT ON COLUMN dong_month_summary.vist_per_resd    IS 'vist_total ÷ resd_total. 높을수록 관광지 성격';

CREATE TABLE dong_year_summary (
  dong_id               SMALLINT     NOT NULL REFERENCES dong(dong_id),
  year                  SMALLINT     NOT NULL,
  months                SMALLINT     NOT NULL,
  resd_mean             DOUBLE PRECISION,
  wrk_mean              DOUBLE PRECISION,
  vist_mean             DOUBLE PRECISION,
  vist_max              DOUBLE PRECISION,
  vist_min              DOUBLE PRECISION,
  vist_std              DOUBLE PRECISION,
  vist_peak_month       SMALLINT,
  vist_low_month        SMALLINT,
  vist_peak_ratio       DOUBLE PRECISION,
  vist_cv               DOUBLE PRECISION,
  vist_per_resd_mean    DOUBLE PRECISION,
  vist_2040_share_mean  DOUBLE PRECISION,
  stay_weekday_day_mean DOUBLE PRECISION,
  walk_weekday_day_mean DOUBLE PRECISION,
  walk_weekday_evening_mean DOUBLE PRECISION,
  walk_weekend_all_mean DOUBLE PRECISION,
  walk_share_mean       DOUBLE PRECISION,
  PRIMARY KEY (dong_id, year)
);
COMMENT ON TABLE  dong_year_summary                  IS '읍면동 연간 요약 (204행)';
COMMENT ON COLUMN dong_year_summary.months           IS '집계된 월 수 (정상이면 12)';
COMMENT ON COLUMN dong_year_summary.vist_max         IS '월별 vist_total 최대';
COMMENT ON COLUMN dong_year_summary.vist_min         IS '월별 vist_total 최소';
COMMENT ON COLUMN dong_year_summary.vist_std         IS '월별 vist_total 표준편차';
COMMENT ON COLUMN dong_year_summary.vist_peak_month  IS '방문 최대 월 1~12';
COMMENT ON COLUMN dong_year_summary.vist_low_month   IS '방문 최소 월 1~12';
COMMENT ON COLUMN dong_year_summary.vist_peak_ratio  IS 'vist_max ÷ vist_min. 성수기/비수기 배율';
COMMENT ON COLUMN dong_year_summary.vist_cv          IS 'vist_std ÷ vist_mean. 변동 계수, 높을수록 계절 편차 큼';
COMMENT ON COLUMN dong_year_summary.stay_weekday_day_mean     IS '주중 낮 평균 체류 (한적함 지표)';
COMMENT ON COLUMN dong_year_summary.walk_weekday_day_mean     IS '주중 낮 평균 도보 (한적함 지표)';
COMMENT ON COLUMN dong_year_summary.walk_weekday_evening_mean IS '주중 저녁 평균 도보 (즐길 거리 지표)';
COMMENT ON COLUMN dong_year_summary.walk_weekend_all_mean     IS '주말 평균 도보 (즐길 거리 지표)';

-- ───────────────────────── 산출 (백엔드 파이프라인) ─────────────────────────

CREATE TABLE metric_def (
  metric_key    VARCHAR(20)  PRIMARY KEY,
  name_ko       VARCHAR(30)  NOT NULL,
  description   TEXT,
  higher_is     VARCHAR(4)   NOT NULL,
  is_core       BOOLEAN      NOT NULL DEFAULT true,
  CHECK (higher_is IN ('good', 'bad'))
);
COMMENT ON TABLE  metric_def             IS '지표 정의 (핵심 5 + 보조 3). 종합 value 는 프리셋별로 달라 정의·저장하지 않는다';
COMMENT ON COLUMN metric_def.metric_key  IS '핵심: ease(시기 여유), quiet(동네 조용함), peer(또래 밀도), overnight(숙박 성향), evening(저녁 활기) / 보조: weekend_gap(주중·주말 차이), peak_share(성수기 집중도), local_life(생활권 지수)';
COMMENT ON COLUMN metric_def.name_ko     IS '지표 한글명';
COMMENT ON COLUMN metric_def.higher_is   IS 'good, bad (점수 방향). v0.3 핵심 5종은 전부 good — 원값이 낮을수록 좋은 지표(ease·quiet)는 역백분위로 점수를 뒤집는다';
COMMENT ON COLUMN metric_def.is_core     IS '핵심 지표 여부 (종합 value 계산에 들어감). 보조 3종은 false';

CREATE TABLE metric (
  scope         VARCHAR(6)   NOT NULL,
  unit_id       SMALLINT     NOT NULL,
  month         SMALLINT     NOT NULL,
  metric_key    VARCHAR(20)  NOT NULL REFERENCES metric_def(metric_key),
  raw_value     DOUBLE PRECISION,
  score         SMALLINT,
  grade         CHAR(1),
  PRIMARY KEY (scope, unit_id, month, metric_key),
  CHECK (scope IN ('region', 'dong')),
  CHECK (month BETWEEN 0 AND 12),
  CHECK (score BETWEEN 0 AND 100),
  CHECK (grade IN ('S', 'A', 'B', 'C'))
);
COMMENT ON TABLE  metric            IS '지표 산출값 (시군구·읍면동 × 월 × 지표). 종합 value 는 프리셋(quiet·lively·custom) 가중 평균이라 저장하지 않고 백엔드(Django)가 요청 시 계산';
COMMENT ON COLUMN metric.scope      IS 'region, dong. 동네 성격 그룹(quiet·peer·evening)은 dong 원본 + region 집계(분자 합 ÷ 분모 합) 둘 다, 시기·체류 그룹(ease·overnight)은 region 만. overnight 은 연간(month 0)만';
COMMENT ON COLUMN metric.unit_id    IS 'region_id 또는 dong_id (논리 참조)';
COMMENT ON COLUMN metric.month      IS '1~12, 0 = 연간';
COMMENT ON COLUMN metric.raw_value  IS '원값 (카드 보조 표기). ease=월 혼잡 지수, quiet=낮 유입 지수, peer=20~40대 비율, overnight=숙박 계수(동 고정효과 회귀 β), evening=저녁 보행÷저녁 체류';
COMMENT ON COLUMN metric.score      IS '백분위 점수 0~100. 같은 scope(시군구 12 / 행정동 203)·같은 달 안에서, 높을수록 좋음';
COMMENT ON COLUMN metric.grade      IS 'S(상위 25%), A(25~50%), B(50~75%), C(하위 25%). 경계는 실데이터 보고 조정';

CREATE TABLE dong_hourly (
  dong_id       SMALLINT     NOT NULL REFERENCES dong(dong_id),
  month         SMALLINT     NOT NULL,
  day_group     VARCHAR(7)   NOT NULL,
  hour          SMALLINT     NOT NULL,
  index_100     SMALLINT,
  PRIMARY KEY (dong_id, month, day_group, hour),
  CHECK (day_group IN ('weekday', 'weekend'))
);
COMMENT ON TABLE  dong_hourly           IS '읍면동 24시간 붐빔 곡선. population_hourly.walk_avg(보행인구)를 월·요일그룹·시간대로 평균';
COMMENT ON COLUMN dong_hourly.month     IS '1~12';
COMMENT ON COLUMN dong_hourly.day_group IS 'weekday, weekend';
COMMENT ON COLUMN dong_hourly.hour      IS '0~23';
COMMENT ON COLUMN dong_hourly.index_100 IS '보행인구 지수. 같은 dong·month·day_group 안에서 최대 시간대 = 100';

CREATE TABLE region_link (
  link_id       SERIAL       PRIMARY KEY,
  region_id     SMALLINT     NOT NULL REFERENCES region(region_id),
  kind          VARCHAR(10)  NOT NULL,
  title         VARCHAR(100) NOT NULL,
  url           TEXT         NOT NULL,
  CHECK (kind IN ('support', 'stay', 'tour'))
);
COMMENT ON TABLE  region_link      IS '지원사업·숙박·관광 외부 링크 (수동 입력)';
COMMENT ON COLUMN region_link.kind IS 'support, stay, tour';

-- ───────────────────────── 사용자 ─────────────────────────

CREATE TABLE app_user (
  user_id       UUID         PRIMARY KEY,
  provider      VARCHAR(10)  NOT NULL,
  provider_uid  VARCHAR(100) NOT NULL,
  nickname      VARCHAR(20)  NOT NULL,
  avatar_url    TEXT,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_uid),
  CHECK (provider IN ('kakao', 'google', 'apple'))
);
COMMENT ON TABLE  app_user              IS '사용자 (소셜 로그인). user_id = Supabase auth.users.id';
COMMENT ON COLUMN app_user.provider     IS 'kakao, google, apple';
COMMENT ON COLUMN app_user.provider_uid IS '소셜 제공자 측 사용자 ID';
COMMENT ON COLUMN app_user.nickname     IS '닉네임 (공개)';
COMMENT ON COLUMN app_user.avatar_url   IS '아바타 URL (공개)';

CREATE TABLE favorite (
  user_id       UUID         NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  scope         VARCHAR(6)   NOT NULL,
  unit_id       SMALLINT     NOT NULL,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, scope, unit_id),
  CHECK (scope IN ('region', 'dong'))
);
COMMENT ON TABLE  favorite         IS '즐겨찾기 (로그인 시 서버 동기화)';
COMMENT ON COLUMN favorite.scope   IS 'region, dong';
COMMENT ON COLUMN favorite.unit_id IS 'region_id 또는 dong_id (논리 참조)';

-- ───────────────────────── 콘텐츠 · 커뮤니티 ─────────────────────────

CREATE TABLE photo (
  photo_id      SERIAL       PRIMARY KEY,
  scope         VARCHAR(6)   NOT NULL,
  unit_id       SMALLINT     NOT NULL,
  month         SMALLINT,
  url           TEXT         NOT NULL,
  source        VARCHAR(8)   NOT NULL,
  credit        VARCHAR(100),
  user_id       UUID         REFERENCES app_user(user_id) ON DELETE SET NULL,
  status        VARCHAR(8)   NOT NULL DEFAULT 'pending',
  is_featured   BOOLEAN      NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CHECK (scope IN ('region', 'dong')),
  CHECK (source IN ('user', 'tourapi', 'team')),
  CHECK (status IN ('pending', 'approved', 'hidden'))
);
COMMENT ON TABLE  photo             IS '지역·동네 × 월 사진 (파일은 Cloudflare)';
COMMENT ON COLUMN photo.scope       IS 'region, dong';
COMMENT ON COLUMN photo.unit_id     IS 'region_id 또는 dong_id (논리 참조)';
COMMENT ON COLUMN photo.month       IS '1~12 (촬영월, NULL = 월 무관)';
COMMENT ON COLUMN photo.url         IS 'Cloudflare 공개 URL';
COMMENT ON COLUMN photo.source      IS 'user, tourapi, team';
COMMENT ON COLUMN photo.credit      IS '출처 크레딧';
COMMENT ON COLUMN photo.user_id     IS 'source=user 일 때 업로더';
COMMENT ON COLUMN photo.status      IS 'pending, approved, hidden';
COMMENT ON COLUMN photo.is_featured IS '이달의 사진';

CREATE INDEX photo_unit_idx ON photo (scope, unit_id, month) WHERE status = 'approved';

CREATE TABLE tip (
  tip_id        SERIAL       PRIMARY KEY,
  user_id       UUID         NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  region_id     SMALLINT     NOT NULL REFERENCES region(region_id),
  dong_id       SMALLINT     REFERENCES dong(dong_id),
  visit_month   SMALLINT,
  stay_days     SMALLINT,
  body          VARCHAR(300) NOT NULL,
  status        VARCHAR(8)   NOT NULL DEFAULT 'public',
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CHECK (status IN ('public', 'hidden'))
);
COMMENT ON TABLE  tip             IS '워케이션 노트 — 한 줄 팁';
COMMENT ON COLUMN tip.dong_id     IS 'NULL 이면 지역 단위 팁';
COMMENT ON COLUMN tip.visit_month IS '방문 월 1~12';
COMMENT ON COLUMN tip.stay_days   IS '체류 기간 (일)';
COMMENT ON COLUMN tip.body        IS '본문 300자';
COMMENT ON COLUMN tip.status      IS 'public, hidden';

CREATE INDEX tip_region_idx ON tip (region_id, created_at DESC) WHERE status = 'public';

CREATE TABLE tip_reaction (
  tip_id        INTEGER      NOT NULL REFERENCES tip(tip_id) ON DELETE CASCADE,
  user_id       UUID         NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (tip_id, user_id)
);
COMMENT ON TABLE tip_reaction IS '도움돼요';

CREATE TABLE tip_reply (
  reply_id      SERIAL       PRIMARY KEY,
  tip_id        INTEGER      NOT NULL REFERENCES tip(tip_id) ON DELETE CASCADE,
  user_id       UUID         NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  body          VARCHAR(300) NOT NULL,
  status        VARCHAR(8)   NOT NULL DEFAULT 'public',
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CHECK (status IN ('public', 'hidden'))
);
COMMENT ON TABLE  tip_reply        IS '팁 답글';
COMMENT ON COLUMN tip_reply.status IS 'public, hidden';

CREATE TABLE report (
  report_id     SERIAL       PRIMARY KEY,
  target_type   VARCHAR(6)   NOT NULL,
  target_id     INTEGER      NOT NULL,
  user_id       UUID         NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  reason        VARCHAR(30)  NOT NULL,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  UNIQUE (target_type, target_id, user_id),
  CHECK (target_type IN ('tip', 'reply', 'photo'))
);
COMMENT ON TABLE  report             IS '신고 (3건 이상 자동 숨김)';
COMMENT ON COLUMN report.target_type IS 'tip, reply, photo';
COMMENT ON COLUMN report.target_id   IS 'tip_id / reply_id / photo_id (논리 참조)';
COMMENT ON COLUMN report.reason      IS '신고 사유';

CREATE TABLE metric_vote (
  user_id       UUID         NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  scope         VARCHAR(6)   NOT NULL,
  unit_id       SMALLINT     NOT NULL,
  month         SMALLINT     NOT NULL,
  metric_key    VARCHAR(20)  NOT NULL REFERENCES metric_def(metric_key),
  agree         BOOLEAN      NOT NULL,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, scope, unit_id, month, metric_key),
  CHECK (scope IN ('region', 'dong'))
);
COMMENT ON TABLE  metric_vote         IS '지표 체감 투표 (그래요/아니요)';
COMMENT ON COLUMN metric_vote.scope   IS 'region, dong';
COMMENT ON COLUMN metric_vote.unit_id IS 'region_id 또는 dong_id (논리 참조)';
COMMENT ON COLUMN metric_vote.agree   IS 'true = 그래요';

-- ───────────────────────── 추가 제안 (명세 미반영) ─────────────────────────

CREATE TABLE share_card (
  card_id       SERIAL       PRIMARY KEY,
  user_id       UUID         REFERENCES app_user(user_id) ON DELETE SET NULL,
  region_id     SMALLINT     NOT NULL REFERENCES region(region_id),
  month         SMALLINT,
  conditions    JSONB        NOT NULL,
  image_url     TEXT         NOT NULL,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  share_card            IS 'F-16 공유 카드 (유입 링크 복원용)';
COMMENT ON COLUMN share_card.user_id    IS '비로그인 생성 시 NULL';
COMMENT ON COLUMN share_card.conditions IS '프리셋·가중치·태그 JSON';
COMMENT ON COLUMN share_card.image_url  IS '1080×1350 PNG';

CREATE TABLE nl_search_log (
  search_id     SERIAL       PRIMARY KEY,
  user_id       UUID         REFERENCES app_user(user_id) ON DELETE SET NULL,
  query         VARCHAR(300) NOT NULL,
  parsed        JSONB,
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);
COMMENT ON TABLE  nl_search_log        IS 'F-09 자연어 검색 로그 (파싱 품질 검증용)';
COMMENT ON COLUMN nl_search_log.query  IS '사용자 입력 문장';
COMMENT ON COLUMN nl_search_log.parsed IS 'LLM 이 변환한 조건·가중치 JSON';

-- ───────────────────────── Supabase RLS ─────────────────────────
-- 프론트는 Supabase 를 직접 호출하지 않는다. 모든 테이블에 RLS 를 켜고 정책을 두지 않으면
-- anon/authenticated 키로는 PostgREST 를 통해 아무것도 읽고 쓸 수 없다.
-- 백엔드(Django)는 DB 커넥션(postgres 롤) 또는 service_role 키로 접근하므로 RLS 를 우회한다.

ALTER TABLE region                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE population_hourly      ENABLE ROW LEVEL SECURITY;
ALTER TABLE population_demographic ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_month_summary     ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_year_summary      ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric_def             ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE dong_hourly            ENABLE ROW LEVEL SECURITY;
ALTER TABLE region_link            ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user               ENABLE ROW LEVEL SECURITY;
ALTER TABLE favorite               ENABLE ROW LEVEL SECURITY;
ALTER TABLE photo                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE tip                    ENABLE ROW LEVEL SECURITY;
ALTER TABLE tip_reaction           ENABLE ROW LEVEL SECURITY;
ALTER TABLE tip_reply              ENABLE ROW LEVEL SECURITY;
ALTER TABLE report                 ENABLE ROW LEVEL SECURITY;
ALTER TABLE metric_vote            ENABLE ROW LEVEL SECURITY;
ALTER TABLE share_card             ENABLE ROW LEVEL SECURITY;
ALTER TABLE nl_search_log          ENABLE ROW LEVEL SECURITY;
