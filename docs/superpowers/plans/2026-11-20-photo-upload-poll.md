# 사진 업로드(R2) · 사진 신고 · 체감 투표 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 노트 사진 첨부 · 이달의 사진 업로드(백엔드 경유 → R2), 사진 신고 · 격리 · 사용자 삭제 · 운영자 검수 명령, 체감 투표(GET 실집계 · POST vote)를 만든다.

**Architecture:** 웹은 사진을 `multipart/form-data` 로 백엔드에 보낸다. 백엔드는 Pillow 로 검증 · WebP 변환(EXIF 제거)한 뒤 `webapi/storage.py`(boto3, R2 S3 API)로 `uploads/` 아래에 올리고 `photo` 행을 쓴다. 신고는 `report` 표에 쌓고 기준 수에 닿으면 `photo.status` 를 `pending` 으로 바꾸고 파일을 `uploads/quarantine/` 으로 옮긴다(버킷이 통째로 공개라 원래 URL 을 막아야 한다). 격리 · 복구 · 숨김 · 영구 삭제 · 사용자 삭제는 `webapi/photo_quarantine.py` 한 곳에 두고 뷰와 `manage.py photos` 가 같이 쓴다. 투표는 새 `poll_vote` 표에 질문 하나당 한 표로 저장한다.

**Tech Stack:** Django 5.2 + DRF (raw SQL, `managed=False`), PostgreSQL, boto3, Pillow, pytest-django.

**설계 문서:** `docs/superpowers/specs/2026-11-20-photo-upload-poll-design.md` (이하 "스펙")

## Global Constraints

- 사진: multipart 필드 이름 `photos`, 장당 10MB 이하, 노트 0~3장 · 이달의 사진 1~3장, JPEG · PNG · WebP 만 (내용으로 판단), 40MP 상한.
- 변환: `ImageOps.exif_transpose` → 가로 1600px 이하 → WebP quality 82, 메타데이터(EXIF · GPS) 없음.
- R2 key: `uploads/{tip|photo}/{uuid4}.webp`, Content-Type `image/webp`. 사용자 id 를 key 에 넣지 않는다. `photos/` 는 건드리지 않는다.
- `photo.url = R2_PUBLIC_URL + "/" + key`.
- R2 업로드가 모두 끝난 뒤 DB 를 쓴다. 업로드 중 실패 → 올린 key 지우고 502 `UPLOAD_FAILED`. DB 실패 → 올린 key 지우고 원래 에러.
- 에러 코드: 400 `INVALID_PHOTO` · 429 `TIP_RATE_LIMIT` · 429 `PHOTO_RATE_LIMIT`(사용자당 1시간 30장) · 502 `UPLOAD_FAILED`. 에러 모양은 `core.errors.error(status, code, message)`.
- `photo.status`: `approved` = 공개(기본값), `pending` = 신고 누적으로 가려짐(파일 격리), `hidden` = 숨김 확정 또는 가려진 사진을 올린 사람이 지움(`deleted_by_user`), `hidden_at` 30일 뒤 영구 삭제.
- 격리: `uploads/quarantine/{uuid4}.webp` 로 복사 → `photo.url` 갱신 → 원래 key 삭제. `source='user'` 이고 url 이 `uploads/` 아래인 사진만. 관광공사 · 팀 사진(`photos/`)은 옮기지도 지우지도 않는다.
- R2 `put` 의 `Cache-Control` 은 `public, max-age=3600` (가린 사진이 캐시에 오래 남지 않게).
- 사용자 삭제 R2 실패: 502 `DELETE_FAILED`, DB 는 그대로.
- 신고 기준 `settings.PHOTO_REPORT_THRESHOLD` (기본 3). `report.reason` 1~30자.
- 노트 사진(`tip_id IS NOT NULL`)은 갤러리 · 투표 photos · `GET /api/me/photos` · `summary.photoCount` 에서 뺀다.
- 투표 `month`: DB 0 = annual, 1~12. `preset`: region 은 `quiet`·`lively`·`custom` 필수, dong 은 `''`.
- 단위 테스트는 실제 R2 를 부르지 않는다 (`config/settings_test.py` 에서 R2 설정을 비우고, 테스트는 `tests/photo_tools.py` 의 `r2` 픽스처로 가짜를 끼운다).
- 기존 스타일을 따른다: 한국어 docstring · 주석, raw SQL(`connection.cursor()`), 뷰는 `LoginAPIView`/`MeAPIView` 상속, 줄 길이 100 (ruff).
- 테스트 실행: `uv run pytest <경로> -q` (로컬 테스트 Postgres 필요: `docker compose up -d db` 또는 `compose.yaml` 기준). 린트: `uv run ruff check . && uv run ruff format --check .`

## 파일 구조

| 파일 | 책임 |
|---|---|
| `webapi/storage.py` (새) | R2 클라이언트, `put` · `copy` · `delete` · `public_url`, `StorageError` |
| `webapi/photo_upload.py` (새) | `process`(검증 · WebP 변환) · `upload`(R2, 실패 시 정리) · `discard`, `BadPhoto` · `UploadFailed` |
| `webapi/photo_quarantine.py` (새) | 격리 · 복구 · 숨김 · 영구 삭제 · 사용자 삭제 · 파일 삭제, `DeleteFailed` |
| `webapi/uploads.py` (새) | 이달의 사진 등록 뷰, 사진 신고 뷰, 내 사진 삭제 뷰 + SQL |
| `webapi/poll.py` (새) | 질문 문구, 집계 · 투표 SQL, `PollView` · `PollVoteView` |
| `webapi/management/commands/photos.py` (새) | `manage.py photos list|restore|hide|purge` |
| `webapi/community.py` | `Target`/`target()`, 노트 사진(`_TIP_PAGE` · `_note` · `create_tip`), `delete_tip` 첨부 사진 처리, `summary` · `my_photos` 필터 |
| `webapi/me.py` | `NoteCreateView` multipart · 사진 처리, `MyTipView` 502 |
| `webapi/views.py` | `empty_poll` 삭제 |
| `webapi/urls.py` | poll GET · vote, 이달의 사진, 신고 경로 |
| `regions/photos.py` | 모든 조회에 `tip_id IS NULL` |
| `db/migrations/20261120_photo-upload-poll.sql` (새) · `db/schema.sql` | 스펙 3절 DDL |
| `config/settings.py` · `config/settings_test.py` · `.env.example` · `pyproject.toml` · `uv.lock` | R2 설정 · 의존성 |
| `docs/개발-가이드.md` | 새 API · 운영 명령 |
| `tests/photo_tools.py` (새) | 가짜 R2 픽스처 `r2`, 테스트 이미지 `make_image` · `upload` |
| `tests/test_storage.py` · `tests/test_photo_upload.py` · `tests/test_note_photos.py` · `tests/test_photo_quarantine.py` · `tests/test_month_photos.py` · `tests/test_photo_delete.py` · `tests/test_poll.py` (새) | 각 기능 테스트 |

---

### Task 1: 의존성 · R2 설정 · storage 모듈 · 가짜 R2

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`uv add` 로)
- Modify: `config/settings.py` (CLOVA 설정 블록 아래), `config/settings_test.py` (끝), `.env.example` (끝)
- Create: `webapi/storage.py`
- Create: `tests/photo_tools.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces:
  - `webapi.storage.StorageError(RuntimeError)`
  - `webapi.storage.put(key: str, data: bytes, content_type: str) -> None` (실패 · 설정 없음 → `StorageError`)
  - `webapi.storage.copy(src: str, dst: str) -> None` (실패 → `StorageError`)
  - `webapi.storage.delete(key: str) -> None` (실패 → `StorageError`)
  - `webapi.storage.public_url(key: str) -> str`
  - `tests.photo_tools.FakeR2` (속성 `objects: dict[str, tuple[bytes, str]]`, `deleted: list[str]`, `puts: int`, `fail_after: int | None`, `fail_copy: bool`, `fail_delete: bool`)
  - 픽스처 `tests.photo_tools.r2` → `FakeR2` (settings.R2_PUBLIC_URL = `"https://pub.r2.example"`)
  - `tests.photo_tools.make_image(fmt="JPEG", size=(800, 600), exif=False) -> bytes`
  - `tests.photo_tools.upload(data: bytes, name="a.jpg") -> SimpleUploadedFile`

- [ ] **Step 1: 의존성 추가**

Run: `uv add 'boto3>=1.35' 'pillow>=11'`
Expected: `pyproject.toml` dependencies 에 두 줄이 생기고 `uv.lock` 갱신.

- [ ] **Step 2: 설정 추가**

`config/settings.py` 의 `CLOVA_API_KEY = ...` 줄 바로 아래에:

```python
# 사진 업로드 (Cloudflare R2, S3 호환 API). 버킷은 데이터 담당 소유라 사용자 사진은 uploads/ 아래에만
# 쓴다. 비어 있으면 업로드가 502 UPLOAD_FAILED. photo.url = R2_PUBLIC_URL + "/" + key
R2_ACCOUNT_ID = env("R2_ACCOUNT_ID", default="")
R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", default="")
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default="")
R2_BUCKET = env("R2_BUCKET", default="")
R2_PUBLIC_URL = env("R2_PUBLIC_URL", default="").rstrip("/")
# 신고가 이만큼 쌓이면 사진을 가리고(pending) 운영자 검수를 기다린다 (manage.py photos)
PHOTO_REPORT_THRESHOLD = env.int("PHOTO_REPORT_THRESHOLD", default=3)
```

`config/settings_test.py` 끝에:

```python
# .env 에 R2 키가 있어도 단위 테스트는 실제 R2 에 올리지 않는다.
# 업로드 테스트는 tests/photo_tools.py 의 r2 픽스처로 가짜를 끼운다
R2_ACCOUNT_ID = ""
R2_ACCESS_KEY_ID = ""
R2_SECRET_ACCESS_KEY = ""
R2_BUCKET = ""
R2_PUBLIC_URL = ""
```

`.env.example` 끝에:

```
# 사진 업로드 (Cloudflare R2). 값은 데이터 담당에게 받는다. 사용자 사진은 uploads/ 아래에만 쓴다
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
R2_PUBLIC_URL=
# 사진을 가리는 신고 수 (기본 3)
# PHOTO_REPORT_THRESHOLD=3
```

- [ ] **Step 3: 실패하는 테스트 작성** — `tests/test_storage.py`

```python
"""R2 저장소 래퍼. 실제 R2 대신 boto3 클라이언트를 가짜로 바꿔 넘기는 값만 본다."""

import pytest
from botocore.exceptions import ClientError

from webapi import storage


class _Client:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def put_object(self, **kw):
        self.calls.append(("put", kw))
        if self.fail:
            raise ClientError({"Error": {"Code": "500", "Message": "boom"}}, "PutObject")

    def delete_object(self, **kw):
        self.calls.append(("delete", kw))

    def copy_object(self, **kw):
        self.calls.append(("copy", kw))


@pytest.fixture
def r2_settings(settings):
    settings.R2_ACCOUNT_ID = "acc"
    settings.R2_ACCESS_KEY_ID = "key"
    settings.R2_SECRET_ACCESS_KEY = "secret"
    settings.R2_BUCKET = "bucket"
    settings.R2_PUBLIC_URL = "https://pub.r2.example"


def test_put_and_delete_pass_bucket_and_key(r2_settings, monkeypatch):
    client = _Client()
    monkeypatch.setattr(storage, "_client", lambda: client)
    storage.put("uploads/photo/x.webp", b"abc", "image/webp")
    storage.delete("uploads/photo/x.webp")
    (op1, put), (op2, delete) = client.calls
    assert (op1, op2) == ("put", "delete")
    assert put["Bucket"] == "bucket" and put["Key"] == "uploads/photo/x.webp"
    assert put["Body"] == b"abc" and put["ContentType"] == "image/webp"
    assert put["CacheControl"] == "public, max-age=3600"  # 가린 사진이 캐시에 오래 남지 않게
    assert delete == {"Bucket": "bucket", "Key": "uploads/photo/x.webp"}


def test_copy_within_bucket(r2_settings, monkeypatch):
    client = _Client()
    monkeypatch.setattr(storage, "_client", lambda: client)
    storage.copy("uploads/photo/a.webp", "uploads/quarantine/b.webp")
    ((op, kw),) = client.calls
    assert op == "copy"
    assert kw["Bucket"] == "bucket" and kw["Key"] == "uploads/quarantine/b.webp"
    assert kw["CopySource"] == {"Bucket": "bucket", "Key": "uploads/photo/a.webp"}


def test_client_error_becomes_storage_error(r2_settings, monkeypatch):
    monkeypatch.setattr(storage, "_client", lambda: _Client(fail=True))
    with pytest.raises(storage.StorageError):
        storage.put("uploads/photo/x.webp", b"abc", "image/webp")


def test_not_configured_is_storage_error():
    with pytest.raises(storage.StorageError):  # settings_test 는 R2 설정이 비어 있다
        storage.put("uploads/photo/x.webp", b"abc", "image/webp")


def test_public_url(r2_settings):
    assert storage.public_url("uploads/tip/a.webp") == "https://pub.r2.example/uploads/tip/a.webp"
```

- [ ] **Step 4: 실패 확인**

Run: `uv run pytest tests/test_storage.py -q`
Expected: FAIL — `ImportError: cannot import name 'storage'`

- [ ] **Step 5: `webapi/storage.py` 작성**

```python
"""Cloudflare R2 (S3 호환 API) 쓰기. R2 는 이 모듈에서만 부른다 (다른 코드는 boto3 를 직접 쓰지 않음).

버킷은 데이터 담당 소유라 사용자 사진은 uploads/ 아래에만 쓴다.
테스트는 put · copy · delete 를 가짜로 바꾼다 (tests/photo_tools.py 의 r2 픽스처).
"""

from functools import cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings


class StorageError(RuntimeError):
    """R2 에 쓰거나 지우지 못했다 (설정 없음 포함)."""


@cache
def _client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(
            signature_version="s3v4",
            connect_timeout=3,
            read_timeout=10,
            retries={"max_attempts": 2},
        ),
    )


def _bucket() -> str:
    if not (settings.R2_ACCOUNT_ID and settings.R2_BUCKET):
        raise StorageError("R2 설정이 없습니다.")
    return settings.R2_BUCKET


def public_url(key: str) -> str:
    return f"{settings.R2_PUBLIC_URL}/{key}"


def put(key: str, data: bytes, content_type: str) -> None:
    bucket = _bucket()
    try:
        # 가린 사진이 브라우저 · CDN 캐시에 오래 남지 않게 1시간만 (스펙 2절)
        _client().put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=3600",
        )
    except (BotoCoreError, ClientError) as ex:
        raise StorageError(str(ex)) from ex


def copy(src: str, dst: str) -> None:
    """같은 버킷 안에서 복사 (격리 · 복구). Content-Type · Cache-Control 도 따라간다."""
    bucket = _bucket()
    try:
        _client().copy_object(Bucket=bucket, Key=dst, CopySource={"Bucket": bucket, "Key": src})
    except (BotoCoreError, ClientError) as ex:
        raise StorageError(str(ex)) from ex


