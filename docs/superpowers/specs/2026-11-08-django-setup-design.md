# Django 초기 세팅 설계

- 작성일: 2026-11-08
- 관련: 이슈 #2 (Supabase DB 접속 환경 설정), workation-docs `설계/시스템-아키텍처.md`, `협업/협업-규칙.md`
- 브랜치: `chore/django-setup`

## 1. 목표

팀원이 바로 API를 붙일 수 있는 Django 프로젝트를 만든다. Supabase에 이미 있는 테이블을 ORM 모델로 읽을 수 있고, 같은 검사를 로컬과 CI에서 돌릴 수 있어야 한다.

완료 기준:

- 로컬에서 `.env`에 Supabase 접속 정보를 넣고 `GET /api/health`가 `{"status": "ok", "db": "ok"}`를 돌려준다.
- `uv run pytest`가 로컬 Postgres 컨테이너에서 통과한다. 테스트는 Supabase에 붙지 않는다.
- GitHub Actions CI가 ruff 검사와 pytest를 돌려 초록색이다.
- `docker build`가 성공하고, 컨테이너가 Gunicorn으로 뜬다.

## 2. 범위

넣는 것:

- Django 프로젝트 뼈대, DRF, Supabase 연결 설정
- ORM 모델 5개 (`managed = False`): `Region`, `Dong`, `MetricDef`, `Metric`, `DongHourly`
- `GET /api/health`
- pytest + pytest-django, ruff, GitHub Actions CI
- `Dockerfile`, `.dockerignore`, `compose.yaml`(테스트용 Postgres), `.env.example`
- README "시작하기", `docs/개발-가이드.md`

넣지 않는 것 (이유):

- 모델을 실제로 쓰는 API: W-02 티어리스트 PR부터 만든다.
- 원천 데이터 모델(`population_*`, `dong_*_summary`): API가 직접 읽을 일이 생기면 넣는다.
- 검색·추천 모델(`dong_feature`, `tag_vocab`, `dong_doc`): pgvector 패키지와 pgvector 테스트 이미지가 필요하다. 자연어 검색 PR에서 함께 정한다.
- 사용자·커뮤니티 모델(`app_user` 등 10개): 웹 스펙 대비 스키마 빈 곳(email, 약관 동의, 알림 설정, 즐겨찾기 메모, 조회수 테이블, 사진 삭제 규칙)과 로그인 주체(Django OAuth인지 Supabase Auth인지)가 정해진 뒤 스키마 보강 → 모델 순서로 한다.
- `dong.geom`(PostGIS): GeoDjango는 지도 API에서 붙인다.
- Django 기본 앱(admin, auth, sessions): Supabase에 `migrate`를 돌릴 일을 만들지 않는다. 로그인(W-07) 작업에서 정한다.
- 배포 구성(앱 컨테이너 compose, Traefik, Redis): 배포 세팅 때 따로 한다.

## 3. 스택과 버전

팀 문서(협업 규칙, 시스템 아키텍처)에는 백엔드 버전이 정해져 있지 않다. 아래는 2026-11-08 PyPI · endoflife.date 조회 기준이다.

| 항목 | 선택 | 이유 |
| --- | --- | --- |
| Python | 하한 `>=3.12`, Docker·CI는 **3.14** 고정 | 3.13은 2026-10-01에 버그 수정이 끝났다(보안만 2029-10). 3.14는 버그 수정 2027-10까지이고 Django 5.2가 지원하는 최고 버전이다(3.15는 미지원). 하한 3.12는 workation-data와 같다 |
| 패키지 관리 | uv 0.13 (`pyproject.toml` + `uv.lock`) | 빠르고 lock 파일로 재현 가능. 로컬 Python도 uv가 설치 |
| Django | 5.2 LTS (`>=5.2,<6`, 현재 5.2.19) | `metric`·`dong_hourly` 복합 기본키를 `CompositePrimaryKey`로 표현(5.2부터). 보안 지원 2028-04-30. 6.1(보안 2027-12)은 pytest-django 4.14·django-environ 0.14의 지원 목록에 아직 없다 |
| DRF | 3.18 (`>=3.18,<4`) | 팀 결정. Django 5.2~6.1, Python 3.10~3.15 지원 |
| DB 드라이버 | `psycopg[binary]` 3.3 | Django 5.2 권장, workation-data와 같은 드라이버 |
| WSGI | Gunicorn 26 gthread 워커 | 아키텍처 문서 결정 (LLM 호출 중 다른 요청이 막히지 않게) |
| 설정 읽기 | `django-environ` 0.14 | `.env`와 환경변수를 같은 방식으로 읽음. Django 5.2 지원 |
| 테스트 | pytest 9.2, pytest-django 4.14 | pytest-django는 Django 5.2·6.0 지원 |
| 린트·포맷 | ruff 0.17 | black·isort·flake8 대신 하나로. workation-data도 ruff |
| 테스트 DB | `postgres:17` 이미지 | Supabase가 PostgreSQL 17 |

