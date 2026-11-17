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
OFF_DATA = ("소음",)  # 지도에 없는 지표. '162위로 낮은 소음도' 같은 말이 붙지 않게
# 등급 자리. 순위만 주면 LLM 이 '162위'를 좋은 쪽으로 읽는다 (2026-11-17 승주읍 C등급이 '조용한 편')
GRADE_POS = {
    "S": "4개 등급 중 가장 높음",
    "A": "4개 등급 중 두 번째",
    "B": "4개 등급 중 세 번째",
    "C": "4개 등급 중 가장 낮음",
}
TOP_IS = {"quiet": "가장 조용함", "peer": "또래가 가장 많음", "evening": "저녁 활기가 가장 큼"}
# 등급마다 결론 하나 (에어코리아 좋음·보통·나쁨처럼 등급 ↔ 라벨 1:1). 판단은 코드가 하고
# LLM 은 이 문구를 옮기기만 한다. 사실 문구 끝과 규칙 문장에 그대로 쓴다
LABEL = {
    "quiet": {"S": "아주 조용한 편", "A": "조용한 편", "B": "보통 수준", "C": "조용하지 않은 편"},
    "peer": {
        "S": "또래가 아주 많은 편",
        "A": "또래가 많은 편",
        "B": "보통 수준",
        "C": "또래가 적은 편",
    },
    "evening": {
        "S": "저녁에 아주 활기 있는 편",
        "A": "저녁에 활기 있는 편",
        "B": "보통 수준",
        "C": "저녁 활기가 적은 편",
    },
}
# 브리핑 문장이 F1 을 인용하면 이 표현 중 하나가 있어야 통과한다 (등급 → 높음·보통·낮음).
# 라벨을 그대로 옮기라고 해도 HCX 는 '또래 밀도가 높은 편'처럼 바꿔 쓴다
# (2026-11-17 실측 25답 중 24답 탈락)
SAYS = {
    ("quiet", "high"): ("조용한 편",),
    ("quiet", "mid"): ("보통",),
    ("quiet", "low"): ("조용하지 않은 편", "조용한 편은 아니", "조용한 편이 아니"),
    ("peer", "high"): ("또래가 많은 편", "또래 밀도가 높은 편"),
    ("peer", "mid"): ("보통",),
    ("peer", "low"): ("또래가 적은 편", "또래 밀도가 낮은 편"),
    ("evening", "high"): (
        "활기 있는 편",
        "활기가 있는 편",
        "활기가 많은 편",
        "활기가 높은 편",
        "활기찬 편",
    ),
    ("evening", "mid"): ("보통",),
    ("evening", "low"): ("활기가 적은 편", "활기가 낮은 편"),
}
LEVEL = {"S": "high", "A": "high", "B": "mid", "C": "low"}
# 표현 바로 뒤가 이러면 뜻이 뒤집힌다: '조용한 편은 아니에요' · '보통보다 시끄러워요'
FLIPPED = re.compile(r"^\s*(?:은|는|이|가)?\s*아니|^보다")
# 표현 앞(이유 자리)의 말은 인용한 사실 문구에 있어야 한다.
# '야경 명소가 많아서' 같은 지어낸 이유를 막는다
WORD = re.compile(r"[가-힣A-Za-z0-9.]+")
JOSA = re.compile(
    r"(?:이라서|라서|이어서|여서|이라|라|이고|이며|이지만|지만|이니|으로|로|에서|에선|에는|엔|"
    r"은|는|이|가|을|를|의|에|도|만|과|와)$"
)
FILLER = {
    "전반적으로", "대체로", "비교적", "꽤", "다소", "조금", "아주", "약간", "그래서", "따라서",
    "점수", "순위", "이", "이곳", "여기", "동네", "지역", "이는", "참고로",
}  # fmt: skip
PLACE = re.compile(r"[가-힣0-9]{2,}[읍면동시군구도]$")  # 중화산2동 · 제주특별자치도
BECAUSE = ("때문", "덕분", "해서", "라서", "니까")


def label(metric: str, grade: str) -> str:
    return LABEL[metric][grade]


def _stray_words(part: str, source: str) -> list[str]:
    """part 의 말 중 사실 문구(source)에 없는 것. 조사를 떼고 보고, 꾸밈말·지명은 받는다."""
    out = []
    for w in WORD.findall(part):
        stem = JOSA.sub("", w)
        if w in FILLER or stem in FILLER:
            continue
        if PLACE.fullmatch(w) or PLACE.fullmatch(stem):  # '…자치도'의 '도'는 조사가 아니다
            continue
        if stem and stem not in source:
            out.append(w)
    return out


def _grade_problems(text: str, fact: Fact, source: str) -> list[str]:
    """F1 사실을 인용한 문장: 등급 쪽 표현이 있고, 뒤집히지 않고, 이유가 사실 안의 말인가."""
    metric, grade = fact.id.removeprefix("F1-"), GRADE_OF.search(fact.text).group(1)
    for said in SAYS[(metric, LEVEL[grade])]:
        if said not in text:
            continue
        before, after = text.split(said, 1)
        if FLIPPED.match(after):
            continue
        stray = _stray_words(before, source)
        if any(b in after for b in BECAUSE):
            stray += _stray_words(after, source)
        return [f"사실에 없는 이유: {stray}"] if stray else []
    return [f"결론 문구 없음: {fact.id}"]


NUMBER = re.compile(r"\d+(?:\.\d+)?")
GRADE = re.compile(r"(?<![A-Za-z])[SABC](?![A-Za-z])")
GRADE_OF = re.compile(r"([SABC])등급")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3})")