def delete(key: str) -> None:
    bucket = _bucket()
    try:
        _client().delete_object(Bucket=bucket, Key=key)
    except (BotoCoreError, ClientError) as ex:
        raise StorageError(str(ex)) from ex
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_storage.py -q`
Expected: 5 passed

- [ ] **Step 7: 테스트 도구 `tests/photo_tools.py` 작성** (다음 태스크들이 쓴다)

```python
"""사진 업로드 테스트 도구. 가짜 R2 와 Pillow 로 만든 테스트 이미지.

쓰는 쪽: from tests.photo_tools import make_image, r2, upload  # noqa: F401
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from webapi import storage

PUBLIC = "https://pub.r2.example"


class FakeR2:
    """put 한 key → (bytes, content_type).

    실패 주입: fail_after 번 성공한 뒤의 put, fail_copy 면 copy, fail_delete 면 delete 가 StorageError.
    """

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []
        self.puts = 0
        self.fail_after: int | None = None
        self.fail_copy = False
        self.fail_delete = False

    def put(self, key, data, content_type):
        if self.fail_after is not None and self.puts >= self.fail_after:
            raise storage.StorageError("가짜 R2 실패")
        self.puts += 1
        self.objects[key] = (data, content_type)

    def copy(self, src, dst):
        if self.fail_copy or src not in self.objects:
            raise storage.StorageError("가짜 R2 복사 실패")
        self.objects[dst] = self.objects[src]

    def delete(self, key):
        if self.fail_delete:
            raise storage.StorageError("가짜 R2 삭제 실패")
        self.deleted.append(key)
        self.objects.pop(key, None)  # S3 처럼 없는 key 도 성공


@pytest.fixture
def r2(settings, monkeypatch):
    settings.R2_PUBLIC_URL = PUBLIC
    fake = FakeR2()
    monkeypatch.setattr(storage, "put", fake.put)
    monkeypatch.setattr(storage, "copy", fake.copy)
    monkeypatch.setattr(storage, "delete", fake.delete)
    return fake


def make_image(fmt: str = "JPEG", size=(800, 600), exif: bool = False) -> bytes:
    """단색 이미지. exif=True 면 카메라 · GPS 태그를 넣는다 (JPEG 만)."""
    im = Image.new("RGB", size, (200, 120, 40))
    out = io.BytesIO()
    kw = {}
    if exif:
        tags = Image.Exif()
        tags[0x010F] = "TestCam"  # Make
        tags[0x8825] = {1: "N", 2: (37.0, 30.0, 0.0), 3: "E", 4: (127.0, 0.0, 0.0)}  # GPSInfo
        kw["exif"] = tags.tobytes()
    im.save(out, fmt, **kw)
    return out.getvalue()


def upload(data: bytes, name: str = "a.jpg") -> SimpleUploadedFile:
    # content_type 은 일부러 틀리게 둘 수 있다. 서버는 내용으로 판단한다
    return SimpleUploadedFile(name, data, content_type="image/jpeg")
```

- [ ] **Step 8: 린트 · 커밋**

```bash
uv run ruff check webapi/storage.py tests/photo_tools.py tests/test_storage.py config
uv run ruff format webapi/storage.py tests/photo_tools.py tests/test_storage.py
git add pyproject.toml uv.lock config/settings.py config/settings_test.py .env.example \
  webapi/storage.py tests/photo_tools.py tests/test_storage.py
git commit -m "feat: R2 저장소 래퍼와 사진 업로드 설정 추가"
```

---

### Task 2: DB 변경 (photo 컬럼 · poll_vote) · summary · 내 사진 필터

**Files:**
- Create: `db/migrations/20261120_photo-upload-poll.sql`
- Modify: `db/schema.sql` (photo 표 위치 · 컬럼, metric_vote → poll_vote, RLS 목록, 상단 표 목록 주석 31행)
- Modify: `webapi/community.py` (모듈 docstring 3행, `summary`, `my_photos`)
- Test: `tests/test_schema.py`, `tests/test_me_api.py` (`test_summary_contract`, `test_my_photos_contract`)

**Interfaces:**
- Produces: 표 `poll_vote(user_id, scope, unit_id, month, preset, choice, created_at)`, `photo.tip_id` · `photo.position` · `photo.hidden_at` · `photo.deleted_by_user` 컬럼. 다음 태스크들은 이 DDL 을 전제로 한다.

- [ ] **Step 1: 실패하는 테스트로 고치기**

`tests/test_schema.py` 의 `EXPECTED_TABLES` 에서 `"metric_vote",` 를 `"poll_vote",` 로 바꾸고, `test_schema_sql_loaded_into_test_db` 끝에 추가:

```python
    assert "metric_vote" not in tables  # 2026-11-20 poll_vote 로 바뀜


def test_photo_upload_columns(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name, column_default FROM information_schema.columns"
            " WHERE table_name = 'photo' AND column_name IN"
            " ('tip_id', 'position', 'status', 'hidden_at', 'deleted_by_user')"
        )
        cols = dict(cursor.fetchall())
    assert set(cols) == {"tip_id", "position", "status", "hidden_at", "deleted_by_user"}
    assert cols["status"].startswith("'approved'")  # 올리면 바로 공개
    assert cols["deleted_by_user"] == "false"
```

`tests/test_me_api.py` 의 `test_summary_contract` 에서 `metric_vote` 를 넣는 `for key in ("quiet", "peer"):` 블록 전체를 아래로 바꾸고, 그 아래 `photoCount` · `voteCount` 기대값은 그대로 둔다:

```python
    tip_id = add_tip(me, 1, status="hidden")  # 숨긴 팁이라 stays · tipCount 는 그대로
    run(  # 노트 사진은 이달의 사진 수에 넣지 않는다
        "INSERT INTO photo (scope, unit_id, url, source, user_id, status, tip_id)"
        " VALUES ('region', 1, 'https://r2.example/note.jpg', 'user', %s, 'approved', %s)",
        [me, tip_id],
    )
    run(
        "INSERT INTO poll_vote (user_id, scope, unit_id, month, preset, choice)"
        " VALUES (%s, 'region', 1, 10, 'quiet', true)",
        [me],
    )
```

아래 기대값(`stays` · `tipCount 3` · `photoCount 2` · `favoriteCount 2` · `voteCount 1`)은 그대로 둔다.

`test_my_photos_contract` 의 `add_photo("region", 1, 10, "approved", user_id=OTHER_ID)` 다음 줄에 추가 (기대 결과는 그대로 — 노트 사진은 안 나온다):

```python
    note = run(
        "INSERT INTO tip (user_id, region_id, body) VALUES (%s, 1, '팁') RETURNING tip_id", [me]
    )[0][0]
    run(
        "INSERT INTO photo (scope, unit_id, url, source, user_id, status, tip_id)"
        " VALUES ('region', 1, 'https://r2.example/note.jpg', 'user', %s, 'approved', %s)",
        [me, note],
    )
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_schema.py tests/test_me_api.py -q -k "schema or summary or my_photos or photo_upload"`
Expected: FAIL (`poll_vote` 없음, `tip_id` 컬럼 없음)

- [ ] **Step 3: 마이그레이션 SQL 작성** — `db/migrations/20261120_photo-upload-poll.sql`

```sql
-- 사진 업로드(R2) · 사진 신고 · 체감 투표 · 2026-11-20
-- 기준: schema.sql 의 photo · tip · app_user 가 있는 DB. 두 번 실행해도 안전 (IF [NOT] EXISTS).
-- 설계: docs/superpowers/specs/2026-11-20-photo-upload-poll-design.md 3절
-- photo.status 의미: approved = 공개(기본), pending = 신고 누적으로 가려짐, hidden = 숨김 확정

BEGIN;

-- 사진: 노트 첨부 연결 · 바로 게시
ALTER TABLE photo ADD COLUMN IF NOT EXISTS tip_id INTEGER REFERENCES tip(tip_id) ON DELETE CASCADE;
ALTER TABLE photo ADD COLUMN IF NOT EXISTS position SMALLINT;
ALTER TABLE photo ALTER COLUMN status SET DEFAULT 'approved';
CREATE INDEX IF NOT EXISTS photo_tip_idx ON photo (tip_id) WHERE tip_id IS NOT NULL;
COMMENT ON COLUMN photo.tip_id   IS '노트 첨부 사진이면 tip_id (갤러리에 섞지 않음)';
COMMENT ON COLUMN photo.position IS '노트 안 순서 0~2';
COMMENT ON COLUMN photo.status   IS 'approved(공개), pending(신고로 가려짐), hidden(숨김 확정)';

-- 격리 · 영구 삭제 (가린 사용자 사진은 uploads/quarantine/ 으로 옮긴다)
ALTER TABLE photo ADD COLUMN IF NOT EXISTS hidden_at TIMESTAMPTZ;
ALTER TABLE photo ADD COLUMN IF NOT EXISTS deleted_by_user BOOLEAN NOT NULL DEFAULT false;
COMMENT ON COLUMN photo.hidden_at       IS 'hidden 이 된 시각 (30일 뒤 manage.py photos purge)';
COMMENT ON COLUMN photo.deleted_by_user IS '가려진 사진을 올린 사람이 지움 (증거로 보관 중)';

-- 체감 투표: metric_vote(지표별) 를 버리고 질문 하나에 한 표
DROP TABLE IF EXISTS metric_vote;
CREATE TABLE IF NOT EXISTS poll_vote (
  user_id     UUID        NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  scope       VARCHAR(6)  NOT NULL,
  unit_id     SMALLINT    NOT NULL,
  month       SMALLINT    NOT NULL,
  preset      VARCHAR(8)  NOT NULL DEFAULT '',
  choice      BOOLEAN     NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, scope, unit_id, month, preset),
  CHECK (scope IN ('region', 'dong')),
  CHECK (month BETWEEN 0 AND 12),
  CHECK (preset IN ('', 'quiet', 'lively', 'custom'))
);
CREATE INDEX IF NOT EXISTS poll_vote_target_idx ON poll_vote (scope, unit_id, month, preset);
ALTER TABLE poll_vote ENABLE ROW LEVEL SECURITY;
COMMENT ON TABLE  poll_vote        IS '체감 투표 (질문 하나에 한 표)';
COMMENT ON COLUMN poll_vote.month  IS '0 = 연간(annual), 1~12';
COMMENT ON COLUMN poll_vote.preset IS 'region: quiet, lively, custom / dong: 빈 문자열';
COMMENT ON COLUMN poll_vote.choice IS 'true = yes';

COMMIT;
```

- [ ] **Step 4: `db/schema.sql` 반영**

1. `CREATE TABLE photo (` 부터 `CREATE INDEX photo_unit_idx ...;` 까지 블록을 잘라서 `CREATE INDEX tip_region_idx ...;` 줄 바로 아래로 옮긴다 (photo 가 tip 을 참조하므로 tip 뒤에 있어야 한다).
2. 옮긴 블록을 아래로 바꾼다:

```sql
CREATE TABLE photo (
  photo_id      SERIAL       PRIMARY KEY,
  scope         VARCHAR(6)   NOT NULL,
  unit_id       SMALLINT     NOT NULL,
  month         SMALLINT,
  url           TEXT         NOT NULL,
  source        VARCHAR(8)   NOT NULL,
  credit        VARCHAR(100),
  user_id       UUID         REFERENCES app_user(user_id) ON DELETE SET NULL,
  status        VARCHAR(8)   NOT NULL DEFAULT 'approved',
  is_featured   BOOLEAN      NOT NULL DEFAULT false,
  tip_id        INTEGER      REFERENCES tip(tip_id) ON DELETE CASCADE,
  position      SMALLINT,
  hidden_at     TIMESTAMPTZ,
  deleted_by_user BOOLEAN    NOT NULL DEFAULT false,
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
COMMENT ON COLUMN photo.status      IS 'approved(공개), pending(신고로 가려짐), hidden(숨김 확정)';
COMMENT ON COLUMN photo.is_featured IS '이달의 사진';
COMMENT ON COLUMN photo.tip_id      IS '노트 첨부 사진이면 tip_id (갤러리에 섞지 않음)';
COMMENT ON COLUMN photo.position    IS '노트 안 순서 0~2';
COMMENT ON COLUMN photo.hidden_at   IS 'hidden 이 된 시각 (30일 뒤 manage.py photos purge)';
COMMENT ON COLUMN photo.deleted_by_user IS '가려진 사진을 올린 사람이 지움 (증거로 보관 중)';

CREATE INDEX photo_unit_idx ON photo (scope, unit_id, month) WHERE status = 'approved';
CREATE INDEX photo_tip_idx ON photo (tip_id) WHERE tip_id IS NOT NULL;
```

3. `CREATE TABLE metric_vote ( ... );` 와 그 `COMMENT ON ... metric_vote` 4줄을 마이그레이션 3단계의 `CREATE TABLE poll_vote` 블록(+ `poll_vote_target_idx` 인덱스 + COMMENT 4줄, `IF NOT EXISTS` 는 빼고)으로 바꾼다.
4. RLS 목록의 `ALTER TABLE metric_vote            ENABLE ROW LEVEL SECURITY;` 를 `ALTER TABLE poll_vote              ENABLE ROW LEVEL SECURITY;` 로.
5. 31행 주석 `photo, tip, tip_reaction, tip_reply, report, metric_vote` → `... report, poll_vote`.

확인: `grep -n metric_vote db/schema.sql` 결과 없음.

- [ ] **Step 5: `webapi/community.py` 고치기**

docstring 3행: `표: favorite · tip · tip_reaction · tip_reply · photo · poll_vote (db/schema.sql).`

`summary` docstring 의 voteCount 줄을 `voteCount 는 투표한 질문 수 (poll_vote 는 질문 하나에 한 행). photoCount 는 이달의 사진만.` 으로 바꾸고, SQL 의 두 서브쿼리를:

```python
                 (SELECT count(*) FROM photo
                   WHERE user_id = %(u)s AND status <> 'hidden' AND tip_id IS NULL),
                 (SELECT count(*) FROM favorite WHERE user_id = %(u)s),
                 (SELECT count(*) FROM poll_vote WHERE user_id = %(u)s)""",
```

`my_photos`: docstring 을 `"""내가 올린 이달의 사진 (숨김 · 노트 사진 제외), 최근 것부터. ..."""` 로, WHERE 를 `WHERE p.user_id = %s AND p.status <> 'hidden' AND p.tip_id IS NULL` 로.

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_schema.py tests/test_me_api.py -q`
Expected: 모두 PASS

- [ ] **Step 7: 커밋**

```bash
git add db/migrations/20261120_photo-upload-poll.sql db/schema.sql webapi/community.py \
  tests/test_schema.py tests/test_me_api.py
git commit -m "feat: photo 노트 연결 컬럼과 poll_vote 표 추가 (metric_vote 대체)"
```

---

### Task 3: 갤러리에서 노트 사진 빼기 (`regions/photos.py`)

**Files:**
- Modify: `regions/photos.py` (docstring 1행, `_pick` 25행, `one_per_unit` 72행, `one_per_dong` 90행)
- Test: `tests/test_api.py` (사진 섹션 끝, `test_tiers_item_photo` 아래)

