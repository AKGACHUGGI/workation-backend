-- 지표 식 확정 · 백엔드 Django 전환 · 사진 저장소 Cloudflare · 2026-11-07
-- 기준: migrations/20261107_metric-v0.3.sql 이 적용된 DB. 주석(COMMENT)만 바꾼다. 두 번 실행해도 안전.
--
-- 바뀌는 것
--   1. evening(저녁 활기) 원값 = 평일 저녁 보행 ÷ 평일 저녁 체류 (명세 v0.3 의 '÷ 낮 체류' 대체)
--   2. overnight(숙박 성향) 원값 = 숙박 계수 β. 시군구별 동 고정효과 회귀(심야 체류 ~ 방문 + 거주)로
--      월 변동에서 추정하므로 연간(month 0) 한 값만 둔다. 계산은 db/03_metrics.sql
--   3. 종합 value 를 계산하는 백엔드를 FastAPI → Django 로 표기
--   4. photo 파일 저장소를 Supabase Storage → Cloudflare 로 표기

BEGIN;

COMMENT ON TABLE  metric            IS '지표 산출값 (시군구·읍면동 × 월 × 지표). 종합 value 는 프리셋(quiet·lively·custom) 가중 평균이라 저장하지 않고 백엔드(Django)가 요청 시 계산';
COMMENT ON COLUMN metric.scope      IS 'region, dong. 동네 성격 그룹(quiet·peer·evening)은 dong 원본 + region 집계(분자 합 ÷ 분모 합) 둘 다, 시기·체류 그룹(ease·overnight)은 region 만. overnight 은 연간(month 0)만';
COMMENT ON COLUMN metric.raw_value  IS '원값 (카드 보조 표기). ease=월 혼잡 지수, quiet=낮 유입 지수, peer=20~40대 비율, overnight=숙박 계수(동 고정효과 회귀 β), evening=저녁 보행÷저녁 체류';

COMMENT ON TABLE  photo             IS '지역·동네 × 월 사진 (파일은 Cloudflare)';
COMMENT ON COLUMN photo.url         IS 'Cloudflare 공개 URL';

COMMIT;