F1_RULE = (
    "- 등급 사실(F1)을 쓸 때는 → 뒤 결론을 고치지 말고 그대로 쓴다. "
    "이유는 그 사실의 등급·점수·순위만 쓴다."
)

SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 브리핑 작성자다. 사용자가 준 사실 목록만 근거로 쓴다.",
        "- 정확히 3문장. 문장마다 60자 이내, 존댓말 해요체.",
        "- 1문장: 하루 흐름(시간대). 2문장: 1년 흐름(한산한 달).",
        "- 3문장: 이 동의 강점(유형·등급). 목록 앞쪽의 드문 사실을 하나 이상 쓴다.",
        "- 목록에 없는 숫자·달·시각·등급·장소 이름·추측을 쓰지 않는다.",
        F1_RULE,
        "- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.",
        "- 문장마다 근거로 쓴 사실 id 를 facts 에 적는다.",
        '- JSON 하나만 출력한다: {"sentences": [{"text": "...", "facts": ["F2-1"]}, ...]}',
    ]
)


# HCX-005 는 system 의 형식 지시를 자주 무시한다 → user 끝에 한 번 더 (2026-11-13 실측)
FORMAT_TAIL = (
    "\n\n위 사실만으로 존댓말 해요체로 쓰고, 반드시 아래 JSON 하나만 출력해. 다른 글은 쓰지 마.\n"
    '{"sentences": [{"text": "문장", "facts": ["사실 id"]}]}'
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
                f"{ko} {grade}등급 {score}점 ({GRADE_POS[grade]}), "
                f"전국 {of}동 중 {rank}위 (1위가 {TOP_IS[metric]}) → {label(metric, grade)}",
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
        if rank == 1 and score >= derive.TYPE_MIN:
            facts.append(
                Fact("F4-1", f"{name} {score}점 (유형 1위)", _vals(score, rank), top_degree / n)
            )
        elif rank == 1:
            # 1위가 기준 미만이면 대표 유형이 아니다 → 강점이 아니므로 뒤로, 문구에도 밝힌다
            text = f"{name} {score}점 (유형 1위, 기준 {derive.TYPE_MIN}점 미만)"
            facts.append(Fact("F4-1", text, _vals(score, rank, derive.TYPE_MIN), 1.0))
        else:
            # 1위가 아닌 유형은 강점이 아니므로 뒤로
            facts.append(
                Fact(f"F4-{rank}", f"{name} {score}점 (유형 {rank}위)", _vals(score, rank), 1.0)
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
    off = [w for w in OFF_DATA if w in text]
    if off:
        problems.append(f"데이터에 없는 말: {off}")
    source = " ".join(facts[c].text for c in cited if c in facts)
    for c in cited:
        if c.removeprefix("F1-") in LABEL and c in facts:
            problems += _grade_problems(text, facts[c], source)
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
    has_type = top_score >= derive.TYPE_MIN  # 대표 유형이 없으면 유형 이름을 쓰지 않는다
    sgg_first = [f.id.split("-")[1] for f in facts if f.id.endswith("-sgg")]
    grades = {g[0]: g for g in rec["grades"]}
    if sgg_first:
        ko = METRIC_KO[min(sgg_first, key=METRICS.index)]
        third = (
            f"{top_name} {top_score}점이고, {sgg} 안 {ko} 1위예요."
            if has_type
            else f"{sgg} 안 {ko} 1위인 동네예요."
        )
    elif grades:
        best = min(grades.values(), key=lambda g: (GRADES.index(g[1]), -g[2], METRICS.index(g[0])))
        grade = f"{METRIC_KO[best[0]]} {best[1]}({best[2]}점)"
        third = (
            f"{top_name} {top_score}점으로, {grade}이에요."
            if has_type
            else f"{grade}이 이 동네의 가장 큰 특징이에요."
        )
    else:
        third = (
            f"{top_name} {top_score}점이에요."
            if has_type
            else "뚜렷하게 맞는 워케이션 유형은 없어요."
        )
    return [first, second, third]


def build_prompt(raw: dict, facts: list[Fact]) -> tuple[str, str]:
    rec = raw["rec"]
    lines = "\n".join(f"{f.id}: {f.text}" for f in facts)
    where = f"{rec['sido']} {rec['region']['name']} {rec['dong']['name']}"
    return SYSTEM, f"동네: {where}\n사실 (드문 것부터):\n{lines}{FORMAT_TAIL}"


def parse_reply(reply: str, sizes: range = range(3, 4)) -> list[tuple[str, list[str]]]:
    """{"sentences": [{"text", "facts"}] × 3} → [(문장, 사실 id 목록)]. 모양이 다르면 ValueError.
    sizes = 받는 문장 수 (브리핑 3, 질문 답 1~3)."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        raise ValueError("JSON 없음")
    body = reply[start : end + 1]
    # 마지막 } · ]} 를 빠뜨린 응답도 읽는다 (HCX-005 실측)
    for candidate in (body, body + "}", body + "]}"):
        try:
            items = json.loads(candidate)["sentences"]
            break
        except json.JSONDecodeError as ex:
            error = ex
        except (KeyError, TypeError) as ex:
            raise ValueError(f"JSON 모양 다름: {ex}") from ex
    else:
        raise ValueError(f"JSON 모양 다름: {error}")
    try:
        out = [(str(i["text"]).strip(), [str(x) for x in i["facts"]]) for i in items]
    except (KeyError, TypeError) as ex:
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