**Interfaces:**
- Consumes: Task 2 의 `photo.tip_id`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_api.py` 의 `test_tiers_item_photo` 아래에

```python
def test_note_photos_stay_out_of_galleries(client, photos):
    """노트 첨부 사진(tip_id 있음)은 지역 · 동네 갤러리와 대표 사진에 섞지 않는다."""
    from django.db import connection

    from regions.photos import one_per_dong, one_per_unit

    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO app_user (user_id, provider, provider_uid, nickname)"
            " VALUES ('9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d', 'kakao', 'x', '바다')"
        )
        cur.execute(
            "INSERT INTO tip (user_id, region_id, body)"
            " VALUES ('9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d', 2, '팁') RETURNING tip_id"
        )
        tip_id = cur.fetchone()[0]
        for scope, unit in [("region", 1), ("region", 2), ("dong", 101), ("dong", 201)]:
            cur.execute(
                "INSERT INTO photo (scope, unit_id, month, url, source, status, tip_id)"
                " VALUES (%s, %s, 10, 'https://r2.example/note.webp', 'user', 'approved', %s)",
                [scope, unit, tip_id],
            )
    body = client.get("/api/v1/regions/51820?month=10").json()
    assert body["photos"]["totalCount"] == 3
    assert "https://r2.example/note.webp" not in [p["url"] for p in body["photos"]["featured"]]
    for _ in range(5):  # 같은 순위 안은 랜덤이라 여러 번 본다
        assert one_per_unit("region", [2], 10)[2]["url"] == "https://r2.example/d.webp"
        assert one_per_dong([(201, 2)], 10)[201]["url"] == "https://r2.example/d.webp"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_api.py::test_note_photos_stay_out_of_galleries -q`
Expected: FAIL (totalCount 4 또는 note.webp 선택)

- [ ] **Step 3: 구현**

`regions/photos.py`:
- 1행 docstring: `"""지역 · 동네 사진 고르기. photo 테이블(파일은 Cloudflare R2)에서 공개(approved) 사진만, 노트 첨부 사진(tip_id)은 빼고.`
- `_pick`: `FROM photo WHERE status = 'approved' AND ({params.pop("_filter")})` → `FROM photo WHERE status = 'approved' AND tip_id IS NULL AND ({params.pop("_filter")})`
- `one_per_unit`: `WHERE status = 'approved' AND scope = %(scope)s` → `WHERE status = 'approved' AND tip_id IS NULL AND scope = %(scope)s`
- `one_per_dong`: `JOIN photo p ON p.status = 'approved'` → `JOIN photo p ON p.status = 'approved' AND p.tip_id IS NULL`

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_api.py tests/test_webapi.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add regions/photos.py tests/test_api.py
git commit -m "feat: 지역 · 동네 갤러리에서 노트 첨부 사진 제외"
```

---

### Task 4: 사진 검증 · WebP 변환 · R2 업로드 (`webapi/photo_upload.py`)

**Files:**
- Create: `webapi/photo_upload.py`
- Test: `tests/test_photo_upload.py`

**Interfaces:**
- Consumes: `webapi.storage.put/delete/public_url/StorageError` (Task 1), `tests.photo_tools.r2/make_image/upload` (Task 1)
- Produces:
  - `photo_upload.MAX_PHOTOS = 3`, `MAX_BYTES = 10 * 1024 * 1024`
  - `photo_upload.BadPhoto(ValueError)` — 메시지는 그대로 400 `INVALID_PHOTO` 에 담는다
  - `photo_upload.UploadFailed(RuntimeError)` — 502 `UPLOAD_FAILED`
  - `photo_upload.process(files: list, *, min_count: int) -> list[bytes]` (WebP 바이트, 들어온 순서)
  - `photo_upload.upload(kind: str, blobs: list[bytes]) -> list[tuple[str, str]]` (`[(key, url)]`, kind = `"tip"` | `"photo"`)
  - `photo_upload.discard(keys: list[str]) -> None` (실패는 로그만)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_photo_upload.py`

```python
"""사진 받기 · 검증 · WebP 변환 · R2 업로드 (스펙 4-0)."""

import io

import pytest
from PIL import Image

from tests.photo_tools import make_image, r2, upload  # noqa: F401
from webapi import photo_upload


def opened(blob: bytes) -> Image.Image:
    return Image.open(io.BytesIO(blob))


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_formats_become_webp(fmt):
    (blob,) = photo_upload.process([upload(make_image(fmt), "x.bin")], min_count=1)
    im = opened(blob)
    assert im.format == "WEBP" and im.size == (800, 600)


def test_wide_photo_is_resized_to_1600():
    (blob,) = photo_upload.process([upload(make_image(size=(3200, 1000)))], min_count=1)
    assert opened(blob).size == (1600, 500)


def test_exif_and_gps_removed():
    src = make_image(exif=True)
    assert len(opened(src).getexif()) > 0  # 원본에는 EXIF 가 있다
    (blob,) = photo_upload.process([upload(src)], min_count=1)
    im = opened(blob)
    assert len(im.getexif()) == 0 and "exif" not in im.info


def test_order_kept():
    files = [upload(make_image(size=(100 + i, 100))) for i in range(3)]
    blobs = photo_upload.process(files, min_count=1)
    assert [opened(b).size[0] for b in blobs] == [100, 101, 102]


@pytest.mark.parametrize(
    ("files", "min_count"),
    [
        ([], 1),
        ([b"x"] * 4, 0),
        ([b"not an image"], 0),
        ([b"GIF89a" + b"\0" * 20], 0),
    ],
)
def test_bad_photos(files, min_count):
    with pytest.raises(photo_upload.BadPhoto):
        photo_upload.process([upload(f) for f in files], min_count=min_count)


def test_gif_rejected():
    out = io.BytesIO()
    Image.new("RGB", (10, 10)).save(out, "GIF")
    with pytest.raises(photo_upload.BadPhoto, match="1번째"):
        photo_upload.process([upload(out.getvalue())], min_count=1)


def test_too_big_rejected(monkeypatch):
    monkeypatch.setattr(photo_upload, "MAX_BYTES", 100)
    with pytest.raises(photo_upload.BadPhoto, match="2번째"):
        photo_upload.process([upload(b"x" * 50), upload(make_image())], min_count=1)


def test_no_photos_ok_when_optional():
    assert photo_upload.process([], min_count=0) == []


def test_upload_keys_and_urls(r2):  # noqa: F811
    done = photo_upload.upload("tip", [b"a", b"b"])
    assert len(done) == 2
    for key, url in done:
        assert key.startswith("uploads/tip/") and key.endswith(".webp")
        assert url == f"https://pub.r2.example/{key}"
        assert r2.objects[key][1] == "image/webp"
    assert [r2.objects[k][0] for k, _ in done] == [b"a", b"b"]


def test_upload_failure_cleans_up(r2):  # noqa: F811
    r2.fail_after = 1
    with pytest.raises(photo_upload.UploadFailed):
        photo_upload.upload("photo", [b"a", b"b"])
    assert r2.objects == {} and len(r2.deleted) == 1
```

`test_bad_photos` 의 두 번째 케이스(`[b"x"] * 4`)는 장수 초과, 세 번째 · 네 번째는 이미지가 아니거나 형식이 틀린 경우다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_photo_upload.py -q`
Expected: FAIL — `ImportError: cannot import name 'photo_upload'`

- [ ] **Step 3: `webapi/photo_upload.py` 작성**

```python
"""사진 받기 · 검증 · WebP 변환 · R2 업로드. 노트 사진 · 이달의 사진 공통 (스펙 4-0).

- 파일 이름 · 요청의 Content-Type 은 믿지 않고 Pillow 로 열어 본 형식으로 판단한다.
- 가로 1600px 이하 WebP(quality 82)로 다시 인코딩한다. 메타데이터를 옮기지 않아 EXIF(GPS 포함)가 사라진다.
- 모두 검증 · 변환한 뒤에 올린다. 올리다 실패하면 이번에 올린 것을 지운다.
"""

from __future__ import annotations

import io
import logging
import uuid

from PIL import Image, ImageOps, UnidentifiedImageError

from webapi import storage

logger = logging.getLogger(__name__)

MAX_PHOTOS = 3
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000  # 압축 폭탄 방지
MAX_WIDTH = 1600  # 관광공사 사진(photos/)과 같은 규격
QUALITY = 82
FORMATS = {"JPEG", "PNG", "WEBP"}


class BadPhoto(ValueError):
    """받은 사진이 규칙에 맞지 않는다. 메시지는 그대로 400 INVALID_PHOTO 에 담긴다."""


class UploadFailed(RuntimeError):
    """R2 에 올리지 못했다 (502 UPLOAD_FAILED)."""


def process(files: list, *, min_count: int) -> list[bytes]:
    """업로드 파일들 → WebP 바이트 (들어온 순서). min_count ~ MAX_PHOTOS 장."""
    if not min_count <= len(files) <= MAX_PHOTOS:
        raise BadPhoto(f"사진은 {min_count}~{MAX_PHOTOS}장 올릴 수 있어요.")
    return [_to_webp(f, n) for n, f in enumerate(files, 1)]


def _to_webp(file, n: int) -> bytes:
    if file.size > MAX_BYTES:
        raise BadPhoto(f"{n}번째 사진이 {MAX_BYTES // (1024 * 1024)}MB 를 넘어요.")
    try:
        with Image.open(file) as src:
            if src.format not in FORMATS:
                raise BadPhoto(f"{n}번째 사진은 JPEG · PNG · WebP 만 올릴 수 있어요.")
            if src.width * src.height > MAX_PIXELS:
                raise BadPhoto(f"{n}번째 사진이 너무 커요.")
            im = ImageOps.exif_transpose(src)
            if im.width > MAX_WIDTH:
                height = round(im.height * MAX_WIDTH / im.width)
                im = im.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "transparency" in im.info or "A" in im.mode else "RGB")
            im.info = {}  # EXIF · XMP · ICC 를 옮기지 않는다
            out = io.BytesIO()
            im.save(out, "WEBP", quality=QUALITY, exif=b"")
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as ex:
        raise BadPhoto(f"{n}번째 파일은 사진이 아니에요.") from ex


def upload(kind: str, blobs: list[bytes]) -> list[tuple[str, str]]:
    """WebP 들을 uploads/{kind}/{uuid}.webp 로 올린다 → [(key, 공개 URL)].

    하나라도 실패하면 이번에 올린 것을 지우고 UploadFailed.
    """
    done: list[str] = []
    try:
        for blob in blobs:
            key = f"uploads/{kind}/{uuid.uuid4()}.webp"
            storage.put(key, blob, "image/webp")
            done.append(key)
    except storage.StorageError as ex:
        discard(done)
        raise UploadFailed(str(ex)) from ex
    return [(key, storage.public_url(key)) for key in done]


def discard(keys: list[str]) -> None:
    """올린 사진을 지운다 (DB 쓰기 실패 뒤 정리). 지우지 못해도 로그만 남긴다."""
    for key in keys:
        try:
            storage.delete(key)
        except storage.StorageError:
            logger.warning("R2 정리 실패: %s", key)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_photo_upload.py -q`
Expected: 모두 PASS. `test_exif_and_gps_removed` 가 실패하면 Pillow 버전의 WebP 저장이 `im.info` 대신 다른 곳에서 EXIF 를 읽는지 확인한다 (`im.save(..., exif=b"")` 이 기준).

- [ ] **Step 5: 커밋**

```bash
git add webapi/photo_upload.py tests/test_photo_upload.py
git commit -m "feat: 사진 검증 · WebP 변환 · R2 업로드 모듈"
```

---

### Task 5: 노트 사진 (`POST .../notes` multipart · `Note.photos`)

**Files:**
- Modify: `webapi/community.py` (`_TIP_PAGE`, `_note`, `tip_page`, `create_tip`, import)
- Modify: `webapi/me.py` (`NoteCreateView`, import)
- Modify: `tests/test_me_api.py` (`Note` 전체 비교 테스트에 `"photos": []`)
- Test: `tests/test_note_photos.py`

**Interfaces:**
- Consumes: `photo_upload.process/upload/discard/BadPhoto/UploadFailed` (Task 4), `photo.tip_id/position` (Task 2), `tests.photo_tools` (Task 1), `tests.test_me_api` 의 `auth`, `run`, `add_tip`, `me`, `OTHER_ID`, `GOSEONG`, `GEOJIN`
- Produces:
  - `community.create_tip(user, region_id, dong, body, visit_month, photos: list[bytes] = ()) -> dict | None` (한도 초과 None, 업로드 실패 `UploadFailed`)
  - 모든 `Note` 에 `"photos": [{"id": str, "url": str}]` (approved, position 순)

- [ ] **Step 1: 기존 테스트에 `photos` 기대값 추가**

`tests/test_me_api.py` 에서 Note 를 dict 전체로 비교하는 곳(`test_post_region_tip_contract` 의 `assert note == {...}`, `test_region_notes_contract` · `test_my_tips_contract` 안의 Note dict 들)마다 `"photos": [],` 항목을 넣는다. 확인: `grep -n '"replies":' tests/test_me_api.py` 로 나오는 dict 마다 바로 아래에 `"photos": [],` 가 있어야 한다.

- [ ] **Step 2: 실패하는 테스트 작성** — `tests/test_note_photos.py`

```python
"""노트 사진 첨부 — POST /api/{regions|dongs}/{id}/notes 를 multipart 로 (스펙 4-2)."""

import pytest
from django.db import DatabaseError

from tests.photo_tools import make_image, r2, upload  # noqa: F401
from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GEOJIN, GOSEONG, OTHER_ID, add_tip, auth, me, run  # noqa: F401
from webapi import community

pytestmark = pytest.mark.django_db


def post_note(client, kind, item_id, body="사진 팁", photos=(), **fields):
    payload = {"body": body, **fields}
    if photos:
        payload["photos"] = [upload(p, f"{i}.jpg") for i, p in enumerate(photos)]
    return client.post(f"/api/{kind}/{item_id}/notes", payload, **auth())


def test_json_note_has_empty_photos(client, me, r2):  # noqa: F811
    r = client.post(
        f"/api/regions/{GOSEONG}/notes", {"body": "팁"}, content_type="application/json", **auth()
    )
    assert r.status_code == 201 and r.json()["photos"] == []
    assert r2.puts == 0


def test_multipart_note_with_photos(client, me, r2):  # noqa: F811
    imgs = [make_image(size=(100 + i, 100)) for i in range(3)]
    r = post_note(client, "dongs", GEOJIN, photos=imgs, visitMonth="2025-10")
    assert r.status_code == 201
    note = r.json()
    assert note["body"] == "사진 팁" and note["visitMonth"] == "2025-10"
    assert len(note["photos"]) == 3
    keys = [p["url"].removeprefix("https://pub.r2.example/") for p in note["photos"]]
    assert all(k.startswith("uploads/tip/") for k in keys) and set(keys) == set(r2.objects)
    rows = run(
        "SELECT photo_id::text, scope, unit_id, month, source, user_id::text, tip_id::text,"
        " position, status FROM photo ORDER BY position"
    )
    assert [r[0] for r in rows] == [p["id"] for p in note["photos"]]
    assert {r[1:] for r in rows} == {
        ("dong", 101, 10, "user", me, note["id"], pos, "approved") for pos in range(3)
    }
    # 노트 목록 · 내 팁에도 같은 순서로 보인다
    listed = client.get(f"/api/dongs/{GEOJIN}/notes").json()["items"][0]
    mine = client.get("/api/me/tips", **auth()).json()["items"][0]
    assert listed["photos"] == mine["photos"] == note["photos"]


def test_multipart_note_without_photos(client, me, r2):  # noqa: F811
    r = post_note(client, "regions", GOSEONG)
    assert r.status_code == 201 and r.json()["photos"] == []


def test_hidden_photo_not_in_note(client, me, r2):  # noqa: F811
    note = post_note(client, "regions", GOSEONG, photos=[make_image(), make_image()]).json()
    run("UPDATE photo SET status = 'pending' WHERE photo_id = %s", [note["photos"][0]["id"]])
    listed = client.get(f"/api/regions/{GOSEONG}/notes").json()["items"][0]
    assert listed["photos"] == note["photos"][1:]


@pytest.mark.parametrize(
    "photos", [[b"not an image"], [make_image()] * 4], ids=["not-image", "four"]
)
def test_bad_photo_creates_nothing(client, me, r2, photos):  # noqa: F811
    r = post_note(client, "regions", GOSEONG, photos=photos)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PHOTO"
    assert run("SELECT count(*) FROM tip") == [(0,)] and r2.puts == 0


def test_bad_body_does_not_touch_photos(client, me, r2):  # noqa: F811
    r = post_note(client, "regions", GOSEONG, body="  ", photos=[b"not an image"])
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_REQUEST"


def test_rate_limit_before_upload(client, me, r2):  # noqa: F811
    for i in range(community.TIP_BURST_LIMIT):
        add_tip(me, 1, None, f"팁 {i}")
    r = post_note(client, "regions", GOSEONG, photos=[make_image()])
    assert r.status_code == 429 and r.json()["error"]["code"] == "TIP_RATE_LIMIT"
    assert r2.puts == 0


def test_upload_failure_502_and_no_tip(client, me, r2):  # noqa: F811
    r2.fail_after = 1
    r = post_note(client, "regions", GOSEONG, photos=[make_image(), make_image()])
    assert r.status_code == 502 and r.json()["error"]["code"] == "UPLOAD_FAILED"
    assert run("SELECT count(*) FROM tip") == [(0,)]
    assert r2.objects == {}


def test_db_failure_removes_uploaded(client, me, r2, monkeypatch):  # noqa: F811
    def boom(*a, **kw):
        raise DatabaseError("가짜 DB 실패")

    monkeypatch.setattr(community, "_insert_tip_photos", boom)
    with pytest.raises(DatabaseError):
        post_note(client, "regions", GOSEONG, photos=[make_image(), make_image()])
    assert r2.puts == 2 and r2.objects == {} and len(r2.deleted) == 2
    assert run("SELECT count(*) FROM tip") == [(0,)]
```

(노트를 지울 때의 첨부 사진 처리는 Task 8.)

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_note_photos.py tests/test_me_api.py -q`
Expected: FAIL (`photos` 키 없음, multipart 415 등)

- [ ] **Step 4: `webapi/community.py` 구현**

import 에 추가:

```python
from contextlib import nullcontext

from django.db import connection, transaction

from webapi import data, photo_upload
```

(기존 `from django.db import connection` · `from webapi import data` 를 위 두 줄로 바꾼다.)

`_TIP_PAGE` 의 `replies` 서브쿼리 뒤에 photos 열을 더한다:

```python
                   (SELECT count(*) FROM tip_reply p
                     WHERE p.tip_id = t.tip_id AND p.status = 'public') AS replies,
                   (SELECT coalesce(json_agg(json_build_object('id', ph.photo_id::text,
                                                               'url', ph.url)
                                             ORDER BY ph.position), '[]'::json)
                      FROM photo ph
                     WHERE ph.tip_id = t.tip_id AND ph.status = 'approved') AS photos
```

`_note`: `"replies": row[10],` 다음 줄에 `"photos": row[11],` 를 넣는다.

`tip_page`: `total, helpful_sum = (totals[0][11], int(totals[0][12])) ...` → `(totals[0][12], int(totals[0][13]))`. 주석 `# photos 열이 더해져 total · helpful_sum 은 12 · 13 번째` 를 그 줄 위에 단다.

`create_tip` 전체를 아래로 바꾼다:

```python
_TIP_INSERT = f"""
    WITH cur AS (
      SELECT count(*) AS n FROM tip
       WHERE user_id = %(user)s AND created_at > now() - interval '{TIP_BURST_WINDOW}'
    ), ins AS (
      INSERT INTO tip (user_id, region_id, dong_id, visit_month, body)
      SELECT %(user)s, %(region)s, %(dong)s, %(month)s, %(body)s
        FROM cur WHERE n < %(limit)s
      RETURNING tip_id, created_at
    )
    SELECT tip_id, created_at FROM ins"""


def _insert_tip_photos(tip_id, scope, unit, month, user_id, urls) -> list[dict]:
    """노트 사진 행을 한 문장으로 넣는다 → [{id, url}] (올린 순서)."""
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO photo (scope, unit_id, month, url, source, user_id, tip_id, position,
                                  status)
               SELECT %(scope)s, %(unit)s, %(month)s, u.url, 'user', %(user)s, %(tip)s, u.n - 1,
                      'approved'
                 FROM unnest(%(urls)s::text[]) WITH ORDINALITY AS u(url, n)
               RETURNING photo_id, url, position""",
            {"scope": scope, "unit": unit, "month": month, "user": user_id, "tip": tip_id,
             "urls": urls},
        )
        rows = sorted(cur.fetchall(), key=lambda r: r[2])
    return [{"id": str(pid), "url": url} for pid, url, _ in rows]


