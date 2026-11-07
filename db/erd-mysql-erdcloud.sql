-- 워케이션맵 ERD v0.3 — ERDCloud 가져오기용 (MySQL 방언)
-- 실제 DB 는 PostgreSQL(Supabase) 이며 원본은 schema.sql 이다.
-- 타입만 바꿨다: DOUBLE PRECISION→DOUBLE, UUID→CHAR(36), TEXT[]→VARCHAR(100),
--   TIMESTAMPTZ→DATETIME, SERIAL→INT AUTO_INCREMENT, JSONB→JSON, BOOLEAN→TINYINT(1)
-- CHECK·부분 인덱스·RLS 는 뺐다. COMMENT 는 ERDCloud 의 논리명/설명으로 들어간다.

CREATE TABLE region (
  region_id              SMALLINT       NOT NULL COMMENT '대리키 1~12',
  sido_name              VARCHAR(20)    NOT NULL COMMENT '시도명 (강원특별자치도, 전북특별자치도, 전라남도, 제주특별자치도)',
  sigungu_name           VARCHAR(20)    NOT NULL COMMENT '시군구명 (전주시는 구 없이 시 단위)',
  sigungu_code           VARCHAR(5)     NULL COMMENT '행정표준 시군구 코드 5자리 (추후 매핑, NULL 허용)',
  area_km2               DECIMAL(8,2)   NULL COMMENT '면적 km²',
  tags                   VARCHAR(100)   NULL COMMENT '속성 태그. 소속 행정동 태그의 합집합 (dong.tags 와 같은 코드)',
  PRIMARY KEY (region_id),
  UNIQUE (sido_name, sigungu_name)
) COMMENT = '시군구 (12개). region_id 는 적재 스크립트가 시도·시군구 정렬 순으로 부여';

CREATE TABLE dong (
  dong_id                SMALLINT       NOT NULL COMMENT '대리키 (예: 101 = 강릉시 첫 번째 읍면동)',
  region_id              SMALLINT       NOT NULL COMMENT '소속 시군구',
  dong_name              VARCHAR(30)    NOT NULL COMMENT '읍면동명. 시군구가 다르면 같은 이름 가능 (송정동·중앙동·서면)',
  adm_code               VARCHAR(10)    NULL COMMENT '행정동 코드 10자리 (U+ 미제공, 추후 매핑, NULL 허용)',
  area_km2               DECIMAL(8,3)   NULL COMMENT '면적 km²',
  is_excluded            TINYINT(1)     NOT NULL DEFAULT false COMMENT 'true 면 지표·등급·지도·백분위에서 제외하고 화면에 "집계 제외" 표기. 데이터 자체는 적재. 수동면(고성군)',
  excluded_reason        VARCHAR(100)   NULL COMMENT '제외 사유 (is_excluded=true 일 때). 예: 특수시설 패턴',
  tags                   VARCHAR(100)   NOT NULL DEFAULT '' COMMENT '속성 태그. 자동: coast(해안선 1 km 이내), beach · island · surf · library · long_stay · park · market · resort(관광공사 장소 1곳 이상), cafe(카페 3곳 이상), food(음식점 10곳 이상). 팀 입력: city · mountain',
  coast_dist_km          DECIMAL(7,3)   NULL COMMENT '경계에서 가장 가까운 해안선까지 거리(km, OSM natural=coastline). 0 = 해안에 닿음, NULL = 내륙(약 18 km 이상) 또는 경계 없음',
  PRIMARY KEY (dong_id),
  UNIQUE (region_id, dong_name),
  CONSTRAINT fk_dong_region_id FOREIGN KEY (region_id) REFERENCES region (region_id)
) COMMENT = '읍면동 (204개, 집계 대상 203개). dong_id = region_id*100 + 시군구 내 가나다 순번';

