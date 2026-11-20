# 사진 업로드(R2) · 사진 신고 · 체감 투표 설계 — 백엔드

- 작성일: 2026-11-20
- 상태: 승인됨 (브레인스토밍 결과, 2026-11-20)
- 배경: 웹(W-03·W-04·W-08)은 노트 사진 첨부 · 이달의 사진 올리기 · 체감 투표 UI 가 이미 있는데, 백엔드는 업로드 경로가 없고 투표는 `empty_poll` 더미, `POST .../poll/vote` 는 404 다.
- 고치는 문서: workation-web W-03·W-04 스펙의 "이달의 사진 = 운영자 검수 후 게시" 규칙. **바로 게시 + 신고 누적 시 가림 → 운영자 검수**로 바꾼다.
- 범위 밖: W-06 지자체 리포트(폐기), 웹 쪽 연결 작업(별도 계획), 팁·답글 신고.
- **2026-11-20 변경 — 사용자 사진은 별도 표 `user_photo`**: `photo` 는 데이터 담당이 적재한 관광공사 · 팀 사진 표라 손대지 않는다. 노트 사진 · 이달의 사진은 새 표 `user_photo` 에 둔다. 아래 본문에서 사용자 사진을 말하는 `photo` · `photo_id` 는 모두 `user_photo` · `user_photo_id` 로 읽는다. 자세한 내용은 3절과 9절.
- 업로드 방식: **백엔드 경유**. 웹은 사진 파일을 `multipart/form-data` 로 백엔드에 보내고, 백엔드가 검증 · 변환해 R2 에 올린다. presigned URL 로 브라우저가 R2 에 직접 올리는 방식은 버킷 CORS 설정이 필요한데 R2 버킷이 데이터 담당 소유라 쓰지 않는다. 업로드 양이 적어(요청당 최대 3장) 서버 부담도 작다. 나중에 양이 늘면 업로드 받는 부분만 presigned 로 바꿀 수 있다.

## 1. 목표 · 완료 기준

- 로그인 사용자가 **노트 사진**(노트 쓰기 요청에 함께) 또는 **이달의 사진**을 백엔드로 올린다. 백엔드는 WebP 로 다시 인코딩(위치정보 제거)해 R2 에 저장하고, 둘 다 등록 즉시 공개된다.
- 사진은 신고 3건이 쌓이면 가려지고(`pending`) 파일이 **격리**된다(원래 URL 이 바로 막힘). 운영자가 `manage.py photos` 명령으로 복구하거나 숨김을 확정하고, 숨김 30일 뒤 영구 삭제한다.
- 사용자가 자기 이달의 사진을 지울 수 있다. 공개 중이면 행 · 파일을 바로 지우고, 신고로 가려진 사진이면 증거로 격리해 둔다. 노트를 지울 때 첨부 사진도 같은 규칙.
- `GET .../poll` 이 실제 집계와 내 투표(`myVote`)를 돌려주고, `POST .../poll/vote` 로 투표 · 변경 · 취소한다.
- 단위 테스트(pytest)가 실제 R2 키 없이 통과한다 (R2 클라이언트는 테스트에서 가짜로 바꾼다).

## 2. 설정 · 의존성

`.env` 에 이미 있는 값을 settings 로 읽는다 (`.env.example` 에도 키 이름 추가).

| 설정 | 쓰임 |
|---|---|
| `R2_ACCOUNT_ID` | 엔드포인트 `https://{account}.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` · `R2_SECRET_ACCESS_KEY` | 접근 키. 서버에만 둔다 |
| `R2_BUCKET` | 버킷 이름 |
| `R2_PUBLIC_URL` | 공개 URL 앞부분. `photo.url = R2_PUBLIC_URL + "/" + key` |
| `PHOTO_REPORT_THRESHOLD` | 가림 기준 신고 수 (기본 3) |

의존성 추가:

- `boto3` — S3 호환 API (`signature_version="s3v4"`, `region_name="auto"`). R2 접근은 `webapi/storage.py` 한 곳에 모은다: `put(key, data: bytes, content_type)`, `copy(src, dst)`, `delete(key)`. 다른 코드는 boto3 를 직접 부르지 않는다. `put` 은 `Cache-Control: public, max-age=3600` 을 붙인다 — 가린 사진이 브라우저 · CDN 캐시에 오래 남지 않게 1시간으로 둔다 (`immutable` 1년은 쓰지 않음).
- `Pillow` — 사진 검증 · 변환 (4-0).

R2 쓰기 키 권한은 2026-11-20 에 확인했다 (PUT · HEAD · DELETE 성공). 버킷 CORS 는 필요 없다 (브라우저가 R2 에 직접 요청하지 않음). 사용자 사진은 `uploads/` 아래에만 쓴다 — 기존 `photos/`(관광공사 사진)와 섞지 않는다. **버킷 소유자(데이터 담당)에게 `uploads/` 경로 사용 동의를 받는다.**

요청 크기: 사진 3장 × 10MB + 본문이므로 앞단 프록시(있다면)의 요청 본문 한도를 32MB 이상으로 둔다. Django 는 2.5MB 넘는 파일을 임시 파일로 받으므로 따로 설정할 것은 없다 (`DATA_UPLOAD_MAX_MEMORY_SIZE` 는 파일 아닌 필드에만 적용).

## 3. DB 변경 (`db/migrations/20261120_photo-upload-poll.sql`, `db/schema.sql` 동시 반영)

```sql
-- 사용자 사진 (노트 첨부 · 이달의 사진). photo(관광공사 · 팀)는 바꾸지 않는다
CREATE TABLE IF NOT EXISTS user_photo (
  user_photo_id   SERIAL       PRIMARY KEY,
  user_id         UUID         NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  scope           VARCHAR(6)   NOT NULL,                 -- region, dong
  unit_id         SMALLINT     NOT NULL,
  month           SMALLINT,                              -- 1~12, NULL = 연간
  url             TEXT         NOT NULL,                 -- R2 공개 URL
  tip_id          INTEGER      REFERENCES tip(tip_id) ON DELETE CASCADE,  -- 노트 사진이면
  position        SMALLINT,                              -- 노트 안 순서 0~2
  status          VARCHAR(8)   NOT NULL DEFAULT 'approved',
  hidden_at       TIMESTAMPTZ,                           -- hidden 이 된 시각 (purge 기준)
  deleted_by_user BOOLEAN      NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
  CHECK (scope IN ('region', 'dong')), CHECK (month BETWEEN 1 AND 12),
  CHECK (status IN ('approved', 'pending', 'hidden'))
);
-- 인덱스 3개(이달의 사진 · 노트 · 내 사진) · RLS 는 마이그레이션 파일 참고

-- 신고 대상에 사용자 사진 추가 (target_type VARCHAR(6) 이라 'upload')
ALTER TABLE report DROP CONSTRAINT IF EXISTS report_target_type_check;
ALTER TABLE report ADD CONSTRAINT report_target_type_check
  CHECK (target_type IN ('tip', 'reply', 'photo', 'upload'));

-- 체감 투표: metric_vote(지표별) 를 버리고 질문 하나에 한 표
DROP TABLE IF EXISTS metric_vote;
CREATE TABLE IF NOT EXISTS poll_vote (
  user_id     UUID        NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  scope       VARCHAR(6)  NOT NULL,
  unit_id     SMALLINT    NOT NULL,
  month       SMALLINT    NOT NULL,          -- 0 = 연간(annual), 1~12
  preset      VARCHAR(8)  NOT NULL DEFAULT '', -- region: quiet·lively·custom, dong: ''
  choice      BOOLEAN     NOT NULL,          -- true = yes
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, scope, unit_id, month, preset),
  CHECK (scope IN ('region', 'dong')),
  CHECK (month BETWEEN 0 AND 12),
  CHECK (preset IN ('', 'quiet', 'lively', 'custom'))
);
CREATE INDEX IF NOT EXISTS poll_vote_target_idx ON poll_vote (scope, unit_id, month, preset);
ALTER TABLE poll_vote ENABLE ROW LEVEL SECURITY;
```

`user_photo.status` 의미:

| 값 | 의미 |
|---|---|
| `approved` | 공개 (올리면 바로 이 상태) |
| `pending` | 신고 누적으로 가려짐 · 운영자 검수 대기 (파일 격리) |
| `hidden` | 운영자 숨김 확정, 또는 가려진 사진을 올린 사람이 지움(`deleted_by_user`). 파일 격리, `hidden_at` 30일 뒤 영구 삭제 |

`metric_vote` 를 읽던 곳(`community.summary` 의 voteCount)은 `poll_vote` 로 바꾼다: 투표한 (scope, unit_id, month, preset) 수 = 행 수.

**사용자 사진은 지역 · 동네 갤러리에 섞지 않는다.** 지역 상세 사진 · 대표 사진(티어 · 인기 지역 · 히트맵)은 지금처럼 `photo` 만 쓰므로 `regions/photos.py` 는 바꾸지 않는다. 이달의 사진은 체감 투표 영역(웹 `PollSection`)에만, 노트 사진은 노트 안에만 나온다. `GET /api/me/photos` · `summary.photoCount` 는 `user_photo` 의 이달의 사진(`tip_id IS NULL`, `hidden` 제외)만 센다.

## 4. API

공통: 로그인 필요한 곳은 기존 Supabase JWT 인증. 에러 모양은 기존 `error(status, code, message)`.

### 4-0. 사진 받기 · 변환 · 저장 (4-2 · 4-3 공통, `webapi/photo_upload.py`)

1. **받기**: multipart 필드 `photos` (같은 이름으로 여러 개). 0~3장(노트) 또는 1~3장(이달의 사진). 장당 10MB 이하. 넘으면 400 `INVALID_PHOTO`.
2. **검증**: Pillow 로 연다. 열리지 않거나 형식이 JPEG · PNG · WebP 가 아니면 400 `INVALID_PHOTO` (몇 번째 사진인지 메시지에). 파일 이름 · 요청의 Content-Type 은 믿지 않고 실제 내용으로 판단한다. 픽셀 수 상한 40MP (압축 폭탄 방지, `Image.MAX_IMAGE_PIXELS`).
3. **변환**: EXIF 회전을 적용(`ImageOps.exif_transpose`)한 뒤 가로 1600px 이하로 줄이고(작으면 그대로) WebP(quality 82)로 다시 인코딩한다. 메타데이터는 옮기지 않으므로 **촬영 위치(GPS) 등 EXIF 가 모두 사라진다**. 기존 관광공사 사진(1600px WebP)과 같은 규격.
4. **저장**: 모든 사진을 검증 · 변환한 뒤에 R2 에 올린다. key = `uploads/{tip|photo}/{uuid4}.webp` (사용자 id 를 공개 URL 에 넣지 않는다), Content-Type `image/webp`.
5. **DB**: R2 업로드가 모두 끝나면 DB 를 한 트랜잭션으로 쓴다. DB 가 실패하면 이번에 올린 key 를 지운다 (실패해도 로그만, 응답은 원래 에러). R2 업로드 중 실패하면 그때까지 올린 key 를 지우고 502 `UPLOAD_FAILED`.

HEIC 은 받지 않는다 (Pillow 기본으로 못 열고, 웹 accept 도 jpeg · png · webp). 변환은 요청 안에서 동기로 한다 — 3장 기준 1초 안팎.

### 4-1. (없음 — presigned 방식은 쓰지 않는다)

### 4-2. 노트 사진: `POST /api/{regions|dongs}/{id}/notes` 확장

- 사진이 없으면 지금처럼 JSON `{ body, visitMonth? }` 로 보낸다 (기존 클라이언트 그대로 동작).
- 사진이 있으면 `multipart/form-data`: 필드 `body`, `visitMonth`(선택), `photos`(파일 0~3). body · visitMonth 검증 규칙은 JSON 과 같다.
- 순서: 본문 검증 → 사진 검증 · 변환(4-0) → 연달아 쓰기 한도 확인 → R2 업로드 → 팁 · 사진 행 한 트랜잭션. 본문이 틀리면 사진을 열지도 않는다. 한도(429 `TIP_RATE_LIMIT`)에 걸리면 R2 에 올리지 않는다.
- 사진 행: `photo(scope, unit_id, month=visit_month, url, source='user', user_id, tip_id, position, status='approved')`. 동 노트면 scope='dong', 지역 노트면 scope='region'.
- `Note` 응답(쓰기 · 노트 목록 · 내 팁 목록 공통)에 `photos: { id, url }[]` 를 더한다. 사진 없으면 `[]`. `approved` 가 아닌 사진은 빼고 `position` 순.

