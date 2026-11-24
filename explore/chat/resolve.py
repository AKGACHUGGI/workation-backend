"""질문 속 동 이름 → 이번 턴 비교 동네 (스펙 §4). 순수 함수.

기준은 늘 패널 동이다. 사전은 탐색 대상 동 전체이고, 같은 시군구 동만 비교에 넣는다.
다른 시군구 동 이름이면 비교 대신 안내 문구를 돌려준다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_DONGS = 3
TOGETHER = ("둘 다", "셋 다", "세 곳", "세 동네", "다 같이")
DIGIT_NAME = re.compile(r"(\D+)(\d+)(동|읍|면)$")
SUFFIX = ("동", "읍", "면")

ASK_DONG = "시군구 말고 동네 이름으로 물어봐 주세요."
TOO_MANY = "한 번에 세 동네까지 비교할 수 있어서 앞의 세 곳만 볼게요."
SAME_SGG_ONLY = "지금은 같은 시군구 안의 동네끼리만 비교할 수 있어요."
# 시군구 채팅에서 동 대화 중이어도 '찾기'로 보는 말 (시군구 채팅 스펙 §4)
FIND_WORDS = ("동네", "어디", "어느", "데는", "곳은", "곳 ")


@dataclass(frozen=True)
class DongName:
    code: str
    name: str
    sgg_code: str
    sgg_name: str


@dataclass(frozen=True)
class Resolved:
    dongs: list[str]  # 패널 동이 맨 앞
    guide: str | None = None  # 있으면 답 대신 이 안내
    note: str | None = None  # 네 곳 이상 → 앞 세 곳만 썼다는 안내
    named: bool = False  # 질문에 패널 말고 같은 시군구 동 이름이 있었다


def aliases(name: str) -> set[str]:
    """'효자2동' → 띄어 쓴 '효자 2동'. '주문진읍' → '주문진'. 두 글자 이름은 줄이지 않는다."""
    out = {name}
    m = DIGIT_NAME.match(name)
    if m:
        out.add(f"{m[1]} {m[2]}{m[3]}")
    elif name.endswith(SUFFIX) and len(name) >= 3:
        out.add(name[:-1])
    return out


def find(question: str, names: list[DongName], sgg_code: str) -> list[DongName]:
    """질문에 나온 동을 나온 순서대로. 긴 이름 먼저 맞추고(송천1동 안의 '송천'을 따로 잡지 않게),
    같은 길이면 패널 시군구 동 먼저(다른 시군구의 같은 이름보다)."""
    pairs = sorted(
        ((alias, n) for n in names for alias in aliases(n.name)),
        key=lambda p: (-len(p[0]), p[1].sgg_code != sgg_code),
    )
    taken: list[tuple[int, int]] = []
    hits: dict[str, tuple[int, DongName]] = {}
    for alias, n in pairs:
        start = question.find(alias)
        while start >= 0:
            end = start + len(alias)
            if not any(s < end and start < e for s, e in taken):
                taken.append((start, end))
                if n.code not in hits or start < hits[n.code][0]:
                    hits[n.code] = (start, n)
            start = question.find(alias, start + 1)
    return [n for _, n in sorted(hits.values(), key=lambda h: h[0])]


def _other_sigungu(question: str, panel: DongName, names: list[DongName]) -> bool:
    for n in names:
        if n.sgg_code == panel.sgg_code:
            continue
        stem = n.sgg_name[:-1] if len(n.sgg_name) >= 3 else n.sgg_name
        if stem in question:
            return True
    return False


def _same_sgg_guide(panel: DongName, names: list[DongName]) -> str:
    near = sorted(n.name for n in names if n.sgg_code == panel.sgg_code and n.code != panel.code)
    if not near:
        return SAME_SGG_ONLY
    return f"{SAME_SGG_ONLY} {panel.sgg_name}에서는 {', '.join(near[:3])} 등과 비교해 볼 수 있어요."


def resolve(question: str, panel: DongName, prev: list[str], names: list[DongName]) -> Resolved:
    keep = prev if prev[:1] == [panel.code] else [panel.code]
    found = find(question, names, panel.sgg_code)
    mine = [n.code for n in found if n.sgg_code == panel.sgg_code and n.code != panel.code]
    if not mine:
        if any(n.sgg_code != panel.sgg_code for n in found):
            return Resolved(keep, guide=_same_sgg_guide(panel, names))
        if found:  # 패널 동 이름만 → 그 동 하나로 돌아간다
            return Resolved([panel.code])
        if _other_sigungu(question, panel, names):
            return Resolved(keep, guide=ASK_DONG)
        return Resolved(keep)
    base = keep[1:] if any(w in question for w in TOGETHER) else []
    picked = [panel.code, *dict.fromkeys([*base, *mine])]
    if len(picked) > MAX_DONGS:
        return Resolved(picked[:MAX_DONGS], note=TOO_MANY, named=True)
    return Resolved(picked, named=True)


def resolve_region(
    question: str, sgg_code: str, prev: list[str], names: list[DongName], follow: bool
) -> Resolved:
    """시군구 채팅 (시군구 채팅 스펙 §4). 기준 동 없이 그 시군구 동 이름을 찾는다.
    dongs 가 비면 시군구 답이다. 이름이 없을 때:
    - '셋 다'·'둘 다' 류면 직전 동들로 비교
    - 직전 턴이 동 답(follow)이고 찾기 말이 없으면("저녁엔?") 그 동을 잇는다
    - 아니면 시군구 답."""
    sgg = next((n for n in names if n.sgg_code == sgg_code), None)
    anchor = DongName("", "", sgg_code, sgg.sgg_name if sgg else "")
    found = find(question, names, sgg_code)
    mine = [n.code for n in found if n.sgg_code == sgg_code]
    if not mine:
        if found or _other_sigungu(question, anchor, names):
            return Resolved(prev, guide=_same_sgg_guide(anchor, names))
        if prev and any(w in question for w in TOGETHER):
            return Resolved(prev[:MAX_DONGS], named=True)
        if follow and prev and not any(w in question for w in FIND_WORDS):
            return Resolved(prev)
        return Resolved([])
    base = prev if any(w in question for w in TOGETHER) else []
    picked = list(dict.fromkeys([*mine, *base]))
    if len(picked) > MAX_DONGS:
        return Resolved(picked[:MAX_DONGS], note=TOO_MANY, named=True)
    return Resolved(picked, named=True)
