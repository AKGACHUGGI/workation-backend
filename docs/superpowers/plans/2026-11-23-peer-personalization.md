# 또래 밀도 연령대 개인화 구현 계획

- 작성일: 2026-11-23
- 설계: `/Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/docs/superpowers/specs/2026-11-23-peer-personalization-design.md` (이하 "설계")
- 설계 전제: 설계 문서의 `## 설계 전제` 절을 따른다. 여기에 다시 쓰지 않는다.
- 저장소: 백엔드 `/Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend`, 웹 `/Users/doo._.hyun/Study/Project/Workcation/repos/workation-web`

## 1. 목표·비범위·완료 조건

목표와 비범위는 설계 1절 그대로다. 완료 조건은 설계 9절의 1~6 이며, 각 조건이 아래 태스크·게이트에 대응한다.

| 완료 조건 (설계 9절) | 대응 |
|---|---|
| 1 백엔드 테스트·ruff | T1, T2 표적 검증 + G1·G2 통합 게이트 |
| 2 새 테스트 단언 | T1(점수·계정), T2(API·더미) |
| 3 웹 테스트·tsc·lint | T3 + G1 통합 게이트 |
| 4 공유 DB 적용과 점검 쿼리 0 | W3 실환경 검증 1~5단계 |
| 5 `dummy_users verify` 종료 코드 0 | W3 실환경 검증 6~8단계 |
| 6 온보딩 화면 사용자 확정 | W3 화면 확인 루프 → T4 |

## 2. 실행 모델

- 메인 세션은 오케스트레이터다. 스폰, 리뷰, 머지 게이트, 공유 DB 작업만 한다. 구현은 아래 실행 프롬프트를 받은 세션(터미널) 또는 서브에이전트가 한다.
- 모델: 상위 = `claude-opus-5-5` 추론 high, 표준 = `claude-opus-5-5` 추론 medium. 두 저장소에 프로젝트 지침 파일(CLAUDE.md·AGENTS.md)이 없어 전역 정의를 따른다.

| 태스크 | 모델 | 사유 |
|---|---|---|
| T1 백엔드 계약 | 상위 | 배치 SQL 의 백분위 정합성, 점수 함수의 캐시 오염 방지, 필수 검증 규칙. 틀려도 조용히 잘못된 점수가 나가는 영역 |
| T2 백엔드 API·더미 | 표준 | T1 계약을 호출부에 넘기는 일과 관리 명령. 판단은 설계에 고정됨 |
| T3 웹 계약·온보딩 초안 | 표준 | 타입·훅·화면 초안 |
| T4 웹 확정 | 표준 | 사용자 확정 뒤 구조 단언 테스트 |
| 리뷰 (태스크별·최종) | 상위 | 머지 게이트 |

## 3. 웨이브와 의존성

| 웨이브 | 태스크 | 저장소 | 선행 | 병렬 |
|---|---|---|---|---|
| W0 | 사전 조건 (오케스트레이터) | 둘 다 | 없음 | |
| W1 | T1 백엔드 계약 | 백엔드 | W0 | T3 와 병렬 |
| W1 | T3 웹 계약·온보딩 초안 | 웹 | W0 | T1 과 병렬 |
| G1 | T1·T3 리뷰와 머지 | 둘 다 | W1 | |
| W2 | T2 백엔드 API·더미 명령 | 백엔드 | T1 머지 | 단독 |
| G2 | T2 리뷰와 머지, 백엔드 전체 회귀 | 백엔드 | W2 | |
| W3 | 실환경 검증 (오케스트레이터, 직렬) + 화면 확인 루프 | 공유 DB·로컬 서버 | G1, G2 | 단독 |
| W4 | T4 웹 확정 | 웹 | 사용자 "확정"과 T3-final 머지 | 단독 |
| G3 | 최종 전체 리뷰 | 둘 다 | W4 | |

T1 과 T3 는 저장소가 달라 쓰기 파일이 겹치지 않는다. T3 가 의존하는 API 계약(`GET/PATCH /api/me` 의 `ageGroup`, 오류 코드)은 설계 5-4절에 고정돼 있어 T1 을 기다리지 않는다. T2 는 T1 이 바꾸는 `metrics/scoring.py`·`accounts/models.py` 와 T1 테스트 픽스처를 쓰므로 T1 머지 뒤에 시작한다.

터미널 분할: W1 에 터미널 2개(T1, T3), W2 에 1개(T2), W4 에 1개(T4). 터미널 1개 = worktree 1개 = 브랜치 1개. 화면 확인 루프의 worktree 는 오케스트레이터가 직접 쓴다(7절).

## 4. W0 사전 조건 (오케스트레이터)

이 계획을 쓰는 시점에 두 저장소 `main` 에 커밋되지 않은 변경(푸터 삭제, 예시 칩, 프리셋 라벨, placeTags, 검색 place 축)이 있다. T1·T2 가 같은 파일(`webapi/views.py`, `tests/test_webapi.py`)을 건드리므로 먼저 정리해야 한다.

1. 사용자에게 미커밋 변경을 어떻게 할지 묻는다(작업 단위 커밋 또는 별도 브랜치). 사용자 답 없이 커밋하지 않는다.
2. 두 저장소 `git status --short` 가 깨끗해진 뒤, 백엔드 루트에서 설계·계획 두 파일만 커밋한다. 커밋 메시지: `또래 밀도 개인화 설계·구현 계획 추가`.
3. 백엔드에 통합 대장 `docs/task-integration-ledger.tsv` 를 빈 파일로 만들어 같은 커밋에 넣는다. 형식은 줄마다 `<태스크>\t<SHA>`.
4. 사람 몫 사전 조건 확인 2줄:
   - 백엔드 루트 `git log --oneline -1` 의 메시지가 `또래 밀도 개인화 설계·구현 계획 추가` 인가
   - 두 저장소 `git status --short` 가 비었는가

## 5. 태스크

### T1 백엔드 계약 (W1)

입력: 설계 4-2, 4-3, 5-1, 5-4절.
산출 계약: `scoring.AGE_GROUPS`, `scoring.PEER_BASIS`, `scoring.PEER_BASES`, `scoring.peer_basis()`, `scoring.unit_scores(scope, month, basis=None)`, `AppUser.age_group`, `GET/PATCH /api/me` 의 `ageGroup` 과 오류 코드 `INVALID_AGE_GROUP`·`AGE_GROUP_REQUIRED`, 테스트 픽스처 `tests/test_peer_personalization.py::peer_data`.

허용 Files
- 생성: `db/migrations/20261123_peer-personalization.sql`, `tests/test_peer_personalization.py`, `tests/test_me_age_group.py`
- 수정: `db/03_metrics.sql`, `db/schema.sql`, `db/README.md`, `metrics/scoring.py`, `accounts/models.py`, `accounts/views.py`, `tests/test_accounts_api.py`

수정 금지: `webapi/`, `regions/`, `metrics/views.py`, `explore/`, 설계·계획 문서.

