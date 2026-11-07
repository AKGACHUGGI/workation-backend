# workation-backend

워케이션(가제) 백엔드 서버.
LG U+ 유동/서비스인구 데이터를 기반으로 워케이션 지역·시기·동네를 추천하는 서비스의 API 서버입니다.

## 관련 레포

- 모바일 앱: [workation-mobile](https://github.com/TEAM-SKPJC/workation-mobile) (Flutter)
- 웹: [workation-web](https://github.com/TEAM-SKPJC/workation-web) (React + Tailwind)
- 팀 문서: [workation-docs](https://github.com/TEAM-SKPJC/workation-docs) (명세서 · 디자인 원칙 · [협업 규칙](https://github.com/TEAM-SKPJC/workation-docs/blob/main/협업/협업-규칙.md))
- 디자인: [Figma 기업연계캡스톤](https://www.figma.com/design/QulBXJPP2qYhRhh8lGzT66)

## 시작하기

Django 5.2 · Django REST Framework · Gunicorn. Python 패키지는 [uv](https://docs.astral.sh/uv/) 로 관리합니다.

```bash
uv sync                                   # Python 3.14 와 의존성 설치
cp .env.example .env                      # Supabase 접속 정보는 데이터 담당에게 직접 받기 (이슈 #2)
uv run python manage.py runserver
curl localhost:8000/api/health            # {"status":"ok","db":"ok"}
```

테스트는 Supabase 가 아니라 로컬 Postgres 컨테이너에서 돕니다.

```bash
docker compose up -d --wait               # 테스트용 postgres:17 (5432 가 사용 중이면 TEST_DB_PORT=5433 을 앞에)
uv run pytest
```

커밋 전 검사, 모델 규칙, 새 API 추가 방법은 [docs/개발-가이드.md](docs/개발-가이드.md) 를 참고하세요.

## DB 스키마

`db/schema.sql` 이 기준입니다 (Supabase PostgreSQL, ERD v0.3). 테이블 설명·다이어그램·변경 규칙은 [db/README.md](db/README.md) 를 참고하세요.

## 환경 변수

`.env` 파일은 커밋하지 않습니다. `.env.example`을 복사해 사용합니다.