정확한 버전은 `uv.lock`으로 고정한다. 하한만 `pyproject.toml`에 적는다.

## 4. 디렉터리 구조

```
workation-backend/
  config/            settings.py, urls.py, wsgi.py
  core/              health 뷰·URL (모델 없음)
  regions/           Region, Dong, DongHourly
  metrics/           MetricDef, Metric
  tests/             conftest.py, test_health.py, test_models.py
  db/                (기존) 스키마 SQL — 스키마의 기준
  docs/개발-가이드.md
  pyproject.toml, uv.lock, manage.py
  Dockerfile, .dockerignore, compose.yaml, .env.example
  .github/workflows/backend-ci.yml
```

- 설정은 `config/settings.py` 하나에 두고 환경변수로 나눈다. base/dev/prod 분리는 필요해질 때 한다.
- 앱 이름은 도메인 단위다. 이후 화면 API는 해당 앱의 `views.py`·`serializers.py`·`urls.py`에 붙인다.

## 5. 설정

`.env.example` (값 없이 이름만):

```
DJANGO_SECRET_KEY=
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DB_HOST=
DB_PORT=5432
DB_NAME=postgres
DB_USER=
DB_PASSWORD=
```

- DB 접속은 URL 하나가 아니라 항목별 변수로 받는다. 비밀번호에 `@`, `!` 같은 기호가 있어도 인코딩이 필요 없다 (이슈 #2).
- Supabase는 **Session pooler(포트 5432)** 로 붙는다. Transaction pooler(6543)는 prepared statement 문제가 있어 쓰지 않는다.
- `INSTALLED_APPS`: `rest_framework`, `core`, `regions`, `metrics`만. `django.contrib.*`는 넣지 않는다.
- DRF는 인증 앱 없이 동작하도록 `DEFAULT_AUTHENTICATION_CLASSES = []`, `DEFAULT_PERMISSION_CLASSES = [AllowAny]`, `UNAUTHENTICATED_USER = None`, 렌더러는 JSON만.
- `TIME_ZONE = "Asia/Seoul"`, `USE_TZ = True`, `LANGUAGE_CODE = "ko-kr"`.

## 6. 모델

모두 `managed = False`, `db_table`은 실제 테이블 이름. 컬럼은 `db/schema.sql`을 보고 손으로 옮긴다 (`inspectdb` 결과를 그대로 쓰지 않음). 모델은 DB에 있는 컬럼의 부분집합이어도 되지만, DB에 없는 컬럼을 가지면 안 된다.

| 모델 | 테이블 | 기본키 | 관계 |
| --- | --- | --- | --- |
| `Region` | `region` | `region_id` | |
| `Dong` | `dong` | `dong_id` | `region` → `Region` (FK, `db_column="region_id"`, `related_name="dongs"`) |
| `DongHourly` | `dong_hourly` | `CompositePrimaryKey("dong_id", "month", "day_group", "hour")` | `dong` → `Dong` (FK, `db_column="dong_id"`) |
| `MetricDef` | `metric_def` | `metric_key` | |
| `Metric` | `metric` | `CompositePrimaryKey("scope", "unit_id", "month", "metric_def_id")` | `metric_def` → `MetricDef` (FK, `db_column="metric_key"`) |

- FK가 복합키에 들어가는 경우 `CompositePrimaryKey`에는 필드의 attname(`dong_id`, `metric_def_id`)을 쓴다.
- `dong.geom`은 모델에 넣지 않는다.
- `tags`는 `ArrayField(TextField)` (`django.contrib.postgres.fields`, 앱 등록 없이 필드만 사용).
- `Metric.unit_id`는 DB에 FK가 없는 논리 참조라 모델에서도 정수 필드로 둔다. 시군구·행정동 이름은 따로 조회해 합친다.
- `CHECK` 제약 값(`scope`, `grade`, `day_group`, `higher_is`)은 `choices`로 옮긴다.
- 각 앱은 `makemigrations`로 만든 마이그레이션 파일을 커밋한다. `managed = False`라 실행해도 테이블을 만들지 않는다.

## 7. 헬스체크

`GET /api/health`

- DB에 `SELECT 1`을 보내 성공하면 200 `{"status": "ok", "db": "ok"}`.
- 실패하면 503 `{"status": "error", "db": "unavailable"}`. 예외 메시지(접속 정보가 들어 있을 수 있음)는 응답에 넣지 않고 로그로만 남긴다.

## 8. 테스트

- 테스트 DB는 로컬 `compose.yaml`의 `postgres:17` 컨테이너, CI는 같은 이미지의 서비스 컨테이너다. 접속 정보는 테스트 전용 기본값(`localhost:5432`, `postgres/postgres`)을 쓴다.
- `managed = False` 모델은 Django가 테스트 DB에 테이블을 만들지 않는다. `tests/conftest.py`에서 pytest-django의 `django_db_setup` 픽스처를 덮어써, 테스트 DB 생성 직후 **`db/schema.sql`을 그대로 실행**한다. `schema.sql`에는 Supabase 전용 구문(확장·롤·`auth` 스키마)이 없고, 일반 PostgreSQL 16에 적용한 기록이 있다(`db/README.md` 적재 흐름). 모델과 스키마가 어긋나면 이 단계 이후 테스트가 깨진다.
- 테스트 목록:
  - `test_health`: DB 정상 시 200과 응답 본문. DB 실패를 흉내 내면 503이고 본문에 예외 문구가 없다.
  - `test_models`: 모델 5개 각각 한 행을 저장하고 다시 읽는다 (테스트 DB에서만). `Dong.region`·`Region.dongs` 관계, `Metric` 복합키 조회(`Metric.objects.get(scope=…, unit_id=…, month=…, metric_def_id="quiet")`), `tags` 배열 읽기, `is_excluded` 기본값 false.
- Supabase 연결 확인(`select count(*) from dong` → 204)은 자동 테스트가 아니라 개발 가이드의 수동 확인 절차로 둔다.

## 9. 린트·포맷

- `pyproject.toml`의 `[tool.ruff]`: `line-length = 100`, `target-version = "py312"`, 규칙 `E, F, I, UP, B, DJ`. 마이그레이션 파일은 제외.
- 커밋 전 명령: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest`.

## 10. CI

`.github/workflows/backend-ci.yml`

- 트리거: main으로 가는 PR, main push (다른 레포와 같음).
- 단계: checkout → `astral-sh/setup-uv` (Python 3.14, 캐시) → `uv sync --frozen` → `ruff check` → `ruff format --check` → `pytest` (`postgres:17` 서비스 컨테이너, health check 대기).
- 비밀값 없음. CI는 Supabase에 붙지 않는다.

## 11. Docker

- `Dockerfile`: `python:3.14-slim` 기반. uv로 `uv sync --frozen --no-dev` 설치, 비루트 사용자, `EXPOSE 8000`, `CMD gunicorn config.wsgi --worker-class gthread --workers 2 --threads 4 --bind 0.0.0.0:8000`. `collectstatic`은 하지 않는다 (API 전용).
- `.dockerignore`: `.git`, `.venv`, `.env`, `db/`, `docs/`, `tests/`, 캐시 폴더.
- `compose.yaml`: 테스트용 `postgres:17` 서비스 하나 (포트 5432, `postgres/postgres`). 앱·Traefik·Redis는 배포 세팅 때 추가한다.

## 12. 문서

- README "시작하기": uv 설치 → `uv sync` → `.env` 작성 → `uv run python manage.py runserver` → `/api/health` 확인, 테스트 실행 방법.
- `docs/개발-가이드.md` (협업 규칙이 각 레포에 두라고 한 문서): 로컬 검사 명령, 모델 규칙(`managed = False`, 스키마 변경은 `db/` SQL이 먼저 — `db/README.md` 스키마 변경 규칙), 새 API 추가 위치, Supabase 수동 연결 확인 절차.
- PR 본문에 남길 것: 이슈 #2의 Supabase 조직 초대 수락은 팀원 각자 할 일, 리전은 도쿄 유지, 협업 규칙의 backend CI 칸은 workation-docs에 별도 PR.

## 13. 위험과 대응

| 위험 | 대응 |
| --- | --- |
| `CompositePrimaryKey`가 `managed = False` 모델이나 FK가 있는 모델에서 예상대로 동작하지 않음 | `test_models`에서 복합키 저장·조회를 먼저 확인한다. 막히면 해당 모델만 기본키 없이 조회 전용으로 두고 `.values()` 쿼리로 대체한다 |
| `schema.sql`이 나중에 Supabase 전용 구문을 갖게 됨 | 테스트가 깨져서 바로 드러난다. 그때 테스트 로드용 분기를 정한다 |
| 접속 정보가 로그·응답에 노출 | 헬스체크는 예외 문구를 응답에 넣지 않는다. `.env`는 이미 `.gitignore`에 있다 |