구현 사양
- 마이그레이션 파일은 설계 4-2절 코드 블록을 그대로 쓴다.
- `db/03_metrics.sql` 은 설계 4-3절의 여섯 군데를 그대로 바꾼다. 그 밖의 줄은 건드리지 않는다.
- `db/schema.sql` `CREATE TABLE app_user`: 설계 4-2절 마지막 문단대로.
- `db/README.md`: 설계 4-3절의 "구현 때 고칠 곳" 두 군데(파일 표 `03_metrics.sql` 행을 "`metric_def` 시드 14행(핵심 5 + 보조 3 + 또래 구간 6)" 으로, 지표 표에 `peer_<구간>` 행)만 고친다. "현재 상태" 절과 적용 이력 표는 건드리지 않는다(W3 에서 오케스트레이터가 쓴다).
- `metrics/scoring.py`: 설계 5-1절 코드 그대로. `region_values`·`dong_kind`·`with_region_metrics` 는 바꾸지 않는다.
- `accounts/models.py`: `age_group = models.CharField(max_length=3, null=True)`.
- `accounts/views.py`: `serialize_me` 에 `"ageGroup": user.age_group`. `patch` 는 설계 5-4절의 검사 순서대로 모든 검사를 끝낸 뒤 한 번에 저장한다.
- `tests/test_accounts_api.py` 의 `test_patch_nickname_and_agree` 는 본문에 `"ageGroup": "30s"` 를 더한다. 그 밖의 단언은 바꾸지 않는다.

테스트 `tests/test_peer_personalization.py`

픽스처 (T2 가 import 한다. 이름과 값을 바꾸지 않는다):

```python
import pytest

from metrics import scoring
from metrics.models import Metric, MetricDef
from tests.test_api import data  # noqa: F401

BUCKET_KEYS = ("peer_under20", "peer_20s", "peer_30s", "peer_40s", "peer_50s", "peer_60plus")


@pytest.fixture
def peer_data(data):  # noqa: F811
    """test_api.data 위에 구간 값. 30s·60plus 만 넣는다 (다른 구간은 '자료 없음' 경로 확인용).
    시군구 1=고성(51820) 2=강릉(51150), 동 101=5182025300 201=5115060500, 월 10."""
    for key in BUCKET_KEYS:
        MetricDef.objects.create(metric_key=key, name_ko=key, higher_is="good")
    rows = [
        ("region", 1, "peer_30s", 10, "C", 0.05),
        ("region", 2, "peer_30s", 90, "S", 0.2),
        ("region", 1, "peer_60plus", 95, "S", 0.4),
        ("region", 2, "peer_60plus", 5, "C", 0.1),
        ("dong", 101, "peer_30s", 15, "C", 0.05),
        ("dong", 201, "peer_30s", 85, "S", 0.25),
    ]
    Metric.objects.bulk_create(
        Metric(scope=s, unit_id=u, month=10, metric_def_id=k, score=sc, grade=g, raw_value=r)
        for s, u, k, sc, g, r in rows
    )
    return data
```

이 파일은 `pytestmark = pytest.mark.django_db` 를 둔다. 테스트 이름과 단언 전량:
1. `test_peer_basis_maps_age_groups()`: `peer_basis("10s") == "under20"`, `peer_basis("30s") == "30s"`, `peer_basis("60s") == "60plus"`, `peer_basis("90s") == "60plus"`, `peer_basis(None) is None`, `peer_basis("99s") is None`.
2. `test_unit_scores_default_keeps_peer_and_hides_buckets(peer_data)`: `s = scoring.unit_scores("region", 10)`; `s[1]["peer"] == {"score": 40, "grade": "B", "raw": 0.3}`; 모든 단위의 모든 키가 `peer_` 로 시작하지 않는다.
3. `test_unit_scores_basis_swaps_peer(peer_data)`: `s = scoring.unit_scores("region", 10, "30s")`; `s[1]["peer"] == {"score": 10, "grade": "C", "raw": 0.05}`; `s[2]["peer"]["score"] == 90`; `s[1]["quiet"]["score"] == 80`; 모든 키가 `peer_` 로 시작하지 않는다.
4. `test_unit_scores_missing_bucket_drops_peer(peer_data)`: `"peer" not in scoring.unit_scores("region", 10, "20s")[1]`.
5. `test_unit_scores_does_not_pollute_cache(peer_data)`: `scoring.unit_scores("region", 10, "30s")` 를 부르고 돌려받은 dict 의 `[1]["peer"]` 를 `{"score": -1}` 로 바꾼 뒤, `scoring.unit_scores("region", 10)[1]["peer"]["score"] == 40` 이고 `scoring.unit_scores("region", 10, "30s")[1]["peer"]["score"] == 10`.
6. `test_unit_scores_rejects_unknown_basis(peer_data)`: `pytest.raises(ValueError)` 로 `scoring.unit_scores("region", 10, "70s")`.
7. `test_unit_scores_dong_scope(peer_data)`: `scoring.unit_scores("dong", 10, "30s")[101]["peer"]["score"] == 15`, `[201]["peer"]["score"] == 85`, `scoring.unit_scores("dong", 10)[101]["peer"]["score"] == 40`.

테스트 `tests/test_me_age_group.py`

머리: `pytestmark = pytest.mark.django_db`. import: `from accounts.models import AppUser`, `from tests.supabase_tokens import supabase  # noqa: F401`, `from tests.test_accounts_api import get_me, patch_me`, `from tests.test_me_api import auth`. 모든 테스트 인자는 `(client, supabase)` 이고 첫 줄에서 `get_me(client, **auth())` 로 사용자를 만든다. `T = "2026-11-23T12:00:00+09:00"`.
1. `test_me_age_group_null_for_new_user`: `get_me` 응답 JSON 의 `ageGroup is None`.
2. `test_patch_age_group_saves`: `patch_me(client, {"ageGroup": "30s"}, **auth())` → 200, 응답 `ageGroup == "30s"`, `AppUser.objects.get().age_group == "30s"`.
3. `test_patch_age_group_rejects_unknown`: `{"ageGroup": "100s"}` 와 `{"ageGroup": 30}` 각각 → 400, `r.json()["error"]["code"] == "INVALID_AGE_GROUP"`, `AppUser.objects.get().age_group is None`.
4. `test_onboarding_requires_age_group`: `{"nickname": "두이두이", "agreedAt": T}` → 400, `code == "AGE_GROUP_REQUIRED"`, `AppUser.objects.get().agreed_at is None`, `nickname` 이 요청 전 값과 같다.
5. `test_invalid_nickname_reported_first`: `{"nickname": "x", "ageGroup": "100s", "agreedAt": T}` → 400, `code == "INVALID_NICKNAME"`.
6. `test_onboarding_with_age_group_ok`: `{"nickname": "두이두이", "agreedAt": T, "ageGroup": "20s"}` → 200, `needsOnboarding is False`, `ageGroup == "20s"`.
7. `test_agreed_user_with_age_may_resend_agreed_at`: 6 과 같은 요청 뒤 `{"agreedAt": T}` 만 → 200.

