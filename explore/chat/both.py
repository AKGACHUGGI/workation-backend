"""기준이 둘인 답 (시군구 채팅 스펙 §7). 규칙 문장만 — 숫자를 정확히 넣어야 해서 LLM 을 쓰지 않는다.

시간대: 차량은 그래프 PEAK_AT(연간), 보행은 동 상세 곡선과 같은 webapi.data.walk_peaks(그 달).
달: 지도 탐색은 그래프 EASE(한산한 달), 방문은 지역 상세 bestMonths 와 같은 값.
"""

from __future__ import annotations

from explore import ask, brief
from explore.brief import josa

DAY_KO = {"weekday": "평일", "weekend": "주말"}
CAR = "차량: 연간 · 지도 탐색 그래프"


def _rule(text: str, evidence: str | None = None) -> dict:
    return {"text": text, "evidence": evidence, "source": "rule"}


def _walk_evidence(label: str) -> str:
    return f"보행: {label} · 24시간 붐빔 곡선과 같은 값"


def _walk(w: dict) -> str:
    """보행 붐빔 · 한산 마디. 낮(9~18시) 차이가 작으면(flat) 한산 시각을 말하지 않는다."""
    if w.get("flat"):
        return f"{w['peakHour']}시에 가장 붐비고, 낮(9~18시)엔 내내 비슷하게 붐벼요."
    return f"{w['peakHour']}시에 가장 붐비고 {w['lowHour']}시에 가장 한산해요."


def hours_both(items: list[tuple[dict, dict | None]], day: str, label: str | None) -> list[dict]:
    """동마다 한 문장. label 이 없으면(요청에 달이 없음) 차량 기준만."""
    ko = DAY_KO[day]
    out = []
    for raw, walk in items:
        rec = raw["rec"]
        name = josa(rec["dong"]["name"])
        car = brief._hours(rec).get(("PEAK_AT", day))
        w = walk.get(day) if walk is not None and label else None
        if car is not None and w is not None:
            text = (
                f"{name} {ko} 차량이 {car}시에 가장 많고, "
                f"걸어 다니는 사람은 {label} 기준 {_walk(w)}"
            )
            out.append(_rule(text, f"{CAR} / {_walk_evidence(label)}"))
        elif car is not None:
            out.append(_rule(f"{name} {ko} 차량이 {car}시에 가장 많아요.", CAR))
        elif w is not None:
            text = f"{name} {ko}에 걸어 다니는 사람이 {label} 기준 {_walk(w)}"
            out.append(_rule(text, _walk_evidence(label)))
    return out or [_rule(ask.NO_DATA)]


def _months(ms: list[int]) -> str:
    return "·".join(map(str, ms))


def months_both(raw: dict, best: list[int]) -> list[dict]:
    """시군구 한산한 달(지도 탐색)과 가기 좋은 달(방문). 같거나 한쪽만 있으면 한 기준만 말한다."""
    sgg = raw["region"]["name"]
    quiet = sorted(m for m, _, q in raw["months"] if q)
    if not quiet and not best:
        return [_rule(ask.NO_DATA)]
    if not best or quiet == best:
        text = f"{josa(sgg)} {_months(quiet)}월이 가장 한산해요."
        return [_rule(text, "지도 탐색: 연간 한산한 달")]
    if not quiet:
        return [
            _rule(
                f"{josa(sgg)} {_months(best)}월이 가기 좋은 달이에요.",
                "방문: 지역 상세 '가기 좋은 달'과 같은 값",
            )
        ]
    text = (
        f"지도 탐색 기준으로는 {_months(quiet)}월이 한산하고, "
        f"방문 기준 가기 좋은 달은 {_months(best)}월이에요."
    )
    return [_rule(text, "지도 탐색: 연간 한산한 달 / 방문: 지역 상세 '가기 좋은 달'과 같은 값")]