### 4-3. 이달의 사진: `POST /api/{regions|dongs}/{id}/photos` (로그인)

요청 `multipart/form-data`: 필드 `month` (`"2025-10"` 또는 `"annual"`), `photos`(파일 1~3).

응답 201 `{ "items": MyPhoto[] }` (기존 `GET /api/me/photos` 항목 모양, status `approved`)

- 사진 처리는 4-0.
- `annual` 이면 `photo.month = NULL`.
- 404 대상 없음 · 400 `INVALID_PHOTO`(month 형식 오류 포함) · 429 `PHOTO_RATE_LIMIT` (사용자당 1시간 30장, 최근 1시간 내 내 사진 행 수로 센다) · 502 `UPLOAD_FAILED`.

### 4-4. 사진 신고: `POST /api/photos/{photoId}/report` (로그인)

요청 `{ "reason": string }` (1~30자, 기존 `report.reason` 길이)

응답 204. 같은 사람이 같은 사진을 다시 신고해도 204 (멱등, `report` UNIQUE 로 무시).

- 대상이 없거나 이미 `approved` 가 아니면 404.
- 넣은 뒤 그 사진의 신고 수가 `PHOTO_REPORT_THRESHOLD`(settings, 기본 3) 이상이면 `approved → pending`. 신고 넣기와 상태 바꾸기는 한 트랜잭션. 가린 직후 파일을 격리한다(4-7). 격리가 실패해도 응답은 204 (로그를 남기고, `photos list` 가 "URL 열림" 으로 보여 주며 `photos hide` 가 다시 격리한다).
- 자기 사진도 신고할 수 있다 (막을 이유 없음).

사진 `id` 는 노트 `photos[].id`, 투표 응답 `photos[].id` 로 웹에 내려간다.

### 4-5. 체감 투표

`GET /api/regions/{sggId}/poll?month=&preset=` · `GET /api/dongs/{dongId}/poll?month=` (로그인 선택)

```json
{
  "question": "10월 고성군, 실제로 조용했나요?",
  "yes": 12, "no": 3, "total": 15,
  "photos": [{ "id": "41", "url": "https://…", "author": "닉네임" }],
  "myVote": "yes"
}
```

- `question`: 웹 mock 규칙과 같게 서버가 만든다. `{월 라벨} {이름}, {꼬리}`. 월 라벨은 `10월` 또는 `연간`. 이름은 시군구명 또는 동명. 꼬리는 preset quiet → `실제로 조용했나요?`, lively → `실제로 활기 있었나요?`, custom · 동 → `어땠나요?`.
- `yes`·`no`·`total`: `poll_vote` 에서 (scope, unit_id, month, preset) 집계. 동은 preset `''`.
- `photos`: 그 대상 · 그 달의 이달의 사진(`approved`, `tip_id IS NULL`, annual 이면 월 무관) 최신순 최대 6장. `author` 는 업로더 닉네임, 팀·관광공사 사진이면 `credit`.
- `myVote`: 로그인했고 투표했으면 `"yes"|"no"`, 아니면 `null`.
- region 의 preset 이 없거나 틀리면 400 (기존 preset 파싱 규칙).

`POST /api/{regions|dongs}/{id}/poll/vote` (로그인)

요청 `{ "month": "2025-10" | "annual", "preset"?: "quiet"|"lively"|"custom", "choice": "yes" | "no" | null }`

응답 200 = 위 GET 과 같은 모양 (갱신된 집계 + `myVote`).

- `choice` 가 값이면 upsert (`ON CONFLICT … DO UPDATE SET choice, created_at`), `null` 이면 삭제 (없어도 200).
- region 은 preset 필수, dong 은 preset 을 무시한다.
- 400 형식 오류 · 404 대상 없음.

