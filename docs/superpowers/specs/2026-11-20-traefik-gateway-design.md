# Traefik 정문 (로컬 1단계) 설계

- 작성일: 2026-11-20
- 상태: 초안 (사용자 검토 대기)
- 바탕 문서: `reports/traefik-활용.md` (에이전트 팀 분석, 통합안 H1-final의 '필수 정문')
- 바탕 코드: `core/views.py`(`health`) · `core/urls.py` · `Dockerfile` · `compose.yaml` · `config/settings.py`

## 1. 목표

Traefik을 리버스 프록시로 앞에 세우고, 앱·Redis·Neo4j는 compose 내부망에만 둔다. 도메인과 서버가 아직 없으므로 이번 단계는 **로컬 Docker에서 HTTP로** 끝까지 동작하는 것을 확인한다.

1. **`/api/live` 신설**: DB를 보지 않는 생존 확인. 컨테이너 헬스체크가 이걸 쓴다.
   - 지금 `/api/health`는 Supabase에 `SELECT 1`을 보낸다. 이걸 헬스체크에 쓰면 Supabase가 잠깐 끊길 때 API 컨테이너 전체가 unhealthy가 되고 Traefik이 라우팅에서 뺀다. DB와 무관한 기능(캐시된 응답, 에러 응답)까지 모두 막힌다.
2. **운영 compose (`compose.prod.yaml`)**: traefik · api · redis · neo4j. 호스트에 여는 포트는 Traefik의 80 하나.
3. **운영 env 예시 (`.env.prod.example`)**: 프록시 뒤에서 필요한 값만 바뀐 형태로.

완료 기준:

- `GET /api/live` 가 DB 연결이 실패해도 200 `{"status": "ok"}` 를 돌려준다 (테스트로 확인).
- `docker compose -f compose.prod.yaml up` 후:
  - `curl http://api.localhost/api/live` → 200 (Traefik 경유).
  - `docker ps` 에서 api 가 `(healthy)`.
  - 호스트에서 8000 · 6379 · 7687 · 7474 에 접속이 안 된다.
  - 헬스체크를 강제로 실패시키면 api 가 라우팅에서 빠진다 (§5-3 실험, `allowEmptyServices` 확정).
  - 위조한 `X-Forwarded-For` 를 보내도 Django가 쓰는 클라이언트 IP가 바뀌지 않는다.
- `uv run pytest` 통과, ruff 통과 (기존 CI 그대로).

범위 밖 (후속 단계):