표적 검증: `TEST_DB_PORT=5439 uv run pytest -q tests/test_peer_personalization.py tests/test_me_age_group.py tests/test_accounts_api.py`
03 문법 검증(빈 스크래치 DB. 테스트가 03 을 실행하지 않으므로 따로 확인): (1) `docker compose -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/compose.yaml exec -T test-db psql -U postgres -c "DROP DATABASE IF EXISTS m03_check" -c "CREATE DATABASE m03_check"` (2) `docker compose -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/compose.yaml exec -T test-db psql -U postgres -d m03_check -v ON_ERROR_STOP=1 -f - < db/schema.sql` (3) `docker compose -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/compose.yaml exec -T test-db psql -U postgres -d m03_check -v ON_ERROR_STOP=1 -f - < db/03_metrics.sql`. 셋 다 종료 코드 0.
전체 회귀: `TEST_DB_PORT=5439 uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`
실패 시 보고 경계: 설계 5-1절 시그니처를 바꿔야 할 것 같으면 바꾸지 말고 BLOCKED.

### T2 백엔드 API·더미 명령 (W2)

입력: 설계 5-2, 5-3, 5-5절, T1 산출 계약.

허용 Files
- 수정: `webapi/views.py`, `webapi/me.py`, `webapi/community.py`, `.env.example`
- 생성: `accounts/management/__init__.py`, `accounts/management/commands/__init__.py`, `accounts/management/commands/dummy_users.py`, `tests/test_peer_api.py`, `tests/test_dummy_users.py`

수정 금지: `metrics/scoring.py`, `accounts/models.py`, `accounts/views.py`, `accounts/services.py`, `db/`, `tests/test_peer_personalization.py`.

구현 사양
- `webapi/views.py`: 설계 5-2절 `viewer_basis` 를 `viewer_id` 바로 아래에 둔다. 핸들러 4개(`tier`, `popular`, `region_detail`, `dong_detail`)에서 그 달 점수를 읽는 `scoring.unit_scores(scope, month)` 호출에 basis 를 넘긴다. 연간 동 점수(`unit_scores("dong", None)` 과 `annual = … if month else dong_scores`)는 설계 5-3절 규칙대로 그대로 둔다. `region_detail` 의 `data.region_monthly(rid, ("ease", "peer"))` 는 설계 5-3절 둘째 표대로 바꾼다.
- `webapi/community.py`: `favorites(user_id, month, basis=None)` 로 바꾸고 그 달 점수 호출 두 곳(`region_scores`, `dong_scores`)에 넘긴다.
- `webapi/me.py` `FavoritesView`: `community.favorites(request.user.user_id, month, scoring.peer_basis(request.user.age_group))`.
- `.env.example`: Supabase 설명 근처에 `# 더미 계정 검증용 (manage.py dummy_users). service role 키는 위 SUPABASE_SERVICE_ROLE_KEY 를 쓴다` 주석과 `SUPABASE_ANON_KEY=`, `DUMMY_USER_PASSWORD=` 두 줄.
- `dummy_users.py`: 설계 5-5절 그대로. 설정 출처(settings 와 os.environ 구분)와 모듈 함수 이름(`admin_create_or_get`, `admin_find`)을 지킨다. 삭제는 `accounts.services.delete_auth_user` 를 import 해 쓴다. HTTP 는 `urllib.request`, DB 쓰기는 `django.db.connection.cursor()`.

테스트 `tests/test_peer_api.py`

머리:

```python
import pytest

from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import auth, run
from tests.test_peer_personalization import peer_data  # noqa: F401

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer(client, supabase, peer_data):  # noqa: F811
    """로그인 사용자 1명을 만들고, set_age(age) 로 연령대를 바꾼다."""
    assert client.get("/api/me", **auth()).status_code == 200

    def set_age(age):
        run("UPDATE app_user SET age_group = %s", [age])

    return set_age


def peer(body, unit_id):
    return next(i for i in body["items"] if i["id"] == unit_id)["scores"]["peer"]
```

테스트 이름과 단언 전량:
1. `test_tier_anonymous_default(client, peer_data)`: `GET /api/tier?month=2025-10&scope=sigungu` 토큰 없이 → `peer(body, "51820") == 40`, `peer(body, "51150") == 70`.
2. `test_tier_30s_viewer(client, viewer)`: `viewer("30s")`, 같은 요청 `**auth()` → `"51820"` 10, `"51150"` 90.
3. `test_tier_viewer_without_age_default(client, viewer)`: 연령대 없이 `**auth()` → `"51820"` 40.
4. `test_tier_bad_token_default(client, peer_data)`: `HTTP_AUTHORIZATION="Bearer garbage"` → 200, `"51820"` 40.
5. `test_tier_dong_scope_bucket(client, viewer)`: `viewer("30s")`, `scope=dong` → `"5182025300"` 15, `"5115060500"` 85.
6. `test_tier_missing_bucket_zero(client, viewer)`: `viewer("20s")` → `"51820"` 0.
7. `test_tier_70s_maps_to_60plus(client, viewer)`: `viewer("70s")` → `"51820"` 95, `"51150"` 5.
8. `test_popular_personalized(client, viewer)`: `viewer("30s")`, `GET /api/regions/popular?month=2025-10` `**auth()` → `"51820"` 10; 토큰 없이 → 40.
9. `test_dong_detail_personalized(client, viewer)`: 토큰 없이 `GET /api/dongs/5182025300?month=2025-10` → `body["stats"]["peerShare"] == 40`, `body["scores"]["peer"] == 40`; `viewer("30s")` 와 `**auth()` → `peerShare == 5`, `scores.peer == 15`.
10. `test_region_detail_peer_share(client, viewer)`: 토큰 없이 `GET /api/regions/51820?month=2025-10` → `body["stats"]["peerShare"] == 30`; `viewer("30s")` 와 `**auth()` → `5`.
11. `test_region_detail_reads_bucket_monthly(client, viewer, monkeypatch)`: `webapi.data.region_monthly` 를 호출한 `keys` 튜플을 목록에 기록하고 `{keys[0]: {m: 50.0 for m in range(1, 13)}, keys[1]: {m: (0.9 if m == 3 else 0.1) for m in range(1, 13)}}` 를 돌려주는 가짜로 바꾼다. `viewer("30s")` 와 `**auth()` 로 `GET /api/regions/51820?month=2025-10` → 200, 기록된 마지막 키가 `("ease", "peer_30s")`, `3 in body["peerOffpeakMonths"]`; 토큰 없이 같은 요청 → 기록된 마지막 키가 `("ease", "peer")`.
12. `test_favorites_personalized(client, viewer)`: `viewer("30s")`, `client.put("/api/me/favorites/regions/51820", **auth())` 후 `GET /api/me/favorites?month=2025-10` `**auth()` → `body["regions"][0]["scores"]["peer"] == 10`.

테스트 `tests/test_dummy_users.py` (네트워크 없이)