def create_tip(
    user, region_id: int, dong: Dong | None, body: str, visit_month: int | None, photos=()
):
    """공개 팁을 쓰고 읽기와 같은 모양(Note)으로 돌려준다. 연달아 쓰는 한도를 넘으면 None.

    최근 TIP_BURST_WINDOW 안의 내 팁을 세는 것과 넣는 것이 한 문장이라 DB 왕복이 한 번이다.
    photos(WebP 바이트)가 있으면 한 트랜잭션 안에서 팁을 넣고(한도를 넘으면 올리지 않음) →
    R2 에 올리고 → 사진 행을 넣는다. 업로드 실패는 UploadFailed(팁도 되돌림),
    사진 행 실패는 올린 파일을 지우고 그 에러를 다시 던진다.
    """
    attached: list[dict] = []
    with transaction.atomic() if photos else nullcontext():
        with connection.cursor() as cur:
            cur.execute(
                _TIP_INSERT,
                {
                    "user": user.user_id,
                    "region": region_id,
                    "dong": dong.dong_id if dong else None,
                    "month": visit_month,
                    "body": body,
                    "limit": TIP_BURST_LIMIT,
                },
            )
            row = cur.fetchone()
        if row is None:
            return None
        tip_id, created_at = row
        if photos:
            uploaded = photo_upload.upload("tip", list(photos))
            scope, unit = ("dong", dong.dong_id) if dong else ("region", region_id)
            try:
                attached = _insert_tip_photos(
                    tip_id, scope, unit, visit_month, user.user_id, [url for _, url in uploaded]
                )
            except Exception:
                photo_upload.discard([key for key, _ in uploaded])
                raise
    return _note(
        (
            tip_id,
            user.nickname,
            created_at,
            None,
            visit_month,
            body,
            region_id,
            dong.adm_code if dong else None,
            dong.dong_name if dong else None,
            0,
            0,
            attached,
        ),
        with_target=False,
    )
```

(사진이 없으면 `atomic` 을 열지 않아 기존 `test_post_tip_query_count` 의 왕복 수 2 · 3 이 그대로다.)

- [ ] **Step 5: `webapi/me.py` 의 `NoteCreateView` 구현**

import 에 추가:

```python
from rest_framework.parsers import JSONParser, MultiPartParser

from webapi import community, data, photo_upload
```

`NoteCreateView` 를 아래로 바꾼다:

```python
class NoteCreateView(MeAPIView):
    """POST /api/{regions|dongs}/{id}/notes. 201 + 만든 팁(Note). 같은 경로의 GET 은 views.py.

    사진이 없으면 JSON {body, visitMonth?}, 있으면 multipart (body · visitMonth · photos 0~3장).
    kind(regions · dongs)는 urls.py 의 하위 클래스가 정한다.
    """

    kind = ""
    parser_classes = [JSONParser, MultiPartParser]

    def post(self, request, sgg_id: str | None = None, dong_id: str | None = None):
        region_id, dong = None, None
        if self.kind == "regions":
            region = community.region_by_code(sgg_id)
            region_id = region.region_id if region else None
        else:
            dong = community.dong_by_code(dong_id)
            if dong is not None and dong.is_excluded:  # 집계 제외 동은 팁 대상이 아니다
                dong = None
            region_id = dong.region_id if dong else None
        if region_id is None:
            return not_found("팁을 쓸 지역 · 동네를 찾을 수 없습니다.")
        payload = request.data
        if (request.content_type or "").startswith("multipart/"):
            payload = {k: payload[k] for k in ("body", "visitMonth") if k in payload}
        try:
            body, visit_month = community.parse_tip(payload)
        except community.BadTip as ex:
            return bad_request(str(ex))
        try:
            photos = photo_upload.process(request.FILES.getlist("photos"), min_count=0)
        except photo_upload.BadPhoto as ex:
            return error(400, "INVALID_PHOTO", str(ex))
        try:
            note = community.create_tip(request.user, region_id, dong, body, visit_month, photos)
        except photo_upload.UploadFailed:
            return error(502, "UPLOAD_FAILED", "사진을 올리지 못했어요. 잠시 후 다시 시도해 주세요.")
        if note is None:
            return error(
                429,
                "TIP_RATE_LIMIT",
                "팁을 너무 빠르게 올리고 있어요. 잠시 후 다시 시도해 주세요.",
            )
        return Response(note, status=201)
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_note_photos.py tests/test_me_api.py tests/test_webapi.py -q`
Expected: 모두 PASS (`test_query_count` · `test_post_tip_query_count` 포함)

- [ ] **Step 7: 커밋**

```bash
uv run ruff check webapi tests && uv run ruff format webapi tests
git add webapi/community.py webapi/me.py tests/test_note_photos.py tests/test_me_api.py
git commit -m "feat: 노트 쓰기에 사진 첨부 (multipart · Note.photos)"
```

---

### Task 6: 격리 · 복구 · 숨김 · 영구 삭제 · 사용자 삭제 (`webapi/photo_quarantine.py`)

**Files:**
- Create: `webapi/photo_quarantine.py`
- Test: `tests/test_photo_quarantine.py`

**Interfaces:**
- Consumes: `storage.copy/delete/public_url/StorageError` (Task 1), `photo.hidden_at` · `deleted_by_user` (Task 2), `tests.photo_tools.r2` (Task 1), `tests.test_me_api` 의 `me`, `run`, `OTHER_ID`
- Produces (뷰 · 명령이 쓴다):
  - `photo_quarantine.QUARANTINE = "uploads/quarantine/"`, `RETENTION_DAYS = 30`
  - `photo_quarantine.DeleteFailed(RuntimeError)` — 502 `DELETE_FAILED`
  - `key_of(url: str) -> str | None` (우리 `uploads/` 사진이 아니면 None), `is_quarantined(url: str) -> bool`
  - `quarantine(photo_id: int) -> bool` (옮겼으면 True, R2 실패 `StorageError`)
  - `try_quarantine(photo_id: int) -> None` (실패는 로그만)
  - `hide(photo_id: int) -> bool` · `restore(photo_id: int) -> bool` (R2 실패 `StorageError`)
  - `purge(days: int = RETENTION_DAYS) -> tuple[int, list[int]]` (지운 수, R2 실패로 남긴 id)
  - `delete_files(urls: list[str]) -> None` (실패 `DeleteFailed`)
  - `delete_mine(user_id, photo_id: int) -> bool` (없으면 False, R2 실패 `DeleteFailed`)
  - 테스트 도우미 `tests.test_photo_quarantine.user_photo(r2, status="approved", tip_id=None, user_id=USER_ID) -> tuple[int, str]` (photo_id, key)

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_photo_quarantine.py`

```python
"""가린 사진 격리 · 복구 · 숨김 · 영구 삭제 · 사용자 삭제 (스펙 4-6 · 4-7)."""

import uuid

import pytest

from tests.photo_tools import PUBLIC, r2  # noqa: F401
from tests.supabase_tokens import USER_ID, supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import OTHER_ID, me, run  # noqa: F401
from webapi import photo_quarantine as pq
from webapi import storage

pytestmark = pytest.mark.django_db


def user_photo(r2, status="approved", tip_id=None, user_id=USER_ID):  # noqa: F811
    """가짜 R2 에 파일이 있는 사용자 사진 행 → (photo_id, key)."""
    key = f"uploads/{'tip' if tip_id else 'photo'}/{uuid.uuid4()}.webp"
    r2.objects[key] = (b"img", "image/webp")
    pid = run(
        "INSERT INTO photo (scope, unit_id, month, url, source, user_id, status, tip_id)"
        " VALUES ('region', 1, 10, %s, 'user', %s, %s, %s) RETURNING photo_id",
        [f"{PUBLIC}/{key}", user_id, status, tip_id],
    )[0][0]
    return pid, key


def tour_photo(status="pending"):
    """관광공사 사진 (버킷 주인 파일 photos/)."""
    return run(
        "INSERT INTO photo (scope, unit_id, url, source, credit, status)"
        " VALUES ('region', 1, %s, 'tourapi', '한국관광공사', %s) RETURNING photo_id",
        [f"{PUBLIC}/photos/1.webp", status],
    )[0][0]


def col(pid, name):
    return run(f"SELECT {name} FROM photo WHERE photo_id = %s", [pid])[0][0]


def key_now(pid):
    return pq.key_of(col(pid, "url"))


def test_key_of(r2):  # noqa: F811
    assert pq.key_of(f"{PUBLIC}/uploads/photo/a.webp") == "uploads/photo/a.webp"
    assert pq.key_of(f"{PUBLIC}/photos/1.webp") is None  # 버킷 주인 파일
    assert pq.key_of("https://other.example/uploads/photo/a.webp") is None


def test_quarantine_moves_file(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "pending")
    assert pq.quarantine(pid) is True
    new = key_now(pid)
    assert new.startswith(pq.QUARANTINE) and pq.is_quarantined(col(pid, "url"))
    assert key not in r2.objects and r2.objects[new] == (b"img", "image/webp")
    assert pq.quarantine(pid) is False  # 이미 격리됨
    assert key_now(pid) == new


def test_quarantine_skips(me, r2):  # noqa: F811
    approved, key = user_photo(r2, "approved")
    tour = tour_photo("pending")
    assert pq.quarantine(approved) is False and key in r2.objects
    assert pq.quarantine(tour) is False and col(tour, "url") == f"{PUBLIC}/photos/1.webp"
    assert pq.quarantine(999999) is False


def test_quarantine_copy_failure_changes_nothing(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "pending")
    r2.fail_copy = True
    with pytest.raises(storage.StorageError):
        pq.quarantine(pid)
    assert key_now(pid) == key and key in r2.objects
    pq.try_quarantine(pid)  # 삼키고 로그만
    assert key_now(pid) == key


def test_quarantine_original_delete_failure_still_moves(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "pending")
    r2.fail_delete = True
    assert pq.quarantine(pid) is True
    assert pq.is_quarantined(col(pid, "url"))  # url 은 격리 위치 (원래 파일 삭제만 실패, 로그)


def test_hide(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "approved")
    assert pq.hide(pid) is True
    assert col(pid, "status") == "hidden" and col(pid, "hidden_at") is not None
    assert pq.is_quarantined(col(pid, "url")) and key not in r2.objects
    assert pq.hide(pid) is True  # 다시 해도 된다 (격리 실패 뒤 재시도용)
    assert pq.hide(999999) is False


def test_restore(me, r2):  # noqa: F811
    tip = run("INSERT INTO tip (user_id, region_id, body) VALUES (%s, 1, '팁') RETURNING tip_id",
              [me])[0][0]
    pid, _ = user_photo(r2, "pending", tip_id=tip)
    pq.quarantine(pid)
    q_key = key_now(pid)
    run(
        "INSERT INTO report (target_type, target_id, user_id, reason) VALUES ('photo', %s, %s, 'x')",
        [pid, OTHER_ID],
    )
    assert pq.restore(pid) is True
    assert col(pid, "status") == "approved"
    assert key_now(pid).startswith("uploads/tip/")  # 노트 사진은 tip 폴더로
    assert q_key not in r2.objects and key_now(pid) in r2.objects
    assert run("SELECT count(*) FROM report WHERE target_id = %s", [pid]) == [(0,)]
    assert pq.restore(pid) is False  # pending 이 아니다


def test_purge(me, r2, monkeypatch):  # noqa: F811
    old, _ = user_photo(r2, "pending")
    fresh, _ = user_photo(r2, "pending")
    stuck, _ = user_photo(r2, "pending")
    for pid in (old, fresh, stuck):
        pq.hide(pid)
    tour = tour_photo("hidden")
    run("UPDATE photo SET hidden_at = now() - interval '31 days' WHERE photo_id IN (%s, %s, %s)",
        [old, stuck, tour])
    old_key, stuck_key = key_now(old), key_now(stuck)

    real_delete = r2.delete

    def delete(key):  # stuck 의 파일만 지우지 못한다
        if key == stuck_key:
            raise storage.StorageError("가짜 실패")
        real_delete(key)

    monkeypatch.setattr(storage, "delete", delete)
    assert pq.purge() == (1, [stuck])
    assert run("SELECT photo_id FROM photo ORDER BY photo_id") == sorted(
        [(fresh,), (stuck,), (tour,)]
    )
    assert old_key not in r2.objects and stuck_key in r2.objects


def test_delete_mine_approved(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "approved")
    r2.fail_delete = True
    with pytest.raises(pq.DeleteFailed):
        pq.delete_mine(me, pid)
    assert col(pid, "status") == "approved"  # 행은 그대로, 다시 시도하면 된다
    r2.fail_delete = False
    assert pq.delete_mine(me, pid) is True
    assert run("SELECT count(*) FROM photo") == [(0,)] and key not in r2.objects


def test_delete_mine_pending_is_kept_as_evidence(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "pending")  # 아직 격리 전
    assert pq.delete_mine(me, pid) is True
    assert (col(pid, "status"), col(pid, "deleted_by_user")) == ("hidden", True)
    assert col(pid, "hidden_at") is not None
    assert pq.is_quarantined(col(pid, "url")) and key not in r2.objects
    assert pq.delete_mine(me, pid) is False  # hidden 은 다시 못 지운다


def test_delete_mine_not_mine(me, r2):  # noqa: F811
    others, _ = user_photo(r2, "approved", user_id=OTHER_ID)
    tip = run("INSERT INTO tip (user_id, region_id, body) VALUES (%s, 1, '팁') RETURNING tip_id",
              [me])[0][0]
    note_photo, _ = user_photo(r2, "approved", tip_id=tip)
    assert pq.delete_mine(me, others) is False
    assert pq.delete_mine(me, note_photo) is False  # 노트 사진은 노트를 지운다
    assert pq.delete_mine(me, 999999) is False
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_photo_quarantine.py -q`
Expected: FAIL — `ImportError: cannot import name 'photo_quarantine'`

