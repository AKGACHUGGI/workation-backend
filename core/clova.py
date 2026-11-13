"""CLOVA Studio 호출 (HyperCLOVA X 채팅 · 임베딩 v2).
키는 settings.CLOVA_API_KEY (.env 의 CLOVA_API_KEY).

임베딩 모델은 DB 의 tag_vocab · dong_doc 을 만든 모델(clova-embedding-v2)과 같아야 한다.
기본 한도가 분당 60회라 429 를 받으면 응답 헤더의 대기 시간만큼 한 번 기다렸다 다시 시도한다.
임베딩은 `emb:{모델}:{sha1(문장)}` 로 기한 없이 캐시한다 (홈 검색 · 동네에 물어보기 공용).
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid

from django.conf import settings
from django.core.cache import cache

BASE = "https://clovastudio.stream.ntruss.com"
CHAT_MODEL = "HCX-005"
EMBED_MODEL = "clova-embedding-v2"


class ClovaError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(getattr(settings, "CLOVA_API_KEY", ""))


def _post(path: str, payload: dict, timeout: float) -> dict:
    if not enabled():
        raise ClovaError("CLOVA_API_KEY 가 설정되지 않았습니다.")
    data = json.dumps(payload).encode()
    for attempt in range(2):
        req = urllib.request.Request(
            BASE + path,
            data=data,
            headers={
                "Authorization": f"Bearer {settings.CLOVA_API_KEY}",
                "Content-Type": "application/json",
                "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read())["result"]
        except urllib.error.HTTPError as ex:
            if ex.code == 429 and attempt == 0:
                reset = (ex.headers.get("x-ratelimit-reset-requests") or "2").rstrip("s")
                time.sleep(min(float(reset) if reset.replace(".", "", 1).isdigit() else 2, 5))
                continue
            raise ClovaError(f"CLOVA HTTP {ex.code}") from None
        except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as ex:
            raise ClovaError(f"CLOVA 호출 실패: {type(ex).__name__}") from None
    raise ClovaError("CLOVA 재시도 초과")


def embed(text: str) -> list[float]:
    """같은 문장 = 같은 벡터라 만료 없이 캐시 (Redis LRU 가 정리). 실패는 캐시하지 않는다."""
    key = f"emb:{EMBED_MODEL}:{hashlib.sha1(text.encode()).hexdigest()}"
    vec = cache.get(key)
    if vec is None:
        vec = _post("/v1/api-tools/embedding/v2", {"text": text}, timeout=10)["embedding"]
        cache.set(key, vec, None)
    return vec


def chat(
    system: str,
    user: str,
    max_tokens: int = 300,
    temperature: float = 0.0,
    timeout: float = 15,
) -> str:
    result = _post(
        f"/v3/chat-completions/{CHAT_MODEL}",
        {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "maxTokens": max_tokens,
            "temperature": temperature,
            "topP": 0.8,
        },
        timeout=timeout,
    )
    return result["message"]["content"]