머리: `pytestmark = pytest.mark.django_db`. 공통 준비 픽스처 `env(settings, monkeypatch)`: `settings.SUPABASE_URL = "https://example.supabase.co"`, `settings.SUPABASE_SERVICE_ROLE_KEY = "service"`, `monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")`, `monkeypatch.setenv("DUMMY_USER_PASSWORD", "pw")`. Admin 함수는 `monkeypatch.setattr` 로 `accounts.management.commands.dummy_users` 모듈의 `admin_create_or_get`, `admin_find`, `delete_auth_user` 를 바꾼다. 이메일마다 고정 UUID 는 `uuid.uuid5(uuid.NAMESPACE_DNS, email)`.
1. `test_dummy_users_requires_service_key(env, settings)`: `settings.SUPABASE_SERVICE_ROLE_KEY = ""` → `pytest.raises(CommandError, match="SUPABASE_SERVICE_ROLE_KEY")` 로 `call_command("dummy_users", "create")`.
2. `test_dummy_users_create_is_idempotent(env, monkeypatch)`: 가짜 `admin_create_or_get` 로 `create` 를 두 번 → `provider='dummy'` 행이 정확히 10개, `dummy-30s@workcation.test` 행의 `age_group == "30s"`, `dummy-none@workcation.test` 행의 `age_group is None`, 10개 모두 `agreed_at is not None`.
3. `test_dummy_users_create_replaces_stale_id(env, monkeypatch)`: `create` 를 한 번 실행한 뒤, `dummy-30s` 만 다른 UUID 를 돌려주게 바꿔 다시 `create` → `provider='dummy'` 행 10개 유지, `dummy-30s` 행의 `user_id` 가 새 UUID.
4. `test_dummy_users_delete_auth_first(env, monkeypatch)`: `create` 뒤, 가짜 `admin_find` 는 고정 UUID, 가짜 `delete_auth_user` 는 호출을 기록하며 `dummy-50s` 의 UUID 에서만 `accounts.services.AuthAdminError` 를 낸다. `pytest.raises(CommandError)` 로 `delete` → 남은 `provider='dummy'` 행은 `dummy-50s` 1개, 가짜 삭제 함수가 10번 불렸다.

5. `test_dummy_users_delete_orphan_row(env, monkeypatch)`: `create` 뒤, 가짜 `admin_find` 가 `dummy-10s@workcation.test` 에만 None 을 돌려주고 나머지는 고정 UUID, 가짜 `delete_auth_user` 는 모두 성공 → `delete` 가 예외 없이 끝나고 `provider='dummy'` 행이 0개, 가짜 삭제 함수는 9번 불렸다.

표적 검증: `TEST_DB_PORT=5439 uv run pytest -q tests/test_peer_api.py tests/test_dummy_users.py`
전체 회귀: `TEST_DB_PORT=5439 uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`

### T3 웹 계약·온보딩 초안 (W1)

입력: 설계 6절.

허용 Files
- 생성: `src/features/auth/ageGroup.ts`, `src/features/auth/ageGroup.test.ts`, `src/features/auth/useInvalidateOnViewerChange.ts`, `src/features/auth/useInvalidateOnViewerChange.test.tsx`
- 수정: `src/features/auth/types.ts`, `src/features/auth/api.ts`, `src/features/auth/AuthProvider.tsx`, `src/features/auth/components/OnboardingModal.tsx`, `src/features/auth/components/OnboardingModal.test.tsx`, `src/mocks/auth/me.ts`, `src/mocks/auth/session.ts`, `src/components/ui/MonthPicker.tsx`(내부 함수 `MonthWheel` 을 export 하고 설계 6-2절의 선택 prop `ariaLabel`·`idPrefix`·`selected` 만 추가. 기본값이 지금 동작과 같아야 하며 `MonthPicker` 본문은 바꾸지 않는다), `src/features/me/components/AccountSection.test.tsx`(`Me` 리터럴에 `ageGroup: null` 추가만. 인증 외 폴더 수정 금지의 예외)

수정 금지: `src/lib/api.ts`, `src/hooks/useFocusTrap.ts`, `src/components/ui/Modal.tsx`, 그 밖의 인증 외 기능 폴더.

구현 사양
- 계약 (전량 고정):

```ts
// src/features/auth/ageGroup.ts
export const AGE_GROUPS = ['10s', '20s', '30s', '40s', '50s', '60s', '70s', '80s', '90s'] as const
export type AgeGroup = (typeof AGE_GROUPS)[number]
export const AGE_GROUP_LABEL: Record<AgeGroup, string> = {
  '10s': '10대', '20s': '20대', '30s': '30대', '40s': '40대', '50s': '50대',
  '60s': '60대', '70s': '70대', '80s': '80대', '90s': '90대',
}
```

- `Me` 에 `ageGroup: AgeGroup | null`, `PatchMeBody` 에 `ageGroup?: AgeGroup`. `src/mocks/auth/me.ts` 의 `DUI` 에 `ageGroup: null`. `src/features/auth/api.ts` 의 mock `patchMe` 는 `ageGroup: body.ageGroup ?? cur.ageGroup` 을 저장.
- `useInvalidateOnViewerChange.ts`: 설계 6-3절의 동작과 `isScoreQuery` 코드 그대로(`isScoreQuery` 도 export). 무효화 호출은 `queryClient.invalidateQueries({ predicate: (q) => isScoreQuery(q.queryKey) })`.
- 온보딩 화면은 화면 초안이다. 설계 6-2절 요구사항만 지키고 배치·문구·스타일은 재량이다. 화면 세부는 사용자 확인으로 바뀐다. 행동 테스트를 깨지 않는 범위에서 단순하게 만든다. 공유 계약(위 타입·훅, 설계 5-4절 API)을 화면 사정으로 고쳐야 하면 멈추고 BLOCKED 로 보고한다.

테스트 (행동만 단언. 새 테스트는 DOM 구조·문구를 고정하지 않는다)
- `ageGroup.test.ts`: `AGE_GROUPS.length === 9`, 첫 값 `'10s'`, 끝 값 `'90s'`, 모든 값에 라벨이 있다.
- `useInvalidateOnViewerChange.test.tsx` (`renderHook` + 실제 `QueryClient`, `invalidateQueries` 를 spy):
  1. `does nothing on first resolved value`: `undefined` → `null` → 호출 0.
  2. `does nothing on first load as logged-in user`: `undefined` → `{id:'u1', ageGroup:'30s'}` → 0.
  3. `invalidates when age group set`: `{u1,null}` → `{u1,'30s'}` → 1회.
  4. `invalidates on logout`: `{u1,'30s'}` → `null` → 1회.
  5. `ignores loading in between`: `{u1,'30s'}` → `undefined` → `{u1,'30s'}` → 0.
  6. `isScoreQuery whitelist`: true 여야 하는 키 `['tier',{month:'2025-10',scope:'sigungu'}]`, `['popular',{month:'2025-10'}]`, `['region','51820',{month:'2025-10'}]`, `['dong','5182025300',{month:'2025-10'}]`, `['me','favorites',{month:'2025-10'}]`; false 여야 하는 키 `['auth','me']`, `['region','51820','notes',{sort:'helpful'}]`, `['dong','5182025300','poll',{month:'2025-10'}]`, `['me','favorites','ids']`, `['geo','51820']`, `['explore','chat','5182025300']`.
