"""운영·개발 설정. 값은 환경변수(.env)에서 읽는다. 변수 목록은 .env.example."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DJANGO_DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Django 기본 앱(admin·auth·sessions)은 로그인(W-07) 작업 때 정한다.
# Supabase 에 migrate 로 테이블이 생기지 않게 지금은 넣지 않는다.
INSTALLED_APPS = [
    "corsheaders",
    "rest_framework",
    "core",
    "regions",
    "metrics",
    "search",
    "webapi",
    "explore",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    # 200바이트 이상 JSON 을 gzip (Accept-Encoding: gzip 일 때). 탐색 동 상세 응답이 크다
    "django.middleware.gzip.GZipMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

# 웹(Vite 개발 서버) 에서 API 를 부르기 위한 CORS. 배포 도메인은 .env 로 더한다.
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS", default=["http://localhost:5173", "http://127.0.0.1:5173"]
)

# 자연어 검색(HyperCLOVA X · CLOVA 임베딩). 비어 있으면 규칙 파서 · 문자열 매칭으로 동작한다.
CLOVA_API_KEY = env("CLOVA_API_KEY", default="")

# 탐색 그래프 (Neo4j, Postgres 의 읽기 전용 복사본). 비어 있으면 /api/explore/* 가 503
NEO4J_URI = env("NEO4J_URI", default="")
NEO4J_USER = env("NEO4J_USER", default="neo4j")
NEO4J_PASSWORD = env("NEO4J_PASSWORD", default="")

# 캐시. REDIS_URL 이 있으면 Redis(홈 검색·탐색·throttle 이 같이 씀), 없으면 프로세스 메모리.
# Redis 가 죽으면 캐시 없이 진행한다 (core/cache.py). 키 접두어: emb: · explore: · throttle_
REDIS_URL = env("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "core.cache.SafeRedisCache",
            "LOCATION": REDIS_URL,
            "KEY_PREFIX": "workation",
            # Redis 가 응답하지 않을 때 요청을 오래 붙잡지 않는다
            "OPTIONS": {"socket_connect_timeout": 1, "socket_timeout": 1},
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "KEY_PREFIX": "workation",
        }
    }

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# Supabase Session pooler(5432). 비밀번호에 기호가 있어도 되도록 URL 이 아니라 항목별로 받는다.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
        "NAME": env("DB_NAME", default="postgres"),
        "USER": env("DB_USER", default="postgres"),
        "PASSWORD": env("DB_PASSWORD", default=""),
        "OPTIONS": {"sslmode": env("DB_SSLMODE", default="prefer")},
        # Supabase Session pooler 는 동시 연결 15개까지. runserver 는 요청마다 새 스레드라
        # 연결을 붙잡아 두면 금방 한도를 넘으므로 개발(DEBUG)에서는 요청마다 닫는다.
        # Gunicorn(2 × 4 스레드)은 재사용이 이득
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=0 if DEBUG else 60),
        "CONN_HEALTH_CHECKS": True,
    }
}

LANGUAGE_CODE = "ko-kr"
TIME_ZONE = "Asia/Seoul"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# 인증 앱이 없으므로 DRF 가 django.contrib.auth 를 찾지 않게 한다.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "UNAUTHENTICATED_USER": None,
    # 동네에 물어보기 IP 당 한도 (explore ask 뷰의 throttle scope). 카운터는 위 CACHES
    "DEFAULT_THROTTLE_RATES": {"explore_ask_burst": "10/min", "explore_ask_day": "200/day"},
    # 프록시 뒤에서 X-Forwarded-For 의 몇 번째를 IP 로 볼지. Traefik 뒤면 1, 없으면 REMOTE_ADDR
    "NUM_PROXIES": env.int("DRF_NUM_PROXIES", default=None),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
