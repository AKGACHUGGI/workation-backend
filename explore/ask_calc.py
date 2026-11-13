"""질문 속 달·시각을 뽑아 코드가 미리 비교·계산한 사실 (F8-*). 순수 함수.

HCX-005 는 "1월과 3월은 2배 차이" 처럼 비교·계산을 자주 틀린다 (2026-11-13 실측).
그래서 계산은 여기서 하고, CLOVA 는 결론을 문장으로만 쓴다. 사용자 글에서 가져오는 건 숫자뿐이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from explore import brief
from explore.brief import Fact, josa

MAX_SLOTS = 3
SAME_PCT = 5  # 두 달 차이가 이 % 미만이면 "거의 비슷함"
MANY_PCT, FEW_PCT = 85, 50  # 하루 최대 대비 이 % 이상이면 많은 편, 이하면 적은 편

MONTH = re.compile(r"(1[0-2]|[1-9])\s*월")
HOUR = re.compile(r"(오전|아침|새벽|오후|낮|저녁|밤)?\s*(2[0-3]|1\d|\d)\s*시")
PM = ("오후", "저녁", "밤")


@dataclass(frozen=True)
class Slots:
    months: list[int] = field(default_factory=list)
    hours: list[int] = field(default_factory=list)


def _hour(prefix: str | None, h: int) -> int:
    if prefix in PM and h < 12:
        return h + 12
    if prefix is None and 1 <= h <= 6:  # "3시" 는 보통 오후
        return h + 12
    return h


def _unique(xs: list[int]) -> list[int]:
    out: list[int] = []
    for x in xs:
        if x not in out:
            out.append(x)
    return out[:MAX_SLOTS]


def extract_slots(question: str) -> Slots:
    months = _unique([int(m) for m in MONTH.findall(question)])
    hours = _unique([_hour(p or None, int(h)) for p, h in HOUR.findall(question)])
    return Slots(months, hours)


def _day(question: str) -> tuple[str, str]:
    return (
        ("weekend", "주말")
        if any(w in question for w in ("주말", "토요일", "일요일"))
        else (
            "weekday",
            "평일",
        )
    )


def _pct(a: float, b: float) -> int:
    return round(abs(a - b) / min(a, b) * 100)


def _month_values(raw: dict) -> dict[int, float]:
    return {m: v for m, v, _ in raw["rec"]["months"] if v is not None}


def _hour_values(raw: dict, day: str) -> dict[int, int]:
    driv = raw["rec"]["dong"].get(f"driv_{day}") or []
    return {h: v for h, v in enumerate(driv) if v is not None and v >= 0}


def _calc(raw: dict, question: str) -> list[tuple[Fact, str]]:
    """(사실, 규칙 문장) 목록. 데이터가 없는 달·시각은 건너뛴다."""
    slots = extract_slots(question)
    sgg = raw["rec"]["region"]["name"]
    out: list[tuple[Fact, str]] = []

    months = _month_values(raw)
    picked = [m for m in slots.months if m in months]
    if len(picked) >= 2:
        a, b = picked[:2]
        va, vb = months[a], months[b]
        pct = _pct(va, vb)
        head = f"{sgg} {a}월 {va:.1f} · {b}월 {vb:.1f} (클수록 붐빔): "
        values = brief._vals(a, b, f"{va:.1f}", f"{vb:.1f}", round(va), round(vb), pct)
        if pct < SAME_PCT:
            text = head + f"거의 비슷함 (차이 {pct}%)"
            rule = f"{josa(sgg)} {a}월({va:.1f})과 {b}월({vb:.1f})이 거의 비슷해요."
        else:
            more, less = (a, b) if va > vb else (b, a)
            text = head + f"{more}월이 {pct}% 더 붐빔"
            rule = f"{josa(sgg)} {more}월이 {less}월보다 {pct}% 더 붐벼요."
        out.append((Fact("F8-1", text, values, 1.0), rule))
    elif len(picked) == 1:
        m = picked[0]
        rank = sorted(months, key=lambda k: (-months[k], k)).index(m) + 1
        text = f"{sgg} {m}월 {months[m]:.1f}: 1년 중 {rank}번째로 붐비는 달"
        values = brief._vals(m, f"{months[m]:.1f}", round(months[m]), rank, 1, len(months))
        rule = f"{josa(sgg)} {m}월이 1년 중 {rank}번째로 붐비는 달이에요."
        out.append((Fact("F8-1", text, values, 1.0), rule))

    day, ko = _day(question)
    hours = _hour_values(raw, day)
    if hours:
        peak = max(hours, key=lambda h: (hours[h], -h))
        for i, h in enumerate((h for h in slots.hours if h in hours), start=2):
            v, pv = hours[h], hours[peak]
            pct = round(v / pv * 100)
            level = "많은 편" if pct >= MANY_PCT else "적은 편" if pct <= FEW_PCT else "보통"
            text = f"{ko} {h}시 차량 이동 인구 {v}명: 하루 최대({peak}시 {pv}명)의 {pct}%, {level}"
            values = brief._vals(h, v, peak, pv, pct)
            rule = f"{ko} {h}시 차량은 하루 가장 많은 {peak}시의 {pct}%로 {level}이에요."
            out.append((Fact(f"F8-{i}", text, values, 1.0), rule))
    return out


def computed_facts(raw: dict, question: str) -> list[Fact]:
    return [fact for fact, _ in _calc(raw, question)]


def computed_rules(raw: dict, question: str) -> list[str]:
    return [rule for _, rule in _calc(raw, question)]