CREATE TABLE population_hourly (
  dong_id                SMALLINT       NOT NULL COMMENT '',
  year                   SMALLINT       NOT NULL COMMENT '',
  month                  SMALLINT       NOT NULL COMMENT '',
  dow                    SMALLINT       NOT NULL COMMENT '요일 0=월 … 6=일',
  hour                   SMALLINT       NOT NULL COMMENT '시간대 코드 0~23. 그 시각부터 1시간',
  day_group              VARCHAR(7)     NOT NULL COMMENT 'weekday(월~금), weekend(토·일)',
  time_band              VARCHAR(7)     NOT NULL COMMENT 'night(23~05), morning(06~09), day(10~16), evening(17~22)',
  weekday_count          SMALLINT       NOT NULL COMMENT '그 달에 해당 요일이 몇 번 있는지 (4 또는 5)',
  stay_sum               DOUBLE         NULL COMMENT '원본 값: 그 달 해당 요일들의 해당 시간대 체류 인구 합',
  walk_sum               DOUBLE         NULL COMMENT '원본 값: 도보 이동 인구 합',
  driv_sum               DOUBLE         NULL COMMENT '원본 값: 차량 이동 인구 합',
  stay_avg               DOUBLE         NULL COMMENT 'stay_sum ÷ weekday_count. 해당 요일·시간대 평균 체류 인구(명)',
  walk_avg               DOUBLE         NULL COMMENT '평균 도보 이동 인구(명)',
  driv_avg               DOUBLE         NULL COMMENT '평균 차량 이동 인구(명)',
  PRIMARY KEY (dong_id, year, month, dow, hour),
  CONSTRAINT fk_population_hourly_dong_id FOREIGN KEY (dong_id) REFERENCES dong (dong_id)
) COMMENT = 'D1 유동인구 정제본 (읍면동 × 월 × 요일 × 시간대). 411,264행';

CREATE TABLE population_demographic (
  dong_id                SMALLINT       NOT NULL COMMENT '',
  year                   SMALLINT       NOT NULL COMMENT '',
  month                  SMALLINT       NOT NULL COMMENT '',
  sex                    VARCHAR(7)     NOT NULL COMMENT 'male, female, unknown',
  age_group              VARCHAR(7)     NOT NULL COMMENT 'under20, 20s, 30s, 40s, 50s, 60plus, unknown',
  is_unknown             TINYINT(1)     NOT NULL DEFAULT false COMMENT '성별·연령 미상 행. 거주·직장은 항상 0, 방문만 값 있음. 총량에는 포함, 성별·연령 비율에서는 제외',
  days_in_month          SMALLINT       NOT NULL COMMENT '그 달 일수',
  resd_sum               DOUBLE         NULL COMMENT '원본 값: 시간별 추정치의 월 누적 (거주)',
  wrk_sum                DOUBLE         NULL COMMENT '원본 값 (직장)',
  vist_sum               DOUBLE         NULL COMMENT '원본 값 (방문)',
  resd_avg               DOUBLE         NULL COMMENT 'resd_sum ÷ (일수 × 24). 일평균 거주 인구(명)',
  wrk_avg                DOUBLE         NULL COMMENT '일평균 직장 인구(명)',
  vist_avg               DOUBLE         NULL COMMENT '일평균 방문 인구(명)',
  PRIMARY KEY (dong_id, year, month, sex, age_group),
  CONSTRAINT fk_population_demographic_dong_id FOREIGN KEY (dong_id) REFERENCES dong (dong_id)
) COMMENT = 'D2 서비스인구 정제본 (읍면동 × 월 × 성별 × 연령). 31,824행';

