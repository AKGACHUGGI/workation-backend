"""동네에 물어보기 답 (v6.1, 스펙 §3·§4·§6). 순수 함수 — 채팅 함수는 인자로 받는다.

의도에 맞는 사실만 고르기 → 프롬프트(의도의 대표 질문만, 사용자 문장 없음) → 응답 파싱 →
문장마다 v6 검증 + 근거어 + 모양 검사 → 실패한 자리만 그 의도의 규칙 문장.
의도 찾기는 ask_match.py, 답 저장·캐시는 views.py 가 한다.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from core.clova import CHAT_MODEL, ClovaError
from explore import brief, derive
from explore.brief import Fact, josa
from explore.graph import METRIC_KO, METRICS

logger = logging.getLogger(__name__)

THRESHOLD = 0.68  # 1위 유사도가 이보다 낮으면 unknown_other (홈 검색과 같은 값에서 시작)
GAP = 0.05  # 2위 의도를 같이 고르는 유사도 차이
SENTENCES = range(1, 4)  # 생성 문장 수 1~3

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
    "F5-1": {"바다", "해안"},
}
NO_DATA = "이 동네는 그 내용을 계산할 데이터가 부족해요."

LATIN_OK = {"km"}  # 바닷가 사실의 단위
URL = re.compile(r"https?://|www\.|\.(?:com|net|org|kr|io|co)\b", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE = re.compile(r"\d{2,4}-\d{3,4}-\d{4}")
LATIN = re.compile(r"[A-Za-z]{2,}")
NORMALIZE = re.compile(r"[\s?.!~]")

SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 안내자다. 사용자가 준 사실 목록만 근거로 질문에 답한다.",
        "- 1~3문장. 문장마다 60자 이내, 존댓말 해요체.",
        "- 목록에 없는 숫자·달·시각·등급·장소 이름·추측을 쓰지 않는다.",
        "- 목록으로 답할 수 없는 부분은 쓰지 않는다.",
        "- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.",
        "- 문장마다 근거로 쓴 사실 id 를 facts 에 적는다.",
        '- JSON 하나만 출력한다: {"sentences": [{"text": "...", "facts": ["F2-1"]}]}',
    ]
)


@dataclass(frozen=True)
class Answer:
    sentences: list[str]
    source: str  # llm · mixed · rule
    model: str | None
    facts_hash: str


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
            text = f"{ko} 시간대별 차량 이동 인구: " + ", ".join(f"{h}시 {v}명" for h, v in pairs)
            values = brief._vals(*(x for pair in pairs for x in pair))
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
    """같은 사실 + 같은 질문(정규형)이면 같은 값. 답 캐시 · dong_answer 키.
    질문 문장이 CLOVA 에 그대로 들어가므로 의도만으로 답을 나눠 쓰지 않는다
    (2026-11-13 사용자 결정)."""
    body = brief.facts_hash(facts) + "\n" + normalize(question)
    return hashlib.sha256(body.encode()).hexdigest()


def facts_for(raw: dict, intents: list[str]) -> list[Fact]:
    """의도에 맞는 사실만. 의도 순서대로, 의도 안에서는 id 순."""
    facts = brief.collect_facts(raw)
    life = life_fact(raw)
    if life is not None:
        facts.append(life)
    facts += detail_facts(raw)
    out = []
    for intent in intents:
        prefix = INTENT_FACTS[intent]
        out += sorted((f for f in facts if f.id.startswith(prefix)), key=lambda f: f.id)
    return out


def anchors(fact: Fact) -> set[str]:
    """이 사실을 인용한 문장에 하나 이상 있어야 하는 말 = values ∪ 핵심어 (스펙 §6-2)."""
    words = set(fact.values)
    if fact.id.startswith("F1-"):
        words |= METRIC_WORDS[fact.id.split("-")[1]]
    elif fact.id.startswith("F4-"):
        name = fact.text.split(" ")[0]  # "장기체류형 61점 (유형 1위)"
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


def check_sentence(text: str, cited: list[str], facts: dict[str, Fact]) -> list[str]:
    """문제 목록. 비어 있으면 통과 (스펙 §6)."""
    problems = brief.validate_sentence(text, cited, facts)
    plain = brief.THOUSANDS.sub("", text)
    tokens = set(brief.NUMBER.findall(plain) + brief.GRADE.findall(plain))
    known = [facts[c] for c in cited if c in facts]
    if known and not any(_has(w, text, tokens) for f in known for w in anchors(f)):
        problems.append("근거어 없음")
    if URL.search(text) or EMAIL.search(text) or PHONE.search(text):
        problems.append("URL·이메일·전화번호 모양")
    latin = [w for w in LATIN.findall(text) if w.lower() not in LATIN_OK]
    if latin:
        problems.append(f"영문 단어: {latin}")
    return problems


def _grade_rule(rec: dict, metric: str, has_sgg: bool) -> list[str]:
    grade = next((g for g in rec["grades"] if g[0] == metric), None)
    if grade is None:
        return [NO_DATA]
    _, g, score, rank, of, _, _ = grade
    ko = METRIC_KO[metric]
    out = [f"{josa(ko)} {g}등급({score}점)으로, 전국 {of}동 중 {rank}위예요."]
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
        if day == "weekday":
            return [f"평일 차량은 {peak}시에 가장 많고, 업무시간엔 {quiet}시가 가장 한산해요."]
        return [f"주말 차량은 {peak}시에 가장 많고, {quiet}시가 가장 한산해요."]
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


def rule_answer(raw: dict, intents: list[str], facts: list[Fact]) -> list[str]:
    """의도 순서대로 규칙 문장. 같은 문장(데이터 부족)은 한 번만."""
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


def compose(
    raw: dict, intents: list[str], items: list[tuple[str, list[str]]], facts: list[Fact]
) -> tuple[list[str], str]:
    """통과한 생성 문장은 그대로, 실패한 문장 자리에는 그 문장이 인용한 의도의 규칙 문장
    (인용이 맞지 않으면 고른 의도 전부). 이미 넣은 규칙 문장은 다시 넣지 않는다."""
    by_id = {f.id: f for f in facts}
    ids = set(by_id)
    out: list[str] = []
    passed = 0
    for text, cited in items:
        problems = check_sentence(text, cited, by_id)
        if not problems:
            out.append(text)
            passed += 1
            continue
        logger.info("질문 답 문장 교체 %r: %s", text, problems)
        for intent in _intents_of(cited, intents):
            out += [s for s in _intent_rule(raw, intent, ids) if s not in out]
    source = "llm" if passed == len(items) else "rule" if passed == 0 else "mixed"
    return out, source


def answer(
    raw: dict, intents: list[str], chat: Callable[[str, str], str] | None, *, question: str
) -> Answer:
    """답할 수 있는 의도 1~2개 → 답. chat(system, user) 이 None(키 없음)이거나 실패하면 규칙 문장.
    서비스 요청 중이라 다시 시도하지 않는다."""
    facts = facts_for(raw, intents)
    digest = prompt_hash(facts, question)
    rules = rule_answer(raw, intents, facts)
    if chat is None or not facts:
        return Answer(rules, "rule", None, digest)
    system, user = build_prompt(raw, facts, question)
    try:
        items = brief.parse_reply(chat(system, user), SENTENCES)
    except ClovaError as ex:
        logger.warning("질문 답 생성 실패: %s", ex)
        return Answer(rules, "rule", None, digest)
    except ValueError as ex:
        logger.warning("질문 답 응답 파싱 실패: %s", ex)
        return Answer(rules, "rule", None, digest)
    sentences, source = compose(raw, intents, items, facts)
    return Answer(sentences, source, None if source == "rule" else CHAT_MODEL, digest)


def examples_for(intents: list[str]) -> list[str]:
    """다음에 누를 예시 질문 3개. 안내면 §3-2 예시, 아니면 고른 의도를 뺀 §3-1 순서."""
    if intents[0] in GUIDE:
        return list(GUIDE[intents[0]][1])
    return [CANONICAL[i] for i in INTENTS if i not in intents][:3]
