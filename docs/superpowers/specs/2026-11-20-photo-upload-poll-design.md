# 사진 업로드(R2) · 사진 신고 · 체감 투표 설계 — 백엔드

- 작성일: 2026-11-20
- 상태: 승인됨 (브레인스토밍 결과, 2026-11-20)
- 배경: 웹(W-03·W-04·W-08)은 노트 사진 첨부 · 이달의 사진 올리기 · 체감 투표 UI 가 이미 있는데, 백엔드는 업로드 경로가 없고 투표는 `empty_poll` 더미, `POST .../poll/vote` 는 404 다.
- 고치는 문서: workation-web W-03·W-04 스펙의 "이달의 사진 = 운영자 검수 후 게시" 규칙. **바로 게시 + 신고 누적 시 가림 → 운영자 검수**로 바꾼다.
- 범위 밖: W-06 지자체 리포트(폐기), 웹 쪽 연결 작업(별도 계획), 팁·답글 신고, 업로드만 하고 등록 안 된 R2 파일 정리(R2 lifecycle 규칙으로 나중에).

## 1. 목표 · 완료 기준

- 로그인 사용자가 R2 presigned URL 로 사진을 직접 올리고, 받은 key 로 **노트 사진**(노트 쓰기와 함께) 또는 **이달의 사진**을 등록한다. 둘 다 등록 즉시 공개된다.
- 사진은 신고 3건이 쌓이면 가려지고(`pending`), 운영자가 `manage.py photos` 명령으로 복구하거나 숨김을 확정한다.
- `GET .../poll` 이 실제 집계와 내 투표(`myVote`)를 돌려주고, `POST .../poll/vote` 로 투표 · 변경 · 취소한다.
- 단위 테스트(pytest)가 실제 R2 키 없이 통과한다 (R2 클라이언트는 테스트에서 가짜로 바꾼다).

## 2. 설정 · 의존성

`.env` 에 이미 있는 값을 settings 로 읽는다 (`.env.example` 에도 키 이름 추가).

| 설정 | 쓰임 |
|---|---|
| `R2_ACCOUNT_ID` | 엔드포인트 `https://{account}.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` · `R2_SECRET_ACCESS_KEY` | 서명 키. 서버에만 둔다 |
| `R2_BUCKET` | 버킷 이름 |
| `R2_PUBLIC_URL` | 공개 URL 앞부분. `photo.url = R2_PUBLIC_URL + "/" + key` |

의존성 `boto3` 추가 (S3 호환 API, `signature_version="s3v4"`, `region_name="auto"`). R2 접근은 `webapi/storage.py` 한 곳에 모은다: `presign_put(key, content_type)`, `head(key) -> (size, content_type) | None`. 다른 코드는 boto3 를 직접 부르지 않는다.

R2 버킷에는 웹 출처(origin)의 `PUT` 을 허용하는 CORS 규칙이 있어야 한다 (운영 설정, 코드 아님).

## 3. DB 변경 (`db/migrations/20261120_photo-upload-poll.sql`, `db/schema.sql` 동시 반영)