CREATE TABLE dong_month_summary (
  dong_id                SMALLINT       NOT NULL COMMENT '',
  year                   SMALLINT       NOT NULL COMMENT '',
  month                  SMALLINT       NOT NULL COMMENT '',
  stay_weekday_night     DOUBLE         NULL COMMENT '',
  stay_weekday_morning   DOUBLE         NULL COMMENT '',
  stay_weekday_day       DOUBLE         NULL COMMENT '월~금 10:00~17:00 평균 체류 인구. 같은 패턴 {stay|walk|driv}_{weekday|weekend}_{night|morning|day|evening}',
  stay_weekday_evening   DOUBLE         NULL COMMENT '',
  stay_weekend_night     DOUBLE         NULL COMMENT '',
  stay_weekend_morning   DOUBLE         NULL COMMENT '',
  stay_weekend_day       DOUBLE         NULL COMMENT '',
  stay_weekend_evening   DOUBLE         NULL COMMENT '',
  walk_weekday_night     DOUBLE         NULL COMMENT '',
  walk_weekday_morning   DOUBLE         NULL COMMENT '',
  walk_weekday_day       DOUBLE         NULL COMMENT '',
  walk_weekday_evening   DOUBLE         NULL COMMENT '',
  walk_weekend_night     DOUBLE         NULL COMMENT '',
  walk_weekend_morning   DOUBLE         NULL COMMENT '',
  walk_weekend_day       DOUBLE         NULL COMMENT '',
  walk_weekend_evening   DOUBLE         NULL COMMENT '',
  driv_weekday_night     DOUBLE         NULL COMMENT '',
  driv_weekday_morning   DOUBLE         NULL COMMENT '',
  driv_weekday_day       DOUBLE         NULL COMMENT '',
  driv_weekday_evening   DOUBLE         NULL COMMENT '',
  driv_weekend_night     DOUBLE         NULL COMMENT '',
  driv_weekend_morning   DOUBLE         NULL COMMENT '',
  driv_weekend_day       DOUBLE         NULL COMMENT '',
  driv_weekend_evening   DOUBLE         NULL COMMENT '',
  stay_weekday_all       DOUBLE         NULL COMMENT '요일그룹 전체(24시간) 평균. {stay|walk|driv}_{weekday|weekend}_all',
  stay_weekend_all       DOUBLE         NULL COMMENT '',
  walk_weekday_all       DOUBLE         NULL COMMENT '',
  walk_weekend_all       DOUBLE         NULL COMMENT '',
  driv_weekday_all       DOUBLE         NULL COMMENT '',
  driv_weekend_all       DOUBLE         NULL COMMENT '',
  walk_share             DOUBLE         NULL COMMENT '주중 도보 ÷ (주중 도보 + 주중 차량). 1에 가까울수록 걸어 다니는 동네',
  resd_total             DOUBLE         NULL COMMENT '거주 인구 합계 (일평균)',
  wrk_total              DOUBLE         NULL COMMENT '직장 인구 합계 (일평균)',
  vist_total             DOUBLE         NULL COMMENT '방문 인구 합계, 미상 포함. 총량 지표(붐빔·피크)에 사용',
  vist_known             DOUBLE         NULL COMMENT '방문 인구 합계, 미상 제외. 성별·연령 비율의 분모',
  vist_unknown           DOUBLE         NULL COMMENT '성별·연령 미상 방문 인구',
  vist_age_under20       DOUBLE         NULL COMMENT '',
  vist_age_20s           DOUBLE         NULL COMMENT '',
  vist_age_30s           DOUBLE         NULL COMMENT '',
  vist_age_40s           DOUBLE         NULL COMMENT '',
  vist_age_50s           DOUBLE         NULL COMMENT '',
  vist_age_60plus        DOUBLE         NULL COMMENT '',
  vist_female            DOUBLE         NULL COMMENT '',
  vist_male              DOUBLE         NULL COMMENT '',
  vist_2040              DOUBLE         NULL COMMENT '20·30·40대 방문 합',
  vist_2040_share        DOUBLE         NULL COMMENT 'vist_2040 ÷ vist_known',
  vist_per_resd          DOUBLE         NULL COMMENT 'vist_total ÷ resd_total. 높을수록 관광지 성격',
  PRIMARY KEY (dong_id, year, month),
  CONSTRAINT fk_dong_month_summary_dong_id FOREIGN KEY (dong_id) REFERENCES dong (dong_id)
) COMMENT = '읍면동 × 월 요약 (2,448행). 추천 점수 계산의 기본 재료';

