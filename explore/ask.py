"""동네에 물어보기 답 (v6.1, 스펙 §3·§4·§6). 순수 함수 — 채팅 함수는 인자로 받는다.

의도에 맞는 사실만 고르기 → 프롬프트(의도의 대표 질문만, 사용자 문장 없음) → 응답 파싱 →
문장마다 v6 검증 + 근거어 + 모양 검사 → 실패한 자리만 그 의도의 규칙 문장.
의도 찾기는 ask_match.py, 답 저장·캐시는 views.py 가 한다.
"""

from __future__ import annotations

import re

from explore import brief
from explore.brief import Fact

THRESHOLD = 0.68  # 1위 유사도가 이보다 낮으면 unknown_other (홈 검색과 같은 값에서 시작)

GAP = 0.05  # 2위 의도를 같이 고르는 유사도 차이

# 답할 수 있는 의도 (스펙 §3-1 표 순서 = 예시 질문 순서)
INTENTS = (
    "weekday_hours", "weekend_hours", "months", "quiet", "peer",
    "evening", "type", "coast", "life", "similar",
)  # fmt: skip

UNKNOWN = ("unknown_place", "unknown_stay", "unknown_transport", "unknown_other")

# CLOVA 에 넘기는 대표 질문. 사용자 문장 대신 이 문장만 프롬프트에 들어간다 (스펙 §4-3)
CANONICAL = {
    "weekday_hours": "평일엔 몇 시가 붐비고 몇 시가 한산해?",
    "weekend_hours": "주말엔 몇 시가 붐비고 몇 시가 한산해?",
    "months": "1년 중 언제 가면 한산해?",
    "quiet": "이 동네 조용해?",
    "peer": "또래 사람들이 많아?",
    "evening": "저녁에 활기 있어?",
    "type": "어떤 워케이션에 잘 맞아?",
    "coast": "바다가 가까워?",
    "life": "생활권이야, 관광지야?",
    "similar": "비슷한 동네는 어디야?",
}

_SEES = "이 지도는 동네의 붐빔과 분위기를 보고 있어서"

# 답할 수 없는 의도: (안내 문구, 예시 질문 3개). CLOVA 를 부르지 않는다 (스펙 §3-2)
GUIDE = {
    "unknown_place": (
        f"{_SEES} 식당·카페는 아직 몰라요. "
        "대신 점심·저녁에 이 동네가 얼마나 북적이는지는 알려 드릴 수 있어요.",
        [CANONICAL["weekday_hours"], CANONICAL["evening"], CANONICAL["quiet"]],
    ),
    "unknown_stay": (
        f"{_SEES} 숙소 정보는 아직 몰라요. "
        "대신 오래 머물기 맞는 동네인지, 언제 가면 한산한지는 알려 드릴 수 있어요.",
        [CANONICAL["type"], CANONICAL["months"], CANONICAL["similar"]],
    ),
    "unknown_transport": (
        f"{_SEES} 교통편은 아직 몰라요. "
        "대신 시간대별로 차가 얼마나 다니는지는 알려 드릴 수 있어요.",
        [CANONICAL["weekday_hours"], CANONICAL["weekend_hours"], CANONICAL["quiet"]],
    ),
    "unknown_other": (
        f"{_SEES} 그 질문엔 아직 답하기 어려워요. 이런 건 알려 드릴 수 있어요.",
        [CANONICAL["quiet"], CANONICAL["months"], CANONICAL["similar"]],
    ),
}

# 의도 → 쓰는 사실 id 앞부분 (스펙 §3-1 표)
INTENT_FACTS = {
    "weekday_hours": "F2-1",
    "weekend_hours": "F2-2",
    "months": "F3-1",
    "quiet": "F1-quiet",
    "peer": "F1-peer",
    "evening": "F1-evening",
    "type": "F4-",
    "coast": "F5-1",
    "life": "F7-1",
    "similar": "F6-",
}

NORMALIZE = re.compile(r"[\s?.!~]")


def normalize(q: str) -> str:
    """캐시 키·문자열 매칭용 정규형: 공백·?.!~ 제거 + 소문자."""
    return NORMALIZE.sub("", q).lower()


def pick_intents(ranked: list[tuple[str, float]]) -> list[str]:
    """[(의도, 유사도)] 유사도 내림차순 → 고른 의도 (스펙 §4-1). 최대 2개, 이름순."""
    if not ranked or ranked[0][1] < THRESHOLD:
        return ["unknown_other"]
    first, top = ranked[0]
    second = next(((i, s) for i, s in ranked if i != first), None)
    if (
        second is not None
        and second[1] >= THRESHOLD
        and round(top - second[1], 6) <= GAP  # 0.84 − 0.79 가 0.05000…4 가 되지 않게
        and first in INTENTS
        and second[0] in INTENTS
    ):
        return sorted([first, second[0]])
    return [first]


def life_fact(raw: dict) -> Fact | None:
    """F7-1 생활권/관광지 (스펙 §4-2). 브리핑(v6)에는 넣지 않는다."""
    me = raw["facts"][raw["rec"]["dong"]["code"]]
    if not me["life"] or me["life"][0].get("value") is None:
        return None
    life = me["life"][0]
    index = f"{life['value']:.2f}"
    return Fact(
        "F7-1",
        f"{life['name']} (생활권 지수 {index})",
        frozenset({index}),
        life["degree"] / raw["dong_count"],
    )


def facts_for(raw: dict, intents: list[str]) -> list[Fact]:
    """의도에 맞는 사실만. 의도 순서대로, 의도 안에서는 id 순."""
    facts = brief.collect_facts(raw)
    life = life_fact(raw)
    if life is not None:
        facts.append(life)
    out = []
    for intent in intents:
        prefix = INTENT_FACTS[intent]
        out += sorted((f for f in facts if f.id.startswith(prefix)), key=lambda f: f.id)
    return out


def examples_for(intents: list[str]) -> list[str]:
    """다음에 누를 예시 질문 3개. 안내면 §3-2 예시, 아니면 고른 의도를 뺀 §3-1 순서."""
    if intents[0] in GUIDE:
        return list(GUIDE[intents[0]][1])
    return [CANONICAL[i] for i in INTENTS if i not in intents][:3]
