# Traefik 정문 (로컬 1단계) 설계

- 작성일: 2026-11-20
- 상태: 확정 (2026-11-20 사용자 승인)
- 바탕 문서: `reports/traefik-활용.md` (에이전트 팀 분석, 통합안 H1-final의 '필수 정문')
- 바탕 코드: `core/views.py`(`health`) · `core/urls.py` · `Dockerfile` · `compose.yaml` · `config/settings.py` · `webapi/views.py`(`EventsView`)

## 1. 목표

Traefik을 리버스 프록시로 Django 앞에 세운다. 도메인과 서버가 아직 없으므로 이번 단계는 **내 컴퓨터의 Docker에서 HTTP로** Traefik → Django가 동작하는 것을 확인한다. 운영용 compose 파일은 따로 만들지 않고 기존 `compose.yaml` 에 profile로 넣는다.

1. **`/api/live` 신설**: DB를 보지 않는 생존 확인. 컨테이너 헬스체크가 이걸 쓴다.
   - 지금 `/api/health`는 Supabase에 `SELECT 1`을 보낸다. 이걸 헬스체크에 쓰면 Supabase가 잠깐 끊길 때 API 컨테이너가 unhealthy가 되고 Traefik이 라우팅에서 뺀다. DB와 무관한 기능(캐시된 응답, 에러 응답)까지 모두 막힌다.
2. **`compose.yaml` 에 `traefik` · `api` 추가**: `proxy` profile로 묶는다. 평소 `docker compose up` 은 지금과 같다.

완료 기준:

- `GET /api/live` 가 DB 연결이 실패해도 200 `{"status": "ok"}` 를 돌려준다 (테스트로 확인).
- `docker compose --profile proxy up -d --build` 후:
  - `curl http://api.localhost/api/live` → 200 (Traefik 경유).
  - `docker ps` 에서 api 가 `(healthy)`.
  - api 컨테이너의 8000은 호스트에 열려 있지 않다 (Traefik으로만 들어간다).
  - 헬스체크를 강제로 실패시키면 api 가 라우팅에서 빠진다 (§5-2 실험, `allowEmptyServices` 확정).
  - 위조한 `X-Forwarded-For` 를 보내도 Django가 쓰는 클라이언트 IP가 바뀌지 않는다.
- profile 없이 `docker compose up -d` 하면 지금처럼 test-db · neo4j · redis 만 뜬다.
- `uv run pytest` 통과, ruff 통과 (기존 CI 그대로).

범위 밖 (후속 단계):