- `OnboardingModal.test.tsx`: 기존 테스트 처리
  - 기존 1번(제목·닉네임·약관 표시): 연령대 휠이 보인다는 단언을 더한다. 제목 `'거의 다 됐어요'` 와 버튼 `'시작하기'` 로 찾는 쿼리는 초안에서도 그 문구를 유지해 그대로 둔다.
  - 기존 2번(약관 2개 체크 전 비활성): "약관 2개를 체크해도 연령대 전에는 비활성, 휠에서 연령대를 고르면 활성" 으로 바꾼다.
  - 기존 3번(제출 본문): 새 테스트 (3)으로 흡수하고 지운다.
  - 그 밖의 기존 테스트(닫기 시 로그아웃 등)는 그대로 둔다.
  - 새 테스트: (1) 이름이 `연령대` 인 listbox 가 있고 선택지가 10대~90대 9개다. (2) 처음에는 `aria-selected="true"` 인 선택지가 없고, 닉네임·약관 2개를 채워도 시작하기가 비활성이다. (3) 휠에서 `30대` 선택지를 누르면 그 선택지가 `aria-selected="true"` 가 되고, 제출하면 `patchMe` 가 `{ nickname: <닉네임>, agreedAt: expect.any(String), ageGroup: '30s' }` 로 불린다. (4) 온보딩이 열린 동안 화면의 `role="dialog"` 가 하나뿐이다(휠이 별도 모달로 뜨지 않는다).
  - `MonthPicker` 회귀: 기존 `src/components/ui` 테스트가 수정 없이 통과해야 한다(기본값이 지금 동작과 같다는 증거).

표적 검증: `pnpm vitest run src/features/auth src/components/ui src/features/me`
전체 회귀: `pnpm vitest run`, `pnpm tsc -b`, `pnpm lint`

### T4 웹 확정 (W4, 사용자 "확정" 뒤)

입력: W3 화면 확인 루프에서 사용자가 확정하고 웹 `main` 에 머지된 온보딩 화면(대장 `T3-final`).
허용 Files: `src/features/auth/components/OnboardingModal.test.tsx` 만. 화면 코드는 바꾸지 않는다.
할 일: 확정된 화면의 제목, 연령대 항목 라벨, 휠 처음 위치 문구, 제출 버튼 문구를 그대로 단언하는 테스트를 더한다.
검증: `pnpm vitest run`, `pnpm tsc -b`, `pnpm lint`

## 6. 게이트

- G1: T1·T3 각각 보고서와 허용 Files 밖 diff 가 없는지 확인(`git diff --stat <기점>..HEAD`), 태스크 리뷰 에이전트(상위 모델, `general-purpose`)가 설계 대비 리뷰, 수정 루프, 오케스트레이터가 표적·전체 검증을 직접 다시 돌린 뒤 각 저장소 `main` 에 머지. 백엔드 머지 직전 `ls db/migrations | sort | tail -1` 이 `20261123_peer-personalization.sql` 인지 다시 확인(더 늦은 날짜 파일이 있으면 BLOCKED). 통합 SHA 를 백엔드 `docs/task-integration-ledger.tsv` 에 `T1\t<sha>`, `T3\t<웹 sha>` 로 기록하고 문서 커밋.
- G2: T2 동일 절차. 대장에 `T2\t<sha>`.
- G3: 최종 전체 리뷰(상위 모델). 설계 9절 1~6 을 하나씩 근거와 함께 판정.

## 7. W3 실환경 검증과 화면 확인 (오케스트레이터만, 직렬)

공유 Supabase 에 쓴다. 쓰기 전 읽기 전용 프리플라이트를 하고, 불명확하면 아무것도 바꾸지 않고 BLOCKED. `Q` 는 `supabase db query --linked --workdir /Users/doo._.hyun/Study/Project/Workcation/.sblink` 를 뜻하고, 백업 폴더 `B` 는 `/Users/doo._.hyun/Study/Project/Workcation/_backups/2026-11-23-peer` 다.

0. 키와 셸 준비(프리플라이트보다 먼저): 백엔드 `.env` 에 `SUPABASE_SERVICE_ROLE_KEY` 와 `SUPABASE_ANON_KEY`(둘 다 `supabase projects api-keys --project-ref ifgtfncfauqlkuwtyryh --workdir /Users/doo._.hyun/Study/Project/Workcation/.sblink -o json` 출력의 `service_role` 과 `anon`), `DUMMY_USER_PASSWORD`(`python3 -c "import secrets;print(secrets.token_urlsafe(18))"`) 를 넣는다. 값은 화면에 출력하지 않는다. `git check-ignore .env` 로 gitignore 확인. 이후 셸 명령은 백엔드 루트에서 `set -a; . ./.env; set +a` 를 먼저 실행해 값을 싣는다.
1. 프리플라이트(읽기)와 저장: `mkdir -p B` 뒤 아래 결과를 `B/preflight.txt` 에 저장.
   - `Q "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = 'app_user'::regclass AND contype = 'c'"`: provider CHECK 이름이 `app_user_provider_check` 가 아니면 BLOCKED(마이그레이션 파일은 고치지 않고 사용자에게 보고).
   - `Q "SELECT count(*) FROM app_user WHERE provider = 'dummy'"` 가 0.
   - `Q "SELECT count(*) FROM metric WHERE metric_key LIKE 'peer\_%'"` 가 0.
   - `curl -s "$SUPABASE_URL/auth/v1/settings" -H "apikey: $SUPABASE_ANON_KEY"` 의 `external.email` 이 true. 아니면 BLOCKED.
   - `Q "SELECT (SELECT count(*) FROM dong_month_summary), (SELECT count(*) FROM population_hourly)"` 가 둘 다 0 보다 크다(03 원천이 있다).
2. 백업과 (라) 전 결과: `Q -o csv "SELECT * FROM app_user" > B/app_user.csv`, `Q -o csv "SELECT * FROM metric_def" > B/metric_def.csv`, `Q -o csv "SELECT scope, unit_id, month, metric_key, round(raw_value::numeric, 9) AS raw_value, score, grade FROM metric WHERE metric_key NOT LIKE 'peer\_%' ORDER BY 1, 2, 3, 4" > B/metric_before.csv`, `Q -o csv "SELECT count(*), sum(index_100) FROM dong_hourly" > B/hourly_before.csv`. 파일이 비지 않았는지 확인.
3. 적용: 공유 DB 쓰기는 권한 정책에 막히므로 사용자에게 아래 두 줄 실행을 요청한다.
   - `! supabase db query --linked --workdir /Users/doo._.hyun/Study/Project/Workcation/.sblink -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/db/migrations/20261123_peer-personalization.sql`
   - `! supabase db query --linked --workdir /Users/doo._.hyun/Study/Project/Workcation/.sblink -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/db/03_metrics.sql`
   - 두 번째가 시간 제한으로 실패하면(한 트랜잭션이라 원래 상태가 남는다) 사용자에게 세션 풀러 직접 실행을 요청한다: `! cd /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend && set -a && . ./.env && set +a && PGPASSWORD="$DB_PASSWORD" psql "host=$DB_HOST port=${DB_PORT:-5432} dbname=${DB_NAME:-postgres} user=$DB_USER sslmode=require" -v ON_ERROR_STOP=1 -f db/03_metrics.sql`
