# Traefik 정문 (로컬 1단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DB를 보지 않는 `/api/live` 를 만들고, `compose.yaml` 의 `proxy` profile로 Traefik → Django(api 컨테이너)를 로컬에서 띄워 검증한다.

**Architecture:** `core` 앱에 `live` 뷰 하나를 추가한다. `compose.yaml` 에 `traefik`(Docker provider, 라벨 기반)과 `api`(기존 `Dockerfile` 빌드, `/api/live` 헬스체크) 두 서비스를 `profiles: ["proxy"]` 로 추가한다. 평소 `docker compose up` 과 `uv run` 개발 흐름은 바뀌지 않는다.

**Tech Stack:** Django 5.2 + DRF, pytest-django, Docker Compose, Traefik v3.7.19

**Spec:** `docs/superpowers/specs/2026-11-20-traefik-gateway-design.md`

## Global Constraints

- Traefik 이미지는 `traefik:v3.7.19` 로 고정한다 (2026-11-20 기준 최신 v3 패치, GitHub releases 확인). `latest`·`v3` 태그 금지.
- 기존 `compose.yaml` 서비스(test-db · neo4j · redis)는 바꾸지 않는다.
- `traefik` · `api` 는 `profiles: ["proxy"]`. profile 없이 `docker compose up -d` 하면 지금처럼 3개만 뜬다.
- 새로 여는 호스트 포트는 `127.0.0.1:80:80` (Traefik) 하나. api 는 `ports:` 없음.
- Traefik: `exposedbydefault=false`, 대시보드 끔, JSON access log, Docker 소켓 `:ro`.
- CORS · 압축은 Traefik에 두지 않는다 (Django가 이미 한다).
- `/api/health` 는 바꾸지 않는다.
- `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .` 통과 (CI와 같음).
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- 작업 브랜치: `feat/traefik-gateway` (이미 있음).

---

### Task 1: `/api/live` 뷰

**Files:**
- Modify: `core/views.py` (파일 끝에 `live` 추가)
- Modify: `core/urls.py`
- Test: `tests/test_health.py` (파일 끝에 테스트 2개 추가)