- [ ] **Step 3: `webapi/photo_quarantine.py` 작성**

```python
"""가린 사진 격리 · 복구 · 숨김 · 영구 삭제, 올린 사람의 삭제 (스펙 4-6 · 4-7).

R2 버킷은 r2.dev 로 통째로 공개라 DB 상태만 바꾸면 URL 을 아는 사람은 계속 본다.
그래서 가린 사용자 사진은 추측할 수 없는 새 key(uploads/quarantine/)로 옮기고 원래 key 를 지운다.
관광공사 · 팀 사진(photos/)은 버킷 주인 파일이라 옮기지도 지우지도 않고 상태만 바꾼다.
뷰(uploads · community)와 manage.py photos 가 같이 쓴다. 관리자 화면을 만들면 여기를 부른다.
"""

from __future__ import annotations

import logging
import uuid

from django.db import connection, transaction

from webapi import storage

logger = logging.getLogger(__name__)

QUARANTINE = "uploads/quarantine/"
RETENTION_DAYS = 30  # hidden 이 된 뒤 영구 삭제까지


class DeleteFailed(RuntimeError):
    """R2 파일을 지우지 못했다 (502 DELETE_FAILED). DB 는 바꾸지 않았다."""


def key_of(url: str) -> str | None:
    """우리가 올린 사진(uploads/)의 R2 key. 관광공사 · 팀 사진이나 다른 주소는 None."""
    prefix = storage.public_url("")
    key = url.removeprefix(prefix) if url.startswith(prefix) else ""
    return key if key.startswith("uploads/") else None


def is_quarantined(url: str) -> bool:
    key = key_of(url)
    return key is not None and key.startswith(QUARANTINE)


def _one(sql: str, params) -> tuple | None:
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone() if cur.description else None


def _move(photo_id: int, src: str, folder: str) -> None:
    """src 를 uploads/{folder}/{uuid}.webp 로 복사 → photo.url 갱신 → src 삭제.

    복사가 실패하면 StorageError 이고 아무것도 바뀌지 않는다. src 삭제 실패는 로그만
    (url 은 이미 새 위치라 화면 · 명령은 새 파일을 본다).
    """
    dst = f"uploads/{folder}/{uuid.uuid4()}.webp"
    storage.copy(src, dst)
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE photo SET url = %s WHERE photo_id = %s", [storage.public_url(dst), photo_id]
        )
    try:
        storage.delete(src)
    except storage.StorageError:
        logger.warning("R2 원래 파일 삭제 실패 (photo %s): %s", photo_id, src)


def quarantine(photo_id: int) -> bool:
    """가린(pending · hidden) 사용자 사진의 파일을 격리한다. 옮겼으면 True.

    공개 사진 · 관광공사 사진 · 이미 격리된 사진은 그대로 둔다. R2 실패는 StorageError.
    """
    row = _one("SELECT url, status FROM photo WHERE photo_id = %s", [photo_id])
    if row is None or row[1] == "approved":
        return False
    key = key_of(row[0])
    if key is None or key.startswith(QUARANTINE):
        return False
    _move(photo_id, key, "quarantine")
    return True


def try_quarantine(photo_id: int) -> None:
    """요청 처리 중의 격리. 실패해도 로그만 남긴다 — photos list 가 'URL 열림' 으로 보여 주고
    photos hide 가 다시 격리한다."""
    try:
        quarantine(photo_id)
    except storage.StorageError:
        logger.warning("격리 실패 (photo %s)", photo_id, exc_info=True)


def hide(photo_id: int) -> bool:
    """숨김 확정 후 격리. 없는 사진이면 False. 이미 hidden 이면 격리만 다시 한다(재시도용).

    R2 실패는 StorageError (상태는 hidden 으로 남는다).
    """
    row = _one(
        """UPDATE photo SET status = 'hidden', hidden_at = coalesce(hidden_at, now())
            WHERE photo_id = %s RETURNING photo_id""",
        [photo_id],
    )
    if row is None:
        return False
    quarantine(photo_id)
    return True


def restore(photo_id: int) -> bool:
    """pending 사진을 다시 공개한다. 격리 파일을 새 공개 key 로 옮기고(원래 key 는 캐시에 404 가
    남았을 수 있어 쓰지 않음) approved 로, 그 사진의 신고를 지운다. pending 이 아니면 False."""
    row = _one("SELECT url, tip_id FROM photo WHERE photo_id = %s AND status = 'pending'", [photo_id])
    if row is None:
        return False
    url, tip_id = row
    if is_quarantined(url):
        _move(photo_id, key_of(url), "tip" if tip_id else "photo")
    with transaction.atomic(), connection.cursor() as cur:
        cur.execute("UPDATE photo SET status = 'approved' WHERE photo_id = %s", [photo_id])
        cur.execute(
            "DELETE FROM report WHERE target_type = 'photo' AND target_id = %s", [photo_id]
        )
    return True


def purge(days: int = RETENTION_DAYS) -> tuple[int, list[int]]:
    """hidden 이 된 지 days 일 지난 사용자 사진을 영구 삭제 → (지운 수, R2 실패로 남긴 id).

    파일을 먼저 지우고 성공한 것만 행 · 신고를 지운다. 실패한 행은 다음 purge 때 다시.
    관광공사 · 팀 사진은 버킷 주인 파일이라 행도 남긴다.
    """
    with connection.cursor() as cur:
        cur.execute(
            """SELECT photo_id, url FROM photo
                WHERE status = 'hidden' AND source = 'user'
                  AND hidden_at < now() - make_interval(days => %s)""",
            [days],
        )
        rows = cur.fetchall()
    gone, failed = [], []
    for pid, url in rows:
        try:
            if key := key_of(url):
                storage.delete(key)
        except storage.StorageError:
            logger.warning("purge: R2 삭제 실패 (photo %s)", pid)
            failed.append(pid)
            continue
        gone.append(pid)
    if gone:
        with transaction.atomic(), connection.cursor() as cur:
            cur.execute("DELETE FROM photo WHERE photo_id = ANY(%s)", [gone])
            cur.execute(
                "DELETE FROM report WHERE target_type = 'photo' AND target_id = ANY(%s)", [gone]
            )
    return len(gone), failed


def delete_files(urls: list[str]) -> None:
    """공개 사진 파일을 지운다. 하나라도 실패하면 DeleteFailed (다시 시도하면 된다 —
    S3 삭제는 없는 key 도 성공)."""
    for url in urls:
        if key := key_of(url):
            try:
                storage.delete(key)
            except storage.StorageError as ex:
                raise DeleteFailed(str(ex)) from ex


def delete_mine(user_id, photo_id: int) -> bool:
    """올린 사람이 이달의 사진을 지운다 (스펙 4-6). 내 이달의 사진이 아니거나 이미 hidden 이면 False.

    공개 사진: 파일을 먼저 지우고(실패 → DeleteFailed, 행 그대로) 행 · 신고를 지운다.
    가려진(pending) 사진: 증거라 hidden · deleted_by_user 로 남기고 격리한다 (30일 뒤 purge).
    """
    row = _one(
        """SELECT url, status FROM photo
            WHERE photo_id = %s AND user_id = %s AND tip_id IS NULL AND status <> 'hidden'""",
        [photo_id, user_id],
    )
    if row is None:
        return False
    url, status = row
    with connection.cursor() as cur:
        if status == "approved":
            delete_files([url])
            cur.execute("DELETE FROM photo WHERE photo_id = %s", [photo_id])
            cur.execute(
                "DELETE FROM report WHERE target_type = 'photo' AND target_id = %s", [photo_id]
            )
            return True
        cur.execute(
            """UPDATE photo SET status = 'hidden', hidden_at = now(), deleted_by_user = true
                WHERE photo_id = %s""",
            [photo_id],
        )
    try_quarantine(photo_id)
    return True
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_photo_quarantine.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff check webapi tests && uv run ruff format webapi tests
git add webapi/photo_quarantine.py tests/test_photo_quarantine.py
git commit -m "feat: 가린 사진 격리 · 복구 · 숨김 · 영구 삭제 모듈"
```

---

### Task 7: 이달의 사진 등록 · 사진 신고 (`webapi/uploads.py`)

**Files:**
- Modify: `webapi/community.py` (`Target` · `target()` 추가, 코드 ↔ unit_id 섹션 끝)
- Create: `webapi/uploads.py`
- Modify: `webapi/urls.py`
- Test: `tests/test_month_photos.py`

**Interfaces:**
- Consumes: `photo_upload.*` (Task 4), `photo_quarantine.try_quarantine` · `is_quarantined` (Task 6), `community.my_photos` 모양 (Task 2), 테스트 도구 (Task 1 · 5 와 같은 import), `tests.test_photo_quarantine.user_photo` (Task 6)
- Produces:
  - `community.Target(NamedTuple)`: `scope: str`, `unit_id: int`, `region_id: int`, `name: str`
  - `community.target(kind: str, code: str) -> Target | None` (kind = `"regions"` | `"dongs"`, 집계 제외 동 None)
  - `uploads.PHOTO_HOURLY_LIMIT = 30`
  - `uploads.MonthPhotoView` (kind 는 urls 하위 클래스), `uploads.PhotoReportView`
  - 경로 `POST /api/regions/<sgg_id>/photos`, `POST /api/dongs/<dong_id>/photos`, `POST /api/photos/<photo_id>/report`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_month_photos.py`

```python
"""이달의 사진 등록 (스펙 4-3) · 사진 신고 (4-4)."""

import pytest

from tests.photo_tools import make_image, r2, upload  # noqa: F401
from tests.supabase_tokens import make_token, supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GEOJIN, GOSEONG, OTHER_ID, add_user, auth, me, run  # noqa: F401
from tests.test_photo_quarantine import user_photo
from webapi import photo_quarantine, uploads

pytestmark = pytest.mark.django_db


def post_photos(client, kind, item_id, month="2025-10", n=1, **headers):
    payload = {"photos": [upload(make_image()) for _ in range(n)]}
    if month is not None:
        payload["month"] = month
    return client.post(f"/api/{kind}/{item_id}/photos", payload, **(headers or auth()))


def add_photo(status="approved", tip_id=None):
    return run(
        "INSERT INTO photo (scope, unit_id, month, url, source, status, tip_id)"
        " VALUES ('region', 1, 10, 'https://r2.example/p.webp', 'team', %s, %s)"
        " RETURNING photo_id",
        [status, tip_id],
    )[0][0]


# ───────────── 이달의 사진 ─────────────


def test_month_photos_contract(client, me, r2):  # noqa: F811
    r = post_photos(client, "regions", GOSEONG, n=2)
    assert r.status_code == 201
    items = r.json()["items"]
    assert [i | {"id": "", "url": ""} for i in items] == [
        {"id": "", "url": "", "month": "2025-10", "regionName": "고성군", "status": "approved"}
    ] * 2
    assert all(i["url"].startswith("https://pub.r2.example/uploads/photo/") for i in items)
    rows = run("SELECT scope, unit_id, month, source, user_id::text, tip_id FROM photo")
    assert rows == [("region", 1, 10, "user", me, None)] * 2
    mine = client.get("/api/me/photos", **auth()).json()["items"]
    assert sorted(i["id"] for i in mine) == sorted(i["id"] for i in items)


def test_month_photos_annual_and_dong(client, me, r2):  # noqa: F811
    r = post_photos(client, "dongs", GEOJIN, month="annual")
    assert r.status_code == 201 and r.json()["items"][0]["month"] == "annual"
    assert run("SELECT scope, unit_id, month FROM photo") == [("dong", 101, None)]


@pytest.mark.parametrize(("month", "n"), [(None, 1), ("2025-13", 1), ("10", 1), ("2025-10", 0)])
def test_month_photos_bad_400(client, me, r2, month, n):  # noqa: F811
    r = post_photos(client, "regions", GOSEONG, month=month, n=n)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PHOTO"
    assert r2.puts == 0


def test_month_photos_404_and_401(client, me, r2):  # noqa: F811
    assert post_photos(client, "regions", "99999").status_code == 404
    assert post_photos(client, "dongs", "0000000000").status_code == 404
    r = client.post(f"/api/regions/{GOSEONG}/photos", {"month": "2025-10"})
    assert r.status_code == 401


def test_month_photos_hourly_limit(client, me, r2, monkeypatch):  # noqa: F811
    monkeypatch.setattr(uploads, "PHOTO_HOURLY_LIMIT", 3)
    assert post_photos(client, "regions", GOSEONG, n=2).status_code == 201
    r = post_photos(client, "regions", GOSEONG, n=2)
    assert r.status_code == 429 and r.json()["error"]["code"] == "PHOTO_RATE_LIMIT"
    assert r2.puts == 2
    run("UPDATE photo SET created_at = now() - interval '61 minutes'")
    assert post_photos(client, "regions", GOSEONG, n=2).status_code == 201


def test_month_photos_upload_failure(client, me, r2):  # noqa: F811
    r2.fail_after = 0
    r = post_photos(client, "regions", GOSEONG)
    assert r.status_code == 502 and r.json()["error"]["code"] == "UPLOAD_FAILED"
    assert run("SELECT count(*) FROM photo") == [(0,)]


# ───────────── 신고 ─────────────


def report(client, photo_id, reason="부적절해요", token=None):
    return client.post(
        f"/api/photos/{photo_id}/report",
        {"reason": reason},
        content_type="application/json",
        **auth(token),
    )


def reporters(client, n):
    """신고할 사용자 n 명의 토큰. 첫 GET /api/me 로 app_user 를 만든다."""
    tokens = []
    for i in range(n):
        sub = f"00000000-0000-4000-8000-00000000000{i}"
        token = make_token(sub=sub, user_metadata={"name": f"신고{i}", "provider_id": sub})
        assert client.get("/api/me", **auth(token)).status_code == 200
        tokens.append(token)
    return tokens


def status_of(photo_id):
    return run("SELECT status FROM photo WHERE photo_id = %s", [photo_id])[0][0]


def test_report_hides_at_threshold(client, me, settings):  # noqa: F811
    settings.PHOTO_REPORT_THRESHOLD = 3
    photo = add_photo()
    t1, t2, t3 = reporters(client, 3)
    assert report(client, photo, token=t1).status_code == 204
    assert report(client, photo, token=t1).status_code == 204  # 같은 사람은 한 번만 센다
    assert report(client, photo, token=t2).status_code == 204
    assert status_of(photo) == "approved"
    assert report(client, photo, token=t3).status_code == 204
    assert status_of(photo) == "pending"
    assert run("SELECT count(*) FROM report WHERE target_type = 'photo'") == [(3,)]
    # 가려진 사진은 갤러리에서 빠지고 다시 신고할 수 없다
    body = client.get("/api/v1/regions/51820?month=10").json()
    assert body["photos"]["totalCount"] == 0
    assert report(client, photo).status_code == 404
    # 팀 사진(r2.example)은 버킷 주인 파일이라 옮기지 않는다
    assert run("SELECT url FROM photo WHERE photo_id = %s", [photo]) == [
        ("https://r2.example/p.webp",)
    ]