4. 점검: 설계 4-3절 점검 쿼리 (가)(나)(다)가 모두 0. 2단계와 같은 두 쿼리를 `B/metric_after.csv`, `B/hourly_after.csv` 로 떠서 `diff B/metric_before.csv B/metric_after.csv` 와 `diff B/hourly_before.csv B/hourly_after.csv` 가 모두 비어야 한다. raw_value 를 소수 9자리로 반올림해 뜨므로 이 diff 가 설계 (라)의 1e-9 허용 기준과 같다. 하나라도 어긋나면 백엔드 기동 전에 멈추고 보고하며, (라)가 다르면 `db/README.md` 의 03 이후 단계 실행 여부를 사용자에게 묻는다.
5. README 갱신: `db/README.md` 적용 이력 표에 `20261123_peer-personalization.sql` 과 03 재실행 행을 더하고, "현재 상태" 절의 `03_metrics.sql` 적재 줄을 `metric_def` 14 · `metric` 실측 행 수(`Q "SELECT count(*) FROM metric"`) · 날짜 2026-11-23 으로 고쳐 문서 커밋.
6. (0단계에서 끝낸 키 준비를 `grep -c` 로만 다시 확인한다. 값은 출력하지 않는다.)
7. 캐시와 서버: `docker exec workation-backend-redis-1 sh -c "redis-cli --scan --pattern '*static:*' | xargs -r redis-cli del"` 로 정적 캐시를 지우고, 백엔드 runserver(127.0.0.1:8000)를 `main` 코드로 다시 띄운다.
8. 더미 검증: `uv run python manage.py dummy_users create`, 이어서 `uv run python manage.py dummy_users verify --base-url http://127.0.0.1:8000 --month 2025-10`. 종료 코드 0 이어야 한다. 출력 표를 사용자에게 보여 준다.
9. 복구 계획(문제가 생기면): `dummy_users delete`. 이어서 사용자에게 실행을 요청할 복구 SQL 을 `B/rollback.sql` 로 만든다. 내용은 `DELETE FROM metric WHERE metric_key LIKE 'peer\_%'; DELETE FROM metric_def WHERE metric_key LIKE 'peer\_%'; ALTER TABLE app_user DROP CONSTRAINT IF EXISTS app_user_age_group_check; ALTER TABLE app_user DROP COLUMN IF EXISTS age_group;` 와, provider CHECK 를 `B/preflight.txt` 에 기록한 원래 정의로 되돌리는 `ALTER TABLE` 두 줄이다.
10. 화면 확인 루프:
    - 웹 루트에서 `git worktree add -b feat/peer-screen /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-screen main` 후 그 폴더에서 `pnpm install --frozen-lockfile`.
    - 온보딩 모달은 새 사용자에게만 뜨고 웹 로그인은 OAuth 뿐이므로, 화면 확인은 mock 모드로 띄운다: `VITE_USE_MOCK=true pnpm dev --port 5174`. PID 를 `B/screen.pid` 에 기록. mock 로그인 버튼을 누르면 `ageGroup: null` 인 mock 사용자로 온보딩이 뜬다.
    - 실서버 연동(PATCH 본문, AGE_GROUP_REQUIRED 처리)은 T1·T3 테스트와 8단계 실측으로 확인한 것으로 본다.
    - 스크린샷과 화면 문장을 점검(가로 넘침, 휠 9개, 시작 버튼 비활성·활성 전환)한 뒤 사용자에게 `http://localhost:5174` 를 넘긴다.
    - 피드백마다 `feat/peer-screen` 에서 고치고 `pnpm vitest run src/features/auth && pnpm tsc -b` 를 돌리고 커밋한 뒤 다시 점검한다. 행동 테스트만 맞춘다.
    - 사용자가 "확정" 이라고 하면 `feat/peer-screen` 을 웹 `main` 에 머지하고, 머지 SHA 를 대장에 `T3-final\t<sha>` 로 기록해 문서 커밋한다. 5174 서버를 끄고(`kill $(cat B/screen.pid)`) `git worktree remove` 로 정리한다. 그 뒤 W4 를 연다.
    - 확정 뒤 웹 5173 을 `main` 코드로 다시 띄운다.

## 8. 터미널별 실행

### 실행 프롬프트 W1-1: T1 백엔드 계약

권장 실행 설정: 모델 `claude-opus-5-5`, 추론 high, Claude Code 세션(에이전트 종류 `general-purpose`).

