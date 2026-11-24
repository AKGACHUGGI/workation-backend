"""동네에 물어보기 답 (v6.1, 스펙 §3·§4·§6). 순수 함수 — 생성 함수(llm.generate)는 인자로 받는다.

의도에 맞는 사실만 고르기 → 프롬프트(의도의 대표 질문만, 사용자 문장 없음) → 구조화 응답 →
문장마다 v6 검증 + 근거어 + 모양 검사 → 실패한 자리만 그 의도의 규칙 문장.
의도 찾기는 ask_match.py, 답 저장·캐시는 views.py 가 한다.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from pydantic import BaseModel, Field, create_model

from explore import ask_calc, brief, derive
from explore.brief import Fact, josa
from explore.chat import llm
from explore.graph import METRIC_KO, METRICS

logger = logging.getLogger(__name__)

THRESHOLD = 0.68  # 1위 유사도가 이보다 낮으면 unknown_other (홈 검색과 같은 값에서 시작)
GAP = 0.05  # 2위 의도를 같이 고르는 유사도 차이
SENTENCES = range(1, 4)  # 생성 문장 수 1~3
ANSWER_VERSION = 4  # 답 캐시 키에 들어간다. 생성 모델·프롬프트·검사 규칙을 바꾸면 올린다

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
HOURS = ("weekday_hours", "weekend_hours")

# 시군구 상세 채팅 의도 (시군구 채팅 스펙 §5). 시군구 사전에서만 찾는다
REGION_INTENTS = (
    "find_quiet", "find_peer", "find_evening", "find_type",
    "find_coast", "find_life", "region_months", "region_profile",
)  # fmt: skip
REGION_CANONICAL = {
    "find_quiet": "조용한 동네 어디야?",
    "find_peer": "또래가 많은 동네는?",
    "find_evening": "저녁에 활기 있는 데는?",
    "find_type": "집중해서 일하기 좋은 동네는?",
    "find_coast": "바다 가까운 동네 있어?",
    "find_life": "관광지 말고 생활권 동네는?",
    "region_months": "몇 월에 가면 한산해?",
    "region_profile": "여긴 어떤 곳이야?",
}
REGION_FIRST = [
    REGION_CANONICAL["find_quiet"],
    REGION_CANONICAL["find_coast"],
    REGION_CANONICAL["region_months"],
]
REGION_HOURS_GUIDE = "시간대는 동네마다 달라요. 동네 이름과 함께 물어봐 주세요."

# 의도 찾기 범위: 동 채팅은 v7 사전, 시군구 채팅은 시군구 의도 + 시간대(안내용) + 모르는 질문
DONG_VOCAB = (*INTENTS, "compare_summary", *UNKNOWN)
REGION_VOCAB = (*REGION_INTENTS, *HOURS, *UNKNOWN)

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
    # 동 1개에서 비교를 물으면 (탐색 v7 채팅 스펙 §5-1). 동 2~3개면 채팅이 비교 답을 만든다
    "compare_summary": (
        "다른 동네 이름과 함께 물어봐 주세요. 같은 시군구 안의 동네끼리 비교할 수 있어요.",
        [CANONICAL["quiet"], CANONICAL["type"], CANONICAL["similar"]],
    ),
}

# 의도 → 쓰는 사실 id 앞부분 (스펙 §3-1 표)
INTENT_FACTS = {
    "weekday_hours": "F2-1",
    "weekend_hours": "F2-2",
    "months": "F3-",
    "quiet": "F1-quiet",
    "peer": "F1-peer",
    "evening": "F1-evening",
    "type": "F4-",
    "coast": "F5-1",
    "life": "F7-1",
    "similar": "F6-",
}
# 사실별 핵심어 (스펙 §6-2). F1 은 지표 이름의 줄기라 '조용한'·'활기 있는' 도 받는다
METRIC_WORDS = {"quiet": {"조용"}, "peer": {"또래"}, "evening": {"저녁", "활기"}}
FIXED_WORDS = {
    "F2-1": {"평일"},
    "F2-2": {"주말"},
    "F2-1h": {"평일"},
    "F2-2h": {"주말"},
    "F3-1": {"월"},
    "F3-2": {"월"},
    "F8-1": {"월"},
    "F8-2": {"시"},
    "F8-3": {"시"},
    "F8-4": {"시"},
    "F5-1": {"바다", "해안"},
}
NO_DATA = "이 동네는 그 내용을 계산할 데이터가 부족해요."

LATIN_OK = {"km"}  # 바닷가 사실의 단위
URL = re.compile(r"https?://|www\.|\.(?:com|net|org|kr|io|co)\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\d{2,4}-\d{3,4}-\d{4}")
LATIN = re.compile(r"[A-Za-z]{2,}")
TIMES = re.compile(r"\d+(?:\.\d+)?\s*배")  # 배수는 사실에 없는 계산이다
NORMALIZE = re.compile(r"[\s?.!~]")
DIGIT = re.compile(r"\d")
IDIOMS = ("1년",)  # 숫자가 사실 값이 아닌 관용 표현 ('1년 중'). 2026-11-17 실측 오탐 5건

SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 안내자다. 사용자가 준 사실 목록만 근거로 질문에 답한다.",
        "- 1~3문장. 문장마다 60자 이내, 존댓말 해요체.",
        "- 목록에 없는 숫자·달·시각·등급·장소 이름·추측을 쓰지 않는다.",
        "- 목록으로 답할 수 없는 부분은 쓰지 않는다.",
        "- 비교·계산은 직접 하지 말고 사실에 적힌 결론(비슷함·% 더 붐빔·많은 편)을 쓴다.",
        "- 시각은 사실에 적힌 '오후 3시'처럼 쓴다. '오후 15시'처럼 섞어 쓰지 않는다.",
        "- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.",
        "- 문장마다 근거로 쓴 사실 id 를 facts 에 적는다.",
    ]
)


@dataclass(frozen=True)
class Answer:
    sentences: list[str]
    source: str  # llm · mixed · rule
    model: str | None
    facts_hash: str
    fallback: bool = False  # 생성기가 없거나 생성이 실패해 규칙 답이 나감 → 캐시하지 않는다


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


def hour_facts(raw: dict, facts: list[Fact]) -> list[Fact]:
    """brief 의 F2-1 · F2-2 를 '오후 3시(15시)' 꼴로 다시 쓴다. id·common 은 brief 값 그대로.
    브리핑은 24시 그대로 둔다 (2026-11-17 사용자 결정: brief.py 는 손대지 않는다)."""
    rec = raw["rec"]
    hours = brief._hours(rec)
    out = []
    for fact in facts:
        day = {"F2-1": "weekday", "F2-2": "weekend"}.get(fact.id)
        if day is None:
            out.append(fact)
            continue
        peak, quiet = hours[("PEAK_AT", day)], hours[("QUIET_AT", day)]
        driv = rec["dong"][f"driv_{day}"]
        busy = f"{ask_calc.label(peak)} {driv[peak]}명으로 가장 많고"
        calm = f"{ask_calc.label(quiet)} {driv[quiet]}명으로 가장 적음"
        if day == "weekday":
            work = f"{ask_calc.clock(9)}~{ask_calc.clock(17)}"
            text = f"평일 차량 이동 인구: {busy}, 업무시간({work}) 중 {calm}"
            values = fact.values | ask_calc.hour_vals(peak, quiet, 9, 17)
        else:
            text = f"주말 차량 이동 인구: {busy}, {calm}"
            values = fact.values | ask_calc.hour_vals(peak, quiet)
        out.append(replace(fact, text=text, values=values))
    return out


def detail_facts(raw: dict) -> list[Fact]:
    """질문에 특정 시각·달이 나와도 답하도록 24시간 차량 값과 월별 원값도 사실로 둔다.
    F2-1h · F2-2h = 평일·주말 시간대별 차량 이동 인구, F3-2 = 시군구 월별 방문 원값.
    브리핑에는 안 씀."""
    rec = raw["rec"]
    d, sgg = rec["dong"], rec["region"]["name"]
    out = []
    for i, (day, ko) in enumerate((("weekday", "평일"), ("weekend", "주말")), start=1):
        pairs = [
            (h, v) for h, v in enumerate(d.get(f"driv_{day}") or []) if v is not None and v >= 0
        ]
        if pairs:
            text = f"{ko} 시간대별 차량 이동 인구: " + ", ".join(
                f"{ask_calc.label(h)} {v}명" for h, v in pairs
            )
            values = brief._vals(*(v for _, v in pairs))
            values |= ask_calc.hour_vals(*(h for h, _ in pairs))
            out.append(Fact(f"F2-{i}h", text, values, 1.0))
    months = [(m, v) for m, v, _ in sorted(rec["months"]) if v is not None]
    if months:
        text = f"{sgg} 월별 방문 원값(클수록 붐빔): " + ", ".join(
            f"{m}월 {v:.1f}" for m, v in months
        )
        values = brief._vals(*(m for m, _ in months), *(f"{v:.1f}" for _, v in months))
        values |= brief._vals(*(round(v) for _, v in months))
        out.append(Fact("F3-2", text, values, 1.0))
    return out


def prompt_hash(facts: list[Fact], question: str) -> str:
    """같은 버전 + 같은 사실 + 같은 질문(정규형)이면 같은 값. 답 캐시 키.
    질문 문장이 CLOVA 에 그대로 들어가므로 의도만으로 답을 나눠 쓰지 않는다
    (2026-11-13 사용자 결정)."""
    body = f"v{ANSWER_VERSION}\n" + brief.facts_hash(facts) + "\n" + normalize(question)
    return hashlib.sha256(body.encode()).hexdigest()


def facts_for(raw: dict, intents: list[str], question: str = "") -> list[Fact]:
    """의도에 맞는 사실만. 의도 순서대로, 의도 안에서는 id 순.
    질문에 달·시각이 있으면 코드가 계산한 비교 사실(F8-*)을 끝에 붙인다."""
    facts = hour_facts(raw, brief.collect_facts(raw))
    life = life_fact(raw)
    if life is not None:
        facts.append(life)
    facts += detail_facts(raw)
    out = []
    for intent in intents:
        prefix = INTENT_FACTS[intent]
        out += sorted((f for f in facts if f.id.startswith(prefix)), key=lambda f: f.id)
    return out + ask_calc.computed_facts(raw, question)


def place_names(raw: dict) -> frozenset[str]:
    """숫자 검사에서 뺄 이름: 질문 동 + 비슷한 동(F6) 이름 중 숫자가 든 것 (예: 중화산2동).
    프롬프트에 동 이름이 들어가 CLOVA 가 쓰는 게 정상인데 '사실에 없는 값'으로 걸렸다
    (2026-11-17 실측 24건)."""
    rec = raw["rec"]
    names = [rec["dong"]["name"], *(name for _, _, name, _ in rec["similar"])]
    return frozenset(n for n in names if DIGIT.search(n))


def anchors(fact: Fact) -> set[str]:
    """이 사실을 인용한 문장에 하나 이상 있어야 하는 말 = values ∪ 핵심어 (스펙 §6-2)."""
    words = set(fact.values)
    if fact.id.startswith("F1-"):
        words |= METRIC_WORDS[fact.id.split("-")[1]]
    elif fact.id.startswith("F4-"):
        name = fact.text.split(" ")[0]  # "로컬살이형 60점 (유형 1위)"
        words |= {name, name.removesuffix("형")}
    elif fact.id.startswith("F6-"):
        words.add(fact.text.split(" ")[-1])  # "비슷한 동 1: 속초시 노학동"
    elif fact.id == "F7-1":
        words.add(fact.text.split(" ")[0])  # 생활권 동에 '관광지' 문장이 붙지 않게
    else:
        words |= FIXED_WORDS.get(fact.id, set())
    return words


def _has(word: str, text: str, tokens: set[str]) -> bool:
    """숫자·등급은 토큰으로(1 이 21 에 걸리지 않게), 나머지는 글자 포함으로."""
    if brief.NUMBER.fullmatch(word) or brief.GRADE.fullmatch(word):
        return word in tokens
    return word in text


def _clock_problems(text: str, known: list[Fact]) -> list[str]:
    """'오후 15시' 같은 섞인 표기, 인용 사실에 없는 '오전·오후 + 시각' (스펙 §4).
    숫자 검사만으로는 '오후 2시'(2 가 사실에 있음)를 못 거른다.
    인용 사실에 시각이 없으면 틀림 검사는 건너뛴다."""
    said = [(p, int(h)) for p, h in ask_calc.CLOCK.findall(text)]
    problems = [f"시각 표기: {p} {h}시" for p, h in said if ask_calc.to_24(p, h) is None]
    hours = {ask_calc.to_24(p, int(h)) for f in known for p, h in ask_calc.CLOCK.findall(f.text)}
    if hours:
        problems += [
            f"시각 틀림: {p} {h}시"
            for p, h in said
            if ask_calc.to_24(p, h) is not None and ask_calc.to_24(p, h) not in hours
        ]
    return problems


def check_sentence(
    text: str, cited: list[str], facts: dict[str, Fact], names: frozenset[str] = frozenset()
) -> list[str]:
    """문제 목록. 비어 있으면 통과 (스펙 §6). names · IDIOMS 속 숫자는 숫자·등급 검사에서만 뺀다.
    숫자를 '#' 로 바꿔 글자 수를 그대로 둔다(60자 검사). 이름 글자 검사(F6 근거어)는 원문으로."""
    masked = text
    for name in (*names, *IDIOMS):
        masked = masked.replace(name, DIGIT.sub("#", name))
    problems = brief.validate_sentence(masked, cited, facts)
    plain = brief.THOUSANDS.sub("", masked)
    tokens = set(brief.NUMBER.findall(plain) + brief.GRADE.findall(plain))
    known = [facts[c] for c in cited if c in facts]
    if known and not any(_has(w, text, tokens) for f in known for w in anchors(f)):
        problems.append("근거어 없음")
    if URL.search(text) or EMAIL.search(text) or PHONE.search(text):
        problems.append("URL·이메일·전화번호 모양")
    latin = [w for w in LATIN.findall(text) if w.lower() not in LATIN_OK]
    if latin:
        problems.append(f"영문 단어: {latin}")
    if not text.rstrip().rstrip(".!?").endswith(("요", "니다")):
        problems.append("존댓말 아님")
    if TIMES.search(text):
        problems.append("배수 표현")
    problems += _clock_problems(text, known)
    return problems


def _grade_rule(rec: dict, metric: str, has_sgg: bool) -> list[str]:
    grade = next((g for g in rec["grades"] if g[0] == metric), None)
    if grade is None:
        return [NO_DATA]
    _, g, score, rank, of, _, _ = grade
    ko = METRIC_KO[metric]
    said = brief.label(metric, g)
    out = [f"{josa(ko)} {g}등급({score}점)으로 전국 {of}동 중 {rank}위, {said}이에요."]
    if has_sgg:
        out.append(f"{rec['region']['name']} 안에서는 {ko} 1위예요.")
    return out


def _intent_rule(raw: dict, intent: str, ids: set[str]) -> list[str]:
    """의도 하나의 규칙 문장 1~2개 (스펙 §4-4). 숫자는 사실 값 그대로라 검증하지 않는다."""
    rec = raw["rec"]
    hours = brief._hours(rec)
    if intent in ("weekday_hours", "weekend_hours"):
        day = intent.split("_")[0]
        peak, quiet = hours.get(("PEAK_AT", day)), hours.get(("QUIET_AT", day))
        if peak is None or quiet is None:
            return [NO_DATA]
        busy, calm = ask_calc.clock(peak), ask_calc.clock(quiet)
        if day == "weekday":
            return [f"평일 차량은 {busy}에 가장 많고, 업무시간엔 {calm}가 가장 한산해요."]
        return [f"주말 차량은 {busy}에 가장 많고, {calm}가 가장 한산해요."]
    if intent == "months":
        quiet_m = sorted(m for m, _, q in rec["months"] if q)
        busiest = derive.busiest_month({m: v for m, v, _ in rec["months"] if v is not None})
        if not quiet_m or busiest is None:
            return [NO_DATA]
        months = "·".join(map(str, quiet_m))
        sgg = josa(rec["region"]["name"])
        return [f"{sgg} {months}월이 가장 한산하고 {busiest}월이 가장 붐벼요."]
    if intent in METRICS:
        return _grade_rule(rec, intent, f"F1-{intent}-sgg" in ids)
    if intent == "type":
        types = sorted(rec["types"])
        if not types:
            return [NO_DATA]
        _, name, score, _ = types[0]
        if score < derive.TYPE_MIN:
            # 대표 유형 없음: 세 점수를 유형 순서(몰입 → 네트워킹 → 로컬)대로 보여 준다
            by_name = {n: s for _, n, s, _ in types}
            listed = " · ".join(
                f"{derive.TYPE_KO[t]} {by_name[derive.TYPE_KO[t]]}점"
                for t in derive.TYPES
                if derive.TYPE_KO[t] in by_name
            )
            return ["뚜렷하게 맞는 유형은 없어요.", f"{listed}이에요."]
        if len(types) == 1:
            return [f"{name} {score}점으로 가장 잘 맞아요."]
        _, name2, score2, _ = types[1]
        return [f"{name} {score}점으로 가장 잘 맞고, 다음은 {name2} {score2}점이에요."]
    if intent == "coast":
        if not rec["near"]:
            return ["해안선에서 1km 넘게 떨어진 동네예요."]
        return [f"해안선까지 {rec['near'][0]:.1f}km인 바닷가 동네예요."]
    if intent == "life":
        if "F7-1" not in ids:
            return [NO_DATA]
        me = raw["facts"][rec["dong"]["code"]]
        return [f"{me['life'][0]['name']} 성격이 강한 동네예요."]
    # similar
    names = [f"{sigungu} {name}" for _, _, name, sigungu in sorted(rec["similar"])]
    if not names:
        return [NO_DATA]
    return [f"지표가 비슷한 동네는 {josa(', '.join(names), '이에요/예요')}."]


def rule_answer(raw: dict, intents: list[str], facts: list[Fact], question: str = "") -> list[str]:
    """질문 속 달·시각을 계산한 규칙 문장이 있으면 그것만(질문에 바로 답한다).
    없으면 의도 순서대로 규칙 문장. 같은 문장(데이터 부족)은 한 번만."""
    computed = ask_calc.computed_rules(raw, question)
    if computed:
        return computed
    ids = {f.id for f in facts}
    out: list[str] = []
    for intent in intents:
        out += [s for s in _intent_rule(raw, intent, ids) if s not in out]
    return out


def build_prompt(raw: dict, facts: list[Fact], question: str) -> tuple[str, str]:
    """사용자 질문을 그대로 넣는다 (2026-11-13 사용자 결정: 인젝션 차단은 일단 빼고 CLOVA 로 바로).
    사실은 의도로 고른 것만이고, 답 문장은 그대로 출력 검사(§6)를 거친다."""
    rec = raw["rec"]
    where = f"{rec['sido']} {rec['region']['name']} {rec['dong']['name']}"
    lines = "\n".join(f"{f.id}: {f.text}" for f in facts)
    return SYSTEM, f"동네: {where}\n질문: {question}\n사실:\n{lines}"


def _intents_of(cited: list[str], intents: list[str]) -> list[str]:
    hit = [i for i in intents if any(c.startswith(INTENT_FACTS[i]) for c in cited)]
    return hit or intents


def _topics(cited: list[str], intents: list[str]) -> set[str]:
    """문장이 다루는 주제: 계산 사실(F8)이면 'calc', 아니면 인용한 의도."""
    if any(c.startswith("F8-") for c in cited):
        return {"calc"}
    return set(_intents_of(cited, intents))


def compose(
    raw: dict,
    intents: list[str],
    items: list[tuple[str, list[str]]],
    facts: list[Fact],
    question: str = "",
) -> tuple[list[str], str]:
    """통과한 생성 문장은 그대로. 실패한 문장은 같은 주제를 통과한 문장이 이미 답했으면 빼고,
    아니면 그 주제의 규칙 문장으로 채운다 (같은 말이 두 번 나오지 않게).
    같은 규칙 문장은 한 번만."""
    by_id = {f.id: f for f in facts}
    ids = set(by_id)
    names = place_names(raw)
    checked = [(text, cited, check_sentence(text, cited, by_id, names)) for text, cited in items]
    covered = set().union(*(_topics(c, intents) for _, c, p in checked if not p))
    out: list[str] = []
    passed = 0
    for text, cited, problems in checked:
        if not problems:
            out.append(text)
            passed += 1
            continue
        logger.info("질문 답 문장 교체 %r: %s", text, problems)
        for topic in sorted(_topics(cited, intents) - covered):
            rules = (
                ask_calc.computed_rules(raw, question)
                if topic == "calc"
                else _intent_rule(raw, topic, ids)
            )
            out += [r for r in rules if r not in out]
    source = "llm" if passed == len(items) else "rule" if passed == 0 else "mixed"
    return out, source


Generate = Callable[[str, str, type[BaseModel]], BaseModel]


def answer_model(fact_ids: list[str]) -> type[BaseModel]:
    """이번 질문에 넘긴 사실 id 만 고를 수 있는 응답 스키마 (compare.answer_model 과 같은 방식)."""
    sentence = create_model(
        "AskSentence",
        text=(str, Field(description="질문에 답하는 한 문장. 해요체, 60자 이내")),
        facts=(
            list[Literal[tuple(fact_ids)]],
            Field(min_length=1, description="이 문장의 근거 사실 id"),
        ),
    )
    return create_model(
        "AskAnswer",
        sentences=(list[sentence], Field(min_length=SENTENCES.start, max_length=SENTENCES[-1])),
    )


def answer(raw: dict, intents: list[str], generate: Generate | None, *, question: str) -> Answer:
    """답할 수 있는 의도 1~2개 → 답. generate(system, user, schema) 가 None(키 없음)이거나
    실패하면 규칙 문장(fallback). 서비스 요청 중이라 다시 시도하지 않는다."""
    digest = prompt_hash(facts_for(raw, intents, question), question)
    # 등급 의도(조용함·또래·저녁 활기)는 판단도 표현도 코드가 한다. 답이 등급 사실 하나라
    # LLM 이 더할 게 없고, 덧붙이면 '일하기 좋은 환경'·'추천' 같은 말만 생긴다 (2026-11-17 실측)
    graded = [i for i in intents if i in METRICS]
    rest = [i for i in intents if i not in METRICS]
    fixed = rule_answer(raw, graded, facts_for(raw, graded), "") if graded else []
    if not rest:
        return Answer(fixed, "rule", None, digest)
    facts = facts_for(raw, rest, question)
    rules = rule_answer(raw, rest, facts, question)
    if not facts:
        return Answer(fixed + rules, "rule", None, digest)
    if generate is None:
        return Answer(fixed + rules, "rule", None, digest, fallback=True)
    system, user = build_prompt(raw, facts, question)
    try:
        reply = generate(system, user, answer_model([f.id for f in facts]))
        items = [(s.text, list(s.facts)) for s in reply.sentences]
    except Exception as ex:  # HCX 오류 · 시간 초과 · 스키마 불일치 모두 규칙 문장으로
        logger.warning("질문 답 생성 실패: %s", ex)
        return Answer(fixed + rules, "rule", None, digest, fallback=True)
    sentences, source = compose(raw, rest, items, facts, question)
    if fixed and source == "llm":
        source = "mixed"
    model = None if source == "rule" else llm.MODEL
    return Answer(fixed + sentences, source, model, digest)


def examples_for(intents: list[str]) -> list[str]:
    """다음에 누를 예시 질문 3개. 안내면 §3-2 예시, 아니면 고른 의도를 뺀 §3-1 순서."""
    if intents[0] in GUIDE:
        return list(GUIDE[intents[0]][1])
    return [CANONICAL[i] for i in INTENTS if i not in intents][:3]
