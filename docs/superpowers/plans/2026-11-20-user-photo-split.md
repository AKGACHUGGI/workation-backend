# 사용자 사진 표 분리 (photo → user_photo) 구현 계획

> 설계: `docs/superpowers/specs/2026-11-20-photo-upload-poll-design.md` 3절 · 9절. 앞 계획(`2026-11-20-photo-upload-poll.md`)으로 만든 코드를 사용자 사진 전용 표 `user_photo` 로 옮긴다.

**공통 기반 (코디네이터가 먼저 커밋)**
- `db/schema.sql`: `photo` 를 main 과 같게 되돌림, `user_photo` 표 · 인덱스 3개 · RLS, `report.target_type` 에 `upload`.
- `db/migrations/20261120_photo-upload-poll.sql`: `photo` 변경 없음, `user_photo` 생성, report CHECK 교체, `poll_vote` (그대로). main 스키마에 두 번 실행해 확인함.
- `webapi/photo_quarantine.REPORT_TYPE = "upload"` — 사용자 사진 신고의 `report.target_type`.
- 테스트 도우미 `tests.photo_tools.user_photo(r2, status="approved", tip_id=None, user_id=None, scope="region", unit_id=1, month=10) -> (user_photo_id, key)`. 다른 테스트의 `from tests.test_photo_quarantine import user_photo` 는 이것으로 바꾼다.

**규칙**
- `user_photo` 컬럼: user_photo_id, user_id(NOT NULL), scope, unit_id, month(NULL=연간), url, tip_id, position, status(approved·pending·hidden, 기본 approved), hidden_at, deleted_by_user, created_at. `source` · `credit` 없음.
- 응답의 사진 id 는 `str(user_photo_id)`. API 경로 · 응답 모양 · 에러 코드는 그대로.
- `photo` 표와 `regions/photos.py` 는 main 과 같게 (갤러리 · 대표 사진은 관광공사 사진만).
- 사용자 사진 신고는 `report.target_type = photo_quarantine.REPORT_TYPE`.
- 격리 · purge · 삭제 대상은 `user_photo` 행 전부 (관광공사 판별 불필요). `key_of` 는 그대로 써도 된다.
- 투표 응답 `photos` 는 `user_photo` 의 이달의 사진(approved, tip_id IS NULL, annual 이면 월 무관) 최신 6장, `author` = `app_user.nickname`.

**작업 A — 노트 · 집계 · 갤러리 원복 · 문서**
- `regions/photos.py` 를 main 과 같게 (`git checkout main -- regions/photos.py`), `tests/test_api.py` 의 `test_note_photos_stay_out_of_galleries` 삭제.
- `tests/test_schema.py`: `user_photo` 표 · 컬럼 · report CHECK(upload) 확인, photo 컬럼 테스트는 photo 가 바뀌지 않았음을 확인하도록.
- `webapi/community.py`: `_TIP_PAGE` photos 서브쿼리 · `_insert_tip_photos` · `delete_tip` · `summary.photoCount` · `my_photos` 를 `user_photo` 로.
- 테스트: `tests/test_me_api.py`, `tests/test_note_photos.py`, `tests/test_photo_delete.py`.
- 문서: `docs/개발-가이드.md`, `db/README.md`, `db/erd-mysql-erdcloud.sql`.

**작업 B — 격리 모듈 · 운영자 명령**
- `webapi/photo_quarantine.py` 전부 `user_photo` 로 (source 조건 삭제, report 는 REPORT_TYPE).
- `webapi/management/commands/photos.py` (list 는 user_photo + report REPORT_TYPE, 지역 · 동 이름 · 노트 여부 · 닉네임).
- 테스트: `tests/test_photo_quarantine.py` (도우미는 photo_tools 것, 관광공사 사진 케이스는 "photo 표는 건드리지 않음" 으로), `tests/test_photos_command.py`.

**작업 C — 이달의 사진 · 신고 · 투표**
- `webapi/uploads.py`: 이달의 사진 INSERT · 시간당 한도 세기 · `_report` 를 `user_photo` 로, 신고는 REPORT_TYPE.
- `webapi/poll.py`: 투표 photos 쿼리를 `user_photo` JOIN `app_user` 로.
- 테스트: `tests/test_month_photos.py`, `tests/test_poll.py`.

**합치기**: 코디네이터가 세 브랜치를 합친 뒤 전체 테스트 · ruff · 마이그레이션 두 번 실행을 확인하고 PR #38 에 push.