CREATE TABLE dong_year_summary (
  dong_id                SMALLINT       NOT NULL COMMENT '',
  year                   SMALLINT       NOT NULL COMMENT '',
  months                 SMALLINT       NOT NULL COMMENT '집계된 월 수 (정상이면 12)',
  resd_mean              DOUBLE         NULL COMMENT '',
  wrk_mean               DOUBLE         NULL COMMENT '',
  vist_mean              DOUBLE         NULL COMMENT '',
  vist_max               DOUBLE         NULL COMMENT '월별 vist_total 최대',
  vist_min               DOUBLE         NULL COMMENT '월별 vist_total 최소',
  vist_std               DOUBLE         NULL COMMENT '월별 vist_total 표준편차',
  vist_peak_month        SMALLINT       NULL COMMENT '방문 최대 월 1~12',
  vist_low_month         SMALLINT       NULL COMMENT '방문 최소 월 1~12',
  vist_peak_ratio        DOUBLE         NULL COMMENT 'vist_max ÷ vist_min. 성수기/비수기 배율',
  vist_cv                DOUBLE         NULL COMMENT 'vist_std ÷ vist_mean. 변동 계수, 높을수록 계절 편차 큼',
  vist_per_resd_mean     DOUBLE         NULL COMMENT '',
  vist_2040_share_mean   DOUBLE         NULL COMMENT '',
  stay_weekday_day_mean  DOUBLE         NULL COMMENT '주중 낮 평균 체류 (한적함 지표)',
  walk_weekday_day_mean  DOUBLE         NULL COMMENT '주중 낮 평균 도보 (한적함 지표)',
  walk_weekday_evening_mean DOUBLE         NULL COMMENT '주중 저녁 평균 도보 (즐길 거리 지표)',
  walk_weekend_all_mean  DOUBLE         NULL COMMENT '주말 평균 도보 (즐길 거리 지표)',
  walk_share_mean        DOUBLE         NULL COMMENT '',
  PRIMARY KEY (dong_id, year),
  CONSTRAINT fk_dong_year_summary_dong_id FOREIGN KEY (dong_id) REFERENCES dong (dong_id)
) COMMENT = '읍면동 연간 요약 (204행)';

CREATE TABLE metric_def (
  metric_key             VARCHAR(20)    NOT NULL COMMENT '핵심: ease(시기 여유), quiet(동네 조용함), peer(또래 밀도), overnight(숙박 성향), evening(저녁 활기) / 보조: weekend_gap(주중·주말 차이), peak_share(성수기 집중도), local_life(생활권 지수)',
  name_ko                VARCHAR(30)    NOT NULL COMMENT '지표 한글명',
  description            TEXT           NULL COMMENT '',
  higher_is              VARCHAR(4)     NOT NULL COMMENT 'good, bad (점수 방향). v0.3 핵심 5종은 전부 good — 원값이 낮을수록 좋은 지표(ease·quiet)는 역백분위로 점수를 뒤집는다',
  is_core                TINYINT(1)     NOT NULL DEFAULT true COMMENT '핵심 지표 여부 (종합 value 계산에 들어감). 보조 3종은 false',
  PRIMARY KEY (metric_key)
) COMMENT = '지표 정의 (핵심 5 + 보조 3). 종합 value 는 프리셋별로 달라 정의·저장하지 않는다';

CREATE TABLE metric (
  scope                  VARCHAR(6)     NOT NULL COMMENT 'region, dong. 동네 성격 그룹(quiet·peer·evening)은 dong 원본 + region 집계(분자 합 ÷ 분모 합) 둘 다, 시기·체류 그룹(ease·overnight)은 region 만. overnight 은 연간(month 0)만',
  unit_id                SMALLINT       NOT NULL COMMENT 'region_id 또는 dong_id (논리 참조)',
  month                  SMALLINT       NOT NULL COMMENT '1~12, 0 = 연간',
  metric_key             VARCHAR(20)    NOT NULL COMMENT '',
  raw_value              DOUBLE         NULL COMMENT '원값 (카드 보조 표기). ease=월 혼잡 지수, quiet=낮 유입 지수, peer=20~40대 비율, overnight=숙박 계수(동 고정효과 회귀 β), evening=저녁 보행÷저녁 체류',
  score                  SMALLINT       NULL COMMENT '백분위 점수 0~100. 같은 scope(시군구 12 / 행정동 203)·같은 달 안에서, 높을수록 좋음',
  grade                  CHAR(1)        NULL COMMENT 'S(상위 25%), A(25~50%), B(50~75%), C(하위 25%). 경계는 실데이터 보고 조정',
  PRIMARY KEY (scope, unit_id, month, metric_key),
  CONSTRAINT fk_metric_metric_key FOREIGN KEY (metric_key) REFERENCES metric_def (metric_key)
) COMMENT = '지표 산출값 (시군구·읍면동 × 월 × 지표). 종합 value 는 프리셋(quiet·lively·custom) 가중 평균이라 저장하지 않고 백엔드(Django)가 요청 시 계산';

