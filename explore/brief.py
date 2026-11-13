"""동네 브리핑 3문장 (월 배치 ④, 스펙 §5-4). 순수 함수 — 채팅 함수는 인자로 받는다.

사실 모으기 → 프롬프트 → 응답 파싱 → 문장마다 코드 검증 → 실패한 자리만 규칙 문장.
사실 id(F1~F6)는 프롬프트·검증용 내부 값이고 화면에는 나가지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from core.clova import CHAT_MODEL, ClovaError
from explore import derive
from explore.graph import GRADES, METRIC_KO, METRICS

logger = logging.getLogger(__name__)

MAX_LEN = 60
RETRIES = 3  # 첫 호출이 실패하면 3번 더
COMMON_LATER = 0.3  # 이보다 흔한 사실은 목록 뒤로
BANNED = ("추천", "최고", "가장 좋", "최적", "강추", "핫플")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
GRADE = re.compile(r"(?<![A-Za-z])[SABC](?![A-Za-z])")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3})")

SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 브리핑 작성자다. 사용자가 준 사실 목록만 근거로 쓴다.",
        "- 정확히 3문장. 문장마다 60자 이내, 존댓말 해요체.",
        "- 1문장: 하루 흐름(시간대). 2문장: 1년 흐름(한산한 달).",
        "- 3문장: 이 동의 강점(유형·등급). 목록 앞쪽의 드문 사실을 하나 이상 쓴다.",
        "- 목록에 없는 숫자·달·시각·등급·장소 이름·추측을 쓰지 않는다.",
        "- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.",
        "- 문장마다 근거로 쓴 사실 id 를 facts 에 적는다.",
        '- JSON 하나만 출력한다: {"sentences": [{"text": "...", "facts": ["F2-1"]}, ...]}',
    ]
)


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    values: frozenset[str]  # 이 사실을 인용한 문장에 나와도 되는 숫자·등급
    common: float  # 같은 사실을 가진 동 비율 (0~1)


@dataclass(frozen=True)
class Brief:
    sentences: list[str]
    source: str  # llm · mixed · rule
    model: str | None


def _vals(*xs) -> frozenset[str]:
    return frozenset(str(x) for x in xs)


def _hours(rec: dict) -> dict[tuple[str, str], int]:
    return {(rel, day): h for rel, day, h in rec["hours"]}


def collect_facts(raw: dict) -> list[Fact]:
    """queries.fetch_dong 결과 → 사실 목록.
    드문 사실이 앞: 흔함 0.3 이하 먼저, 그 안에서 흔함 오름차순, 같으면 id 순."""
    rec, n = raw["rec"], raw["dong_count"]
    d, sgg = rec["dong"], rec["region"]["name"]
    me = raw["facts"][d["code"]]
    deg = {f"grade:{g['metric']}": g["degree"] for g in me["grades"]}
    deg |= {f"quiet:{q['day']}": q["degree"] for q in me["quiet"]}
    facts = []

    for metric, grade, score, rank, of, rank_sgg, of_sgg in rec["grades"]:
        ko = METRIC_KO[metric]
        facts.append(
            Fact(
                f"F1-{metric}",
                f"{ko} {grade}등급 {score}점, 전국 {of}동 중 {rank}위",
                _vals(grade, score, of, rank),
                deg[f"grade:{metric}"] / n,
            )
        )
        if rank_sgg == 1 and of_sgg > 1:
            facts.append(
                Fact(
                    f"F1-{metric}-sgg",
                    f"{sgg} {of_sgg}동 중 {ko} 1위",
                    _vals(1, of_sgg),
                    raw["region_count"] / n,
                )
            )

    hours = _hours(rec)
    for i, day in enumerate(("weekday", "weekend"), start=1):
        peak, quiet = hours.get(("PEAK_AT", day)), hours.get(("QUIET_AT", day))
        if peak is None or quiet is None:
            continue
        driv = d[f"driv_{day}"]
        if day == "weekday":
            text = (
                f"평일 차량 이동 인구: {peak}시 {driv[peak]}명으로 가장 많고, "
                f"업무시간(9~17시) 중 {quiet}시 {driv[quiet]}명으로 가장 적음"
            )
            values = _vals(peak, quiet, driv[peak], driv[quiet], 9, 17)
        else:
            text = (
                f"주말 차량 이동 인구: {peak}시 {driv[peak]}명으로 가장 많고, "
                f"{quiet}시 {driv[quiet]}명으로 가장 적음"
            )
            values = _vals(peak, quiet, driv[peak], driv[quiet])
        facts.append(Fact(f"F2-{i}", text, values, deg[f"quiet:{day}"] / n))

    month_raw = {m: v for m, v, _ in rec["months"] if v is not None}
    quiet_m = sorted(m for m, _, q in rec["months"] if q)
    busiest = derive.busiest_month(month_raw)
    if quiet_m and busiest is not None:
        facts.append(
            Fact(
                "F3-1",
                f"{sgg} 월별 방문: 가장 한산한 달 {'·'.join(map(str, quiet_m))}월, "
                f"가장 붐비는 달 {busiest}월",
                _vals(*quiet_m, busiest),
                me["region"]["degree"] / n,
            )
        )

    top_degree = me["top"][0]["degree"] if me["top"] else n
    for rank, name, score, _ in sorted(rec["types"]):
        common = top_degree / n if rank == 1 else 1.0  # 1위가 아닌 유형은 강점이 아니므로 뒤로
        facts.append(
            Fact(f"F4-{rank}", f"{name} {score}점 (유형 {rank}위)", _vals(score, rank), common)
        )

    if rec["near"]:
        km = f"{rec['near'][0]:.1f}"
        facts.append(
            Fact("F5-1", f"바닷가 (해안선까지 {km}km)", _vals(km), me["near"][0]["degree"] / n)
        )

    for rank, _, name, sigungu in sorted(rec["similar"]):
        facts.append(Fact(f"F6-{rank}", f"비슷한 동 {rank}: {sigungu} {name}", frozenset(), 1.0))

    return sorted(facts, key=lambda f: (f.common > COMMON_LATER, f.common, f.id))


def facts_hash(facts: list[Fact]) -> str:
    """사실 목록이 같으면 같은 값. 같으면 브리핑을 다시 만들지 않는다."""
    body = [[f.id, f.text, sorted(f.values)] for f in facts]
    return hashlib.sha256(json.dumps(body, ensure_ascii=False).encode()).hexdigest()


def validate_sentence(text: str, cited: list[str], facts: dict[str, Fact]) -> list[str]:
    """문제 목록. 비어 있으면 통과."""
    problems = []
    if len(text) > MAX_LEN:
        problems.append(f"{MAX_LEN}자 초과")
    unknown = [c for c in cited if c not in facts]
    if not cited or unknown:
        problems.append(f"인용 id 없음: {unknown or '빈 목록'}")
    banned = [w for w in BANNED if w in text]
    if banned:
        problems.append(f"금지어: {banned}")
    allowed = set().union(*(facts[c].values for c in cited if c in facts))
    plain = THOUSANDS.sub("", text)
    stray = [x for x in NUMBER.findall(plain) + GRADE.findall(plain) if x not in allowed]
    if stray:
        problems.append(f"사실에 없는 값: {stray}")
    return problems


def josa(word: str, pair: str = "은/는") -> str:
    """받침이 있으면 앞, 없으면 뒤. josa('강릉시') → '강릉시는'."""
    with_final, without = pair.split("/")
    code = ord(word[-1]) - 0xAC00
    has_final = 0 <= code <= 11171 and code % 28 != 0
    return word + (with_final if has_final else without)


def rule_sentences(raw: dict, facts: list[Fact]) -> list[str]:
    """검증이 필요 없는 규칙 문장 3개 (스펙 §5-4 5번)."""
    rec = raw["rec"]
    sgg = rec["region"]["name"]
    hours = _hours(rec)
    peak, quiet = hours.get(("PEAK_AT", "weekday")), hours.get(("QUIET_AT", "weekday"))
    if peak is None or quiet is None:
        first = "평일 시간대별 차량 자료가 아직 없어요."
    else:
        first = f"평일 차량은 {peak}시에 가장 많고, 업무시간엔 {quiet}시가 가장 한산해요."

    month_raw = {m: v for m, v, _ in rec["months"] if v is not None}
    quiet_m = sorted(m for m, _, q in rec["months"] if q)
    busiest = derive.busiest_month(month_raw)
    if not quiet_m or busiest is None:
        second = f"{josa(sgg)} 월별 방문 자료가 아직 없어요."
    else:
        months = "·".join(map(str, quiet_m))
        second = f"{josa(sgg)} {months}월이 가장 한산하고 {busiest}월이 가장 붐벼요."

    _, top_name, top_score, _ = sorted(rec["types"])[0]
    sgg_first = [f.id.split("-")[1] for f in facts if f.id.endswith("-sgg")]
    grades = {g[0]: g for g in rec["grades"]}
    if sgg_first:
        metric = min(sgg_first, key=METRICS.index)
        third = f"{top_name} {top_score}점이고, {sgg} 안 {METRIC_KO[metric]} 1위예요."
    elif grades:
        best = min(grades.values(), key=lambda g: (GRADES.index(g[1]), -g[2], METRICS.index(g[0])))
        third = f"{top_name} {top_score}점으로, {METRIC_KO[best[0]]} {best[1]}({best[2]}점)이에요."
    else:
        third = f"{top_name} {top_score}점이에요."
    return [first, second, third]


def build_prompt(raw: dict, facts: list[Fact]) -> tuple[str, str]:
    rec = raw["rec"]
    lines = "\n".join(f"{f.id}: {f.text}" for f in facts)
    where = f"{rec['sido']} {rec['region']['name']} {rec['dong']['name']}"
    return SYSTEM, f"동네: {where}\n사실 (드문 것부터):\n{lines}"


def parse_reply(reply: str, sizes: range = range(3, 4)) -> list[tuple[str, list[str]]]:
    """{"sentences": [{"text", "facts"}] × 3} → [(문장, 사실 id 목록)]. 모양이 다르면 ValueError.
    sizes = 받는 문장 수 (브리핑 3, 질문 답 1~3)."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON 없음")
    try:
        items = json.loads(reply[start : end + 1])["sentences"]
        out = [(str(i["text"]).strip(), [str(x) for x in i["facts"]]) for i in items]
    except (json.JSONDecodeError, KeyError, TypeError) as ex:
        raise ValueError(f"JSON 모양 다름: {ex}") from ex
    if len(out) not in sizes:
        raise ValueError(f"문장 수 {len(out)}")
    return out


