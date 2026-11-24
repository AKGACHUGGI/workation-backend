"""질문 → 의도 (스펙 §4-1). 질문 임베딩 → 사전 파일(explore.vocab) 최근접, 못 쓰면 2글자 조각 매칭.

임베딩으로 찾은 결과만 30일 캐시한다. 키에 사전 파일 해시가 들어 있어 표현을 고치면 새로 찾는다.
문자열 매칭 결과는 캐시하지 않는다(CLOVA 가 잠깐 안 될 때 덜 정확한 답이 30일 남지 않게).
"""

from __future__ import annotations

import hashlib
import logging

from django.core.cache import cache

from core import clova
from explore import ask, vocab

logger = logging.getLogger(__name__)

TTL = 30 * 24 * 60 * 60
TOP_K = 5


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)}


def match_text(norm: str, vocab: list[tuple[str, str, bool]]) -> list[tuple[str, float]]:
    """정규형 질문과 2글자 조각이 가장 많이 겹치는 표현의 의도 [(의도, 1.0)]. 겹침 없음 → [].
    같으면 대표 질문(canonical) 먼저, 그다음 사전 순서."""
    mine = _bigrams(norm)
    best, best_key = None, (0, False)
    for intent, phrase, canonical in vocab:
        key = (len(mine & _bigrams(ask.normalize(phrase))), canonical)
        if key[0] and key > best_key:
            best, best_key = intent, key
    return [] if best is None else [(best, 1.0)]


def _allowed(scope: str) -> tuple[str, ...]:
    return ask.REGION_VOCAB if scope == "region" else ask.DONG_VOCAB


def _by_text(norm: str, allowed: tuple[str, ...]) -> tuple[list[str], float]:
    ranked = match_text(norm, [v for v in vocab.entries() if v[0] in allowed])
    return ask.pick_intents(ranked), ranked[0][1] if ranked else 0.0


def find_intents(question: str, scope: str = "dong") -> tuple[list[str], float]:
    """(고른 의도 1~2개, 1위 유사도). 문자열 매칭이면 유사도 1.0(맞음) 또는 0.0(없음).
    scope: dong(v7 사전) · region(시군구 의도 + 시간대 + 모르는 질문)."""
    norm = ask.normalize(question)
    allowed = _allowed(scope)
    key = f"explore:ask:q:{scope}:{hashlib.sha1(norm.encode()).hexdigest()}:{vocab.version()}"
    hit = cache.get(key)
    if hit is not None:
        return hit[0], hit[1]
    try:
        ranked = [
            (i, sim) for i, _, sim in vocab.nearest(clova.embed(question), TOP_K, intents=allowed)
        ]
    except clova.ClovaError as ex:
        logger.warning("질문 임베딩 실패 → 문자열 매칭: %s", ex)
        return _by_text(norm, allowed)
    intents, top = ask.pick_intents(ranked), ranked[0][1] if ranked else 0.0
    cache.set(key, [intents, top], TTL)
    return intents, top