```text
[1단계: 작업 공간 만들기] 다른 무엇보다 먼저 이것부터 해라.
cd /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend
BASE=$(git rev-parse HEAD) 로 기점 커밋 SHA 를 셸 변수에 담고 echo "$BASE" 로 기록한다. git log --oneline -1 의 메시지가 "또래 밀도 개인화 설계·구현 계획 추가" 인지 확인한다. 아니면 BLOCKED 로 보고하고 멈춘다.
git worktree add -b feat/peer-t1-contract /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/.claude/worktrees/peer-t1 "$BASE"
EnterWorktree 도구에 path=/Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/.claude/worktrees/peer-t1 로 진입한다.
진입 후 아래를 확인하고, 하나라도 어긋나면 BLOCKED 로 보고하고 멈춘다:
  - pwd 가 /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/.claude/worktrees/peer-t1 인지
  - git rev-parse HEAD 가 기록한 기점 SHA($BASE 값)와 같은지
  - docs/superpowers/specs/2026-11-23-peer-personalization-design.md 와 docs/superpowers/plans/2026-11-23-peer-personalization.md 가 있는지
  - git status --short 가 비었는지
  - TEST_DB_PORT=5439 docker compose -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/compose.yaml up -d --wait test-db 가 성공하는지
  - TEST_DB_PORT=5439 uv run pytest -q 가 실행되는지 (기준선. 통과·실패 수를 보고서에 적는다)

[2단계: 실행] 너는 Task T1(백엔드 계약) 구현자다. 계획서 docs/superpowers/plans/2026-11-23-peer-personalization.md 의 "T1 백엔드 계약" 절과 설계 문서 4-2, 4-3, 5-1, 5-4절을 읽고 그대로 구현한다.
허용 Files: db/migrations/20261123_peer-personalization.sql(생성), tests/test_peer_personalization.py(생성), tests/test_me_age_group.py(생성), db/03_metrics.sql, db/schema.sql, db/README.md, metrics/scoring.py, accounts/models.py, accounts/views.py, tests/test_accounts_api.py.
수정 금지: webapi/, regions/, metrics/views.py, explore/, 설계·계획 문서. 설계 5-1절 시그니처를 바꿔야 하면 바꾸지 말고 BLOCKED.
순서: 테스트를 먼저 쓰고 실패를 확인한 뒤 구현한다. 테스트 이름과 단언은 계획서에 적힌 그대로 쓴다.
검증: TEST_DB_PORT=5439 uv run pytest -q tests/test_peer_personalization.py tests/test_me_age_group.py tests/test_accounts_api.py, 이어서 TEST_DB_PORT=5439 uv run pytest -q, uv run ruff check ., uv run ruff format --check .
03 문법 검증: 아래 세 명령을 차례로 실행해 모두 종료 코드 0 인지 확인한다. (1) docker compose -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/compose.yaml exec -T test-db psql -U postgres -c "DROP DATABASE IF EXISTS m03_check" -c "CREATE DATABASE m03_check" (2) docker compose -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/compose.yaml exec -T test-db psql -U postgres -d m03_check -v ON_ERROR_STOP=1 -f - < db/schema.sql (3) docker compose -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/compose.yaml exec -T test-db psql -U postgres -d m03_check -v ON_ERROR_STOP=1 -f - < db/03_metrics.sql. 셋 다 종료 코드 0. 03 의 VALUES 추가 행은 각 목록의 기존 마지막 행 앞에 넣는다(설계 4-3절 5번).
금지: 커밋, 머지, 푸시, 백그라운드 실행, 하위 에이전트 스폰, 공유 Supabase DB 접속, 다른 작업자의 변경 되돌리기.
보고서: /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/.claude/worktrees/peer-t1/REPORT-T1.md 에 pwd, git rev-parse HEAD, 기점 SHA, 변경 파일, 실행한 검증 명령과 결과(통과 수), 남은 위험을 적는다. 이 파일은 커밋하지 않는다.
완료 형식: 마지막 응답 첫 줄을 "status: READY_FOR_REVIEW" 또는 "status: BLOCKED" 로 시작하고 변경 파일, 검증 결과, 보고서 경로, 남은 위험을 적는다.
```

### 실행 프롬프트 W1-2: T3 웹 계약·온보딩 초안

권장 실행 설정: 모델 `claude-opus-5-5`, 추론 medium, Claude Code 세션(에이전트 종류 `general-purpose`).

```text
[1단계: 작업 공간 만들기] 다른 무엇보다 먼저 이것부터 해라.
cd /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web
git status --short 가 비었는지 확인한다. 아니면 BLOCKED 로 보고하고 멈춘다.
BASE=$(git rev-parse HEAD) 로 기점 커밋 SHA 를 셸 변수에 담고 echo "$BASE" 로 기록한다.
git worktree add -b feat/peer-t3-onboarding-age /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-t3 "$BASE"
EnterWorktree 도구에 path=/Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-t3 로 진입한다.
진입 후 아래를 확인하고, 하나라도 어긋나면 BLOCKED 로 보고하고 멈춘다:
  - pwd 가 /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-t3 인지
  - git rev-parse HEAD 가 기록한 기점 SHA($BASE 값)와 같은지
  - 설계 /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/docs/superpowers/specs/2026-11-23-peer-personalization-design.md 와 계획서 /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/docs/superpowers/plans/2026-11-23-peer-personalization.md 가 있는지 (백엔드 저장소에 있다. 읽기만 한다)
  - git status --short 가 비었는지
  - pnpm install --frozen-lockfile 이 성공하는지
  - pnpm vitest run 이 실행되는지 (기준선. 통과·실패 수를 보고서에 적는다)

[2단계: 실행] 너는 Task T3(웹 계약·온보딩 초안) 구현자다. 위 계획서의 "T3 웹 계약·온보딩 초안" 절과 설계 6절을 읽고 그대로 구현한다.
허용 Files: src/features/auth/ageGroup.ts(생성), src/features/auth/ageGroup.test.ts(생성), src/features/auth/useInvalidateOnViewerChange.ts(생성), src/features/auth/useInvalidateOnViewerChange.test.tsx(생성), src/features/auth/types.ts, src/features/auth/api.ts, src/features/auth/AuthProvider.tsx, src/features/auth/components/OnboardingModal.tsx, src/features/auth/components/OnboardingModal.test.tsx, src/mocks/auth/me.ts, src/mocks/auth/session.ts, src/components/ui/MonthPicker.tsx(MonthWheel export 와 선택 prop ariaLabel·idPrefix·selected 추가만, 기본값은 지금 동작 그대로), src/features/me/components/AccountSection.test.tsx(Me 리터럴에 ageGroup: null 추가만).
수정 금지: src/lib/api.ts, src/hooks/useFocusTrap.ts, src/components/ui/Modal.tsx, 그 밖의 인증 외 기능 폴더.
온보딩 화면은 초안이다. 화면 세부는 재량이고 사용자 확인으로 바뀐다. 행동 테스트를 깨지 않는 범위에서 단순하게 만든다. 휠은 별도 모달로 띄우지 말고 온보딩 모달 안에 MonthWheel 로 바로 둔다. 공유 계약(AgeGroup 타입, Me.ageGroup, PatchMeBody.ageGroup, useInvalidateOnViewerChange, isScoreQuery)을 화면 사정으로 고쳐야 하면 멈추고 BLOCKED 로 보고한다. 새 테스트는 행동만 단언하고 DOM 구조·문구를 고정하지 않는다. 기존 OnboardingModal 테스트는 계획서의 "기존 테스트 처리" 대로 다룬다.
순서: 테스트를 먼저 쓰고 실패를 확인한 뒤 구현한다. 테스트 이름과 단언은 계획서에 적힌 그대로 쓴다.
검증: pnpm vitest run src/features/auth src/components/ui src/features/me, 이어서 pnpm vitest run, pnpm tsc -b, pnpm lint
금지: 커밋, 머지, 푸시, 백그라운드 실행, 하위 에이전트 스폰, 백엔드 저장소 수정, 다른 작업자의 변경 되돌리기.
보고서: /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-t3/REPORT-T3.md 에 pwd, git rev-parse HEAD, 기점 SHA, 변경 파일, 실행한 검증 명령과 결과(통과 수), 남은 위험을 적는다. 이 파일은 커밋하지 않는다.
완료 형식: 마지막 응답 첫 줄을 "status: READY_FOR_REVIEW" 또는 "status: BLOCKED" 로 시작하고 변경 파일, 검증 결과, 보고서 경로, 남은 위험을 적는다.
```

### 실행 프롬프트 W2-1: T2 백엔드 API·더미 명령

권장 실행 설정: 모델 `claude-opus-5-5`, 추론 medium, Claude Code 세션(에이전트 종류 `general-purpose`).