CREATE TABLE dong_hourly (
  dong_id                SMALLINT       NOT NULL COMMENT '',
  month                  SMALLINT       NOT NULL COMMENT '1~12',
  day_group              VARCHAR(7)     NOT NULL COMMENT 'weekday, weekend',
  hour                   SMALLINT       NOT NULL COMMENT '0~23',
  index_100              SMALLINT       NULL COMMENT '보행인구 지수. 같은 dong·month·day_group 안에서 최대 시간대 = 100',
  PRIMARY KEY (dong_id, month, day_group, hour),
  CONSTRAINT fk_dong_hourly_dong_id FOREIGN KEY (dong_id) REFERENCES dong (dong_id)
) COMMENT = '읍면동 24시간 붐빔 곡선. population_hourly.walk_avg(보행인구)를 월·요일그룹·시간대로 평균';

CREATE TABLE dong_feature (
  dong_id                SMALLINT       NOT NULL COMMENT '',
  month                  SMALLINT       NOT NULL COMMENT '0 = 연간, 1~12',
  metric_vec             TEXT           NOT NULL COMMENT '[동네 조용함 점수, 또래 밀도 점수, 저녁 활기 점수, 생활권 지수] 모두 0~1 (PostgreSQL 은 vector(4))',
  curve_vec              TEXT           NOT NULL COMMENT '24시간 붐빔 곡선 [주중 0~23시, 주말 0~23시] index_100 ÷ 100 (PostgreSQL 은 vector(48))',
  updated_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (dong_id, month),
  CONSTRAINT fk_dong_feature_dong_id FOREIGN KEY (dong_id) REFERENCES dong (dong_id)
) COMMENT = '행정동 × 월 특징 벡터 (pgvector). 비슷한 동네 찾기용. db/04_dong_feature.sql 이 metric · dong_hourly 에서 계산';

CREATE TABLE tag_vocab (
  vocab_id               INT            NOT NULL AUTO_INCREMENT COMMENT '',
  target_kind            VARCHAR(6)     NOT NULL COMMENT 'tag = dong.tags 코드, metric = metric_def.metric_key',
  target_key             VARCHAR(20)    NOT NULL COMMENT '',
  direction              SMALLINT       NOT NULL DEFAULT 1 COMMENT '지표 방향. 1 = 점수 높은 곳, -1 = 점수 낮은 곳. 태그는 항상 1',
  phrase                 VARCHAR(50)    NOT NULL COMMENT '',
  embedding              TEXT           NULL COMMENT 'PostgreSQL 은 vector(1024)',
  model                  VARCHAR(40)    NULL COMMENT '임베딩 모델 이름 (예: clova-embedding-v2)',
  updated_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (vocab_id),
  UNIQUE (target_kind, target_key, direction, phrase)
) COMMENT = '자연어 표현 사전. 사용자 질문 조각을 임베딩해 가장 가까운 표현의 조건(태그·지표)으로 바꾼다. 원본은 db/seed/tag_vocab.csv';

CREATE TABLE dong_doc (
  dong_id                SMALLINT       NOT NULL COMMENT '',
  body                   TEXT           NOT NULL COMMENT '지표 등급·태그·붐비는 시간·조용한 달로 만든 문장. db/05_dong_doc.sql 이 생성',
  embedding              TEXT           NULL COMMENT 'body 의 임베딩. PostgreSQL 은 vector(1024)',
  model                  VARCHAR(40)    NULL COMMENT '',
  updated_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (dong_id),
  CONSTRAINT fk_dong_doc_dong_id FOREIGN KEY (dong_id) REFERENCES dong (dong_id)
) COMMENT = '행정동 소개문 (연간 기준 203곳). 모호한 질문의 보조 검색과 LLM 답변 근거';