def test_report_quarantines_user_photo(client, me, r2, settings):  # noqa: F811
    settings.PHOTO_REPORT_THRESHOLD = 1
    photo, key = user_photo(r2)
    assert report(client, photo).status_code == 204
    url = run("SELECT url FROM photo WHERE photo_id = %s", [photo])[0][0]
    assert photo_quarantine.is_quarantined(url) and key not in r2.objects


def test_report_quarantine_failure_still_204(client, me, r2, settings):  # noqa: F811
    settings.PHOTO_REPORT_THRESHOLD = 1
    photo, key = user_photo(r2)
    r2.fail_copy = True
    assert report(client, photo).status_code == 204
    assert status_of(photo) == "pending"  # 가리기는 됐고, 파일은 photos hide 로 다시 격리
    assert key in r2.objects


@pytest.mark.parametrize("reason", ["", "   ", "가" * 31, None, 3])
def test_report_bad_reason(client, me, reason):  # noqa: F811
    r = report(client, add_photo(), reason=reason)
    assert r.status_code == 400


def test_report_404_and_401(client, me):  # noqa: F811
    assert report(client, 999999).status_code == 404
    assert report(client, "abc").status_code == 404
    assert report(client, add_photo("hidden")).status_code == 404
    r = client.post(
        f"/api/photos/{add_photo()}/report", {"reason": "x"}, content_type="application/json"
    )
    assert r.status_code == 401
```

`reporters` 의 토큰은 `sub` 와 `user_metadata.provider_id` 만 바꾼다. `accounts/services.py` 의 `get_or_create_user` 가 `provider_id` 로 `provider_uid` 를 정하므로 사용자마다 달라야 한다 (같으면 앞 사용자 행이 지워진다).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_month_photos.py -q`
Expected: FAIL (경로 404)

- [ ] **Step 3: `community.target` 추가** — `webapi/community.py` 의 `unit_id` 함수 아래

```python
class Target(NamedTuple):
    """사진 · 투표 대상. unit_id 는 region_id 또는 dong_id, region_id 는 소속 시군구."""

    scope: str
    unit_id: int
    region_id: int
    name: str


def target(kind: str, code: str) -> Target | None:
    """웹 경로의 (regions|dongs, 코드) → Target. 없는 코드 · 집계 제외 동은 None."""
    if kind == "regions":
        r = region_by_code(code)
        return Target("region", r.region_id, r.region_id, r.sigungu_name) if r else None
    d = dong_by_code(code)
    if d is None or d.is_excluded:
        return None
    return Target("dong", d.dong_id, d.region_id, d.dong_name)
```

import 에 `from typing import NamedTuple` 추가.

- [ ] **Step 4: `webapi/uploads.py` 작성**

```python
"""이달의 사진 올리기 · 사진 신고 (스펙 4-3 · 4-4). 사진 처리는 webapi/photo_upload.py.

사진은 올리면 바로 공개(approved)되고, 신고가 PHOTO_REPORT_THRESHOLD 건 쌓이면 가려진다(pending).
가려진 사진은 운영자가 manage.py photos 로 복구하거나 숨김을 확정한다.
"""

from django.conf import settings
from django.db import connection
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response

from core.errors import bad_request, error, not_found
from webapi import community, data, photo_quarantine, photo_upload
from webapi.me import MeAPIView

PHOTO_HOURLY_LIMIT = 30  # 사용자당 최근 1시간 이달의 사진 장수
REASON_MAX_LEN = 30  # report.reason VARCHAR(30)


def _bad_photo(message: str) -> Response:
    return error(400, "INVALID_PHOTO", message)


class MonthPhotoView(MeAPIView):
    """POST /api/{regions|dongs}/{id}/photos. multipart: month · photos(1~3장). 201 {items: MyPhoto[]}"""

    kind = ""
    parser_classes = [MultiPartParser]

    def post(self, request, sgg_id: str | None = None, dong_id: str | None = None):
        t = community.target(self.kind, sgg_id or dong_id)
        if t is None:
            return not_found("사진을 올릴 지역 · 동네를 찾을 수 없습니다.")
        raw = request.data.get("month")
        if not raw:
            return _bad_photo("month 는 'annual' 또는 '2025-01' ~ '2025-12' 입니다.")
        try:
            month, month_str = data.parse_month(raw)
            blobs = photo_upload.process(request.FILES.getlist("photos"), min_count=1)
        except data.BadMonth as ex:
            return _bad_photo(str(ex))
        except photo_upload.BadPhoto as ex:
            return _bad_photo(str(ex))
        user_id = request.user.user_id
        if _recent_count(user_id) + len(blobs) > PHOTO_HOURLY_LIMIT:
            return error(
                429,
                "PHOTO_RATE_LIMIT",
                f"사진은 1시간에 {PHOTO_HOURLY_LIMIT}장까지 올릴 수 있어요.",
            )
        try:
            uploaded = photo_upload.upload("photo", blobs)
        except photo_upload.UploadFailed:
            return error(502, "UPLOAD_FAILED", "사진을 올리지 못했어요. 잠시 후 다시 시도해 주세요.")
        try:
            ids = _insert(t, month, user_id, [url for _, url in uploaded])
        except Exception:
            photo_upload.discard([key for key, _ in uploaded])
            raise
        region_name = data.regions_by_id()[t.region_id].sigungu_name
        items = [
            {
                "id": str(pid),
                "url": url,
                "month": month_str,
                "regionName": region_name,
                "status": "approved",
            }
            for pid, (_, url) in zip(ids, uploaded, strict=True)
        ]
        return Response({"items": items}, status=201)


def _recent_count(user_id) -> int:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM photo WHERE user_id = %s AND tip_id IS NULL"
            " AND created_at > now() - interval '1 hour'",
            [user_id],
        )
        return cur.fetchone()[0]


def _insert(t: community.Target, month: int | None, user_id, urls: list[str]) -> list[int]:
    """사진 행을 한 문장으로 넣는다 → photo_id (urls 순서)."""
    with connection.cursor() as cur:
        cur.execute(
            """INSERT INTO photo (scope, unit_id, month, url, source, user_id, status)
               SELECT %(scope)s, %(unit)s, %(month)s, u.url, 'user', %(user)s, 'approved'
                 FROM unnest(%(urls)s::text[]) WITH ORDINALITY AS u(url, n)
               RETURNING photo_id, url""",
            {"scope": t.scope, "unit": t.unit_id, "month": month, "user": user_id, "urls": urls},
        )
        by_url = {url: pid for pid, url in cur.fetchall()}
    return [by_url[url] for url in urls]


class PhotoReportView(MeAPIView):
    """POST /api/photos/{photoId}/report {reason}. 204. 같은 사람의 다시 신고도 204 (한 번만 센다)."""

    def post(self, request, photo_id: str):
        reason = request.data.get("reason") if isinstance(request.data, dict) else None
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= REASON_MAX_LEN:
            return bad_request(f"reason 은 1~{REASON_MAX_LEN}자입니다.")
        pid = photo_id_or_none(photo_id)
        found, hid = _report(pid, request.user.user_id, reason.strip()) if pid else (False, False)
        if not found:
            return not_found("사진을 찾을 수 없습니다.")
        if hid:  # 가렸으면 원래 URL 도 막는다. 실패해도 응답은 204 (스펙 4-4)
            photo_quarantine.try_quarantine(pid)
        return Response(status=204)


def photo_id_or_none(raw: str) -> int | None:
    """경로의 photoId. photo_id 는 SERIAL 이라 그 범위의 숫자만 본다."""
    return int(raw) if raw.isascii() and raw.isdigit() and len(raw) <= 9 else None


def _report(photo_id: int, user_id, reason: str) -> tuple[bool, bool]:
    """신고를 넣고, 신고 수가 기준에 닿으면 가린다 → (공개 사진이 있었나, 이번에 가렸나).

    한 문장(= 한 트랜잭션). CTE 는 같은 문장에서 넣은 행을 못 보므로 기존 신고 수 + 이번에 넣은 수로 센다.
    """
    with connection.cursor() as cur:
        cur.execute(
            """WITH p AS (
                 SELECT photo_id FROM photo
                  WHERE photo_id = %(id)s AND status = 'approved' FOR UPDATE
               ), ins AS (
                 INSERT INTO report (target_type, target_id, user_id, reason)
                 SELECT 'photo', photo_id, %(user)s, %(reason)s FROM p
                 ON CONFLICT DO NOTHING
                 RETURNING 1
               ), up AS (
                 UPDATE photo SET status = 'pending'
                  WHERE photo_id IN (SELECT photo_id FROM p)
                    AND (SELECT count(*) FROM report
                          WHERE target_type = 'photo' AND target_id = %(id)s)
                        + (SELECT count(*) FROM ins) >= %(threshold)s
                 RETURNING 1
               )
               SELECT (SELECT count(*) FROM p), (SELECT count(*) FROM up)""",
            {
                "id": photo_id,
                "user": user_id,
                "reason": reason,
                "threshold": settings.PHOTO_REPORT_THRESHOLD,
            },
        )
        found, hid = cur.fetchone()
    return found > 0, hid > 0
```

- [ ] **Step 5: 경로 추가** — `webapi/urls.py`

import 를 `from webapi import me, uploads, views` 로 바꾸고, `DongNoteCreateView` 아래에:

```python
class RegionMonthPhotoView(uploads.MonthPhotoView):
    kind = "regions"


class DongMonthPhotoView(uploads.MonthPhotoView):
    kind = "dongs"
```

`urlpatterns` 의 `regions/<str:sgg_id>/poll` 줄 위에:

```python
    path("regions/<str:sgg_id>/photos", RegionMonthPhotoView.as_view(), name="web-region-photos"),
```

`dongs/<str:dong_id>/poll` 줄 위에:

```python
    path("dongs/<str:dong_id>/photos", DongMonthPhotoView.as_view(), name="web-dong-photos"),
```

`path("search/parse", ...)` 위에:

```python
    path("photos/<str:photo_id>/report", uploads.PhotoReportView.as_view(), name="web-photo-report"),
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_month_photos.py tests/test_me_api.py -q`
Expected: 모두 PASS

- [ ] **Step 7: 커밋**

```bash
uv run ruff check webapi tests && uv run ruff format webapi tests
git add webapi/community.py webapi/uploads.py webapi/urls.py tests/test_month_photos.py
git commit -m "feat: 이달의 사진 올리기와 사진 신고 API"
```

---

### Task 8: 사진 삭제 (`DELETE /api/me/photos/{id}`) · 노트 삭제 시 첨부 사진

**Files:**
- Modify: `webapi/uploads.py` (`MyPhotoView` 추가), `webapi/urls.py` (경로)
- Modify: `webapi/community.py` (`delete_tip`, import), `webapi/me.py` (`MyTipView`)
- Test: `tests/test_photo_delete.py`

**Interfaces:**
- Consumes: `photo_quarantine.delete_mine/delete_files/try_quarantine/quarantine/is_quarantined/DeleteFailed` (Task 6), `uploads.photo_id_or_none` (Task 7), `tests.test_photo_quarantine.user_photo` (Task 6)
- Produces: `DELETE /api/me/photos/<photo_id>` (204 · 404 · 502 `DELETE_FAILED`), `community.delete_tip(user_id, tip_id) -> bool` (R2 실패 `DeleteFailed`), `DELETE /api/me/tips/{id}` 의 502 `DELETE_FAILED`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_photo_delete.py`

```python
"""올린 사람의 사진 삭제 · 노트 삭제 시 첨부 사진 (스펙 4-6)."""

import pytest

from tests.photo_tools import PUBLIC, make_image, r2, upload  # noqa: F401
from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GOSEONG, OTHER_ID, auth, me, run  # noqa: F401
from tests.test_photo_quarantine import user_photo
from webapi import photo_quarantine

pytestmark = pytest.mark.django_db


def delete_photo(client, photo_id, **headers):
    return client.delete(f"/api/me/photos/{photo_id}", **(headers or auth()))


def test_delete_my_photo(client, me, r2):  # noqa: F811
    pid, key = user_photo(r2)
    assert delete_photo(client, pid).status_code == 204
    assert key not in r2.objects and run("SELECT count(*) FROM photo") == [(0,)]
    assert client.get("/api/me/photos", **auth()).json()["items"] == []


def test_delete_my_photo_r2_failure(client, me, r2):  # noqa: F811
    pid, key = user_photo(r2)
    r2.fail_delete = True
    r = delete_photo(client, pid)
    assert r.status_code == 502 and r.json()["error"]["code"] == "DELETE_FAILED"
    assert run("SELECT count(*) FROM photo") == [(1,)] and key in r2.objects


def test_delete_reported_photo_keeps_evidence(client, me, r2):  # noqa: F811
    pid, _ = user_photo(r2, "pending")
    assert delete_photo(client, pid).status_code == 204
    assert client.get("/api/me/photos", **auth()).json()["items"] == []
    assert run("SELECT status, deleted_by_user FROM photo") == [("hidden", True)]
    assert photo_quarantine.is_quarantined(run("SELECT url FROM photo")[0][0])


def test_delete_photo_404_401(client, me, r2):  # noqa: F811
    others, _ = user_photo(r2, user_id=OTHER_ID)
    for photo_id in (others, 999999, "abc"):
        r = delete_photo(client, photo_id)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert client.delete(f"/api/me/photos/{others}").status_code == 401


def post_note(client, n):
    payload = {"body": "사진 팁", "photos": [upload(make_image()) for _ in range(n)]}
    return client.post(f"/api/regions/{GOSEONG}/notes", payload, **auth()).json()


def delete_note(client, note):
    return client.delete(f"/api/me/tips/{note['id']}", **auth())


def test_delete_note_deletes_photo_files(client, me, r2):  # noqa: F811
    note = post_note(client, 2)
    keys = [p["url"].removeprefix(f"{PUBLIC}/") for p in note["photos"]]
    assert delete_note(client, note).status_code == 204
    assert not set(keys) & set(r2.objects)
    assert run("SELECT count(*) FROM photo") == [(0,)]
    assert run("SELECT count(*) FROM tip") == [(0,)]


def test_delete_note_keeps_reported_photo(client, me, r2):  # noqa: F811
    note = post_note(client, 2)
    reported = note["photos"][0]["id"]
    run("UPDATE photo SET status = 'pending' WHERE photo_id = %s", [reported])
    photo_quarantine.quarantine(int(reported))
    assert delete_note(client, note).status_code == 204
    rows = run("SELECT photo_id::text, tip_id, status, deleted_by_user, url FROM photo")
    assert [r[:4] for r in rows] == [(reported, None, "hidden", True)]
    assert photo_quarantine.is_quarantined(rows[0][4])
    assert run("SELECT count(*) FROM tip") == [(0,)]