```text
[1단계: 작업 공간 만들기] 다른 무엇보다 먼저 이것부터 해라.
cd /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend
T1SHA=$(awk -F'\t' '$1=="T1"{print $2}' docs/task-integration-ledger.tsv) 로 T1 통합 SHA 를 셸 변수에 담는다. 비었으면 BLOCKED.
git cat-file -e "$T1SHA" 와 git merge-base --is-ancestor "$T1SHA" HEAD 가 모두 성공하는지 확인한다. 아니면 BLOCKED.
git status --short 가 비었는지 확인한다. 아니면 BLOCKED.
BASE=$(git rev-parse HEAD) 로 기점 커밋 SHA 를 셸 변수에 담고 echo "$BASE" 로 기록한다.
git worktree add -b feat/peer-t2-api /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/.claude/worktrees/peer-t2 "$BASE"
EnterWorktree 도구에 path=/Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/.claude/worktrees/peer-t2 로 진입한다.
진입 후 아래를 확인하고, 하나라도 어긋나면 BLOCKED 로 보고하고 멈춘다:
  - pwd 가 /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/.claude/worktrees/peer-t2 인지
  - git rev-parse HEAD 가 기록한 기점 SHA($BASE 값)와 같은지
  - docs/superpowers/specs/2026-11-23-peer-personalization-design.md, docs/superpowers/plans/2026-11-23-peer-personalization.md, tests/test_peer_personalization.py 가 있고, grep -n "def peer_basis" metrics/scoring.py 가 한 줄을 내는지
  - git status --short 가 비었는지
  - TEST_DB_PORT=5439 docker compose -f /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/compose.yaml up -d --wait test-db 가 성공하는지
  - TEST_DB_PORT=5439 uv run pytest -q 가 실행되는지 (기준선. 통과·실패 수를 보고서에 적는다)

[2단계: 실행] 너는 Task T2(백엔드 API·더미 명령) 구현자다. 계획서 docs/superpowers/plans/2026-11-23-peer-personalization.md 의 "T2 백엔드 API·더미 명령" 절과 설계 5-2, 5-3, 5-5절을 읽고 그대로 구현한다.
허용 Files: webapi/views.py, webapi/me.py, webapi/community.py, .env.example, accounts/management/__init__.py(생성), accounts/management/commands/__init__.py(생성), accounts/management/commands/dummy_users.py(생성), tests/test_peer_api.py(생성), tests/test_dummy_users.py(생성).
수정 금지: metrics/scoring.py, accounts/models.py, accounts/views.py, accounts/services.py, db/, tests/test_peer_personalization.py. T1 계약이 부족하면 고치지 말고 BLOCKED.
순서: 테스트를 먼저 쓰고 실패를 확인한 뒤 구현한다. 테스트 이름, 머리(import·픽스처), 단언은 계획서에 적힌 그대로 쓴다. 테스트는 네트워크를 쓰지 않는다.
검증: TEST_DB_PORT=5439 uv run pytest -q tests/test_peer_api.py tests/test_dummy_users.py, 이어서 TEST_DB_PORT=5439 uv run pytest -q, uv run ruff check ., uv run ruff format --check .
금지: 커밋, 머지, 푸시, 백그라운드 실행, 하위 에이전트 스폰, 공유 Supabase DB·Supabase Admin API 실제 호출, manage.py dummy_users 실제 실행, 다른 작업자의 변경 되돌리기.
보고서: /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/.claude/worktrees/peer-t2/REPORT-T2.md 에 pwd, git rev-parse HEAD, 기점 SHA, 변경 파일, 실행한 검증 명령과 결과(통과 수), 남은 위험을 적는다. 이 파일은 커밋하지 않는다.
완료 형식: 마지막 응답 첫 줄을 "status: READY_FOR_REVIEW" 또는 "status: BLOCKED" 로 시작하고 변경 파일, 검증 결과, 보고서 경로, 남은 위험을 적는다.
```

### 실행 프롬프트 W4-1: T4 웹 확정

권장 실행 설정: 모델 `claude-opus-5-5`, 추론 medium, Claude Code 세션(에이전트 종류 `general-purpose`).

```text
[1단계: 작업 공간 만들기] 다른 무엇보다 먼저 이것부터 해라.
cd /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web
FINAL=$(awk -F'\t' '$1=="T3-final"{print $2}' /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/docs/task-integration-ledger.tsv) 로 확정 화면 머지 SHA 를 셸 변수에 담는다. 비었으면 BLOCKED (사용자 확정 전이다).
git cat-file -e "$FINAL" 와 git merge-base --is-ancestor "$FINAL" HEAD 가 모두 성공하는지 확인한다. 아니면 BLOCKED.
git status --short 가 비었는지 확인한다. 아니면 BLOCKED.
BASE=$(git rev-parse HEAD) 로 기점 커밋 SHA 를 셸 변수에 담고 echo "$BASE" 로 기록한다.
git worktree add -b feat/peer-t4-onboarding-final /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-t4 "$BASE"
EnterWorktree 도구에 path=/Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-t4 로 진입한다.
진입 후 아래를 확인하고, 하나라도 어긋나면 BLOCKED 로 보고하고 멈춘다:
  - pwd 가 /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-t4 인지
  - git rev-parse HEAD 가 기록한 기점 SHA($BASE 값)와 같은지
  - src/features/auth/ageGroup.ts 와 src/features/auth/components/OnboardingModal.tsx 가 있는지
  - git status --short 가 비었는지
  - pnpm install --frozen-lockfile 이 성공하고 pnpm vitest run src/features/auth 가 실행되는지 (기준선)

[2단계: 실행] 너는 Task T4(웹 확정) 구현자다. 계획서 /Users/doo._.hyun/Study/Project/Workcation/repos/workation-backend/docs/superpowers/plans/2026-11-23-peer-personalization.md 의 "T4 웹 확정" 절을 읽는다.
현재 src/features/auth/components/OnboardingModal.tsx 가 사용자가 확정한 화면이다. 화면 코드는 바꾸지 않는다. 그 화면의 제목, 연령대 항목 라벨, 휠 처음 위치 문구, 제출 버튼 문구를 그대로 단언하는 테스트를 src/features/auth/components/OnboardingModal.test.tsx 에 더한다.
허용 Files: src/features/auth/components/OnboardingModal.test.tsx 만. 화면 코드 수정이 필요해 보이면 고치지 말고 BLOCKED.
검증: pnpm vitest run, pnpm tsc -b, pnpm lint
금지: 커밋, 머지, 푸시, 백그라운드 실행, 하위 에이전트 스폰, 다른 작업자의 변경 되돌리기.
보고서: /Users/doo._.hyun/Study/Project/Workcation/repos/workation-web/.claude/worktrees/peer-t4/REPORT-T4.md 에 pwd, git rev-parse HEAD, 기점 SHA, 변경 파일, 실행한 검증 명령과 결과를 적는다. 이 파일은 커밋하지 않는다.
완료 형식: 마지막 응답 첫 줄을 "status: READY_FOR_REVIEW" 또는 "status: BLOCKED" 로 시작하고 변경 파일, 검증 결과, 보고서 경로를 적는다.
```