def compose(
    items: list[tuple[str, list[str]]], facts: list[Fact], rules: list[str]
) -> tuple[list[str], str]:
    """검증을 통과한 생성 문장은 그대로, 실패한 자리만 규칙 문장. (문장 3개, source)"""
    by_id = {f.id: f for f in facts}
    out, passed = [], 0
    for (text, cited), rule in zip(items, rules, strict=True):
        problems = validate_sentence(text, cited, by_id)
        if problems:
            logger.info("브리핑 문장 교체 %r: %s", text, problems)
            out.append(rule)
        else:
            out.append(text)
            passed += 1
    source = "llm" if passed == 3 else "rule" if passed == 0 else "mixed"
    return out, source


def generate(raw: dict, facts: list[Fact], chat: Callable[[str, str], str] | None) -> Brief:
    """chat(system, user) → 응답 문자열. None 이면(키 없음) 규칙 문장만."""
    rules = rule_sentences(raw, facts)
    if chat is None:
        return Brief(rules, "rule", None)
    system, user = build_prompt(raw, facts)
    reply = None
    for attempt in range(1 + RETRIES):
        try:
            reply = chat(system, user)
            break
        except ClovaError as ex:
            logger.warning("브리핑 생성 실패 (%d번째): %s", attempt + 1, ex)
    if reply is None:
        return Brief(rules, "rule", None)
    try:
        items = parse_reply(reply)
    except ValueError as ex:
        logger.warning("브리핑 응답 파싱 실패: %s", ex)
        return Brief(rules, "rule", None)
    sentences, source = compose(items, facts, rules)
    return Brief(sentences, source, None if source == "rule" else CHAT_MODEL)