- HTTPS · ACME(Let's Encrypt) · HTTP→HTTPS 리다이렉트 · HSTS. 도메인과 서버가 생기면 한다 (sslip.io 또는 실도메인).
- LLM 경로 격리(`api-llm` 서비스 + InFlightReq), k6 p95 측정.
- 무중단 배포(docker rollout), Prometheus 메트릭.
- 서버 프로비저닝, CI에서 이미지 빌드·배포.

## 2. 결정 요약

| 항목 | 결정 | 이유 |
|---|---|---|
| 헬스체크 대상 | 신설 `/api/live` | DB 순단이 앱 전체 차단으로 번지지 않게. `/api/health` 는 외부 모니터링용으로 그대로 둔다 |
| 헬스체크 방식 | Docker `HEALTHCHECK` + python urllib | 이미지에 curl 없음. Traefik Docker provider가 unhealthy·starting 컨테이너를 라우팅에서 뺀다. Traefik `loadbalancer.healthcheck` 는 쓰지 않는다 (같은 일을 두 번) |
| compose 파일 | 개발 `compose.yaml` 은 그대로, `compose.prod.yaml` 새로 | 로컬 개발(테스트 DB, 127.0.0.1 포트)과 운영 구성이 섞이지 않게 |
| 공개 포트 | Traefik 80만 (443은 HTTPS 단계에서) | 앱·Redis·Neo4j는 `ports:` 없이 내부망 |
| 라우팅 | `Host(${API_DOMAIN})` → api:8000 | 로컬은 `api.localhost` (브라우저·curl이 127.0.0.1로 풀어 줌) |
| 프록시 뒤 IP | `DRF_NUM_PROXIES=1` | Traefik v3 기본: 신뢰하지 않는 외부 XFF는 지우고 실제 접속 IP를 붙인다. DRF는 오른쪽 끝 값을 쓴다 |
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

## 4. 운영 compose

`compose.prod.yaml` (요지. 정확한 값은 구현 계획에서):

```yaml
services:
  traefik:
    image: traefik:v3.x.y            # 패치 버전까지 고정 (§2)
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --providers.docker.allowEmptyServices=true   # §5-3 실험으로 확정
      - --entrypoints.web.address=:80
      - --accesslog=true
      - --accesslog.format=json
      - --api.dashboard=false
    ports: ["80:80"]
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped

  api:
    build: .
    env_file: .env.prod
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/live', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 20s
    labels:
      - traefik.enable=true
      - traefik.http.routers.api.rule=Host(`${API_DOMAIN}`)
      - traefik.http.routers.api.entrypoints=web
      - traefik.http.services.api.loadbalancer.server.port=8000
    depends_on:
      redis: { condition: service_healthy }
      neo4j: { condition: service_healthy }
    restart: unless-stopped

  redis:      # compose.yaml 과 같은 이미지·설정(maxmemory 256mb allkeys-lru)·healthcheck, ports 없음
  neo4j:      # compose.yaml 과 같은 이미지·볼륨·healthcheck, ports 없음
```

- `urlopen` 은 2xx가 아니거나 연결이 안 되면 예외 → 종료 코드 1 → unhealthy.
- 헬스체크 요청의 Host 헤더는 `localhost:8000` 이다. 그래서 `DJANGO_ALLOWED_HOSTS` 에 `localhost` 가 있어야 한다 (Django는 포트를 떼고 비교한다). 없으면 400 → unhealthy.
- `API_DOMAIN` 은 compose 변수 치환에 쓰이므로 `--env-file .env.prod` 로 compose에도 넘긴다 (`env_file:` 은 컨테이너 안에만 들어간다).
- Neo4j 그래프 동기화: `docker compose -f compose.prod.yaml run --rm api python manage.py sync_graph`. Neo4j Browser가 필요하면 그때만 `127.0.0.1:7474` 를 연다 (공개 안 함).
- Docker 소켓은 읽기 전용으로 마운트한다.

## 5. 운영 env와 검증

### 5-1. `.env.prod.example`

`.env.example` 을 바탕으로 다음만 다르다.

```
API_DOMAIN=api.localhost
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=api.localhost,localhost
DRF_NUM_PROXIES=1
REDIS_URL=redis://redis:6379/0
NEO4J_URI=bolt://neo4j:7687
```

- 서버가 생기면 `API_DOMAIN` · `DJANGO_ALLOWED_HOSTS` 의 `api.localhost` 를 실제 API 도메인으로 바꾼다. `localhost` 는 헬스체크 때문에 남긴다.
- `CORS_ALLOWED_ORIGINS` 에는 Vercel 웹 주소를 넣는다 (ALLOWED_HOSTS 와 다르다: ALLOWED_HOSTS는 '요청 받는 우리 주소', CORS는 '요청 보내는 웹 주소').
- `.env.prod` 는 커밋하지 않는다. `.gitignore`·`.dockerignore` 의 `.env.*` 가 막는다. 단 `.gitignore` 는 `.env.example` 만 예외라 `!.env.prod.example` 예외를 추가해야 예시 파일이 커밋된다.

### 5-2. 기본 검증 (로컬)

1. `docker compose -f compose.prod.yaml --env-file .env.prod up -d --build`
2. `docker ps` → api `(healthy)`.
3. `curl -i http://api.localhost/api/live` → 200. `curl -i http://api.localhost/api/health` → 200 (Supabase 접속 가능 시).
4. `curl http://localhost:8000/...` · `redis-cli -p 6379 ping` · `nc -z localhost 7687` → 모두 연결 거부.
5. 다른 Host(`curl -H 'Host: evil.example' http://localhost/`) → Traefik 404 (라우터 없음).

### 5-3. 헬스 실패 실험 (`allowEmptyServices` 확정)

1. api 컨테이너에서 헬스체크를 실패하게 만든다 (예: `.env.prod` 의 `DJANGO_ALLOWED_HOSTS` 에서 `localhost` 를 빼고 재생성 → 헬스체크가 400).
2. `docker ps` 가 `(unhealthy)` 가 된 뒤 `curl -i http://api.localhost/api/live`.
3. 기대: 503 (서비스는 있지만 살아 있는 서버가 없음). access log에 api로 전달된 기록이 없다.
4. 판정: 503이고 api로 트래픽이 가지 않으면 `allowEmptyServices=true` 확정. **unhealthy 컨테이너로 트래픽이 가면 `false` 로 바꾸고 404를 받아들인다.**
5. 결과를 이 문서 §7에 적는다.

### 5-4. X-Forwarded-For 위조 확인

1. `curl -H 'X-Forwarded-For: 1.2.3.4' http://api.localhost/api/live` 를 보낸다.
2. Django가 판정한 IP를 확인한다. 방법: throttle 키(Redis `throttle_*`)에 들어간 IP 또는 임시 로그. 상시 코드로 남기지 않는다.
3. 기대: `1.2.3.4` 가 아니라 Docker 네트워크의 실제 접속 IP(호스트 게이트웨이).

## 6. 영향 받는 파일

| 파일 | 변경 |
|---|---|
| `core/views.py` | `live` 뷰 추가 |
| `core/urls.py` | `live` 경로 추가 |
| `tests/test_health.py` | 테스트 2개 추가 |
| `compose.prod.yaml` | 신규 |
| `.env.prod.example` | 신규 |
| `.gitignore` | `!.env.prod.example` 예외 한 줄 추가 |
| `compose.yaml` | 변경 없음 ("앱 · Traefik 은 배포 세팅 때 추가한다" 주석만 `compose.prod.yaml` 을 가리키게 수정) |
| `Dockerfile` | 변경 없음 (HEALTHCHECK는 compose에 둔다) |

## 7. 실험 결과

(구현 후 §5-3 · §5-4 결과를 적는다.)