### 4-6. 사진 삭제 (올린 사람)

`DELETE /api/me/photos/{photoId}` (로그인) — 내 이달의 사진. 응답 204.

| 사진 상태 | 처리 |
|---|---|
| `approved` | R2 파일을 먼저 지우고 행을 지운다. 파일 삭제가 실패하면 502 `DELETE_FAILED` 이고 행은 그대로 (다시 시도하면 된다 — S3 삭제는 없는 key 도 성공) |
| `pending` | 증거라 지우지 않는다: `hidden` · `deleted_by_user = true` · `hidden_at = now()`. 화면(내 사진 포함)에서 바로 사라지고 30일 뒤 영구 삭제. 아직 격리 전이면 격리한다 |
| `hidden` · 남의 사진 · 노트 사진 · 없음 | 404 |

노트 삭제(`DELETE /api/me/tips/{id}`)도 같은 규칙으로 첨부 사진을 처리한다.

- `approved` 첨부 사진은 R2 파일을 먼저 지운 뒤(실패 → 502 `DELETE_FAILED`, 노트 그대로) 노트와 함께 행을 지운다.
- `pending` · `hidden` 첨부 사진은 노트에서 떼어(`tip_id = NULL`) `hidden` · `deleted_by_user` 로 남긴다. 노트 자체는 지운다. 떼어 낸 사진은 `hidden` 이라 어디에도 나오지 않고, `restore` 대상(`pending`)도 아니다.

### 4-7. 격리 (공개 버킷에서 가리기)

R2 버킷은 r2.dev 로 통째로 공개라, DB 상태만 바꾸면 URL 을 아는 사람은 계속 볼 수 있다. 그래서 가린 사용자 사진은 파일을 옮긴다.

- **격리**: 파일을 `uploads/quarantine/{uuid4}.webp` 로 복사 → `photo.url` 을 새 URL 로 → 원래 key 삭제. 원래 URL 은 바로 404 가 되고 새 key 는 밖에 알려지지 않는다 (r2.dev 는 목록 조회 불가, uuid4 는 추측 불가). 원래 key 삭제가 실패하면 로그만 (url 은 이미 격리 위치).
- **대상**: `source = 'user'` 이고 url 이 `uploads/` 아래인 사진만. 관광공사 · 팀 사진(`photos/`)은 버킷 주인 파일이라 옮기지 않고 상태만 바꾼다.
- **언제**: 신고로 `pending` 이 될 때(4-4), 운영자 `hide`, 사용자가 가려진 사진을 지울 때(4-6). 이미 격리된 사진은 건너뛴다.
- **복구(`restore`)**: 격리 파일을 새 공개 key `uploads/{tip|photo}/{uuid4}.webp` 로 복사 → url 갱신 → 격리 파일 삭제. 원래 key 를 다시 쓰지 않는 것은 캐시에 남은 404 를 피하려는 것.
- **영구 삭제(`purge`)**: `hidden` 이고 `hidden_at` 이 30일 지난 사진. 사용자 사진은 R2 파일을 지운 뒤 행을 지우고(파일 삭제 실패 시 그 행은 남겨 다음 purge 때 다시), 관광공사 · 팀 사진은 행만 둔다(버킷 주인 파일 · 행 유지).

## 5. 운영자 검수: `manage.py photos`

운영자는 서버 셸이나 운영 `.env`(Supabase · R2 키)를 가진 팀 개발자다. 관리자 화면 · 역할은 이번 범위 밖이다. 나중에 관리자 화면을 만들면 같은 함수(`webapi/photo_quarantine.py`)를 부른다.

| 명령 | 동작 |
|---|---|
| `photos list` | `pending` 사진 목록: id · 대상(지역/동 이름) · 노트 사진 여부 · url(격리 위치 — 브라우저로 열어 확인) · 신고 수 · 신고 사유들 · 올린 사람 닉네임 · 격리 안 됐으면 "URL 열림" 표시 |
| `photos restore <id…>` | `pending → approved`, 파일을 공개 위치로 되돌리고 그 사진의 `report` 행 삭제 (같은 신고로 다시 가려지지 않게) |
| `photos hide <id…>` | `pending`·`approved → hidden`, `hidden_at = now()`, 격리 (신고 없이도 바로 내릴 수 있다) |
| `photos purge [--days 30]` | 4-7 영구 삭제. 매일 돌리거나 운영자가 손으로 돌린다 |

