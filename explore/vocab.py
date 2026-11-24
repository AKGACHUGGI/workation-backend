"""질문 의도 사전 (explore/data/ask_vocab.json). 처음 쓸 때 한 번 읽어 메모리에 둔다.

파일은 db/tools/build_embeddings.py ask 가 db/seed/ask_vocab.csv 에서 만든다.
의도 목록과 같이 바뀌는 설정값이라 저장소에 둔다.
벡터는 읽을 때 길이 1로 맞춰 두고, 최근접은 내적(코사인)으로 찾는다.
"""

from __future__ import annotations

import functools
import hashlib
import json
import math
import operator
from dataclasses import dataclass
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "ask_vocab.json"


def _unit(v: list[float]) -> tuple[float, ...]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / n for x in v)


@dataclass(frozen=True)
class Vocab:
    rows: tuple[tuple[str, str, bool], ...]  # (intent, phrase, canonical), 파일 순서
    units: tuple[tuple[float, ...], ...]  # rows 와 같은 순서의 정규화 벡터
    version: str  # 파일 내용 해시. 캐시 키에 쓴다

    @classmethod
    def load(cls, path: Path) -> Vocab:
        raw = path.read_bytes()
        items = json.loads(raw)["items"]
        return cls(
            rows=tuple((r["intent"], r["phrase"], r["canonical"]) for r in items),
            units=tuple(_unit(r["embedding"]) for r in items),
            version=hashlib.sha1(raw).hexdigest()[:12],
        )

    def nearest(
        self, vec: list[float], k: int = 5, intents: tuple[str, ...] | None = None
    ) -> list[tuple[str, str, float]]:
        """질문 임베딩에 가까운 표현 k개 (intent, phrase, 코사인 유사도), 가까운 순.
        intents 가 있으면 그 의도 표현만 본다(동 채팅 · 시군구 채팅 사전 범위)."""
        q = _unit(vec)
        scored = [
            (intent, phrase, sum(map(operator.mul, u, q)))
            for (intent, phrase, _), u in zip(self.rows, self.units, strict=True)
            if intents is None or intent in intents
        ]
        scored.sort(key=lambda r: r[2], reverse=True)
        return scored[:k]

    def entries(self) -> list[tuple[str, str, bool]]:
        """문자열 매칭용 사전 전체 (intent, phrase, canonical)."""
        return list(self.rows)


@functools.cache
def get() -> Vocab:
    return Vocab.load(PATH)


def nearest(
    vec: list[float], k: int = 5, intents: tuple[str, ...] | None = None
) -> list[tuple[str, str, float]]:
    return get().nearest(vec, k, intents)


def entries() -> list[tuple[str, str, bool]]:
    return get().entries()


def version() -> str:
    return get().version