**Interfaces:**
- Consumes: 없음
- Produces: `GET /api/live` → 200 `{"status": "ok"}`. Task 2의 컨테이너 헬스체크가 `http://localhost:8000/api/live` 로 부른다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_health.py` 끝에 추가한다. 기존 파일에 `pytest` · `core.views` · `_BrokenConnection` 이 이미 있다.

```python
def test_live_ok(client):
    response = client.get("/api/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_live_ignores_db(client, monkeypatch):
    monkeypatch.setattr(core.views, "connection", _BrokenConnection())

    response = client.get("/api/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

두 테스트 모두 `@pytest.mark.django_db` 를 붙이지 않는다. pytest-django가 DB 접근을 막으므로, 뷰가 DB를 건드리면 테스트가 실패한다 (DB 무관임을 이중으로 확인).

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_health.py -k live -v`
Expected: 2개 FAIL, `assert 404 == 200`

- [ ] **Step 3: 구현**

`core/views.py` 끝에 추가:

```python
@api_view(["GET"])
def live(request):
    """프로세스가 요청을 받는지만 본다. DB 는 보지 않는다 (컨테이너 헬스체크용)."""
    return Response({"status": "ok"})
```

`core/urls.py`:

```python
from django.urls import path

from core import views

urlpatterns = [
    path("health", views.health, name="health"),
    path("live", views.live, name="live"),
]
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_health.py -v`
Expected: 4 passed (기존 health 2개 포함)

- [ ] **Step 5: 전체 테스트와 lint**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check .`
Expected: 전부 통과 (단위 테스트는 test-db 필요: `docker compose up -d --wait test-db`)

- [ ] **Step 6: 커밋**

```bash
git add core/views.py core/urls.py tests/test_health.py
git commit -m "feat: DB 를 보지 않는 생존 확인 GET /api/live

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `compose.yaml` 에 traefik · api 추가 (proxy profile)

**Files:**
- Modify: `compose.yaml` (맨 위 주석, `volumes:` 앞에 서비스 2개 추가)

**Interfaces:**
- Consumes: Task 1의 `GET /api/live`
- Produces: `docker compose --profile proxy up -d --build` 로 `http://api.localhost` 에서 Django가 응답. Task 3의 실험이 이 구성을 쓴다.

- [ ] **Step 1: 맨 위 주석 교체**

`compose.yaml` 1행

```yaml
# 로컬 테스트 DB · 탐색 그래프 Neo4j · 캐시 Redis. 앱 · Traefik 은 배포 세팅 때 추가한다.
```

을 다음으로 바꾼다 (2행 "단위 테스트는 …" 은 그대로).

```yaml
# 로컬 테스트 DB · 탐색 그래프 Neo4j · 캐시 Redis.
# --profile proxy 로 Traefik + 앱 컨테이너를 띄운다. 웹·앱 팀원은 이걸로 백엔드 전체를 띄우고
# http://api.localhost 를 부른다. 백엔드 개발은 평소대로 uv run (compose 는 위 3개만).
```

- [ ] **Step 2: 서비스 추가**

`redis:` 서비스 블록 뒤, 최상위 `volumes:` 앞에 추가한다.

```yaml
  # Django 앞의 리버스 프록시. 라벨(traefik.enable=true) 붙은 컨테이너만 공개한다
  traefik:
    image: traefik:v3.7.19
    profiles: ["proxy"]
    command:
      - --providers.docker=true
      - --providers.docker.exposedbydefault=false
      - --providers.docker.allowEmptyServices=true
      - --entrypoints.web.address=:80
      - --accesslog=true
      - --accesslog.format=json
      - --api.dashboard=false
    ports:
      - "127.0.0.1:80:80"   # 80 을 다른 프로그램이 쓰면 127.0.0.1:8080:80 으로
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro

  # Django (gunicorn). 호스트 포트 없이 Traefik 으로만 들어온다.
  # .env 를 uv run 과 같이 쓰고, 컨테이너 안에서 달라지는 값만 environment 로 덮어쓴다
  api:
    build: .
    profiles: ["proxy"]
    env_file: .env
    environment:
      REDIS_URL: redis://redis:6379/0
      NEO4J_URI: bolt://neo4j:7687
      DJANGO_ALLOWED_HOSTS: api.localhost,localhost   # localhost 는 아래 헬스체크용
      DRF_NUM_PROXIES: "1"
    healthcheck:
      # DB 를 보지 않는 /api/live. 이미지에 curl 이 없어 python 으로 부른다
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/live', timeout=3)"]
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
      redis:
        condition: service_healthy
      neo4j:
        condition: service_healthy
```

- [ ] **Step 3: 문법 확인**

Run: `docker compose config -q && docker compose --profile proxy config -q && echo OK`
Expected: `OK`

Run: `docker compose config --services`
Expected: `test-db`, `neo4j`, `redis` 3개만 (traefik · api 없음)

- [ ] **Step 4: 띄우기**

`uv run` 개발 서버가 8000에서 돌고 있으면 먼저 끈다.

Run: `docker compose --profile proxy up -d --build --wait`
Expected: 모든 서비스 Healthy/Running 으로 끝남. 실패하면 `docker compose logs api traefik` 확인.

Run: `docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'`
Expected: api `(healthy)`, api 의 Ports 칸이 비어 있거나 `8000/tcp` 만 (호스트 매핑 없음), traefik `127.0.0.1:80->80/tcp`

- [ ] **Step 5: 경유 확인 (스펙 §5-1)**

```bash
curl -i http://api.localhost/api/live
curl -i http://api.localhost/api/health
curl -i --max-time 3 http://localhost:8000/api/live
curl -i -H 'Host: evil.example' http://localhost/
```

Expected (순서대로):
1. `200` + `{"status":"ok"}`
2. `200` + `{"status":"ok","db":"ok"}` (`.env` 의 Supabase 접속이 될 때)
3. 연결 실패 (`Failed to connect` / `Connection refused`)
4. `404 page not found` (Traefik 응답)

`api.localhost` 가 안 풀리면 `curl -i -H 'Host: api.localhost' http://127.0.0.1/api/live` 로 대신한다.

Run: `docker compose logs traefik --tail 5`
Expected: JSON 한 줄씩 access log (`"RequestHost":"api.localhost"` 등)

- [ ] **Step 6: profile 없이 띄우면 그대로인지 확인**

```bash
docker compose --profile proxy down
docker compose up -d --wait
docker compose ps --format '{{.Service}}'
```

Expected: `test-db`, `neo4j`, `redis` 만

- [ ] **Step 7: 커밋**

```bash
git add compose.yaml
git commit -m "feat: compose proxy profile 로 Traefik + 앱 컨테이너 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 헬스 실패 · XFF 위조 실험과 결과 기록

**Files:**
- Modify: `compose.yaml` (실험 결과가 `allowEmptyServices=false` 를 요구할 때만)
- Modify: `docs/superpowers/specs/2026-11-20-traefik-gateway-design.md` (§7 실험 결과)

**Interfaces:**
- Consumes: Task 2의 proxy profile 구성
- Produces: §7에 기록된 실험 결과, 확정된 `allowEmptyServices` 값

- [ ] **Step 1: 헬스체크 강제 실패 (스펙 §5-2)**

`compose.yaml` api 의 `DJANGO_ALLOWED_HOSTS: api.localhost,localhost` 를 잠시 `DJANGO_ALLOWED_HOSTS: api.localhost` 로 바꾼다 (커밋하지 않는다).

```bash
docker compose --profile proxy up -d
# 헬스체크가 400 → 3회 실패 후 unhealthy (약 30~50초)
until docker inspect -f '{{.State.Health.Status}}' $(docker compose ps -q api) | grep -q unhealthy; do sleep 5; done
curl -i http://api.localhost/api/live
docker compose logs traefik --tail 3
```

Expected: `503 Service Unavailable`. 마지막 access log의 `ServiceURL`(또는 `ServiceAddr`)이 비어 있다 (api 로 전달 안 됨).

판정:
- 503이고 전달 기록이 없으면 → `allowEmptyServices=true` 유지.
- api 응답(400 등)이 오거나 전달 기록이 있으면 → `compose.yaml` 의 `--providers.docker.allowEmptyServices=true` 줄을 지우고(기본값 false, 응답은 404) 다시 확인한다.

- [ ] **Step 2: 되돌리기**

`DJANGO_ALLOWED_HOSTS: api.localhost,localhost` 로 되돌린다.

Run: `docker compose --profile proxy up -d --wait && curl -s http://api.localhost/api/live`
Expected: `{"status":"ok"}`

Run: `git diff compose.yaml`
Expected: 비어 있음 (Step 1 판정으로 `allowEmptyServices` 를 바꾼 경우만 그 한 줄 차이)

- [ ] **Step 3: X-Forwarded-For 위조 (스펙 §5-3)**

```bash
curl -i -X POST -H 'Content-Type: application/json' -H 'X-Forwarded-For: 1.2.3.4' -d '{}' http://api.localhost/api/events
docker compose exec redis redis-cli --scan --pattern '*throttle_events*'
```

Expected:
1. `400` (본문 오류. DRF는 throttle을 본문 검사보다 먼저 센다)
2. `...throttle_events_burst_<IP>` · `...throttle_events_day_<IP>` 키. `<IP>` 가 `1.2.3.4` 가 **아니고** Docker 네트워크 IP(예: `192.168.65.1`, `172.x.x.1`)

`1.2.3.4` 가 보이면 실패다. 멈추고 `DRF_NUM_PROXIES` 와 Traefik `forwardedHeaders` 동작을 다시 확인한다 (임의로 설정을 바꾸지 않는다).

- [ ] **Step 4: 정리**

Run: `docker compose --profile proxy down && docker compose up -d --wait`
Expected: 평소 개발 상태 (test-db · neo4j · redis)

- [ ] **Step 5: 스펙 §7에 결과 기록**

`docs/superpowers/specs/2026-11-20-traefik-gateway-design.md` 의

```
(구현 후 §5-2 · §5-3 결과를 적는다.)
```

를 실제 관찰 값으로 바꾼다. 형식:

```markdown
실행일: 2026-11-XX, Traefik v3.7.19, Docker Desktop <버전>

- §5-2 헬스 실패: unhealthy 상태에서 `/api/live` → <상태 코드>. access log 전달 기록 <없음/있음>. 판정: `allowEmptyServices=<true/false>`.
- §5-3 XFF 위조: `X-Forwarded-For: 1.2.3.4` 로 보낸 요청의 throttle 키 IP = <관찰한 IP>. 위조 값 <무시됨/반영됨>.
```

- [ ] **Step 6: 커밋**

```bash
git add docs/superpowers/specs/2026-11-20-traefik-gateway-design.md compose.yaml
git commit -m "docs: Traefik 헬스 실패 · XFF 위조 실험 결과 기록

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