명령은 id 마다 결과(성공 · 건너뜀 · R2 실패)를 출력한다.

## 6. 코드 배치

| 파일 | 내용 |
|---|---|
| `webapi/storage.py` (새) | R2 클라이언트, `put`, `copy`, `delete`. 테스트는 이 모듈을 가짜로 바꾼다 |
| `webapi/photo_upload.py` (새) | 4-0: 받은 파일 검증 · WebP 변환 · R2 업로드(실패 시 정리) → `[(key, url)]` |
| `webapi/photo_quarantine.py` (새) | 4-6 · 4-7: 격리 · 복구 · 숨김 · 영구 삭제 · 사용자 삭제. 명령과 뷰가 같이 쓴다 |
| `webapi/uploads.py` (새) | 이달의 사진 등록 뷰 · 신고 뷰 · 내 사진 삭제 뷰 |
| `webapi/poll.py` (새) | 질문 문구 · 집계 · 투표 upsert/삭제 · GET/POST 뷰 (`empty_poll` 삭제) |
| `webapi/community.py` | 노트 쓰기 multipart 파싱, `create_tip` 사진 넣기, `_note` 에 photos, `delete_tip` 첨부 사진 처리, `summary` voteCount · photoCount, `my_photos` 필터 |
| `regions/photos.py` | 모든 조회에 `tip_id IS NULL` |
| `webapi/management/commands/photos.py` (새) | 5절 명령 |
| `webapi/urls.py` | 새 경로 3개(이달의 사진 · 신고 · 내 사진 삭제)와 poll/vote, poll GET 을 새 뷰로 |
| `config/settings.py` · `.env.example` | R2 설정, `PHOTO_REPORT_THRESHOLD` |
| `pyproject.toml` | `boto3`, `Pillow` |

## 7. 테스트

- `storage` 는 메모리 가짜(fake)로 바꿔 끼운다 (`put` · `copy` · `delete` 를 기록, 실패 주입 가능). 실제 R2 키 없이 돈다.
- 사진 처리(4-0): 테스트 안에서 Pillow 로 만든 JPEG · PNG · WebP 는 통과하고 결과가 WebP · 가로 1600 이하. GPS EXIF 를 넣은 JPEG 의 결과에 EXIF 가 없음. 이미지 아닌 파일 · 10MB 초과 · 4장 → 400 `INVALID_PHOTO`.
- 노트 사진: JSON(사진 없음)은 지금처럼 동작. multipart 정상이면 Note.photos 순서대로, 노트 목록 · 내 팁 목록에도 보임. 사진이 틀리면 팁도 R2 도 안 생김. 쓰기 한도에 걸리면 R2 에 안 올림. DB 실패를 주입하면 올린 key 가 지워짐. R2 실패 주입 → 502 이고 팁 없음.
- 이달의 사진: annual → month NULL, 201 응답 모양, `GET /api/me/photos` 에 보임, 노트 사진은 안 보임, 시간당 한도 429.
- 신고: 2건까지 approved, 3건째 pending · 갤러리와 투표 photos 에서 빠짐 · 파일이 `uploads/quarantine/` 으로 옮겨지고 원래 key 삭제, 같은 사람 중복 신고는 수에 안 셈, 없는/가려진 사진 404. 격리 R2 실패여도 204 · pending.
- 격리: 관광공사 사진은 옮기지 않음, 이미 격리된 사진은 다시 옮기지 않음, 복사 실패면 url 그대로.
- 사용자 삭제: approved → 행 · 파일 삭제, R2 실패 → 502 · 행 남음, pending → hidden · deleted_by_user · 파일 격리 유지 · 내 사진에서 사라짐, 남의 사진 · 노트 사진 · hidden 404. 노트 삭제: approved 첨부 파일 삭제 · 행 삭제, pending 첨부는 떼어 hidden 으로 남음, R2 실패 → 502 · 노트 남음.
- 명령: restore 후 approved · 공개 위치 url · 격리 파일 삭제 · 신고 0, hide 후 hidden · hidden_at · 격리, purge 는 30일 지난 hidden 만 지우고 관광공사 사진 행은 남김, list 의 "URL 열림" 표시.
- 투표: 비로그인 GET 은 myVote null, 투표 → 집계 반영, 바꾸기 → yes/no 이동, null → 취소, 동은 preset 무시, region preset 누락 400, 질문 문구 3종 + 연간.
- 갤러리 회귀: `regions/photos.py` 가 노트 사진을 고르지 않음.