def test_delete_note_r2_failure_keeps_note(client, me, r2):  # noqa: F811
    note = post_note(client, 1)
    r2.fail_delete = True
    r = delete_note(client, note)
    assert r.status_code == 502 and r.json()["error"]["code"] == "DELETE_FAILED"
    assert run("SELECT count(*) FROM tip") == [(1,)]
    assert run("SELECT count(*) FROM photo") == [(1,)]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_photo_delete.py -q`
Expected: FAIL (`DELETE /api/me/photos/...` 경로 없음 405/404, 노트 삭제 뒤 파일이 남음)

- [ ] **Step 3: `community.delete_tip` 바꾸기**

import 를 `from webapi import data, photo_quarantine, photo_upload` 로 바꾸고, `delete_tip` 전체를 아래로 바꾼다:

```python
def delete_tip(user_id, tip_id: int) -> bool:
    """내 팁을 지운다 (도움돼요 · 답글은 ON DELETE CASCADE). 내 팁이 아니면 False.

    첨부 사진 (스펙 4-6):
    - 공개(approved) 사진은 R2 파일을 먼저 지운다. 실패하면 DeleteFailed 이고 팁은 그대로
      (다시 시도하면 된다). 행은 팁과 함께 지워진다 (CASCADE).
    - 가려진(pending · hidden) 사진은 증거라 팁에서 떼어(tip_id = NULL) hidden · deleted_by_user 로
      남긴다. 30일 뒤 manage.py photos purge 가 지운다.
    """
    with connection.cursor() as cur:
        cur.execute(
            """SELECT p.photo_id, p.url, p.status
                 FROM tip t JOIN photo p ON p.tip_id = t.tip_id
                WHERE t.tip_id = %s AND t.user_id = %s""",
            [tip_id, user_id],
        )
        photos = cur.fetchall()
    photo_quarantine.delete_files([url for _, url, status in photos if status == "approved"])
    kept = [pid for pid, _, status in photos if status != "approved"]
    with transaction.atomic(), connection.cursor() as cur:
        if kept:
            cur.execute(
                """UPDATE photo SET tip_id = NULL, status = 'hidden',
                          hidden_at = coalesce(hidden_at, now()), deleted_by_user = true
                    WHERE photo_id = ANY(%s)""",
                [kept],
            )
        cur.execute("DELETE FROM tip WHERE tip_id = %s AND user_id = %s", [tip_id, user_id])
        deleted = cur.rowcount > 0
    for pid in kept:  # 보통 이미 격리돼 있다 (신고로 가려질 때)
        photo_quarantine.try_quarantine(pid)
    return deleted
```

- [ ] **Step 4: `MyTipView` 에 502 추가** — `webapi/me.py`

import 를 `from webapi import community, data, photo_quarantine, photo_upload` 로 바꾸고 `MyTipView` 를:

```python
class MyTipView(MeAPIView):
    def delete(self, request, tip_id: str):
        # tip_id 는 INTEGER 라 그 범위의 숫자만 본다
        if not (tip_id.isascii() and tip_id.isdigit() and len(tip_id) <= 9):
            return not_found("내 팁을 찾을 수 없습니다.")
        try:
            deleted = community.delete_tip(request.user.user_id, int(tip_id))
        except photo_quarantine.DeleteFailed:
            return error(502, "DELETE_FAILED", "사진을 지우지 못했어요. 잠시 후 다시 시도해 주세요.")
        if not deleted:
            return not_found("내 팁을 찾을 수 없습니다.")
        return Response(status=204)
```

- [ ] **Step 5: `MyPhotoView` · 경로 추가**

`webapi/uploads.py` 끝에:

```python
class MyPhotoView(MeAPIView):
    """DELETE /api/me/photos/{photoId}. 204. 내 이달의 사진이 아니면 404 (스펙 4-6)."""

    def delete(self, request, photo_id: str):
        pid = photo_id_or_none(photo_id)
        try:
            deleted = pid is not None and photo_quarantine.delete_mine(request.user.user_id, pid)
        except photo_quarantine.DeleteFailed:
            return error(502, "DELETE_FAILED", "사진을 지우지 못했어요. 잠시 후 다시 시도해 주세요.")
        if not deleted:
            return not_found("내 사진을 찾을 수 없습니다.")
        return Response(status=204)
```

`webapi/urls.py` 의 `path("me/photos", ...)` 줄 아래에:

```python
    path("me/photos/<str:photo_id>", uploads.MyPhotoView.as_view(), name="me-photo"),
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_photo_delete.py tests/test_me_api.py tests/test_note_photos.py -q`
Expected: 모두 PASS (`test_delete_my_tip` 포함)

- [ ] **Step 7: 커밋**

```bash
uv run ruff check webapi tests && uv run ruff format webapi tests
git add webapi/community.py webapi/me.py webapi/uploads.py webapi/urls.py tests/test_photo_delete.py
git commit -m "feat: 내 사진 삭제와 노트 삭제 시 첨부 사진 정리 (가려진 사진은 증거로 보관)"
```

---

### Task 9: 체감 투표 (`webapi/poll.py`)

**Files:**
- Create: `webapi/poll.py`
- Modify: `webapi/urls.py` (poll 경로), `webapi/views.py` (`empty_poll` 섹션 삭제, 모듈 docstring 의 "체감 투표" 언급 정리)
- Test: `tests/test_poll.py`

**Interfaces:**
- Consumes: `community.target/Target` (Task 7), `poll_vote` 표 (Task 2), `accounts.views.LoginAPIView`, `webapi.me.MeAPIView`
- Produces:
  - `poll.question(month: int | None, name: str, preset: str) -> str`
  - `poll.PollView` (GET, 로그인 선택), `poll.PollVoteView` (POST, 로그인) — kind 는 urls 하위 클래스
  - 경로 `GET /api/{regions|dongs}/{id}/poll`, `POST /api/{regions|dongs}/{id}/poll/vote`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_poll.py`

```python
"""체감 투표 (스펙 4-5)."""

import pytest

from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GEOJIN, GOSEONG, OTHER_ID, auth, me, run  # noqa: F401
from webapi import poll

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("month", "name", "preset", "text"),
    [
        (10, "고성군", "quiet", "10월 고성군, 실제로 조용했나요?"),
        (3, "고성군", "lively", "3월 고성군, 실제로 활기 있었나요?"),
        (None, "고성군", "custom", "연간 고성군, 어땠나요?"),
        (10, "거진읍", "", "10월 거진읍, 어땠나요?"),
    ],
)
def test_question(month, name, preset, text):
    assert poll.question(month, name, preset) == text


def vote(client, kind, item_id, choice, month="2025-10", preset="quiet"):
    payload = {"month": month, "choice": choice}
    if preset is not None:
        payload["preset"] = preset
    return client.post(
        f"/api/{kind}/{item_id}/poll/vote", payload, content_type="application/json", **auth()
    )


def other_votes(scope, unit, month, preset, choice):
    run(
        "INSERT INTO poll_vote (user_id, scope, unit_id, month, preset, choice)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        [OTHER_ID, scope, unit, month, preset, choice],
    )


def test_get_anonymous(client, me):  # noqa: F811
    other_votes("region", 1, 10, "quiet", False)
    body = client.get(f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=quiet").json()
    assert body == {
        "question": "10월 고성군, 실제로 조용했나요?",
        "yes": 0,
        "no": 1,
        "total": 1,
        "photos": [],
        "myVote": None,
    }


def test_vote_change_cancel(client, me):  # noqa: F811
    other_votes("region", 1, 10, "quiet", True)
    r = vote(client, "regions", GOSEONG, "yes")
    assert r.status_code == 200
    assert (r.json()["yes"], r.json()["no"], r.json()["myVote"]) == (2, 0, "yes")
    r = vote(client, "regions", GOSEONG, "no")
    assert (r.json()["yes"], r.json()["no"], r.json()["myVote"]) == (1, 1, "no")
    got = client.get(f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=quiet", **auth()).json()
    assert got == r.json()
    r = vote(client, "regions", GOSEONG, None)
    assert (r.json()["total"], r.json()["myVote"]) == (1, None)
    assert vote(client, "regions", GOSEONG, None).status_code == 200  # 없어도 200


def test_votes_are_per_question(client, me):  # noqa: F811
    vote(client, "regions", GOSEONG, "yes", preset="quiet")
    lively = client.get(f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=lively").json()
    annual = client.get(f"/api/regions/{GOSEONG}/poll?month=annual&preset=quiet").json()
    assert lively["total"] == annual["total"] == 0


def test_dong_ignores_preset(client, me):  # noqa: F811
    r = vote(client, "dongs", GEOJIN, "yes", month="annual", preset="lively")
    assert r.status_code == 200 and r.json()["question"] == "연간 거진읍, 어땠나요?"
    assert run("SELECT scope, unit_id, month, preset FROM poll_vote") == [("dong", 101, 0, "")]
    assert client.get(f"/api/dongs/{GEOJIN}/poll?month=annual").json()["total"] == 1


def test_poll_photos(client, me):  # noqa: F811
    tip = run("INSERT INTO tip (user_id, region_id, body) VALUES (%s, 1, '팁') RETURNING tip_id",
              [me])[0][0]
    rows = [  # (month, source, user_id, credit, status, tip_id)
        (10, "user", me, None, "approved", None),
        (10, "tourapi", None, "한국관광공사 포토코리아 / 갑", "approved", None),
        (7, "user", me, None, "approved", None),  # 다른 달
        (10, "user", me, None, "pending", None),  # 가려짐
        (10, "user", me, None, "approved", tip),  # 노트 사진
    ]
    ids = []
    for i, (month, source, user, credit, status, tip_id) in enumerate(rows):
        ids.append(run(
            "INSERT INTO photo (scope, unit_id, month, url, source, user_id, credit, status,"
            " tip_id, created_at) VALUES ('region', 1, %s, %s, %s, %s, %s, %s, %s,"
            " now() - make_interval(mins => %s)) RETURNING photo_id",
            [month, f"https://r2.example/{i}.webp", source, user, credit, status, tip_id, i],
        )[0][0])
    body = client.get(f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=quiet").json()
    assert body["photos"] == [  # 최신순, 업로더 닉네임 또는 출처
        {"id": str(ids[0]), "url": "https://r2.example/0.webp", "author": "두이"},
        {"id": str(ids[1]), "url": "https://r2.example/1.webp",
         "author": "한국관광공사 포토코리아 / 갑"},
    ]
    annual = client.get(f"/api/regions/{GOSEONG}/poll?month=annual&preset=quiet").json()
    assert len(annual["photos"]) == 3  # 연간은 달을 가리지 않는다


@pytest.mark.parametrize(
    "url",
    [
        f"/api/regions/{GOSEONG}/poll?month=2025-10",
        f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=calm",
        f"/api/regions/{GOSEONG}/poll?month=10&preset=quiet",
    ],
)
def test_get_bad_400(client, me, url):  # noqa: F811
    assert client.get(url).status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"month": "2025-10", "preset": "quiet"},  # choice 없음
        {"month": "2025-10", "preset": "quiet", "choice": "maybe"},
        {"month": "2025-10", "choice": "yes"},  # region 은 preset 필수
        {"preset": "quiet", "choice": "yes"},  # month 없음
        {"month": "2025-13", "preset": "quiet", "choice": "yes"},
    ],
)
def test_vote_bad_400(client, me, payload):  # noqa: F811
    r = client.post(
        f"/api/regions/{GOSEONG}/poll/vote", payload, content_type="application/json", **auth()
    )
    assert r.status_code == 400
    assert run("SELECT count(*) FROM poll_vote") == [(0,)]


def test_poll_404_and_vote_401(client, me):  # noqa: F811
    assert client.get("/api/regions/99999/poll?month=2025-10&preset=quiet").status_code == 404
    assert client.get("/api/dongs/0000000000/poll?month=2025-10").status_code == 404
    r = client.post(
        f"/api/regions/{GOSEONG}/poll/vote",
        {"month": "2025-10", "preset": "quiet", "choice": "yes"},
        content_type="application/json",
    )
    assert r.status_code == 401
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_poll.py -q`
Expected: FAIL — `ImportError: cannot import name 'poll'`

- [ ] **Step 3: `webapi/poll.py` 작성**

```python
"""체감 투표 (스펙 4-5). GET /api/{regions|dongs}/{id}/poll · POST .../poll/vote.

질문 하나 = (scope, unit_id, month, preset) 에 사용자당 한 표 (poll_vote).
month 는 DB 에서 0 = 연간. 동은 preset 이 없어 '' 로 저장한다.
"""

from django.db import connection
from rest_framework.response import Response

from accounts.views import LoginAPIView
from core.errors import bad_request, not_found
from webapi import community, data
from webapi.me import MeAPIView

PRESETS = ("quiet", "lively", "custom")
TAILS = {"quiet": "실제로 조용했나요?", "lively": "실제로 활기 있었나요?"}
PHOTO_LIMIT = 6
CHOICES = {"yes": True, "no": False, None: None}


class BadPoll(ValueError):
    pass


def question(month: int | None, name: str, preset: str) -> str:
    """웹 mock(getPollMock)과 같은 문구. '10월 고성군, 실제로 조용했나요?'"""
    label = f"{month}월" if month else "연간"
    return f"{label} {name}, {TAILS.get(preset, '어땠나요?')}"


def _preset(scope: str, raw) -> str:
    if scope == "dong":
        return ""
    if raw not in PRESETS:
        raise BadPoll(f"preset 은 {', '.join(PRESETS)} 중 하나입니다.")
    return raw


def _month(raw) -> int | None:
    if not isinstance(raw, str) or not raw:
        raise BadPoll("month 는 'annual' 또는 '2025-01' ~ '2025-12' 입니다.")
    try:
        return data.parse_month(raw)[0]
    except data.BadMonth as ex:
        raise BadPoll(str(ex)) from ex


def result(t: community.Target, month: int | None, preset: str, user_id) -> dict:
    key = {"scope": t.scope, "unit": t.unit_id, "month": month or 0, "preset": preset}
    with connection.cursor() as cur:
        cur.execute(
            """SELECT count(*) FILTER (WHERE choice), count(*) FILTER (WHERE NOT choice),
                      bool_or(choice) FILTER (WHERE user_id = %(user)s)
                 FROM poll_vote
                WHERE scope = %(scope)s AND unit_id = %(unit)s
                  AND month = %(month)s AND preset = %(preset)s""",
            key | {"user": user_id},
        )
        yes, no, mine = cur.fetchone()
        cur.execute(
            """SELECT p.photo_id, p.url, coalesce(u.nickname, p.credit, '')
                 FROM photo p LEFT JOIN app_user u ON u.user_id = p.user_id
                WHERE p.status = 'approved' AND p.tip_id IS NULL
                  AND p.scope = %(scope)s AND p.unit_id = %(unit)s
                  AND (%(month)s::int IS NULL OR p.month = %(month)s)
                ORDER BY p.created_at DESC, p.photo_id DESC
                LIMIT %(limit)s""",
            {"scope": t.scope, "unit": t.unit_id, "month": month, "limit": PHOTO_LIMIT},
        )
        photos = [{"id": str(pid), "url": url, "author": a} for pid, url, a in cur.fetchall()]
    return {
        "question": question(month, t.name, preset),
        "yes": yes,
        "no": no,
        "total": yes + no,
        "photos": photos,
        "myVote": None if mine is None else ("yes" if mine else "no"),
    }


def save_vote(user_id, t: community.Target, month: int | None, preset: str, choice) -> None:
    """choice True/False 면 넣거나 바꾸고, None 이면 지운다 (없어도 그대로)."""
    key = {"user": user_id, "scope": t.scope, "unit": t.unit_id, "month": month or 0,
           "preset": preset}
    with connection.cursor() as cur:
        if choice is None:
            cur.execute(
                """DELETE FROM poll_vote WHERE user_id = %(user)s AND scope = %(scope)s
                     AND unit_id = %(unit)s AND month = %(month)s AND preset = %(preset)s""",
                key,
            )
        else:
            cur.execute(
                """INSERT INTO poll_vote (user_id, scope, unit_id, month, preset, choice)
                   VALUES (%(user)s, %(scope)s, %(unit)s, %(month)s, %(preset)s, %(choice)s)
                   ON CONFLICT (user_id, scope, unit_id, month, preset)
                   DO UPDATE SET choice = EXCLUDED.choice, created_at = now()""",
                key | {"choice": choice},
            )


class PollView(LoginAPIView):
    """GET /api/{regions|dongs}/{id}/poll?month=&preset=. 로그인하면 myVote 가 채워진다."""

    kind = ""

    def get(self, request, sgg_id: str | None = None, dong_id: str | None = None):
        t = community.target(self.kind, sgg_id or dong_id)
        if t is None:
            return not_found("투표 대상을 찾을 수 없습니다.")
        q = request.query_params
        try:
            month = _month(q.get("month") or "annual")
            preset = _preset(t.scope, q.get("preset"))
        except BadPoll as ex:
            return bad_request(str(ex))
        user_id = request.user.user_id if request.user else None
        return Response(result(t, month, preset, user_id))


class PollVoteView(MeAPIView):
    """POST /api/{regions|dongs}/{id}/poll/vote {month, preset?, choice}. 200 + 갱신된 투표."""

    kind = ""

    def post(self, request, sgg_id: str | None = None, dong_id: str | None = None):
        t = community.target(self.kind, sgg_id or dong_id)
        if t is None:
            return not_found("투표 대상을 찾을 수 없습니다.")
        body = request.data if isinstance(request.data, dict) else {}
        choice = body.get("choice", ...)  # 키가 없으면 Ellipsis → CHOICES 에 없어 400
        try:
            month = _month(body.get("month"))
            preset = _preset(t.scope, body.get("preset"))
            # 리스트 · dict 는 해시가 안 돼 in 검사 전에 거른다
            if not (choice is None or isinstance(choice, str)) or choice not in CHOICES:
                raise BadPoll("choice 는 'yes', 'no', null 중 하나입니다.")
        except BadPoll as ex:
            return bad_request(str(ex))
        user_id = request.user.user_id
        save_vote(user_id, t, month, preset, CHOICES[choice])
        return Response(result(t, month, preset, user_id))
```