- 서버 배포 구성: 운영 env, Redis · Neo4j 포트 완전히 닫기(`ports:` 제거), 서버 방화벽. 서버가 생기면 한다.
- HTTPS · ACME(Let's Encrypt) · HTTP→HTTPS 리다이렉트 · HSTS. 도메인이 생기면 한다 (sslip.io 또는 실도메인).
- LLM 경로 격리(`api-llm` 서비스 + InFlightReq), k6 p95 측정.
- 무중단 배포(docker rollout), Prometheus 메트릭.

## 2. 결정 요약

| 항목 | 결정 | 이유 |
|---|---|---|
| 헬스체크 대상 | 신설 `/api/live` | DB 순단이 앱 전체 차단으로 번지지 않게. `/api/health` 는 외부 모니터링용으로 그대로 둔다 |
| 헬스체크 방식 | compose `healthcheck` + python urllib | 이미지에 curl 없음. Traefik Docker provider가 unhealthy·starting 컨테이너를 라우팅에서 뺀다. Traefik `loadbalancer.healthcheck` 는 쓰지 않는다 (같은 일을 두 번) |
| compose 파일 | `compose.yaml` 하나, `traefik` · `api` 는 `profiles: ["proxy"]` | 운영 파일을 따로 두지 않는다. profile로 묶어 평소 개발(`uv run` + 백엔드 서비스)에는 영향이 없게 |
| 호스트 포트 | Traefik `127.0.0.1:80` 만 새로 연다. api는 `ports:` 없음 | 다른 서비스처럼 이 컴퓨터에서만 연다. Redis · Neo4j 의 127.0.0.1 포트는 개발용이라 그대로 둔다 |
| 라우팅 | `Host(\`api.localhost\`)` → api:8000 | `api.localhost` 는 브라우저·curl이 127.0.0.1로 풀어 준다. 실제 도메인은 배포 단계에서 바꾼다 |
| api 환경변수 | `.env` 를 읽고, 컨테이너 안에서 달라지는 값만 `environment:` 로 덮어쓴다 | `.env` 하나를 `uv run` 과 같이 쓴다. Redis · Neo4j 주소가 컨테이너 안에서는 `localhost` 가 아니라 서비스 이름이다 |
| 프록시 뒤 IP | `DRF_NUM_PROXIES=1` (api 컨테이너만) | Traefik v3 기본: 신뢰하지 않는 외부 XFF는 지우고 실제 접속 IP를 붙인다. DRF는 오른쪽 끝 값을 쓴다 |
| Traefik 노출 범위 | `exposedbydefault=false`, 대시보드 끔, JSON access log | 라벨 붙인 서비스만 공개. 대시보드는 공개할 이유가 없다 |
| Traefik 버전 | v3 패치 버전까지 고정 | 보안 권고 GHSA-v67p-phpq-fc8x 수정 이후 버전. 구현 시 최신 v3 패치를 확인해 그 태그로 고정한다 (`latest`·`v3` 태그 금지) |
| CORS · 압축 | Traefik에 두지 않음 | Django `django-cors-headers` · `GZipMiddleware` 가 이미 한다 |

## 3. `/api/live`

`core/views.py`:

```python
@api_view(["GET"])
def live(request):
    """프로세스가 요청을 받는지만 본다. DB 는 보지 않는다 (컨테이너 헬스체크용)."""
    return Response({"status": "ok"})
```

`core/urls.py` 에 `path("live", views.live, name="live")` 추가. 최종 경로는 `/api/live`.

- 인증·throttle 없음. DRF 기본값이 인증 없음·`AllowAny`·전역 throttle 없음이라 따로 설정하지 않는다 (`health` 와 같다).
- `/api/health` 는 바꾸지 않는다.

테스트 (`tests/test_health.py` 에 추가):

1. `test_live_ok`: `GET /api/live` → 200, `{"status": "ok"}`.
2. `test_live_ignores_db`: 기존 `test_health_db_down_hides_error` 와 같은 방식(monkeypatch)으로 DB 커서가 `DatabaseError` 를 내게 만든 뒤에도 200.

## 4. `compose.yaml` 추가분

기존 test-db · neo4j · redis 는 바꾸지 않는다. 아래 두 서비스를 추가한다 (요지. 정확한 값은 구현 계획에서).

```yaml
  # Django 앞의 리버스 프록시. --profile proxy 일 때만 뜬다
  traefik:
    image: traefik:v3.x.y            # 패치 버전까지 고정 (§2)
    profiles: ["proxy"]
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --providers.docker.allowEmptyServices=true   # §5-2 실험으로 확정
      - --entrypoints.web.address=:80
      - --accesslog=true
      - --accesslog.format=json
      - --api.dashboard=false
    ports:
      - "127.0.0.1:80:80"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

  # Django (gunicorn). 호스트 포트 없음, Traefik 으로만 들어온다
  api:
    build: .
    profiles: ["proxy"]
    env_file: .env
    environment:                     # 컨테이너 안에서 달라지는 값만 덮어쓴다
      REDIS_URL: redis://redis:6379/0
      NEO4J_URI: bolt://neo4j:7687
      DJANGO_ALLOWED_HOSTS: api.localhost,localhost
      DRF_NUM_PROXIES: "1"
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/live', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 20s
    labels:
      - traefik.enable=true
      - traefik.http.routers.api.rule=Host(`api.localhost`)
      - traefik.http.routers.api.entrypoints=web
      - traefik.http.services.api.loadbalancer.server.port=8000
    depends_on:
      redis: { condition: service_healthy }
      neo4j: { condition: service_healthy }
```

- `environment:` 는 `env_file:` 보다 우선한다. 그래서 `.env` 의 `REDIS_URL=redis://localhost:6379/0` 은 `uv run` 에서, 덮어쓴 `redis://redis:6379/0` 은 컨테이너에서 쓰인다.
- DB는 `.env` 의 Supabase 값을 그대로 쓴다 (test-db는 단위 테스트 전용).
- `urlopen` 은 2xx가 아니거나 연결이 안 되면 예외 → 종료 코드 1 → unhealthy.
- 헬스체크 요청의 Host 헤더는 `localhost:8000` 이다. 그래서 `DJANGO_ALLOWED_HOSTS` 에 `localhost` 가 있어야 한다 (Django는 포트를 떼고 비교한다). 없으면 400 → unhealthy.
- 맨 위 주석 "앱 · Traefik 은 배포 세팅 때 추가한다" 를 "`--profile proxy` 로 Traefik + 앱 컨테이너를 띄운다. 웹·앱 팀원은 이걸로 백엔드 전체를 띄우고 `http://api.localhost` 를 부른다. 백엔드 개발은 평소대로 `uv run`" 으로 바꾼다.
- Docker 소켓은 읽기 전용으로 마운트한다.
- 호스트의 80 포트를 다른 프로그램이 쓰고 있으면 Traefik이 뜨지 않는다. 그때는 `127.0.0.1:8080:80` 으로 바꾸고 검증 주소에 `:8080` 을 붙인다.

## 5. 검증

### 5-1. 기본 검증

1. `docker compose --profile proxy up -d --build`
2. `docker ps` → api `(healthy)`.
3. `curl -i http://api.localhost/api/live` → 200. `curl -i http://api.localhost/api/health` → 200 (Supabase 접속 가능 시).
4. `curl http://localhost:8000/api/live` → 연결 거부 (`uv run` 개발 서버를 끈 상태에서).
5. 다른 Host(`curl -i -H 'Host: evil.example' http://localhost/`) → Traefik 404 (라우터 없음).
6. `docker compose down` 후 `docker compose up -d` → traefik · api 는 뜨지 않는다.

### 5-2. 헬스 실패 실험 (`allowEmptyServices` 확정)

1. api 헬스체크를 실패하게 만든다: `compose.yaml` 의 api `DJANGO_ALLOWED_HOSTS` 에서 `localhost` 를 잠시 빼고 `docker compose --profile proxy up -d api` (헬스체크가 400).
2. `docker ps` 가 `(unhealthy)` 가 된 뒤 `curl -i http://api.localhost/api/live`.
3. 기대: 503 (서비스는 있지만 살아 있는 서버가 없음). Traefik access log에 api 서버로 전달된 기록이 없다.
4. 판정: 503이고 api로 트래픽이 가지 않으면 `allowEmptyServices=true` 확정. **unhealthy 컨테이너로 트래픽이 가면 `false` 로 바꾸고 404를 받아들인다.**
5. `localhost` 를 되돌리고, 결과를 이 문서 §7에 적는다.

### 5-3. X-Forwarded-For 위조 확인

`/api/live` 는 throttle이 없어 IP가 남지 않는다. throttle이 걸린 `POST /api/events` 를 쓴다. DRF는 throttle을 본문 검사보다 먼저 세므로, 빈 본문(400, DB 쓰기 없음)으로도 IP가 Redis에 남는다.

1. `curl -i -X POST -H 'Content-Type: application/json' -H 'X-Forwarded-For: 1.2.3.4' -d '{}' http://api.localhost/api/events` → 400.
2. `docker compose exec redis redis-cli --scan --pattern '*throttle_events*'` 로 키를 본다.
3. 기대: 키에 `1.2.3.4` 가 없고, Docker 네트워크의 실제 접속 IP(호스트 게이트웨이)가 있다.

## 6. 영향 받는 파일

| 파일 | 변경 |
|---|---|
| `core/views.py` | `live` 뷰 추가 |
| `core/urls.py` | `live` 경로 추가 |
| `tests/test_health.py` | 테스트 2개 추가 |
| `compose.yaml` | `traefik` · `api` 서비스 추가 (`proxy` profile), 맨 위 주석 수정. 기존 서비스는 그대로 |
| `Dockerfile` | 변경 없음 (헬스체크는 compose에 둔다) |

## 7. 실험 결과

실행일: 2026-11-20, Traefik v3.7.19, Docker Desktop 4.62.0 (Engine 29.2.1)

- §5-2 헬스 실패: unhealthy 상태에서 `/api/live` → **500** (`Internal Server Error`, 본문 21바이트. 예상한 503 이 아니다). access log 의 `ServiceURL` · `ServiceAddr` 가 비어 있고, api 로그에는 `api.localhost` 요청이 한 건도 없다 (헬스체크의 `localhost:8000` DisallowedHost 만 있음). 전달 기록 없음 → 500 은 Traefik 이 직접 낸 응답이다. 판정: `allowEmptyServices=true` (unhealthy 컨테이너로 트래픽이 가지 않는다는 목적은 충족. 상태 코드만 503 이 아닌 500).
- §5-3 XFF 위조: `X-Forwarded-For: 1.2.3.4` 로 보낸 `POST /api/events` → 400. throttle 키 = `throttle_events_burst_172.25.0.1` · `throttle_events_day_172.25.0.1` (Docker 네트워크 게이트웨이, Traefik access log 의 `ClientHost` 와 같음). 위조 값 무시됨.