```sql
-- 사진: 노트 첨부 연결 · 바로 게시
ALTER TABLE photo ADD COLUMN IF NOT EXISTS tip_id INTEGER REFERENCES tip(tip_id) ON DELETE CASCADE;
ALTER TABLE photo ADD COLUMN IF NOT EXISTS position SMALLINT;   -- 노트 안 순서 0~2
ALTER TABLE photo ALTER COLUMN status SET DEFAULT 'approved';
CREATE INDEX IF NOT EXISTS photo_tip_idx ON photo (tip_id) WHERE tip_id IS NOT NULL;

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

`photo.status` 의미를 다시 정한다 (CHECK 값은 그대로):

| 값 | 의미 |
|---|---|
| `approved` | 공개 (올리면 바로 이 상태) |
| `pending` | 신고 누적으로 가려짐 · 운영자 검수 대기 |
| `hidden` | 운영자가 숨김 확정 |

`metric_vote` 를 읽던 곳(`community.summary` 의 voteCount)은 `poll_vote` 로 바꾼다: 투표한 (scope, unit_id, month, preset) 수 = 행 수.

**노트 사진은 지역·동네 갤러리에 섞지 않는다.** `regions/photos.py` 의 모든 조회(`_pick`, `one_per_unit`, `one_per_dong`)와 투표 응답 `photos` 에 `tip_id IS NULL` 을 더한다. `GET /api/me/photos` 와 `summary.photoCount` 는 이달의 사진만 센다 (`tip_id IS NULL`). 노트 사진은 노트와 함께 보이고 노트와 함께 지워진다 (CASCADE).

## 4. API

공통: 로그인 필요한 곳은 기존 Supabase JWT 인증. 에러 모양은 기존 `error(status, code, message)`.

### 4-1. `POST /api/uploads/presign` (로그인)

요청 `{ "purpose": "tip" | "photo", "contentType": "image/jpeg" | "image/png" | "image/webp" }`

응답 200 `{ "key": "uploads/photo/{userId}/{uuid}.jpg", "uploadUrl": "https://…", "expiresIn": 300 }`

- 웹은 `uploadUrl` 에 `PUT` 하고 `Content-Type` 헤더를 요청과 같게 보낸다 (서명에 포함).
- 확장자는 contentType 에서 정한다 (jpeg→jpg, png, webp). HEIC 은 받지 않는다 (브라우저가 못 보여 줌).
- 400 `INVALID_UPLOAD` (purpose·contentType 이상) · 429 `UPLOAD_RATE_LIMIT` (사용자당 1분 10회, DRF throttle).

### 4-2. 노트 사진: `POST /api/{regions|dongs}/{id}/notes` 확장

요청에 `photoKeys?: string[]` (0~3개, 중복 불가) 추가.

- 각 key 는 `uploads/tip/{내 userId}/` 로 시작해야 한다. 아니면 400 `INVALID_PHOTO`.
- 각 key 를 R2 `HEAD` 로 확인: 없음 · 10MB 초과 · 이미지 contentType 아님 → 400 `INVALID_PHOTO` (어느 key 인지 메시지에).
- 검증은 팁을 넣기 전에 한다. 팁 · 사진 행은 한 트랜잭션: `photo(scope, unit_id, month=visit_month, url, source='user', user_id, tip_id, position, status='approved')`. 동 노트면 scope='dong', 지역 노트면 scope='region'.
- `Note` 응답(쓰기 · 노트 목록 · 내 팁 목록 공통)에 `photos: { id, url }[]` 를 더한다. 사진 없으면 `[]`. 숨김·가림(`approved` 아님) 사진은 빼고 `position` 순.

### 4-3. 이달의 사진: `POST /api/{regions|dongs}/{id}/photos` (로그인)

요청 `{ "month": "2025-10" | "annual", "keys": string[] }` (1~3개)

응답 201 `{ "items": MyPhoto[] }` (기존 `GET /api/me/photos` 항목 모양, status `approved`)

- key 접두어 `uploads/photo/{내 userId}/`, HEAD 검증은 4-2 와 같다.
- `annual` 이면 `photo.month = NULL`.
- 404 대상 없음 · 400 `INVALID_PHOTO` · 429 `PHOTO_RATE_LIMIT` (사용자당 1시간 30장).

### 4-4. 사진 신고: `POST /api/photos/{photoId}/report` (로그인)

요청 `{ "reason": string }` (1~30자, 기존 `report.reason` 길이)

응답 204. 같은 사람이 같은 사진을 다시 신고해도 204 (멱등, `report` UNIQUE 로 무시).

- 대상이 없거나 이미 `approved` 가 아니면 404.
- 넣은 뒤 그 사진의 신고 수가 `PHOTO_REPORT_THRESHOLD`(settings, 기본 3) 이상이면 `approved → pending`. 신고 넣기와 상태 바꾸기는 한 트랜잭션.
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

## 5. 운영자 검수: `manage.py photos`

| 명령 | 동작 |
|---|---|
| `photos list` | `pending` 사진 목록: id · 대상(지역/동 이름) · 노트 사진 여부 · url · 신고 수 · 신고 사유들 · 올린 사람 닉네임 |
| `photos restore <id…>` | `pending → approved`, 그 사진의 `report` 행 삭제 (같은 신고로 다시 가려지지 않게) |
| `photos hide <id…>` | `pending`·`approved → hidden` (숨김 확정. 신고 없이도 바로 내릴 수 있다) |

R2 파일은 지우지 않는다 (복구 여지 · 증거 보존).

## 6. 코드 배치

| 파일 | 내용 |
|---|---|
| `webapi/storage.py` (새) | R2 클라이언트, `presign_put`, `head`, key 규칙 · 검증 `check_keys(user_id, purpose, keys)` |
| `webapi/uploads.py` (새) | presign 뷰 · 이달의 사진 등록 뷰 · 신고 뷰 |
| `webapi/poll.py` (새) | 질문 문구 · 집계 · 투표 upsert/삭제 · GET/POST 뷰 (`empty_poll` 삭제) |
| `webapi/community.py` | `parse_tip` 에 photoKeys, `create_tip` 사진 넣기, `_note` 에 photos, `summary` voteCount · photoCount, `my_photos` 필터 |
| `regions/photos.py` | 모든 조회에 `tip_id IS NULL` |
| `webapi/management/commands/photos.py` (새) | 5절 명령 |
| `webapi/urls.py` | 새 경로 4개, poll 경로를 새 뷰로 |
| `config/settings.py` · `.env.example` | R2 설정, `PHOTO_REPORT_THRESHOLD` |

## 7. 테스트

- `storage` 는 가짜(fake) 로 바꿔 끼운다: presign 은 고정 URL, head 는 테스트가 정한 (size, contentType) 또는 None.
- presign: 비로그인 401, purpose·contentType 검증, key 접두어에 내 userId.
- 노트 사진: 남의 key · purpose 다른 key · HEAD 없음 · 10MB 초과 · 4장 → 400 이고 팁도 안 생김. 정상이면 Note.photos 순서대로, 노트 목록 · 내 팁 목록에도 보임. 팁 삭제 시 사진 행도 삭제.
- 이달의 사진: annual → month NULL, 201 응답 모양, `GET /api/me/photos` 에 보임, 노트 사진은 안 보임.
- 신고: 2건까지 approved, 3건째 pending · 갤러리와 투표 photos 에서 빠짐, 같은 사람 중복 신고는 수에 안 셈, 없는/가려진 사진 404.
- 명령: restore 후 approved · 신고 0, hide 후 hidden.
- 투표: 비로그인 GET 은 myVote null, 투표 → 집계 반영, 바꾸기 → yes/no 이동, null → 취소, 동은 preset 무시, region preset 누락 400, 질문 문구 3종 + 연간.
- 갤러리 회귀: `regions/photos.py` 가 노트 사진을 고르지 않음.

## 8. 웹 쪽 후속 (이 스펙 범위 밖, 메모)

- `usePhotoPicker` 결과를 presign → PUT → key 로 바꾸는 업로드 훅. `TipComposer` 는 `photoKeys` 를, `PhotoUploadModal` 은 `POST .../photos` 를 부른다. accept 는 jpeg·png·webp.
- `Note.photos` 렌더, 사진 신고 버튼(노트 사진 · 이달의 사진).
- 문구: "운영자 검수 후 게시돼요" → 바로 게시 안내로. 마이페이지 `MyPhoto.status === 'pending'` 문구는 "신고로 검토 중".
- W-06 리포트 라우트 · 코드 정리 (폐기 결정).