- [ ] **Step 4: 경로 바꾸기 · `empty_poll` 삭제**

`webapi/urls.py`: import 를 `from webapi import me, poll, uploads, views` 로. 하위 클래스 추가:

```python
class RegionPollView(poll.PollView):
    kind = "regions"


class DongPollView(poll.PollView):
    kind = "dongs"


class RegionPollVoteView(poll.PollVoteView):
    kind = "regions"


class DongPollVoteView(poll.PollVoteView):
    kind = "dongs"
```

경로:

```python
    path("regions/<str:sgg_id>/poll/vote", RegionPollVoteView.as_view(), name="web-region-vote"),
    path("regions/<str:sgg_id>/poll", RegionPollView.as_view(), name="web-region-poll"),
```

```python
    path("dongs/<str:dong_id>/poll/vote", DongPollVoteView.as_view(), name="web-dong-vote"),
    path("dongs/<str:dong_id>/poll", DongPollView.as_view(), name="web-dong-poll"),
```

`webapi/views.py`: `# ─── 체감 투표 (데이터 없음) ───` 섹션 제목과 `empty_poll` 함수를 지운다. 모듈 docstring 의 `아직 데이터가 없는 것(랜드마크 · 접근 안내 · 체감 투표)은 빈 값이다.` → `아직 데이터가 없는 것(랜드마크 · 접근 안내)은 빈 값이다. 체감 투표는 webapi/poll.py.`

확인: `grep -rn empty_poll webapi tests` 결과 없음.

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_poll.py tests/test_webapi.py -q`
Expected: 모두 PASS (`test_notes_poll_view_and_auth` 의 dong poll GET 은 `total == 0`)

- [ ] **Step 6: 커밋**

```bash
uv run ruff check webapi tests && uv run ruff format webapi tests
git add webapi/poll.py webapi/urls.py webapi/views.py tests/test_poll.py
git commit -m "feat: 체감 투표 집계 · 투표 API (poll_vote)"
```

---

### Task 10: 운영자 검수 명령 `manage.py photos`

**Files:**
- Create: `webapi/management/__init__.py` (빈 파일), `webapi/management/commands/__init__.py` (빈 파일)
- Create: `webapi/management/commands/photos.py`
- Test: `tests/test_photos_command.py`

**Interfaces:**
- Consumes: `photo_quarantine.restore/hide/purge/is_quarantined/RETENTION_DAYS` (Task 6), `storage.StorageError` (Task 1), `tests.test_photo_quarantine.user_photo` (Task 6)
- Produces: `manage.py photos list` · `photos restore <id…>` · `photos hide <id…>` · `photos purge [--days 30]`

운영자는 서버 셸이나 운영 `.env` 를 가진 팀 개발자다 (스펙 5절). 명령은 얇게 두고 일은 `photo_quarantine` 이 한다 — 나중에 관리자 화면이 같은 함수를 부른다.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_photos_command.py`

```python
"""운영자 검수 명령 manage.py photos (스펙 5절)."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.photo_tools import r2  # noqa: F401
from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import OTHER_ID, me, run  # noqa: F401
from tests.test_photo_quarantine import user_photo
from webapi import photo_quarantine

pytestmark = pytest.mark.django_db


def photos_cmd(*args) -> str:
    out = StringIO()
    call_command("photos", *args, stdout=out, stderr=out)
    return out.getvalue()


def col(pid, name):
    return run(f"SELECT {name} FROM photo WHERE photo_id = %s", [pid])[0][0]


def test_list(me, r2):  # noqa: F811
    moved, _ = user_photo(r2, "pending")
    photo_quarantine.quarantine(moved)
    open_, _ = user_photo(r2, "pending")  # 격리 실패로 남은 사진
    public, _ = user_photo(r2, "approved")
    run(
        "INSERT INTO report (target_type, target_id, user_id, reason)"
        " VALUES ('photo', %s, %s, '광고'), ('photo', %s, %s, '무관한 사진')",
        [moved, me, moved, OTHER_ID],
    )
    out = photos_cmd("list")
    lines = {line.split("\t")[0]: line for line in out.splitlines() if "\t" in line}
    assert set(lines) == {str(moved), str(open_)}  # 공개 사진은 안 나온다
    assert "고성군" in lines[str(moved)] and "신고 2" in lines[str(moved)]
    assert "uploads/quarantine/" in lines[str(moved)] and "URL 열림" not in lines[str(moved)]
    assert "URL 열림" in lines[str(open_)]
    assert "광고 / 무관한 사진" in out
    assert str(public) not in lines


def test_restore_and_hide(me, r2):  # noqa: F811
    a, _ = user_photo(r2, "pending")
    b, _ = user_photo(r2, "approved")
    photo_quarantine.quarantine(a)
    assert "1장 복구" in photos_cmd("restore", str(a), "999999")
    assert col(a, "status") == "approved" and not photo_quarantine.is_quarantined(col(a, "url"))
    out = photos_cmd("hide", str(a), str(b))
    assert "2장 숨김" in out
    for pid in (a, b):
        assert col(pid, "status") == "hidden" and photo_quarantine.is_quarantined(col(pid, "url"))


def test_hide_r2_failure_reported_and_retryable(me, r2):  # noqa: F811
    pid, key = user_photo(r2, "approved")
    r2.fail_copy = True
    out = photos_cmd("hide", str(pid))
    assert "R2 실패" in out and col(pid, "status") == "hidden" and key in r2.objects
    r2.fail_copy = False
    assert "1장 숨김" in photos_cmd("hide", str(pid))  # 다시 하면 격리된다
    assert photo_quarantine.is_quarantined(col(pid, "url"))


def test_purge(me, r2):  # noqa: F811
    pid, _ = user_photo(r2, "approved")
    photos_cmd("hide", str(pid))
    assert "0장 영구 삭제" in photos_cmd("purge")
    run("UPDATE photo SET hidden_at = now() - interval '3 days'")
    assert "0장 영구 삭제" in photos_cmd("purge")
    assert "1장 영구 삭제" in photos_cmd("purge", "--days", "2")
    assert run("SELECT count(*) FROM photo") == [(0,)]


def test_needs_ids():
    with pytest.raises(CommandError):
        photos_cmd("hide")
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_photos_command.py -q`
Expected: FAIL — `CommandError: Unknown command: 'photos'`

- [ ] **Step 3: 명령 작성** — `webapi/management/commands/photos.py` (빈 `__init__.py` 두 개도 만든다)

```python
"""신고로 가려진 사진 검수 (스펙 5절). 일은 webapi/photo_quarantine.py 가 한다.

  manage.py photos list               가려진(pending) 사진 · 신고 사유 · 격리 위치 URL
  manage.py photos restore <id…>      다시 공개 (파일을 공개 위치로, 신고 삭제)
  manage.py photos hide <id…>         숨김 확정 + 격리 (공개 중인 사진도 바로 내릴 수 있다)
  manage.py photos purge [--days 30]  숨김 뒤 30일 지난 사용자 사진을 영구 삭제

R2 가 실패한 id 는 다시 실행하면 된다.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from webapi import photo_quarantine, storage

ACTIONS = {"restore": (photo_quarantine.restore, "복구"), "hide": (photo_quarantine.hide, "숨김")}


class Command(BaseCommand):
    help = "신고로 가려진 사진 검수: list · restore <id…> · hide <id…> · purge [--days 30]"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["list", "restore", "hide", "purge"])
        parser.add_argument("ids", nargs="*", type=int)
        parser.add_argument("--days", type=int, default=photo_quarantine.RETENTION_DAYS)

    def handle(self, *args, action, ids, days, **options):
        if action == "list":
            return self._list()
        if action == "purge":
            n, failed = photo_quarantine.purge(days)
            self.stdout.write(f"{n}장 영구 삭제")
            if failed:
                self.stdout.write(f"R2 실패로 남김 (다음에 다시): {_ids(failed)}")
            return
        if not ids:
            raise CommandError("사진 id 를 하나 이상 주세요.")
        fn, verb = ACTIONS[action]
        done, skipped, failed = [], [], []
        for pid in ids:
            try:
                (done if fn(pid) else skipped).append(pid)
            except storage.StorageError as ex:
                failed.append(pid)
                self.stderr.write(f"{pid}: R2 실패 — {ex}")
        self.stdout.write(f"{len(done)}장 {verb}: {_ids(done)}")
        if skipped:
            self.stdout.write(f"건너뜀 (없거나 상태가 맞지 않음): {_ids(skipped)}")
        if failed:
            self.stdout.write(f"R2 실패 (다시 실행하세요): {_ids(failed)}")

    def _list(self):
        with connection.cursor() as cur:
            cur.execute(
                """SELECT p.photo_id, coalesce(r.sigungu_name, d.dong_name, '?'),
                          p.tip_id IS NOT NULL, p.url, p.source, u.nickname,
                          count(rp.report_id),
                          coalesce(string_agg(rp.reason, ' / ' ORDER BY rp.created_at), '')
                     FROM photo p
                     LEFT JOIN region r ON p.scope = 'region' AND r.region_id = p.unit_id
                     LEFT JOIN dong d ON p.scope = 'dong' AND d.dong_id = p.unit_id
                     LEFT JOIN app_user u ON u.user_id = p.user_id
                     LEFT JOIN report rp ON rp.target_type = 'photo'
                                        AND rp.target_id = p.photo_id
                    WHERE p.status = 'pending'
                    GROUP BY p.photo_id, r.sigungu_name, d.dong_name, u.nickname
                    ORDER BY p.photo_id"""
            )
            rows = cur.fetchall()
        if not rows:
            self.stdout.write("가려진 사진이 없습니다.")
        for pid, name, is_note, url, source, nickname, n, reasons in rows:
            where = f"{name} (노트)" if is_note else name
            flag = ""
            if source == "user" and not photo_quarantine.is_quarantined(url):
                flag = "\t※ URL 열림 — hide 로 다시 격리"
            self.stdout.write(f"{pid}\t{where}\t신고 {n}\t{nickname or '-'}\t{url}{flag}")
            self.stdout.write(f"    사유: {reasons or '-'}")


def _ids(ids) -> str:
    return ", ".join(map(str, sorted(ids))) or "-"
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_photos_command.py -q`
Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff check webapi tests && uv run ruff format webapi tests
git add webapi/management tests/test_photos_command.py
git commit -m "feat: 신고된 사진 검수 명령 manage.py photos (list · restore · hide · purge)"
```

---

### Task 11: 문서 · 전체 확인

**Files:**
- Modify: `docs/개발-가이드.md` (60행 근처, 95~112행 표와 목록)

- [ ] **Step 1: 개발 가이드 고치기**

60행 `- 사진 · 팁 · 투표 · 지원사업 링크는 데이터가 없어 빈 값으로 내려줍니다.` → `- 지원사업 링크는 데이터가 없어 빈 값으로 내려줍니다.`

95행 표에 행을 더한다 (`DELETE /api/me/tips/{id}` 행 아래):

```markdown
| `POST /api/regions/{id}/notes` (multipart) | 사진 첨부 노트. 필드 `body` · `visitMonth?` · `photos`(0~3장, 장당 10MB, JPEG · PNG · WebP). 응답 `Note.photos` |
| `POST /api/regions/{id}/photos` · `/api/dongs/{id}/photos` | 이달의 사진. multipart `month`(`2025-MM` · `annual`) · `photos`(1~3장). 201 `{items: MyPhoto[]}` · 400 `INVALID_PHOTO` · 429 `PHOTO_RATE_LIMIT`(1시간 30장) · 502 `UPLOAD_FAILED` |
| `POST /api/photos/{id}/report` | 사진 신고 `{reason}`(1~30자). 204. 신고 `PHOTO_REPORT_THRESHOLD`(기본 3)건이면 가려지고 파일이 격리됨 |
| `DELETE /api/me/photos/{id}` | 내 이달의 사진 삭제. 204. 공개 사진은 행 · 파일 삭제, 신고로 가려진 사진은 증거로 30일 보관 · 404 · 502 `DELETE_FAILED` |
| `GET /api/regions/{id}/poll?month=&preset=` · `/api/dongs/{id}/poll?month=` | 체감 투표 집계 · `myVote`(로그인 시). region 은 `preset` 필수 |
| `POST …/poll/vote` | `{month, preset?, choice: yes\|no\|null}`. 200 + 갱신된 투표. `null` 은 취소 |
```

목록에 추가:

```markdown
- 사진은 백엔드가 받아 WebP(가로 1600px, EXIF 제거)로 바꿔 R2 `uploads/{tip|photo}/` 에 올립니다 (`webapi/photo_upload.py` · `webapi/storage.py`). 올리면 바로 공개(`approved`)되고 신고가 쌓이면 `pending` 으로 가려집니다.
- R2 버킷은 통째로 공개라 가린 사진은 파일을 `uploads/quarantine/` 으로 옮겨 원래 URL 을 막습니다 (`webapi/photo_quarantine.py`). 관광공사 사진(`photos/`)은 버킷 주인 파일이라 옮기지 않습니다.
- 가려진 사진 검수(서버 셸 또는 운영 `.env`): `uv run python manage.py photos list` · `photos restore <id…>` · `photos hide <id…>` · `photos purge`(숨김 30일 지난 사진 영구 삭제, 매일 돌림). R2 실패한 id 는 다시 실행하면 됩니다.
- 노트를 지우면 공개 첨부 사진은 파일까지 지우고, 신고로 가려진 첨부 사진은 노트에서 떼어 30일 보관합니다.
- 노트 사진(`photo.tip_id`)은 지역 · 동네 갤러리, 투표 사진, `/api/me/photos` 에 섞지 않습니다.
```

`summary.voteCount` 설명 줄의 `투표한 대상(지역 · 동네 × 월) 수` → `투표한 질문 수 (poll_vote 행 수)`.

마지막 줄 `- 아직 없는 것: ...` → `- 아직 없는 것: 알림 설정 \`/api/me/settings\`(501), 도움돼요 쓰기.`

- [ ] **Step 2: 전체 테스트 · 린트**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: 모두 PASS, 린트 오류 없음

- [ ] **Step 3: 커밋**

```bash
git add docs/개발-가이드.md
git commit -m "docs: 사진 업로드 · 신고 · 체감 투표 API 를 개발 가이드에 반영"
```

- [ ] **Step 4: 배포 전 확인 (사람이 할 일 — 자동으로 하지 않는다)**

1. 버킷 소유자(데이터 담당)에게 `uploads/` 경로 사용 동의를 받았는지 확인.
2. Supabase 에 `db/migrations/20261120_photo-upload-poll.sql` 적용은 사용자 확인 후 진행 (`metric_vote` 를 지우므로 되돌릴 수 없음. 지금 행이 없는지 먼저 `SELECT count(*) FROM metric_vote`).
3. 앞단 프록시가 있으면 요청 본문 한도 32MB 이상.
4. `manage.py photos purge` 를 매일 돌리도록 서버 cron(또는 배포 환경의 스케줄러)에 등록.