CREATE TABLE region_link (
  link_id                INT AUTO_INCREMENT NOT NULL COMMENT '',
  region_id              SMALLINT       NOT NULL COMMENT '',
  kind                   VARCHAR(10)    NOT NULL COMMENT 'support, stay, tour',
  title                  VARCHAR(100)   NOT NULL COMMENT '',
  url                    TEXT           NOT NULL COMMENT '',
  PRIMARY KEY (link_id),
  CONSTRAINT fk_region_link_region_id FOREIGN KEY (region_id) REFERENCES region (region_id)
) COMMENT = '지원사업·숙박·관광 외부 링크 (수동 입력)';

CREATE TABLE app_user (
  user_id                CHAR(36)       NOT NULL COMMENT '',
  provider               VARCHAR(10)    NOT NULL COMMENT 'kakao, google, apple',
  provider_uid           VARCHAR(100)   NOT NULL COMMENT '소셜 제공자 측 사용자 ID',
  nickname               VARCHAR(20)    NOT NULL COMMENT '닉네임 (공개)',
  avatar_url             TEXT           NULL COMMENT '아바타 URL (공개)',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (user_id),
  UNIQUE (provider, provider_uid)
) COMMENT = '사용자 (소셜 로그인). user_id = Supabase auth.users.id';

CREATE TABLE favorite (
  user_id                CHAR(36)       NOT NULL COMMENT '',
  scope                  VARCHAR(6)     NOT NULL COMMENT 'region, dong',
  unit_id                SMALLINT       NOT NULL COMMENT 'region_id 또는 dong_id (논리 참조)',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (user_id, scope, unit_id),
  CONSTRAINT fk_favorite_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) COMMENT = '즐겨찾기 (로그인 시 서버 동기화)';

CREATE TABLE photo (
  photo_id               INT AUTO_INCREMENT NOT NULL COMMENT '',
  scope                  VARCHAR(6)     NOT NULL COMMENT 'region, dong',
  unit_id                SMALLINT       NOT NULL COMMENT 'region_id 또는 dong_id (논리 참조)',
  month                  SMALLINT       NULL COMMENT '1~12 (촬영월, NULL = 월 무관)',
  url                    TEXT           NOT NULL COMMENT 'Cloudflare 공개 URL',
  source                 VARCHAR(8)     NOT NULL COMMENT 'user, tourapi, team',
  credit                 VARCHAR(100)   NULL COMMENT '출처 크레딧',
  user_id                CHAR(36)       NULL COMMENT 'source=user 일 때 업로더',
  status                 VARCHAR(8)     NOT NULL DEFAULT 'pending' COMMENT 'pending, approved, hidden',
  is_featured            TINYINT(1)     NOT NULL DEFAULT false COMMENT '이달의 사진',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (photo_id),
  CONSTRAINT fk_photo_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) COMMENT = '지역·동네 × 월 사진 (파일은 Cloudflare)';

CREATE TABLE tip (
  tip_id                 INT AUTO_INCREMENT NOT NULL COMMENT '',
  user_id                CHAR(36)       NOT NULL COMMENT '',
  region_id              SMALLINT       NOT NULL COMMENT '',
  dong_id                SMALLINT       NULL COMMENT 'NULL 이면 지역 단위 팁',
  visit_month            SMALLINT       NULL COMMENT '방문 월 1~12',
  stay_days              SMALLINT       NULL COMMENT '체류 기간 (일)',
  body                   VARCHAR(300)   NOT NULL COMMENT '본문 300자',
  status                 VARCHAR(8)     NOT NULL DEFAULT 'public' COMMENT 'public, hidden',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (tip_id),
  CONSTRAINT fk_tip_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id),
  CONSTRAINT fk_tip_region_id FOREIGN KEY (region_id) REFERENCES region (region_id),
  CONSTRAINT fk_tip_dong_id FOREIGN KEY (dong_id) REFERENCES dong (dong_id)
) COMMENT = '워케이션 노트 — 한 줄 팁';