## 8. 웹 쪽 후속 (이 스펙 범위 밖, 메모)

- `usePhotoPicker` 가 고른 파일을 `FormData` 로 보낸다. `TipComposer` 는 사진이 있으면 노트 쓰기를 multipart 로, `PhotoUploadModal` 은 `POST .../photos` 를 부른다. accept 는 jpeg·png·webp. `usePhotoPicker` · `PhotoUploadModal` · `TipComposer` 의 presign TODO 주석을 이 흐름으로 고친다. `api()` 헬퍼가 FormData 일 때 `Content-Type` 을 직접 넣지 않게(브라우저가 boundary 를 붙이도록) 확인.
- `Note.photos` 렌더, 사진 신고 버튼(노트 사진 · 이달의 사진). 마이페이지 내 사진 삭제 버튼(`DELETE /api/me/photos/{id}`).
- 문구: "운영자 검수 후 게시돼요" → 바로 게시 안내로. 마이페이지 `MyPhoto.status === 'pending'` 문구는 "신고로 검토 중".
- W-06 리포트 라우트 · 코드 정리 (폐기 결정).

## 9. 변경 기록 — 사용자 사진 표 분리 (2026-11-20)

처음 설계는 `photo` 표에 `tip_id` 등 칸을 더해 관광공사 사진과 사용자 사진을 한 표에 담았다. 다음 이유로 `user_photo` 로 나눈다.

- `photo` 는 데이터 담당이 스크립트로 적재한 표다 (관광공사 1,148장). 서비스 콘텐츠를 섞으면 재적재 · 정리 때 서로 꼬인다.
- 신고 · 격리 · 삭제 · 노트 연결 칸은 사용자 사진에만 필요하다.
- 격리 · 삭제가 "관광공사 사진인지(photos/)" 를 매번 판별하지 않아도 된다. `user_photo` 의 모든 행은 우리 `uploads/` 파일이다.
- `photo` 를 바꾸지 않으니 이 기능을 배포하기 전에도 갤러리 · 대표 사진이 그대로 동작한다.

바뀌는 것:

| 항목 | 처음 설계 | 바뀐 설계 |
|---|---|---|
| 사용자 사진 저장 | `photo` (source=user) | `user_photo` |
| 사진 id (Note.photos · MyPhoto · 투표 photos · 신고 · 삭제 경로) | `photo_id` | `user_photo_id` |
| 신고 `report.target_type` | `photo` | `upload` |
| 갤러리 · 대표 사진 (`regions/photos.py`) | `tip_id IS NULL` 조건 추가 | 바꾸지 않음 (`photo` 만) |
| 투표 응답 `photos` | 이달의 사진 + 관광공사 사진(credit) | 사용자 이달의 사진만, `author` = 닉네임 |
| 격리 대상 판별 | source=user 이고 url 이 uploads/ | `user_photo` 의 모든 행 |
| purge | source=user 만, 관광공사 행 유지 | `user_photo` 의 hidden 30일 지난 행 |
| `photo` 표 변경 | 칸 4개 추가 · status 기본값 | 없음 |

API 경로 · 응답 모양 · 에러 코드는 그대로다 (`/api/photos/{id}/report` 의 id 가 `user_photo_id` 가 될 뿐).

계정 삭제(`DELETE /api/me`) 시 `user_photo` 는 `ON DELETE CASCADE` 로 행이 지워지고 R2 파일은 남는다 — 정책 미정, 후속.