CREATE TABLE tip_reaction (
  tip_id                 INTEGER        NOT NULL COMMENT '',
  user_id                CHAR(36)       NOT NULL COMMENT '',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (tip_id, user_id),
  CONSTRAINT fk_tip_reaction_tip_id FOREIGN KEY (tip_id) REFERENCES tip (tip_id),
  CONSTRAINT fk_tip_reaction_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) COMMENT = '도움돼요';

CREATE TABLE tip_reply (
  reply_id               INT AUTO_INCREMENT NOT NULL COMMENT '',
  tip_id                 INTEGER        NOT NULL COMMENT '',
  user_id                CHAR(36)       NOT NULL COMMENT '',
  body                   VARCHAR(300)   NOT NULL COMMENT '',
  status                 VARCHAR(8)     NOT NULL DEFAULT 'public' COMMENT 'public, hidden',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (reply_id),
  CONSTRAINT fk_tip_reply_tip_id FOREIGN KEY (tip_id) REFERENCES tip (tip_id),
  CONSTRAINT fk_tip_reply_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) COMMENT = '팁 답글';

CREATE TABLE report (
  report_id              INT AUTO_INCREMENT NOT NULL COMMENT '',
  target_type            VARCHAR(6)     NOT NULL COMMENT 'tip, reply, photo',
  target_id              INTEGER        NOT NULL COMMENT 'tip_id / reply_id / photo_id (논리 참조)',
  user_id                CHAR(36)       NOT NULL COMMENT '',
  reason                 VARCHAR(30)    NOT NULL COMMENT '신고 사유',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (report_id),
  UNIQUE (target_type, target_id, user_id),
  CONSTRAINT fk_report_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) COMMENT = '신고 (3건 이상 자동 숨김)';

CREATE TABLE metric_vote (
  user_id                CHAR(36)       NOT NULL COMMENT '',
  scope                  VARCHAR(6)     NOT NULL COMMENT 'region, dong',
  unit_id                SMALLINT       NOT NULL COMMENT 'region_id 또는 dong_id (논리 참조)',
  month                  SMALLINT       NOT NULL COMMENT '',
  metric_key             VARCHAR(20)    NOT NULL COMMENT '',
  agree                  TINYINT(1)     NOT NULL COMMENT 'true = 그래요',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (user_id, scope, unit_id, month, metric_key),
  CONSTRAINT fk_metric_vote_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id),
  CONSTRAINT fk_metric_vote_metric_key FOREIGN KEY (metric_key) REFERENCES metric_def (metric_key)
) COMMENT = '지표 체감 투표 (그래요/아니요)';

CREATE TABLE share_card (
  card_id                INT AUTO_INCREMENT NOT NULL COMMENT '',
  user_id                CHAR(36)       NULL COMMENT '비로그인 생성 시 NULL',
  region_id              SMALLINT       NOT NULL COMMENT '',
  month                  SMALLINT       NULL COMMENT '',
  conditions             JSON           NOT NULL COMMENT '프리셋·가중치·태그 JSON',
  image_url              TEXT           NOT NULL COMMENT '1080×1350 PNG',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (card_id),
  CONSTRAINT fk_share_card_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id),
  CONSTRAINT fk_share_card_region_id FOREIGN KEY (region_id) REFERENCES region (region_id)
) COMMENT = 'F-16 공유 카드 (유입 링크 복원용)';

CREATE TABLE nl_search_log (
  search_id              INT AUTO_INCREMENT NOT NULL COMMENT '',
  user_id                CHAR(36)       NULL COMMENT '',
  query                  VARCHAR(300)   NOT NULL COMMENT '사용자 입력 문장',
  parsed                 JSON           NULL COMMENT 'LLM 이 변환한 조건·가중치 JSON',
  created_at             DATETIME       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '',
  PRIMARY KEY (search_id),
  CONSTRAINT fk_nl_search_log_user_id FOREIGN KEY (user_id) REFERENCES app_user (user_id)
) COMMENT = 'F-09 자연어 검색 로그 (파싱 품질 검증용)';
