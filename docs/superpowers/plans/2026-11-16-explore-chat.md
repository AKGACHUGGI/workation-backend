# 탐색 v7 동 패널 채팅 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 지도 탐색 동 패널에서 여러 턴 채팅으로 묻고, 같은 시군구 동네 이름이 나오면 그래프 값으로 비교해 사용자 기준에 맞춘 상황별 안내를 돌려주는 `POST /api/explore/chat` 을 만든다.

**Architecture:** `explore/chat/` 에 순수 함수 모듈(동네 찾기 `resolve` · 비교 재료 `arrange` · 비교 답 `compare`)을 두고, LangGraph `StateGraph`(`flow`)가 이들을 노드로 묶는다. 대화 상태는 LangGraph Redis 체크포인터(redis:8, TTL 30분)에 의도·동네만 남긴다. 비교 답은 LangChain `ChatClovaX`(HCX-007) json_schema 구조화 출력으로 만들고, 코드가 방향·이름·숫자·금지어를 검사해 틀린 문장만 규칙 문장으로 바꾼다. 동 1개 답은 v6.1 `ask.answer`(HCX-005)를 그대로 쓴다.

**Tech Stack:** Django 5.2 · DRF · Neo4j(읽기, 기존 `queries.fetch_dong`) · Postgres · Redis 8 · langgraph 1.2 · langgraph-checkpoint-redis 0.6 · langchain-naver 0.1 · pydantic 2 · pytest-django

**설계 문서:** `docs/superpowers/specs/2026-11-16-explore-chat-design.md` (이하 "스펙")

## Global Constraints

- 같은 시군구 동네끼리만 비교한다. 비교 대상은 패널 동 포함 최대 3개다.
- 비교 답 프롬프트에 사용자 문장을 넣지 않는다(의도의 대표 질문 + 코드 정리만).
- 답 문장에 "지표·벡터·거리·백분위·노드·그래프·데이터"와 `brief.BANNED`("추천", "최고", "가장 좋", "최적", "강추", "핫플")를 쓰지 않는다. 등급 글자 S/A/B/C 는 근거 줄에서만 쓴다.
- 틀린 비교 문장은 규칙 문장으로 바꾼다: `{축}을 원한다면 {1등 동}이 맞아요.` / 동점이면 `{축}은 {동·동}이 같은 등급이에요.`(등급 축) · `…이 비슷해요.`(그 밖의 축).
- 근거 줄은 코드가 만든다. LLM 출력은 근거 줄에 쓰지 않는다.
- 비교 답 생성: `ChatClovaX(model="HCX-007", thinking={"effort": "none"}, temperature=0.3, max_tokens=512)`, `with_structured_output(..., method="json_schema")`, 시간 제한 10초, 재시도 없음.
- 체크포인터: `RedisSaver`, `ttl={"default_ttl": 30, "refresh_on_read": True}`, 실행은 `durability="exit"`, 마지막 노드에서 `question` 을 비운다. `REDIS_URL` 이 없으면 `InMemorySaver`. Redis 오류면 체크포인터 없이 한 번 더 실행한다.
- 비교 답 캐시: Redis(Django cache) **1일**. 키 = 정렬한 동 코드 + 의도 + 축 + 프롬프트 해시 + 모델 + 프롬프트 버전.
- 서버 저장소에 질문 원문을 남기지 않는다. 채팅 뷰는 `sources.log_question` 을 부르지 않는다.
- throttle: 기존 `explore_ask_burst`(10/min) · `explore_ask_day`(200/day) 그대로.
- 질문 1~100자.
- 패키지: `langgraph>=1.2.22,<2` · `langgraph-checkpoint-redis>=0.6.0,<0.7` · `langchain-naver>=0.1.1,<0.2`.
- `uv run pytest` 는 Redis·Neo4j·HCX 없이 통과해야 한다(테스트 DB 는 기존대로 `docker compose up -d --wait test-db`).
- 코드 스타일: 한국어 docstring·주석, 줄 길이 100, `uv run ruff check . && uv run ruff format --check .` 통과.

## 스펙과 다르게 정한 것 (구현하며 알게 된 점)

| 스펙 | 계획 | 이유 |
| --- | --- | --- |
| §1 "사용자 문장은 HCX 프롬프트에 들어가지 않는다(v6.1 원칙 유지)" | **비교 답에만** 적용. 동 1개 답은 지금 `ask.build_prompt` 대로 질문을 넣는다 | `main` 의 v6.1 코드는 2026-11-13 사용자 결정으로 질문을 그대로 넣고 있다. 스펙 §1 문구를 이 계획 커밋에서 고친다 |
| §5-1 신규 의도 `similarity` | 만들지 않고 `similar` 에 표현을 더한다 | 스펙에서도 두 의도를 같게 처리한다 |
| §3 `fetch_graph` 노드가 `facts` 등을 상태에 둔다 | 답 노드가 1시간 캐시된 `dong_raw` 를 직접 읽고, 상태에는 넣지 않는다 | 체크포인트 크기(스펙 §11). 상태에는 의도·동네만 남는다 |
| §5-1 `weekday_hours` · `weekend_hours` · `months` · `similar` 비교 | 규칙 문장만(LLM 안 씀) | 축별 상황 고르기가 아니라 나란히 보여 주기라 LLM 이 할 일이 없다 |
| §8 문장별 `source` | 동 1개 답은 문장마다 전체 `source`(llm · mixed · rule)를 그대로 붙인다 | `ask.compose` 가 문장별 출처를 돌려주지 않는다 |

## 파일 구조

| 파일 | 할 일 |
| --- | --- |
| Create `explore/chat/__init__.py` | 빈 파일 |
| Create `explore/chat/resolve.py` | 질문 속 동 이름 → 이번 턴 비교 동네 (스펙 §4). 순수 |
| Create `explore/chat/arrange.py` | 동 원재료 → 축 값 · 축별 순서 · 공통점 · 연결 · 닮음 · 사용자 기준 → 축 (스펙 §5-2·§5-3). 순수 |
| Create `explore/chat/compare.py` | 프롬프트 · 응답 스키마 · 검사 · 규칙 문장 · 근거 · 규칙만 쓰는 비교 의도 (스펙 §6). 순수 |
| Create `explore/chat/llm.py` | `ChatClovaX` 구조화 생성 어댑터 |
| Create `explore/chat/data.py` | 캐시된 읽기: 동 원재료(1시간) · 동 이름 사전 · 닮음 분포(1일) |
| Create `explore/chat/flow.py` | LangGraph 상태 · 노드 · 그래프 조립 · 체크포인터 · 실행 · 응답 모양 |
| Modify `explore/sources.py` | `load_dong_names` · `load_metric_vecs` 추가 |
| Modify `explore/ask.py` | `GUIDE["compare_summary"]` 추가 |
| Modify `explore/views.py` | `_raw` 를 `data.dong_raw` 로, `DongAsk._answer` 를 모듈 함수로, `ExploreChat` 추가 |
| Modify `explore/urls.py` | `chat` 경로 |
| Modify `db/seed/ask_vocab.csv` · `db/tools/build_embeddings.py` | `compare_summary` 의도와 비교 표현 |
| Modify `compose.yaml` | `redis:7` → `redis:8` |
| Modify `pyproject.toml` · `uv.lock` | 패키지 3개 |
| Create `tests/explore_chat_samples.py` | 같은 시군구 동 3개 원재료 · 이름 사전 |
| Create `tests/test_explore_chat_resolve.py` · `…_arrange.py` · `…_compare.py` · `…_data.py` · `…_flow.py` · `…_api.py` | 각 모듈 테스트 |

작업 브랜치: `docs/explore-chat-spec` 에서 `feat/explore-chat` 를 만들어 진행한다.

```bash
cd workation-backend
git switch -c feat/explore-chat docs/explore-chat-spec
```

---

### Task 1: 동네 찾기 (`resolve.py`)

**Files:**
- Create: `explore/chat/__init__.py`
- Create: `explore/chat/resolve.py`
- Create: `tests/explore_chat_samples.py`
- Test: `tests/test_explore_chat_resolve.py`

**Interfaces:**
- Produces:
  - `DongName(code: str, name: str, sgg_code: str, sgg_name: str)` (frozen dataclass)
  - `Resolved(dongs: list[str], guide: str | None = None, note: str | None = None, named: bool = False)`
  - `resolve(question: str, panel: DongName, prev: list[str], names: list[DongName]) -> Resolved`
  - 상수 `MAX_DONGS = 3`, `TOO_MANY`, `ASK_DONG`
  - `tests/explore_chat_samples.py`: `HYOJA`, `YEOUI`, `GYO`(코드 문자열), `NAMES: list[DongName]`, `chat_raw(...) -> dict`, `RAWS: dict[str, dict]`

- [ ] **Step 1: 테스트 재료 만들기**

`explore/chat/__init__.py` 는 빈 파일로 만든다.

`tests/explore_chat_samples.py`:

```python
"""탐색 v7 채팅 테스트 공용 재료. 전주시 동 3개(효자2동 · 여의동 · 교1동)와 이름 사전.

chat_raw() 는 queries.fetch_dong 이 돌려주는 모양을 채팅이 쓰는 필드만 채워 만든다.
점수는 축별 순서를 확인하기 좋게 고른 값이다 (실측 아님).
"""

from explore.chat.resolve import DongName
from explore.graph import METRICS

HYOJA, YEOUI, GYO = "4511164500", "4511163500", "4511152000"
JEONJU = ("45111", "전주시")

NAMES = [
    DongName(HYOJA, "효자2동", *JEONJU),
    DongName(YEOUI, "여의동", *JEONJU),
    DongName(GYO, "교1동", *JEONJU),
    DongName("4511153000", "송천1동", *JEONJU),
    DongName("4511154000", "송천동", *JEONJU),
    DongName("4511199000", "송정동", *JEONJU),
    DongName("5115025000", "주문진읍", "51150", "강릉시"),
    DongName("5115060500", "경포동", "51150", "강릉시"),
    DongName("5115099000", "송정동", "51150", "강릉시"),
]


def chat_raw(code, name, scores, grades, *, near=None, types=(60, 50, 36), life=0.7, similar=()):
    """scores · grades = (조용함, 또래 밀도, 저녁 활기). types = (로컬살이형, 네트워킹형, 몰입형) 점수.
    near = 해안선까지 km (None 이면 내륙). similar = [(순위, 코드, 이름, 시군구)]."""
    rec = {
        "dong": {
            "code": code,
            "name": name,
            "coast_km": 5.0 if near is None else near,
            "driv_weekday": [100] * 24,
            "driv_weekend": [100] * 24,
        },
        "region": {"code": JEONJU[0], "name": JEONJU[1]},
        "sido": "전북특별자치도",
        "types": [
            [1, "로컬살이형", types[0], ""],
            [2, "네트워킹형", types[1], ""],
            [3, "몰입형", types[2], ""],
        ],
        "grades": [
            [m, g, s, 1, 203, 1, 10] for m, s, g in zip(METRICS, scores, grades, strict=True)
        ],
        "hours": [
            ["PEAK_AT", "weekday", 12],
            ["QUIET_AT", "weekday", 9],
            ["PEAK_AT", "weekend", 14],
            ["QUIET_AT", "weekend", 3],
        ],
        "near": [] if near is None else [near],
        "months": [[m, 80.0 + m, m in (1, 2, 8)] for m in range(1, 13)],
        "similar": [list(s) for s in similar],
    }
    row = {
        "code": code,
        "grades": [
            {"metric": m, "grade": g, "score": s, "degree": 50}
            for m, s, g in zip(METRICS, scores, grades, strict=True)
        ],
        "top": [{"id": "type:local", "name": "로컬살이형", "degree": 40}],
        "quiet": [{"day": "weekday", "h": 9, "degree": 30}],
        "near": [] if near is None else [{"id": "place:coast", "kind": "바닷가", "degree": 97}],
        "life": [{"id": "life:local", "name": "생활권", "degree": 107, "value": life}],
        "region": {"id": f"region:{JEONJU[0]}", "name": JEONJU[1], "degree": 10},
    }
    return {
        "rec": rec,
        "facts": {code: row},
        "synced_at": "2026-11-16T00:00:00",
        "dong_count": 203,
        "region_count": 12,
    }


RAWS = {
    HYOJA: chat_raw(
        HYOJA, "효자2동", (73, 56, 79), ("S", "A", "S"), similar=[(2, YEOUI, "여의동", "전주시")]
    ),
    YEOUI: chat_raw(
        YEOUI, "여의동", (57, 79, 96), ("A", "S", "S"), near=0.4, types=(40, 70, 30), life=0.6
    ),
    GYO: chat_raw(GYO, "교1동", (40, 60, 96), ("B", "A", "S"), types=(50, 45, 55), life=0.8),
}
```

- [ ] **Step 2: 실패하는 테스트 쓰기**

`tests/test_explore_chat_resolve.py`:

```python
"""질문 속 동 이름 → 비교 동네 (스펙 §4)."""

from explore.chat import resolve as r
from tests.explore_chat_samples import GYO, HYOJA, NAMES, YEOUI

PANEL = NAMES[0]


def go(question, prev=()):
    return r.resolve(question, PANEL, list(prev), NAMES)


def test_same_sigungu_name_is_added():
    got = go("여의동이랑 비교하면?")
    assert got == r.Resolved([HYOJA, YEOUI], named=True)


def test_spaced_digit_name():
    assert go("교 1동은 어때?").dongs == [HYOJA, GYO]


def test_longest_name_wins():
    # '송천1동' 안의 '송천'(송천동 약칭)이 따로 잡히지 않는다
    assert go("송천1동이랑 비교").dongs == [HYOJA, "4511153000"]


def test_same_name_prefers_panel_sigungu():
    assert go("송정동은?").dongs == [HYOJA, "4511199000"]


def test_other_sigungu_dong_guides():
    got = go("경포동이랑 비교해 줘")
    assert got.dongs == [HYOJA]
    assert got.guide.startswith("지금은 같은 시군구 안의 동네끼리만 비교할 수 있어요.")
    assert "전주시에서는 교1동, 송정동, 송천1동 등과" in got.guide


def test_other_sigungu_name_only_asks_for_dong():
    assert go("강릉시는 어때?").guide == r.ASK_DONG


def test_own_sigungu_name_is_not_a_guide():
    assert go("전주시 1년 중 언제 한산해?") == r.Resolved([HYOJA])


def test_no_name_inherits_previous():
    assert go("주말엔?", [HYOJA, YEOUI]) == r.Resolved([HYOJA, YEOUI])


def test_new_name_replaces_previous():
    assert go("그럼 교1동은?", [HYOJA, YEOUI]).dongs == [HYOJA, GYO]


def test_together_keeps_previous():
    assert go("셋 다 비교하면? 교1동도", [HYOJA, YEOUI]).dongs == [HYOJA, YEOUI, GYO]


def test_more_than_three_keeps_first_three():
    got = go("여의동, 교1동, 송천1동 다 비교해 줘")
    assert got.dongs == [HYOJA, YEOUI, GYO]
    assert got.note == r.TOO_MANY


def test_panel_name_only_goes_back_to_one():
    assert go("효자2동 조용해?", [HYOJA, YEOUI]) == r.Resolved([HYOJA])


def test_previous_of_other_panel_is_ignored():
    assert go("주말엔?", ["x", "y"]).dongs == [HYOJA]


def test_aliases():
    assert r.aliases("효자2동") == {"효자2동", "효자 2동"}
    assert r.aliases("주문진읍") == {"주문진읍", "주문진"}
    assert r.aliases("교동") == {"교동"}
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_chat_resolve.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'explore.chat.resolve'`

- [ ] **Step 4: 구현**

`explore/chat/resolve.py`:

```python
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
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_explore_chat_resolve.py -q`
Expected: PASS (14 passed)

- [ ] **Step 6: 커밋**

```bash
uv run ruff check explore/chat tests/explore_chat_samples.py tests/test_explore_chat_resolve.py
uv run ruff format explore/chat tests/explore_chat_samples.py tests/test_explore_chat_resolve.py
git add explore/chat/__init__.py explore/chat/resolve.py tests/explore_chat_samples.py tests/test_explore_chat_resolve.py
git commit -m "feat: 탐색 v7 채팅 동네 찾기 (같은 시군구 · 이어받기 · 최대 3곳)"
```

---

### Task 2: 비교 재료 정리 (`arrange.py`)

**Files:**
- Create: `explore/chat/arrange.py`
- Test: `tests/test_explore_chat_arrange.py`

**Interfaces:**
- Consumes: `tests/explore_chat_samples.RAWS` · `HYOJA` · `YEOUI` · `GYO`; `queries.fact_list`; `derive.TYPES` · `derive.TYPE_KO`; `graph.METRICS` · `METRIC_KO` · `GRADES`
- Produces:
  - `Dong(code: str, name: str, values: dict[str, float], grades: dict[str, str])`
  - `Order(axis: str, ranked: list[tuple[str, float]], top: str | None)` — `ranked` 는 (동 이름, 값) 높은 순, `top` 은 단독 1등 이름(동점이면 None)
  - `Link(dong: str, other: str, rank: int)`
  - `Pairs(same: list[float], every: list[float])`
  - `dong_of(raw: dict) -> Dong`
  - `orders(dongs: list[Dong], axes: list[str]) -> list[Order]`
  - `order_line(o: Order, by_name: dict[str, Dong]) -> str`
  - `evidence(o: Order, by_name: dict[str, Dong]) -> str`
  - `common(raws: list[dict]) -> list[str]`
  - `links(raws: list[dict], codes: list[str]) -> list[Link]`
  - `pair_distances(vecs: dict[str, tuple[tuple[float, ...], bool]]) -> Pairs`
  - `likeness(a: str, b: str, vecs, pairs: Pairs) -> tuple[int, str] | None`
  - `clean_criteria(raw) -> dict` (키 `chips` · `metric` · `terrain` · `recent`, 칩이 이상하면 `ValueError`)
  - `choose_axes(criteria: dict, dongs: list[Dong], has_link: bool) -> list[str]`
  - 상수 `AXIS_KO`, `GRADE_AXES`, `INLAND = -99.0`, `CHIPS`, `WEB_METRIC`, `INTENT_AXES`, `LINK = "link"`, `MAX_AXES = 3`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_explore_chat_arrange.py`:

```python
"""비교 재료 정리 (스펙 §5-2·§5-3)."""

import pytest

from explore.chat import arrange as a
from tests.explore_chat_samples import GYO, HYOJA, RAWS, YEOUI

H, Y, G = (a.dong_of(RAWS[c]) for c in (HYOJA, YEOUI, GYO))


def by_name(*dongs):
    return {d.name: d for d in dongs}


def test_dong_of_values():
    assert H.values == {
        "quiet": 73,
        "peer": 56,
        "evening": 79,
        "coast": a.INLAND,
        "type:local": 60,
        "type:networking": 50,
        "type:immersion": 36,
        "life": 0.7,
    }
    assert H.grades == {"quiet": "S", "peer": "A", "evening": "S"}
    assert Y.values["coast"] == -0.4


def test_orders_and_top():
    quiet, coast = a.orders([H, Y], ["quiet", "coast"])
    assert quiet == a.Order("quiet", [("효자2동", 73), ("여의동", 57)], "효자2동")
    assert coast.top == "여의동"


def test_tie_has_no_top():
    (evening,) = a.orders([Y, G], ["evening"])
    assert evening.top is None
    assert evening.ranked == [("여의동", 96), ("교1동", 96)]


def test_order_line():
    quiet, coast = a.orders([H, Y], ["quiet", "coast"])
    assert a.order_line(quiet, by_name(H, Y)) == "조용함 높은 순: 효자2동(S) > 여의동(A)"
    assert a.order_line(coast, by_name(H, Y)) == "바다 가까운 순: 여의동(바닷가) > 효자2동(내륙)"
    (evening,) = a.orders([Y, G], ["evening"])
    assert a.order_line(evening, by_name(Y, G)) == "저녁 활기 높은 순: 여의동(S) = 교1동(S)"


def test_evidence():
    quiet, coast, local, life = a.orders([H, Y], ["quiet", "coast", "type:local", "life"])
    names = by_name(H, Y)
    assert a.evidence(quiet, names) == "조용함: 효자2동 S(73점) · 여의동 A(57점)"
    assert a.evidence(coast, names) == "바다 가까움: 여의동 해안선 0.4km · 효자2동 내륙"
    assert a.evidence(local, names) == "로컬살이형: 효자2동 60점 · 여의동 40점"
    assert a.evidence(life, names) == "생활권 분위기: 효자2동 0.70 · 여의동 0.60"


def test_common_is_rare_first_without_region():
    assert a.common([RAWS[HYOJA], RAWS[YEOUI]]) == [
        "평일 9시 한산",
        "1위 로컬살이형",
        "저녁 활기 S",
        "생활권",
    ]


def test_links():
    assert a.links([RAWS[HYOJA], RAWS[YEOUI]], [YEOUI]) == [a.Link("효자2동", "여의동", 2)]
    assert a.links([RAWS[HYOJA], RAWS[YEOUI]], [GYO]) == []


VECS = {
    "a": ((0.1, 0.1, 0.1, 0.5), False),
    "b": ((0.2, 0.1, 0.1, 0.5), False),
    "c": ((0.9, 0.9, 0.9, 0.1), False),
    "d": ((0.5, 0.5, 0.5, 0.5), True),
}


def test_likeness_percentile_and_bucket():
    pairs = a.pair_distances(VECS)
    assert len(pairs.same) == 3 and len(pairs.every) == 6
    assert a.likeness("a", "b", VECS, pairs) == (34, "보통 정도로 닮은 편")
    assert a.likeness("a", "c", VECS, pairs) == (100, "꽤 다른 편")
    assert a.likeness("a", "d", VECS, pairs) == (50, "보통 정도로 닮은 편")  # 바닷가가 다르면 전체 쌍
    assert a.likeness("a", "zz", VECS, pairs) is None


def test_clean_criteria():
    got = a.clean_criteria(
        {
            "chips": ["quiet", "quiet", "coast"],
            "interest": {
                "metric": {"quiet": 0.6, "vibe": "x"},
                "terrain": {"coast": 0.5},
                "recent": ["1", "2", "3", "4", 5],
            },
        }
    )
    assert got == {
        "chips": ["quiet", "coast"],
        "metric": {"quiet": 0.6},
        "terrain": {"coast": 0.5},
        "recent": ["1", "2", "3"],
    }
    assert a.clean_criteria(None) == {"chips": [], "metric": {}, "terrain": {}, "recent": []}
    with pytest.raises(ValueError):
        a.clean_criteria({"chips": ["cheap"]})
    with pytest.raises(ValueError):
        a.clean_criteria("quiet")


def crit(**kw):
    return {"chips": [], "metric": {}, "terrain": {}, "recent": [], **kw}


def test_choose_axes_priority():
    # 칩 > 지표 관심
    assert a.choose_axes(crit(chips=["coast", "quiet"], metric={"peer": 0.6}), [H, Y], False) == [
        "coast",
        "quiet",
        "peer",
    ]
    # 웹 키 vibe → evening, crowd 는 동 축이 없어 버림, 남은 자리는 등급 차 큰 축
    assert a.choose_axes(
        crit(metric={"crowd": 0.9, "vibe": 0.5, "quiet": 0.2}), [H, Y], False
    ) == ["evening", "quiet", "peer"]
    # 바다 관심 > 최근 본 동 연결 > 등급 차
    assert a.choose_axes(crit(terrain={"coast": 0.5, "city": 0.2}), [H, Y], True) == [
        "coast",
        "link",
        "peer",
    ]
    # 아무 기준 없음: 등급 단계 차 → 점수 차 순 (peer 1단계·23점, quiet 1단계·16점, evening 0단계)
    assert a.choose_axes(crit(), [H, Y], False) == ["peer", "quiet", "evening"]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_chat_arrange.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'explore.chat.arrange'`

- [ ] **Step 3: 구현**

`explore/chat/arrange.py`:

```python
"""비교 재료 정리 (스펙 §5-2·§5-3). fetch_dong 원재료 여러 개 → 축 값 · 축별 순서 · 공통점 ·
연결 · 닮음, 사용자 기준 → 비교 축. 순수 함수.

축 값은 클수록 그 축이 강하다(바다는 해안선 거리에 − 를 붙이고, 내륙은 INLAND).
LLM 에는 등급과 순서만, 원래 숫자는 근거 줄에만 쓴다.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass

from explore import derive, queries
from explore.graph import GRADES, METRIC_KO, METRICS

INLAND = -99.0
LINK = "link"  # 최근 본 동과 이어짐. LLM 축이 아니라 규칙 문장 하나
MAX_AXES = 3
GRADE_AXES = frozenset(METRICS)
AXIS_KO = {
    **METRIC_KO,
    "coast": "바다 가까움",
    "life": "생활권 분위기",
    **{f"type:{t}": derive.TYPE_KO[t] for t in derive.TYPES},
}
# 기준 칩 키 → 축 (스펙 §5-3 칩 목록)
CHIPS = {
    "quiet": "quiet",
    "peer": "peer",
    "evening": "evening",
    "coast": "coast",
    "immersion": "type:immersion",
    "networking": "type:networking",
    "local": "type:local",
}
# 웹 관심 기록(features/interest)의 지표 키 → 축. crowd · overnight 는 동 단위 축이 없다
WEB_METRIC = {"quiet": "quiet", "peer": "peer", "vibe": "evening"}
# 동 2~3개일 때 의도 → 축. compare_summary 는 choose_axes 로 고른다
INTENT_AXES = {
    "quiet": ["quiet"],
    "peer": ["peer"],
    "evening": ["evening"],
    "coast": ["coast"],
    "life": ["life"],
    "type": [f"type:{t}" for t in derive.TYPES],
}
# 닮음 백분위(가까운 쪽 %) 구간
LIKENESS = ((30, "많이 닮은 편"), (70, "보통 정도로 닮은 편"), (100, "꽤 다른 편"))


@dataclass(frozen=True)
class Dong:
    code: str
    name: str
    values: dict[str, float]
    grades: dict[str, str]  # 등급 축만


@dataclass(frozen=True)
class Order:
    axis: str
    ranked: list[tuple[str, float]]  # (동 이름, 값) 높은 순. 같으면 원래 순서(패널 먼저)
    top: str | None  # 단독 1등. 1등이 둘 이상이면 None


@dataclass(frozen=True)
class Link:
    dong: str  # 비교 동 이름
    other: str  # 그 동의 비슷한 동(SIMILAR) 이름
    rank: int


@dataclass(frozen=True)
class Pairs:
    same: list[float]  # 바닷가 여부가 같은 동끼리 L2, 오름차순
    every: list[float]  # 모든 쌍


def dong_of(raw: dict) -> Dong:
    rec = raw["rec"]
    d = rec["dong"]
    values: dict[str, float] = {}
    grades: dict[str, str] = {}
    for metric, grade, score, *_ in rec["grades"]:
        values[metric], grades[metric] = score, grade
    values["coast"] = -rec["near"][0] if rec["near"] else INLAND
    by_name = {name: score for _, name, score, _ in rec["types"]}
    for t in derive.TYPES:
        if derive.TYPE_KO[t] in by_name:
            values[f"type:{t}"] = by_name[derive.TYPE_KO[t]]
    life = raw["facts"][d["code"]]["life"]
    if life and life[0].get("value") is not None:
        values["life"] = life[0]["value"]
    return Dong(d["code"], d["name"], values, grades)


def orders(dongs: list[Dong], axes: list[str]) -> list[Order]:
    """축마다 값 높은 순. 값이 있는 동이 둘 미만인 축은 뺀다."""
    out = []
    for axis in axes:
        have = [(d.name, d.values[axis]) for d in dongs if axis in d.values]
        if len(have) < 2:
            continue
        ranked = sorted(have, key=lambda p: -p[1])
        top = ranked[0][0] if ranked[0][1] > ranked[1][1] else None
        out.append(Order(axis, ranked, top))
    return out


def _head(axis: str) -> str:
    if axis == "coast":
        return "바다 가까운 순"
    if axis == "life":
        return "생활권 성격 강한 순"
    if axis.startswith("type:"):
        return f"{AXIS_KO[axis]}에 잘 맞는 순"
    return f"{AXIS_KO[axis]} 높은 순"


def _tag(axis: str, dong: Dong) -> str:
    if axis in GRADE_AXES:
        return f"({dong.grades[axis]})"
    if axis == "coast":
        return "(내륙)" if dong.values["coast"] <= INLAND else "(바닷가)"
    return ""


def order_line(o: Order, by_name: dict[str, Dong]) -> str:
    """LLM 입력 한 줄: '조용함 높은 순: 효자2동(S) > 여의동(A)'. 같은 값은 '='."""
    text = ""
    for i, (name, value) in enumerate(o.ranked):
        if i:
            text += " = " if value == o.ranked[i - 1][1] else " > "
        text += name + _tag(o.axis, by_name[name])
    return f"{_head(o.axis)}: {text}"


def evidence(o: Order, by_name: dict[str, Dong]) -> str:
    """근거 줄 (스펙 §6-3). 원래 숫자는 여기에만 쓴다."""

    def show(name: str, v: float) -> str:
        if o.axis in GRADE_AXES:
            return f"{name} {by_name[name].grades[o.axis]}({v}점)"
        if o.axis == "coast":
            return f"{name} {'내륙' if v <= INLAND else f'해안선 {-v:.1f}km'}"
        if o.axis == "life":
            return f"{name} {v:.2f}"
        return f"{name} {v}점"

    return f"{AXIS_KO[o.axis]}: " + " · ".join(show(n, v) for n, v in o.ranked)


def common(raws: list[dict]) -> list[str]:
    """모든 동이 함께 가진 사실 문구. 이어진 동이 적은(드문) 것부터.
    시군구는 뺀다(같은 시군구끼리라 늘 같다)."""
    lists = [queries.fact_list(r["facts"][r["rec"]["dong"]["code"]]) for r in raws]
    keys = set(lists[0]).intersection(*lists[1:])
    keys = {k for k in keys if not k.startswith("region:")}
    first = lists[0]
    return [first[k][0] for k in sorted(keys, key=lambda k: (first[k][1], k))]


def links(raws: list[dict], codes: list[str]) -> list[Link]:
    """비교 동의 비슷한 동(SIMILAR) 중 codes 에 있는 것. 비교 동 순서 → 순위 순."""
    out = []
    for r in raws:
        me = r["rec"]["dong"]["code"]
        for rank, code, name, _ in sorted(r["rec"]["similar"]):
            if code in codes and code != me:
                out.append(Link(r["rec"]["dong"]["name"], name, rank))
    return out


def pair_distances(vecs: dict[str, tuple[tuple[float, ...], bool]]) -> Pairs:
    """동 쌍 전체의 metric_vec L2 분포. 203동이면 약 2만 쌍이라 한 번 만들어 캐시한다."""
    codes = sorted(vecs)
    same, every = [], []
    for i, x in enumerate(codes):
        for y in codes[i + 1 :]:
            d = math.dist(vecs[x][0], vecs[y][0])
            every.append(d)
            if vecs[x][1] == vecs[y][1]:
                same.append(d)
    return Pairs(sorted(same), sorted(every))


def likeness(
    a: str, b: str, vecs: dict[str, tuple[tuple[float, ...], bool]], pairs: Pairs
) -> tuple[int, str] | None:
    """(가까운 쪽 %, 구간 말). 바닷가 여부가 같으면 같은 쪽 쌍끼리, 다르면 전체 쌍과 비교."""
    if a not in vecs or b not in vecs:
        return None
    pool = pairs.same if vecs[a][1] == vecs[b][1] else pairs.every
    if not pool:
        return None
    d = math.dist(vecs[a][0], vecs[b][0])
    pct = max(1, math.ceil(bisect.bisect_right(pool, d) / len(pool) * 100))
    return pct, next(word for limit, word in LIKENESS if pct <= limit)


def _weights(d) -> dict[str, float]:
    if not isinstance(d, dict):
        return {}
    return {
        k: float(v)
        for k, v in d.items()
        if isinstance(k, str) and isinstance(v, int | float) and not isinstance(v, bool)
    }


def clean_criteria(raw) -> dict:
    """웹 criteria → {chips, metric, terrain, recent}. 칩 키가 허용 목록 밖이면 ValueError."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("criteria 는 객체")
    chips = raw.get("chips") or []
    if not isinstance(chips, list) or any(c not in CHIPS for c in chips):
        raise ValueError(f"모르는 칩: {chips}")
    interest = raw.get("interest") or {}
    if not isinstance(interest, dict):
        raise ValueError("interest 는 객체")
    recent = interest.get("recent") or []
    return {
        "chips": list(dict.fromkeys(chips)),
        "metric": _weights(interest.get("metric")),
        "terrain": _weights(interest.get("terrain")),
        "recent": [c for c in recent if isinstance(c, str)][:3] if isinstance(recent, list) else [],
    }


def _gap(dongs: list[Dong], metric: str) -> tuple[int, float]:
    have = [d for d in dongs if metric in d.values]
    if len(have) < 2:
        return (-1, -1)
    steps = [GRADES.index(d.grades[metric]) for d in have]
    scores = [d.values[metric] for d in have]
    return (max(steps) - min(steps), max(scores) - min(scores))


def choose_axes(criteria: dict, dongs: list[Dong], has_link: bool) -> list[str]:
    """사용자 기준 → 비교 축 (스펙 §5-3). 칩 > 지표 관심 상위 2 > 바다 관심 > 최근 본 동 연결 >
    등급 차 큰 축. 최대 3, 같은 축은 한 번만, 값이 있는 동이 둘 미만인 축은 뺀다."""
    picked = [CHIPS[k] for k in criteria["chips"]]
    liked = sorted(criteria["metric"].items(), key=lambda kv: -kv[1])
    picked += [WEB_METRIC[k] for k, w in liked if k in WEB_METRIC and w > 0][:2]
    terrain = criteria["terrain"]
    if terrain.get("coast", 0) > 0 and terrain["coast"] >= max(terrain.values()):
        picked.append("coast")
    if has_link:
        picked.append(LINK)
    picked += sorted(METRICS, key=lambda m: _gap(dongs, m), reverse=True)
    out: list[str] = []
    for axis in picked:
        usable = axis == LINK or sum(axis in d.values for d in dongs) >= 2
        if usable and axis not in out:
            out.append(axis)
    return out[:MAX_AXES]
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_explore_chat_arrange.py -q`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
uv run ruff check explore/chat tests/test_explore_chat_arrange.py
uv run ruff format explore/chat tests/test_explore_chat_arrange.py
git add explore/chat/arrange.py tests/test_explore_chat_arrange.py
git commit -m "feat: 탐색 v7 채팅 비교 재료 (축별 순서 · 공통점 · 닮음 · 기준 → 축)"
```

---

### Task 3: 비교 답 생성 · 검사 · 근거 (`compare.py` · `llm.py`)

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`langchain-naver`)
- Create: `explore/chat/compare.py`
- Create: `explore/chat/llm.py`
- Test: `tests/test_explore_chat_compare.py`

**Interfaces:**
- Consumes: Task 2 의 `Dong` · `Order` · `Link` · `Pairs` · `AXIS_KO` · `GRADE_AXES` · `order_line` · `evidence` · `likeness`; `ask.CANONICAL` · `ask.NO_DATA`; `brief.BANNED` · `brief.NUMBER` · `brief.GRADE` · `brief.MAX_LEN` · `brief.josa` · `brief._hours`
- Produces:
  - `Generate = Callable[[str, str, type[BaseModel]], BaseModel]` — `(system, user, schema) → schema 인스턴스`
  - `PROMPT_VERSION: str`, `CACHE_TTL = 86400`, `SYSTEM: str`, `JARGON`
  - `answer_model(axes: list[str], names: list[str]) -> type[BaseModel]`
  - `build_prompt(intent: str, dongs: list[Dong], orders: list[Order], common: list[str], links: list[Link]) -> str`
  - `problems(s: dict, by_axis: dict[str, Order], names: list[str], seen: set[str]) -> list[str]`
  - `rule_sentence(o: Order) -> str`
  - `compose(generate: Generate | None, user: str, dongs: list[Dong], orders: list[Order]) -> tuple[list[dict], str]` — 문장 dict 는 `{"text", "evidence", "source"}`, 두 번째 값은 `llm` · `mixed` · `rule`
  - `hours_sentences(raws: list[dict], day: str) -> list[dict]`
  - `months_sentences(raws: list[dict]) -> list[dict]`
  - `similarity_sentences(raws: list[dict], common: list[str], vecs, pairs) -> list[dict]`
  - `link_sentence(link: Link) -> dict`
  - `cache_key(codes: list[str], intent: str, axes: list[str], user: str) -> str`
  - `llm.MODEL = "HCX-007"`, `llm.enabled() -> bool`, `llm.client() -> ChatClovaX`, `llm.generate(system, user, schema) -> BaseModel`

- [ ] **Step 1: 패키지 추가와 생성자 확인**

```bash
uv add 'langchain-naver>=0.1.1,<0.2'
uv run python -c "from langchain_naver import ChatClovaX; c = ChatClovaX(model='HCX-007', api_key='x', thinking={'effort': 'none'}, temperature=0.3, max_tokens=512, timeout=10, max_retries=0); print(c.model_name)"
```
Expected: `HCX-007` (네트워크 호출 없음). 인자 이름이 다르다는 오류가 나면 `ChatClovaX.model_fields` 를 출력해 맞는 이름으로 `llm.client()` 를 고친다.

- [ ] **Step 2: 실패하는 테스트 쓰기**

`tests/test_explore_chat_compare.py`:

```python
"""비교 답 (스펙 §6). 구조화 생성은 가짜 함수로 바꿔 끼운다."""

import pydantic
import pytest

from explore.chat import arrange as a
from explore.chat import compare as c
from explore.chat import llm
from tests.explore_chat_samples import GYO, HYOJA, RAWS, YEOUI

H, Y, G = (a.dong_of(RAWS[x]) for x in (HYOJA, YEOUI, GYO))
NAMES = ["효자2동", "여의동"]
ORDERS = a.orders([H, Y], ["quiet", "coast"])
BY_AXIS = {o.axis: o for o in ORDERS}
GOOD = {"text": "조용한 환경이 우선이면 효자2동이 맞아요.", "axis": "quiet", "pick": "효자2동"}
COAST = {"text": "바다 가까이 머물고 싶다면 여의동이 맞아요.", "axis": "coast", "pick": "여의동"}


def fake(*sentences, calls=None):
    def generate(system, user, schema):
        if calls is not None:
            calls.append(user)
        return schema.model_validate({"sentences": list(sentences)})

    return generate


def test_answer_model_limits_axis_and_pick():
    model = c.answer_model(["quiet", "coast"], NAMES)
    model.model_validate({"sentences": [GOOD]})
    with pytest.raises(pydantic.ValidationError):
        model.model_validate({"sentences": [{**GOOD, "axis": "peer"}]})
    with pytest.raises(pydantic.ValidationError):
        model.model_validate({"sentences": [{**GOOD, "pick": "교1동"}]})
    with pytest.raises(pydantic.ValidationError):
        model.model_validate({"sentences": [GOOD, COAST, GOOD]})  # 축 수보다 많음


def test_build_prompt_has_orders_not_user_words():
    user = c.build_prompt(
        "compare_summary", [H, Y], ORDERS, ["평일 9시 한산"], [a.Link("효자2동", "여의동", 2)]
    )
    assert "고른 동네: 효자2동, 여의동" in user
    assert "quiet: 조용함 높은 순: 효자2동(S) > 여의동(A)" in user
    assert "coast: 바다 가까운 순: 여의동(바닷가) > 효자2동(내륙)" in user
    assert "공통: 평일 9시 한산" in user
    assert "연결: 효자2동은 여의동과 비슷한 동 2위" in user
    assert "73" not in user  # 원래 점수는 넣지 않는다


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        (GOOD, []),
        ({**GOOD, "text": "조용한 곳을 원하면 여의동이 맞아요.", "pick": "여의동"}, ["방향"]),
        ({**GOOD, "text": "조용한 곳을 원하면 그쪽이 맞아요."}, ["이름"]),
        ({**GOOD, "text": "조용함 지표로 보면 효자2동이 맞아요."}, ["금지어"]),
        ({**GOOD, "text": "조용함 73점인 효자2동이 맞아요."}, ["숫자"]),
        ({**GOOD, "text": "S등급인 효자2동이 맞아요."}, ["숫자"]),
        ({**GOOD, "text": "조용한 곳은 효자2동"}, ["해요체"]),
        ({**GOOD, "text": "두 곳 모두 효자2동처럼 조용해요."}, ["금지어"]),
    ],
)
def test_problems(sentence, expected):
    got = c.problems(sentence, BY_AXIS, NAMES, set())
    assert len(got) == len(expected)
    assert all(p.startswith(e) for p, e in zip(got, expected, strict=True))


def test_problems_tie_and_duplicate():
    (evening,) = a.orders([Y, G], ["evening"])
    tie = {"text": "저녁 활기를 원하면 여의동이 맞아요.", "axis": "evening", "pick": "여의동"}
    assert c.problems(tie, {"evening": evening}, ["여의동", "교1동"], set())[0].startswith("방향")
    assert "같은 축 중복" in c.problems(GOOD, BY_AXIS, NAMES, {"quiet"})


def test_rule_sentences():
    quiet, coast = ORDERS
    assert c.rule_sentence(quiet) == "조용함을 원한다면 효자2동이 맞아요."
    assert c.rule_sentence(coast) == "바다 가까움을 원한다면 여의동이 맞아요."
    (evening,) = a.orders([Y, G], ["evening"])
    assert c.rule_sentence(evening) == "저녁 활기는 여의동·교1동이 같은 등급이에요."
    (inland,) = a.orders([H, G], ["coast"])
    assert c.rule_sentence(inland) == "바다 가까움은 효자2동·교1동이 비슷해요."


def test_compose_all_pass():
    sentences, source = c.compose(fake(GOOD, COAST), "u", [H, Y], ORDERS)
    assert source == "llm"
    assert sentences == [
        {
            "text": GOOD["text"],
            "evidence": "조용함: 효자2동 S(73점) · 여의동 A(57점)",
            "source": "llm",
        },
        {
            "text": COAST["text"],
            "evidence": "바다 가까움: 여의동 해안선 0.4km · 효자2동 내륙",
            "source": "llm",
        },
    ]


def test_compose_replaces_wrong_and_fills_missing():
    wrong = {**GOOD, "text": "조용한 곳을 원하면 여의동이 맞아요.", "pick": "여의동"}
    sentences, source = c.compose(fake(wrong), "u", [H, Y], ORDERS)
    assert source == "rule"
    assert [s["text"] for s in sentences] == [
        "조용함을 원한다면 효자2동이 맞아요.",
        "바다 가까움을 원한다면 여의동이 맞아요.",
    ]
    sentences, source = c.compose(fake(COAST), "u", [H, Y], ORDERS)
    assert source == "mixed"
    assert [s["source"] for s in sentences] == ["rule", "llm"]  # 축 순서대로


def test_compose_without_llm_or_on_error():
    assert c.compose(None, "u", [H, Y], ORDERS)[1] == "rule"

    def boom(system, user, schema):
        raise TimeoutError("10초")

    sentences, source = c.compose(boom, "u", [H, Y], ORDERS)
    assert source == "rule" and len(sentences) == 2


def test_hours_months_similarity_link():
    raws = [RAWS[HYOJA], RAWS[YEOUI]]
    assert c.hours_sentences(raws, "weekday")[0] == {
        "text": "효자2동은 평일 12시에 가장 붐비고, 업무시간엔 9시가 한산해요.",
        "evidence": "평일 차량 이동 인구 가장 많은 시각: 12시 100명",
        "source": "rule",
    }
    assert c.hours_sentences(raws, "weekend")[1]["text"] == "여의동은 주말 14시에 차가 가장 많아요."
    assert c.months_sentences(raws)[0]["text"] == (
        "효자2동·여의동은 같은 전주시라 1·2·8월이 가장 한산해요."
    )
    vecs = {HYOJA: ((0.1, 0.1, 0.1, 0.5), False), YEOUI: ((0.2, 0.1, 0.1, 0.5), False),
            GYO: ((0.9, 0.9, 0.9, 0.1), False)}  # fmt: skip
    got = c.similarity_sentences(raws, ["평일 9시 한산", "저녁 활기 S"], vecs, a.pair_distances(vecs))
    assert got[0]["text"] == "효자2동과 여의동은 분위기가 보통 정도로 닮은 편이에요."
    assert got[0]["evidence"] == "두 동네 닮음: 전국 동네 쌍 중 가까운 쪽 34%"
    assert got[1]["text"] == "공통점은 평일 9시 한산 · 저녁 활기 S예요."
    assert c.link_sentence(a.Link("효자2동", "여의동", 2)) == {
        "text": "최근 보신 여의동과 비슷한 쪽은 효자2동이에요.",
        "evidence": "효자2동의 비슷한 동 2위: 여의동",
        "source": "rule",
    }


def test_cache_key_changes_with_prompt_version(monkeypatch):
    key = c.cache_key([YEOUI, HYOJA], "compare_summary", ["quiet", "coast"], "u")
    assert key.startswith(f"explore:chat:a:{HYOJA}+{YEOUI}:compare_summary:quiet+coast:")
    assert key.endswith(f":{llm.MODEL}:{c.PROMPT_VERSION}")
    monkeypatch.setattr(c, "PROMPT_VERSION", "test")
    assert c.cache_key([YEOUI, HYOJA], "compare_summary", ["quiet", "coast"], "u") != key


def test_llm_client_settings(settings):
    settings.CLOVA_API_KEY = ""
    assert not llm.enabled()
    settings.CLOVA_API_KEY = "test-key"
    assert llm.enabled()
    assert llm.client().model_name == "HCX-007"
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_chat_compare.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'explore.chat.compare'`

- [ ] **Step 4: `llm.py` 구현**

`explore/chat/llm.py`:

```python
"""비교 답 구조화 생성 (스펙 §6-1). LangChain ChatClovaX(HCX-007) + json_schema 구조화 출력.

HCX-005 는 구조화 출력을 지원하지 않아 비교 답만 HCX-007 을 쓴다(실측 3/3 성공 · 1.9초).
CLOVA 는 parallel_tool_calls · toolChoice 를 받지 않아(400) json_schema 경로만 쓴다.
키는 settings.CLOVA_API_KEY 를 직접 넘긴다(ChatClovaX 기본 환경 변수 이름과 다르다).
"""

from __future__ import annotations

from django.conf import settings
from langchain_naver import ChatClovaX
from pydantic import BaseModel

MODEL = "HCX-007"
TIMEOUT = 10  # 초. 사용자가 기다리는 요청


def enabled() -> bool:
    return bool(getattr(settings, "CLOVA_API_KEY", ""))


def client() -> ChatClovaX:
    return ChatClovaX(
        model=MODEL,
        api_key=settings.CLOVA_API_KEY,
        thinking={"effort": "none"},
        temperature=0.3,
        max_tokens=512,
        timeout=TIMEOUT,
        max_retries=0,
    )


def generate(system: str, user: str, schema: type[BaseModel]) -> BaseModel:
    structured = client().with_structured_output(schema, method="json_schema")
    return structured.invoke([("system", system), ("user", user)])
```

- [ ] **Step 5: `compare.py` 구현**

`explore/chat/compare.py`:

```python
"""비교 답 (스펙 §6). 순수 함수 — 구조화 생성 함수는 인자로 받는다.

축별 순서(코드 계산)만 프롬프트에 넣고 LLM 은 상황 고르기와 문장만 맡는다.
문장마다 방향(pick 이 그 축 단독 1등) · 이름 · 숫자 · 금지어 · 모양을 검사하고,
걸린 문장은 그 축의 규칙 문장으로 바꾼다. 근거 줄은 코드가 만든다.
시각 · 달 · 닮음 비교는 상황을 고를 게 없어 규칙 문장만 쓴다.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, create_model

from explore import ask, brief
from explore.brief import josa
from explore.chat import arrange, llm
from explore.chat.arrange import AXIS_KO, GRADE_AXES, Dong, Link, Order, Pairs

logger = logging.getLogger(__name__)

Generate = Callable[[str, str, type[BaseModel]], BaseModel]

PROMPT_VERSION = "c1"  # 프롬프트 · 검사를 고치면 올린다 (캐시 키에 들어간다)
CACHE_TTL = 24 * 60 * 60  # 비교 답 캐시 1일 (스펙 §7-2)
JARGON = ("지표", "벡터", "거리", "백분위", "노드", "그래프", "데이터")
NO_CONCLUSION = ("모두", "둘 다", "셋 다")
QUESTION = {"compare_summary": "어느 동네가 나한테 맞아?"}

SYSTEM = "\n".join(
    [
        "너는 워케이션 지도 서비스의 동네 비교 안내자다. 사용자가 준 축별 순서만 근거로 쓴다.",
        "- 문장마다 '…을 원한다면 ○○이 맞아요'처럼 상황 하나와 동네 하나를 고른다.",
        "- 존댓말 해요체, 문장마다 60자 이내.",
        "- 문장마다 다른 axis 를 쓴다. pick 은 그 축 순서의 맨 앞 동네이고, 문장 안에 그 이름을 쓴다.",
        "- 순서가 '='로 같은 축은 쓰지 않는다.",
        "- 동네 이름에서 받는 인상(읍·면은 시골이라 조용하다 등)이나 상식으로 판단하지 않는다.",
        "- 숫자와 등급 글자(S·A·B·C)를 쓰지 않는다.",
        "- '추천', '최고', '가장 좋', '최적', '강추', '핫플'을 쓰지 않는다.",
        "- '지표', '벡터', '거리', '백분위', '노드', '그래프', '데이터' 같은 말을 쓰지 않는다.",
        "- '두 곳 모두'처럼 결론 없는 문장은 쓰지 않는다.",
    ]
)


def answer_model(axes: list[str], names: list[str]) -> type[BaseModel]:
    """이번 턴 축 · 동 이름만 고를 수 있는 응답 스키마. direction 은 받지 않는다(스펙 §6-1)."""
    sentence = create_model(
        "Sentence",
        text=(str, Field(description="상황별 안내 한 문장. 해요체, 60자 이내, 숫자 없이")),
        axis=(Literal[tuple(axes)], Field(description="이 문장이 쓰는 축")),
        pick=(Literal[tuple(names)], Field(description="그 축 순서의 맨 앞 동네")),
    )
    return create_model(
        "CompareAnswer",
        sentences=(list[sentence], Field(min_length=1, max_length=len(axes))),
    )


def build_prompt(
    intent: str, dongs: list[Dong], orders: list[Order], common: list[str], links: list[Link]
) -> str:
    """사용자 문장 없이 의도의 대표 질문 + 코드 정리만."""
    by_name = {d.name: d for d in dongs}
    lines = [
        f"질문: {QUESTION.get(intent) or ask.CANONICAL[intent]} (동네끼리 비교)",
        f"고른 동네: {', '.join(by_name)}",
        f"이번에 볼 기준: {', '.join(AXIS_KO[o.axis] for o in orders)}",
        "",
        "[축별 순서 · 코드 계산]",
        *(f"{o.axis}: {arrange.order_line(o, by_name)}" for o in orders),
    ]
    if common:
        lines.append("공통: " + " · ".join(common[:4]))
    lines += [f"연결: {josa(x.dong)} {josa(x.other, '과/와')} 비슷한 동 {x.rank}위" for x in links]
    return "\n".join(lines)


def problems(s: dict, by_axis: dict[str, Order], names: list[str], seen: set[str]) -> list[str]:
    """문제 목록. 비어 있으면 통과 (스펙 §6-2)."""
    text, axis, pick = s["text"], s["axis"], s["pick"]
    out = []
    order = by_axis.get(axis)
    if order is None or order.top != pick:
        top = order.top if order else None
        out.append(f"방향: {axis} 1등 {top}, 고른 동 {pick}")
    if pick not in text:
        out.append("이름: 문장에 고른 동 이름 없음")
    if axis in seen:
        out.append("같은 축 중복")
    plain = text
    for name in sorted(names, key=len, reverse=True):
        plain = plain.replace(name, "")  # '효자2동' 의 2 가 숫자로 걸리지 않게
    stray = brief.NUMBER.findall(plain) + brief.GRADE.findall(plain)
    if stray:
        out.append(f"숫자·등급: {stray}")
    if len(text) > brief.MAX_LEN:
        out.append(f"{brief.MAX_LEN}자 초과")
    banned = [w for w in (*brief.BANNED, *JARGON, *NO_CONCLUSION) if w in text]
    if banned:
        out.append(f"금지어: {banned}")
    if not text.rstrip().rstrip(".!?").endswith("요"):
        out.append("해요체 아님")
    return out


def rule_sentence(o: Order) -> str:
    ko = AXIS_KO[o.axis]
    if o.top is None:
        tied = "·".join(n for n, v in o.ranked if v == o.ranked[0][1])
        same = "같은 등급이에요" if o.axis in GRADE_AXES else "비슷해요"
        return f"{josa(ko)} {josa(tied, '이/가')} {same}."
    return f"{josa(ko, '을/를')} 원한다면 {josa(o.top, '이/가')} 맞아요."


def _item(text: str, o: Order, by_name: dict[str, Dong], source: str) -> dict:
    return {"text": text, "evidence": arrange.evidence(o, by_name), "source": source}


def compose(
    generate: Generate | None, user: str, dongs: list[Dong], orders: list[Order]
) -> tuple[list[dict], str]:
    """생성 → 검사 → 실패한 문장 · 빠진 축은 규칙 문장. 축 순서대로. (문장들, source)"""
    if not orders:
        return [], "rule"
    by_axis = {o.axis: o for o in orders}
    by_name = {d.name: d for d in dongs}
    names = [d.name for d in dongs]
    items: list[dict] = []
    if generate is not None:
        try:
            reply = generate(SYSTEM, user, answer_model(list(by_axis), names))
            items = [s.model_dump() for s in reply.sentences]
        except Exception as ex:  # HCX 오류 · 시간 초과 · 스키마 불일치 모두 규칙 문장으로
            logger.warning("비교 답 생성 실패: %s", ex)
    done: dict[str, dict] = {}
    seen: set[str] = set()
    for s in items:
        found = problems(s, by_axis, names, seen)
        seen.add(s["axis"])
        if s["axis"] in done:
            continue
        o = by_axis[s["axis"]]
        if found:
            logger.info("비교 답 문장 교체 %r: %s", s["text"], found)
            done[o.axis] = _item(rule_sentence(o), o, by_name, "rule")
        else:
            done[o.axis] = _item(s["text"], o, by_name, "llm")
    out = [done.get(o.axis) or _item(rule_sentence(o), o, by_name, "rule") for o in orders]
    passed = sum(s["source"] == "llm" for s in out)
    source = "llm" if passed == len(out) else "rule" if passed == 0 else "mixed"
    return out, source


def _rule(text: str, evidence: str | None = None) -> dict:
    return {"text": text, "evidence": evidence, "source": "rule"}


def hours_sentences(raws: list[dict], day: str) -> list[dict]:
    """동별 나란히: 평일은 붐비는 시각 · 업무시간 한산 시각, 주말은 붐비는 시각만(스펙 §11)."""
    ko = "평일" if day == "weekday" else "주말"
    out = []
    for r in raws:
        rec = r["rec"]
        hours = brief._hours(rec)
        peak, quiet = hours.get(("PEAK_AT", day)), hours.get(("QUIET_AT", day))
        if peak is None:
            continue
        name = josa(rec["dong"]["name"])
        if day == "weekday" and quiet is not None:
            text = f"{name} 평일 {peak}시에 가장 붐비고, 업무시간엔 {quiet}시가 한산해요."
        else:
            text = f"{name} {ko} {peak}시에 차가 가장 많아요."
        driv = rec["dong"][f"driv_{day}"][peak]
        out.append(_rule(text, f"{ko} 차량 이동 인구 가장 많은 시각: {peak}시 {driv}명"))
    return out or [_rule(ask.NO_DATA)]


def months_sentences(raws: list[dict]) -> list[dict]:
    """같은 시군구라 한산한 달은 늘 같다 → 공통점 한 문장."""
    rec = raws[0]["rec"]
    quiet = sorted(m for m, _, q in rec["months"] if q)
    if not quiet:
        return [_rule(ask.NO_DATA)]
    sgg = rec["region"]["name"]
    names = "·".join(r["rec"]["dong"]["name"] for r in raws)
    text = f"{josa(names)} 같은 {josa(sgg, '이라/라')} {'·'.join(map(str, quiet))}월이 가장 한산해요."
    return [_rule(text, f"월별 붐빔은 {sgg} 단위 값이에요")]


def similarity_sentences(
    raws: list[dict],
    common: list[str],
    vecs: dict[str, tuple[tuple[float, ...], bool]],
    pairs: Pairs,
) -> list[dict]:
    """패널 동과 나머지 동의 닮음 구간 + 공통점."""
    panel = raws[0]["rec"]["dong"]
    out = []
    for r in raws[1:]:
        other = r["rec"]["dong"]
        like = arrange.likeness(panel["code"], other["code"], vecs, pairs)
        if like is None:
            continue
        pct, word = like
        pair = f"{josa(panel['name'], '과/와')} {other['name']}"
        out.append(
            _rule(
                f"{josa(pair)} 분위기가 {word}이에요.",
                f"두 동네 닮음: 전국 동네 쌍 중 가까운 쪽 {pct}%",
            )
        )
    if common:
        labels = " · ".join(common[:3])
        out.append(_rule(f"공통점은 {josa(labels, '이에요/예요')}."))
    return out or [_rule(ask.NO_DATA)]


def link_sentence(link: Link) -> dict:
    return _rule(
        f"최근 보신 {josa(link.other, '과/와')} 비슷한 쪽은 {josa(link.dong, '이에요/예요')}.",
        f"{link.dong}의 비슷한 동 {link.rank}위: {link.other}",
    )


def cache_key(codes: list[str], intent: str, axes: list[str], user: str) -> str:
    """사용자 문장은 넣지 않는다. 프롬프트 해시가 축별 순서 · 공통점 · 연결을 대신한다."""
    digest = hashlib.sha256(user.encode()).hexdigest()[:16]
    return (
        f"explore:chat:a:{'+'.join(sorted(codes))}:{intent}:{'+'.join(axes)}:{digest}"
        f":{llm.MODEL}:{PROMPT_VERSION}"
    )
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_explore_chat_compare.py -q`
Expected: PASS (18 passed)

- [ ] **Step 7: 커밋**

```bash
uv run ruff check explore/chat tests/test_explore_chat_compare.py
uv run ruff format explore/chat tests/test_explore_chat_compare.py
git add pyproject.toml uv.lock explore/chat/compare.py explore/chat/llm.py tests/test_explore_chat_compare.py
git commit -m "feat: 탐색 v7 비교 답 (HCX-007 구조화 출력 · 방향 검사 · 규칙 문장 · 근거 줄)"
```

---

### Task 4: `compare_summary` 의도와 비교 표현

**Files:**
- Modify: `db/seed/ask_vocab.csv`
- Modify: `db/tools/build_embeddings.py:139` (`ASK_ANSWERABLE`)
- Modify: `explore/ask.py` (`GUIDE`)
- Modify: `tests/test_ask_vocab_seed.py`
- Test: `tests/test_explore_ask.py` (안내 한 건 추가)

**Interfaces:**
- Produces: `ask.GUIDE["compare_summary"] = (안내 문구, 예시 3개)`. 의도 찾기(`ask_match.find_intents`)가 `["compare_summary"]` 를 돌려줄 수 있다. `ask.INTENTS` 에는 넣지 않는다(동 1개 답이 없다).
- Consumes: 없음

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_ask_vocab_seed.py` 의 `CANONICAL` 에 한 줄을 더하고, 비교 표현 확인을 더한다:

```python
CANONICAL = {
    # … 기존 10개 그대로 …
    "similar": "비슷한 동네는 어디야?",
    "compare_summary": "두 동네를 비교해 줘",
}
```

`test_seed_follows_spec` 끝에 추가:

```python
    assert phrases["둘 중 어디가 나아?"] == "compare_summary"
    assert phrases["둘이 비슷해?"] == "similar"
```

`tests/test_explore_ask.py:53` 의 `assert set(ask.GUIDE) == set(ask.UNKNOWN)` 을 아래로 바꾸고:

```python
    assert set(ask.GUIDE) == set(ask.UNKNOWN) | {"compare_summary"}
```

같은 파일 끝에 추가:

```python
def test_compare_summary_is_a_guide_for_one_dong():
    text, examples = ask.GUIDE["compare_summary"]
    assert "다른 동네 이름과 함께" in text
    assert ask.examples_for(["compare_summary"]) == examples
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_ask_vocab_seed.py tests/test_explore_ask.py -q`
Expected: FAIL — `KeyError: '둘 중 어디가 나아?'` · `KeyError: 'compare_summary'`

- [ ] **Step 3: 구현**

`db/tools/build_embeddings.py` 의 `ASK_ANSWERABLE` 튜플 끝에 `"compare_summary"` 를 더한다(줄바꿈은 그 파일 모양대로).

`db/seed/ask_vocab.csv` 의 `similar` 줄들 뒤에 추가(`similar` 2개 + `compare_summary` 8개):

```csv
similar,둘이 비슷해?,false
similar,두 동네 닮았어?,false
compare_summary,두 동네를 비교해 줘,true
compare_summary,비교해 줘,false
compare_summary,둘 중 어디가 나아?,false
compare_summary,뭐가 달라?,false
compare_summary,어디가 더 나아?,false
compare_summary,차이가 뭐야?,false
compare_summary,셋 중엔 어디가 맞아?,false
compare_summary,나한테는 어디가 맞아?,false
```

`explore/ask.py` 의 `GUIDE` 딕셔너리 끝에 추가:

```python
    # 동 1개에서 비교를 물으면 (탐색 v7 채팅 스펙 §5-1). 동 2~3개면 채팅이 비교 답을 만든다
    "compare_summary": (
        "다른 동네 이름과 함께 물어봐 주세요. 같은 시군구 안의 동네끼리 비교할 수 있어요.",
        [CANONICAL["quiet"], CANONICAL["type"], CANONICAL["similar"]],
    ),
```

`ask.pick_intents` 는 `INTENTS` 에 있는 의도끼리만 둘을 묶으므로 `compare_summary` 는 늘 혼자 나온다. 기존 `DongAsk` 는 `intents[0] in ask.GUIDE` 로 안내를 내므로 따로 고칠 곳이 없다.

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_ask_vocab_seed.py tests/test_explore_ask.py tests/test_explore_ask_api.py tests/test_explore_ask_match.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
uv run ruff check explore tests && uv run ruff format --check explore tests
git add db/seed/ask_vocab.csv db/tools/build_embeddings.py explore/ask.py tests/test_ask_vocab_seed.py tests/test_explore_ask.py
git commit -m "feat: 동네에 물어보기 의도 compare_summary · 비교 표현 추가"
```

배포 DB 의 `ask_vocab` 임베딩 갱신은 Task 8 에서 한다(CLOVA 키 필요).

---

### Task 5: 채팅 읽기 (`sources` · `data.py`)

**Files:**
- Modify: `explore/sources.py` (`load_dong_names` · `load_metric_vecs` 추가)
- Create: `explore/chat/data.py`
- Modify: `explore/views.py` (`_raw` → `data.dong_raw`)
- Test: `tests/test_explore_chat_data.py`

**Interfaces:**
- Consumes: Task 1 `DongName`, Task 2 `pair_distances` · `Pairs`, 기존 `queries.fetch_dong`, `derive.metric_vec` · `derive.is_coastal`
- Produces:
  - `sources.load_dong_names() -> list[tuple[str, str, str, str]]` — (adm_code, 동 이름, 시군구 코드, 시군구 이름), dong_id 순
  - `sources.load_metric_vecs() -> dict[str, tuple[tuple[float, ...], bool]]` — adm_code → (연간 metric_vec, 바닷가 여부)
  - `data.dong_raw(code: str) -> dict | None` (1시간 캐시, 키 `explore:dong:raw:{code}` 그대로, `GraphUnavailable` 은 위로)
  - `data.dong_names() -> list[DongName]` (1일 캐시)
  - `data.metric_vecs() -> dict` (1일 캐시)
  - `data.pairs() -> Pairs` (1일 캐시)

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_explore_chat_data.py`:

```python
"""채팅 읽기: Postgres 동 이름 사전 · metric_vec, 캐시."""

import pytest
from django.db import connection

from explore import queries, sources
from explore.chat import data
from explore.chat.resolve import DongName


@pytest.fixture
def rows(db):
    with connection.cursor() as c:
        c.execute(
            "INSERT INTO region (region_id, sido_name, sigungu_name, sigungu_code)"
            " VALUES (9, '전북특별자치도', '전주시', '45111')"
        )
        c.execute(
            """INSERT INTO dong (dong_id, region_id, dong_name, adm_code, coast_dist_km, is_excluded)
               VALUES (901, 9, '효자2동', '4511164500', 12.5, false),
                      (902, 9, '여의동', '4511163500', 0.4, false),
                      (903, 9, '코드없음동', NULL, NULL, false),
                      (904, 9, '제외동', '4511100000', 3, true)"""
        )
        c.execute(
            """INSERT INTO metric_def (metric_key, name_ko, higher_is)
               VALUES ('quiet', 'q', 'good'), ('peer', 'p', 'good'),
                      ('evening', 'e', 'good'), ('local_life', 'l', 'good')"""
        )
        for d, (q, p, e, life) in {901: (73, 56, 79, 0.7), 902: (57, 79, 96, 0.6)}.items():
            for key, score in (("quiet", q), ("peer", p), ("evening", e)):
                c.execute(
                    "INSERT INTO metric (scope, unit_id, month, metric_key, score)"
                    " VALUES ('dong', %s, 0, %s, %s)",
                    [d, key, score],
                )
            c.execute(
                "INSERT INTO metric (scope, unit_id, month, metric_key, raw_value)"
                " VALUES ('dong', %s, 0, 'local_life', %s)",
                [d, life],
            )
        c.execute(
            """INSERT INTO dong_type_score (dong_id, type, score, rank, reason)
               VALUES (901, 'local', 60, 1, 'x'), (902, 'networking', 70, 1, 'x')"""
        )


def test_load_dong_names(rows):
    assert sources.load_dong_names() == [
        ("4511164500", "효자2동", "45111", "전주시"),
        ("4511163500", "여의동", "45111", "전주시"),
    ]


def test_load_metric_vecs(rows):
    got = sources.load_metric_vecs()
    assert set(got) == {"4511164500", "4511163500"}
    assert got["4511164500"][0] == pytest.approx((0.73, 0.56, 0.79, 0.7))
    assert got["4511164500"][1] is False
    assert got["4511163500"][1] is True


def test_names_and_pairs_are_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(
        sources,
        "load_dong_names",
        lambda: calls.append("names") or [("1", "가동", "11", "가시")],
    )
    monkeypatch.setattr(
        sources,
        "load_metric_vecs",
        lambda: calls.append("vecs") or {"1": ((0, 0, 0, 0), False), "2": ((1, 0, 0, 0), False)},
    )
    assert data.dong_names() == [DongName("1", "가동", "11", "가시")]
    assert data.dong_names() == [DongName("1", "가동", "11", "가시")]
    assert data.pairs().same == [1.0]
    assert data.pairs().same == [1.0]
    assert calls == ["names", "vecs"]


def test_dong_raw_cache_skips_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(queries, "fetch_dong", lambda code: calls.append(code) or None)
    assert data.dong_raw("x") is None
    assert data.dong_raw("x") is None
    assert calls == ["x", "x"]  # 없는 동은 캐시하지 않는다
```

- [ ] **Step 2: 실패 확인**

Run: `docker compose up -d --wait test-db && uv run pytest tests/test_explore_chat_data.py -q`
Expected: FAIL — `AttributeError: module 'explore.sources' has no attribute 'load_dong_names'`

- [ ] **Step 3: `sources.py` 에 추가**

`explore/sources.py` 의 `load_brief_targets` 아래에 추가한다(`derive` import 가 없으면 `from explore import derive` 를 더한다):

```python
def load_dong_names() -> list[tuple[str, str, str, str]]:
    """채팅 동 이름 사전 (adm_code, 동 이름, 시군구 코드, 시군구 이름). 탐색 대상 동만."""
    return _rows(
        f"""SELECT d.adm_code, d.dong_name, r.sigungu_code, r.sigungu_name
              FROM dong d JOIN region r ON r.region_id = d.region_id
             WHERE {DONG_OK}
               AND EXISTS (SELECT 1 FROM dong_type_score t WHERE t.dong_id = d.dong_id)
             ORDER BY d.dong_id"""
    )


def load_metric_vecs() -> dict[str, tuple[tuple[float, ...], bool]]:
    """adm_code → (연간 metric_vec, 바닷가 여부). 채팅 닮음 백분위용 (derive.metric_vec 와 같은 정의)."""
    values: dict[int, dict[str, float]] = defaultdict(dict)
    for d, key, score, raw in _rows(
        """SELECT unit_id, metric_key, score, raw_value FROM metric
            WHERE scope = 'dong' AND month = 0
              AND metric_key IN ('quiet', 'peer', 'evening', 'local_life')"""
    ):
        value = raw if key == "local_life" else score
        if value is not None:
            values[d][key] = value
    out = {}
    for d, code, km in _rows(f"SELECT dong_id, adm_code, coast_dist_km FROM dong d WHERE {DONG_OK}"):
        v = values.get(d, {})
        if all(k in v for k in ("quiet", "peer", "evening", "local_life")):
            vec = derive.metric_vec(v["quiet"], v["peer"], v["evening"], v["local_life"])
            out[code] = (vec, derive.is_coastal(_float(km)))
    return out
```

- [ ] **Step 4: `data.py` 만들기**

`explore/chat/data.py`:

```python
"""채팅이 쓰는 읽기 (캐시 포함). 동 원재료 1시간, 동 이름 사전 · 닮음 분포 1일.

Redis 가 죽어도 SafeRedisCache 가 캐시 없이 진행시킨다. Neo4j 문제(GraphUnavailable)는 위로 올린다.
"""

from __future__ import annotations

from django.core.cache import cache

from explore import queries, sources
from explore.chat import arrange
from explore.chat.resolve import DongName

RAW_TTL = 60 * 60
STATIC_TTL = 24 * 60 * 60


def dong_raw(code: str) -> dict | None:
    """fetch_dong 원재료. 없는 동(None)은 캐시하지 않는다. 키는 v6.1 질문 API 와 같이 쓴다."""
    key = f"explore:dong:raw:{code}"
    raw = cache.get(key)
    if raw is None:
        raw = queries.fetch_dong(code)
        if raw is not None:
            cache.set(key, raw, RAW_TTL)
    return raw


def _static(key: str, load):
    hit = cache.get(key)
    if hit is None:
        hit = load()
        cache.set(key, hit, STATIC_TTL)
    return hit


def dong_names() -> list[DongName]:
    return _static(
        "explore:chat:names", lambda: [DongName(*row) for row in sources.load_dong_names()]
    )


def metric_vecs() -> dict[str, tuple[tuple[float, ...], bool]]:
    return _static("explore:chat:vecs", sources.load_metric_vecs)


def pairs() -> arrange.Pairs:
    return _static("explore:chat:pairs", lambda: arrange.pair_distances(metric_vecs()))
```

- [ ] **Step 5: `views._raw` 를 옮긴 함수로 바꾸기**

`explore/views.py` 에서 `_raw` 함수 정의를 지우고 import 와 호출을 바꾼다:

```python
from explore.chat import data as chat_data
```

`DongAsk.post` 의 `raw = _raw(dong_id)` → `raw = chat_data.dong_raw(dong_id)`.

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_explore_chat_data.py tests/test_explore_ask_api.py -q`
Expected: PASS (`test_explore_ask_api` 의 `explore:dong:raw:` 캐시 확인도 그대로 통과)

- [ ] **Step 7: 커밋**

```bash
uv run ruff check explore tests && uv run ruff format --check explore tests
git add explore/sources.py explore/chat/data.py explore/views.py tests/test_explore_chat_data.py
git commit -m "feat: 탐색 v7 채팅 읽기 (동 이름 사전 · 연간 metric_vec · 닮음 분포 캐시)"
```

---

### Task 6: LangGraph 흐름과 대화 기억 (`flow.py`)

**Files:**
- Modify: `pyproject.toml`, `uv.lock` (`langgraph` · `langgraph-checkpoint-redis`)
- Modify: `compose.yaml` (`redis:7` → `redis:8`)
- Create: `explore/chat/flow.py`
- Test: `tests/test_explore_chat_flow.py`

**Interfaces:**
- Consumes: Task 1 `resolve` · `DongName`, Task 2 `dong_of` · `orders` · `common` · `links` · `choose_axes` · `INTENT_AXES` · `LINK` · `Pairs`, Task 3 `compose` · `build_prompt` · `cache_key` · `CACHE_TTL` · `hours_sentences` · `months_sentences` · `similarity_sentences` · `link_sentence` · `Generate`, Task 4 `ask.GUIDE["compare_summary"]`, `ask.UNKNOWN` · `ask.GUIDE` · `ask.NO_DATA` · `ask.examples_for`
- Produces:
  - `ChatState` (TypedDict)
  - `Deps(names, raw, find_intents, single, generate, vecs, pairs)` (frozen dataclass)
    - `names: Callable[[], list[DongName]]`
    - `raw: Callable[[str], dict | None]`
    - `find_intents: Callable[[str], tuple[list[str], float]]`
    - `single: Callable[[str, dict, list[str], str], tuple[list[str], str]]` — (동 코드, 원재료, 의도들, 질문) → (문장들, source)
    - `generate: Generate | None`
    - `vecs: Callable[[], dict]`, `pairs: Callable[[], Pairs]`
  - `settle_intents(found: list[str], prev: list[str], named: bool, count: int) -> list[str]`
  - `build(deps: Deps) -> StateGraph`
  - `saver()` — 체크포인터 (Redis 또는 메모리, 한 번 만들어 둔다)
  - `run(graph: StateGraph, conversation_id: str | None, panel: str, question: str, criteria: dict) -> tuple[str, dict]`
  - `respond(conversation_id: str, state: dict, names: list[DongName]) -> dict` — §8 응답 본문
  - 상수 `TTL_MINUTES = 30`, `COMPARE_EXAMPLES`

- [ ] **Step 1: 패키지 · Redis 8**

```bash
uv add 'langgraph>=1.2.22,<2' 'langgraph-checkpoint-redis>=0.6.0,<0.7'
uv run python -c "import inspect; from langgraph.checkpoint.redis import RedisSaver; from langgraph.pregel import Pregel; print(inspect.signature(RedisSaver.__init__)); print('durability' in inspect.signature(Pregel.invoke).parameters)"
```
Expected: 생성자에 `redis_url` · `ttl` 인자가 있고, 두 번째 줄이 `True`. 다르면 `flow.saver()` 를 `RedisSaver.from_conn_string` 으로(컨텍스트 매니저를 열어 둔 채 보관), `durability` 가 없으면 버전 고정을 다시 본다.

`compose.yaml` 의 redis 서비스:

```yaml
  # 캐시 (홈 검색 · 탐색 · throttle 공용) + 탐색 채팅 대화 기억(LangGraph 체크포인터).
  # 체크포인터가 RedisJSON · RediSearch 를 써서 8 이상이 필요하다 (7 은 FT.INFO 없음으로 시작 실패)
  # 앱은 REDIS_URL=redis://localhost:6379/0. 단위 테스트는 Redis 없이 돈다 (settings_test 는 메모리 캐시)
  redis:
    image: redis:8
```

(`command` · `ports` · `healthcheck` 는 그대로.)

- [ ] **Step 2: 실패하는 테스트 쓰기**

`tests/test_explore_chat_flow.py`:

```python
"""LangGraph 흐름 (스펙 §3·§7-1·§7-2-1). 의도 · 동 1개 답 · 생성은 가짜, 체크포인터는 메모리."""

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from explore import ask
from explore.chat import arrange, flow
from explore.chat.resolve import TOO_MANY
from tests.explore_chat_samples import GYO, HYOJA, NAMES, RAWS, YEOUI

VECS = {
    HYOJA: ((0.73, 0.56, 0.79, 0.7), False),
    YEOUI: ((0.57, 0.79, 0.96, 0.6), False),
    GYO: ((0.4, 0.6, 0.96, 0.8), False),
}
INTENTS = {
    "이 동네 조용해?": ["quiet"],
    "주말엔?": ["weekend_hours"],
    "여의동이랑 비교해 줘": ["compare_summary"],
    "비교해 줘": ["compare_summary"],
    "여의동, 교1동, 송천1동 다 비교해 줘": ["compare_summary"],
    "둘이 비슷해?": ["similar"],
}


@pytest.fixture(autouse=True)
def memory_saver(settings, monkeypatch):
    settings.REDIS_URL = ""
    monkeypatch.setattr(flow, "_saver", None)


def deps(generate=None, singles=None):
    def single(code, raw, intents, question):
        if singles is not None:
            singles.append((code, intents))
        return ["한 동 답이에요."], "llm"

    return flow.Deps(
        names=lambda: NAMES,
        raw=RAWS.get,
        find_intents=lambda q: (INTENTS.get(q, ["unknown_other"]), 0.9),
        single=single,
        generate=generate,
        vecs=lambda: VECS,
        pairs=lambda: arrange.pair_distances(VECS),
    )


def ask_(graph, question, cid=None, panel=HYOJA, criteria=None):
    criteria = criteria or arrange.clean_criteria(None)
    return flow.run(graph, cid, panel, question, criteria)


def test_settle_intents():
    assert flow.settle_intents(["unknown_other"], ["quiet"], True, 2) == ["quiet"]
    assert flow.settle_intents(["unknown_other"], [], True, 2) == ["compare_summary"]
    assert flow.settle_intents(["unknown_other"], ["unknown_place"], True, 2) == [
        "compare_summary"
    ]
    assert flow.settle_intents(["unknown_other"], ["quiet"], False, 2) == ["unknown_other"]
    assert flow.settle_intents(["weekend_hours"], ["quiet"], True, 2) == ["weekend_hours"]


def test_three_turns_carry_dongs_and_intent():
    singles = []
    graph = flow.build(deps(singles=singles))
    cid, s1 = ask_(graph, "이 동네 조용해?")
    assert s1["dongs"] == [HYOJA]
    assert s1["sentences"] == [{"text": "한 동 답이에요.", "evidence": None, "source": "llm"}]
    assert singles == [(HYOJA, ["quiet"])]

    cid2, s2 = ask_(graph, "여의동이랑 비교하면?", cid)  # 의도를 못 찾음 → 직전 의도(quiet)
    assert cid2 == cid
    assert s2["dongs"] == [HYOJA, YEOUI]
    assert s2["intents"] == ["quiet"]
    assert s2["sentences"] == [
        {
            "text": "조용함을 원한다면 효자2동이 맞아요.",
            "evidence": "조용함: 효자2동 S(73점) · 여의동 A(57점)",
            "source": "rule",
        }
    ]

    _, s3 = ask_(graph, "주말엔?", cid)  # 동 이름 없음 → 두 동 이어받기
    assert s3["dongs"] == [HYOJA, YEOUI]
    assert [x["text"] for x in s3["sentences"]] == [
        "효자2동은 주말 14시에 차가 가장 많아요.",
        "여의동은 주말 14시에 차가 가장 많아요.",
    ]
    assert len(s3["turns"]) == 3


def test_question_is_not_checkpointed():
    graph = flow.build(deps())
    cid, state = ask_(graph, "아이와 함께 여의동이랑 비교해 줘")
    assert state["question"] == ""
    saved = list(flow.saver().list({"configurable": {"thread_id": cid}}))
    assert saved
    assert "아이와" not in repr(saved)


def test_other_panel_starts_new_conversation():
    graph = flow.build(deps())
    cid, _ = ask_(graph, "여의동이랑 비교해 줘")
    cid2, state = ask_(graph, "주말엔?", cid, panel=YEOUI)
    assert cid2 != cid
    assert state["dongs"] == [YEOUI]


def test_redis_error_runs_without_memory(monkeypatch):
    def broken():
        raise RedisConnectionError("down")

    monkeypatch.setattr(flow, "saver", broken)
    cid, state = ask_(flow.build(deps()), "여의동이랑 비교해 줘")
    assert len(cid) == 32
    assert state["dongs"] == [HYOJA, YEOUI]
    assert state["sentences"]


def test_guides():
    graph = flow.build(deps())
    _, other = ask_(graph, "경포동이랑 비교해 줘")
    assert other["guide"].startswith("지금은 같은 시군구 안의 동네끼리만")
    assert other["sentences"] == []
    _, single = ask_(graph, "비교해 줘")
    assert single["guide"] == ask.GUIDE["compare_summary"][0]
    _, unknown = ask_(graph, "맛집 있어?")
    assert unknown["guide"] == ask.GUIDE["unknown_other"][0]


def test_too_many_note_comes_first():
    _, state = ask_(flow.build(deps()), "여의동, 교1동, 송천1동 다 비교해 줘")
    assert state["dongs"] == [HYOJA, YEOUI, GYO]
    assert state["sentences"][0] == {"text": TOO_MANY, "evidence": None, "source": "rule"}


def test_compare_uses_criteria_and_cache():
    calls = []

    def generate(system, user, schema):
        calls.append(user)
        return schema.model_validate(
            {
                "sentences": [
                    {"text": "바다 가까이라면 여의동이 맞아요.", "axis": "coast", "pick": "여의동"}
                ]
            }
        )

    graph = flow.build(deps(generate=generate))
    criteria = arrange.clean_criteria({"chips": ["coast"]})
    _, first = ask_(graph, "여의동이랑 비교해 줘", criteria=criteria)
    assert first["sentences"][0]["text"] == "바다 가까이라면 여의동이 맞아요."
    assert first["source"] == "mixed"  # coast 는 LLM, 나머지 축은 규칙 문장
    assert "바다 가까운 순" in calls[0]
    _, again = ask_(graph, "여의동이랑 비교해 줘", criteria=criteria)  # 새 대화, 같은 비교
    assert again["sentences"] == first["sentences"]
    assert len(calls) == 1  # 1일 캐시


def test_similar_with_two_dongs():
    graph = flow.build(deps())
    cid, _ = ask_(graph, "여의동이랑 비교해 줘")
    _, state = ask_(graph, "둘이 비슷해?", cid)
    assert state["sentences"][0]["text"].startswith("효자2동과 여의동은 분위기가")


def test_respond_shape():
    graph = flow.build(deps())
    cid, state = ask_(graph, "여의동이랑 비교해 줘")
    body = flow.respond(cid, state, NAMES)
    assert body["conversationId"] == cid
    assert body["dongs"] == [{"id": HYOJA, "name": "효자2동"}, {"id": YEOUI, "name": "여의동"}]
    assert body["intent"] == "compare_summary"
    assert body["guide"] is None
    assert body["examples"] == list(flow.COMPARE_EXAMPLES)
    assert set(body["sentences"][0]) == {"text", "evidence", "source"}
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_chat_flow.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'explore.chat.flow'`

- [ ] **Step 4: 구현**

`explore/chat/flow.py`:

```python
"""탐색 v7 동 패널 채팅 흐름 (스펙 §3). LangGraph StateGraph + 체크포인터.

START → resolve_dongs ─(안내)→ finish
                      └→ find_intent → guide | single_answer | compare_answer → finish → END

노드는 상태를 받아 바뀐 필드만 돌려준다. 바깥 의존(읽기 · 의도 · 생성)은 Deps 로 받는다.
상태에는 의도 · 동네만 남기고(원재료는 1시간 캐시에서 다시 읽는다), 질문은 finish 에서 비운다.
실행은 durability="exit" 라 끝난 상태 하나만 저장된다 (스펙 §7-2-1).
"""

from __future__ import annotations

import logging
import operator
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, TypedDict

from django.conf import settings
from django.core.cache import cache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from redis.exceptions import RedisError

from explore import ask
from explore.chat import arrange, compare
from explore.chat.resolve import DongName, resolve

logger = logging.getLogger(__name__)

TTL_MINUTES = 30  # 대화 기억 (스펙 §7-1). 읽을 때마다 다시 30분
COMPARE_EXAMPLES = ("둘이 비슷해?", "주말엔 몇 시가 붐벼?", "언제 가면 한산해?")
RULE_ONLY = ("weekday_hours", "weekend_hours", "months", "similar")


class ChatState(TypedDict, total=False):
    question: str  # 이번 질문. finish 에서 비운다
    panel: str
    criteria: dict  # arrange.clean_criteria 결과
    dongs: list[str]  # 이번 턴 비교 대상 (패널 동 먼저)
    named: bool  # 질문에 같은 시군구 동 이름이 있었다
    intents: list[str]
    guide: str | None
    note: str | None
    sentences: list[dict]  # {text, evidence, source}
    source: str  # llm · mixed · rule · guide
    turns: Annotated[list[dict], operator.add]  # {intents, dongs} 턴 기록


@dataclass(frozen=True)
class Deps:
    names: Callable[[], list[DongName]]
    raw: Callable[[str], dict | None]
    find_intents: Callable[[str], tuple[list[str], float]]
    single: Callable[[str, dict, list[str], str], tuple[list[str], str]]
    generate: compare.Generate | None
    vecs: Callable[[], dict]
    pairs: Callable[[], arrange.Pairs]


def settle_intents(found: list[str], prev: list[str], named: bool, count: int) -> list[str]:
    """의도를 못 찾았고(unknown_other) 질문에 동 이름이 있으면 직전 의도를 잇는다.
    직전 의도도 없거나 답할 수 없는 의도였으면, 비교 중일 때 compare_summary."""
    if found != ["unknown_other"] or not named:
        return found
    if prev and prev[0] not in ask.UNKNOWN:
        return prev
    return ["compare_summary"] if count >= 2 else found


def _sentences(texts: list[str], source: str) -> list[dict]:
    return [{"text": t, "evidence": None, "source": source} for t in texts]


def build(deps: Deps) -> StateGraph:
    def resolve_dongs(s: ChatState) -> dict:
        names = deps.names()
        panel = next(n for n in names if n.code == s["panel"])  # 뷰가 미리 확인한다
        r = resolve(s["question"], panel, s.get("dongs") or [], names)
        return {
            "dongs": r.dongs,
            "named": r.named,
            "guide": r.guide,
            "note": r.note,
            "sentences": [],
            "source": "guide" if r.guide else "",
        }

    def find_intent(s: ChatState) -> dict:
        found, _ = deps.find_intents(s["question"])
        prev = s.get("intents") or []
        return {"intents": settle_intents(found, prev, s["named"], len(s["dongs"]))}

    def route(s: ChatState) -> str:
        first = s["intents"][0]
        if len(s["dongs"]) >= 2 and first not in ask.UNKNOWN:
            return "compare_answer"
        if first in ask.GUIDE:
            return "guide"
        return "single_answer"

    def guide(s: ChatState) -> dict:
        return {"guide": ask.GUIDE[s["intents"][0]][0], "source": "guide"}

    def single_answer(s: ChatState) -> dict:
        code = s["dongs"][0]
        texts, source = deps.single(code, deps.raw(code), s["intents"], s["question"])
        return {"sentences": _sentences(texts, source), "source": source}

    def compare_answer(s: ChatState) -> dict:
        raws = [deps.raw(c) for c in s["dongs"]]
        intent = s["intents"][0]
        if intent in RULE_ONLY:
            if intent == "months":
                out = compare.months_sentences(raws)
            elif intent == "similar":
                common = arrange.common(raws)
                out = compare.similarity_sentences(raws, common, deps.vecs(), deps.pairs())
            else:
                out = compare.hours_sentences(raws, intent.split("_")[0])
            return {"sentences": out, "source": "rule"}
        dongs = [arrange.dong_of(r) for r in raws]
        criteria = s.get("criteria") or arrange.clean_criteria(None)
        recent = [c for c in criteria["recent"] if c not in s["dongs"]]
        recent_links = arrange.links(raws, recent)
        axes = arrange.INTENT_AXES.get(intent) or arrange.choose_axes(
            criteria, dongs, bool(recent_links)
        )
        orders = arrange.orders(dongs, [a for a in axes if a != arrange.LINK])
        user = compare.build_prompt(
            intent, dongs, orders, arrange.common(raws), arrange.links(raws, s["dongs"])
        )
        key = compare.cache_key(s["dongs"], intent, axes, user)
        hit = cache.get(key)
        if hit is None:
            hit = compare.compose(deps.generate, user, dongs, orders)
            if hit[1] != "rule":  # HCX 가 잠깐 안 될 때의 규칙 답은 1일 남기지 않는다
                cache.set(key, hit, compare.CACHE_TTL)
        sentences, source = hit
        if arrange.LINK in axes and recent_links:
            sentences = [*sentences, compare.link_sentence(recent_links[0])]
        if not sentences:
            sentences = _sentences([ask.NO_DATA], "rule")
        logger.info("비교 답 dongs=%s intent=%s axes=%s source=%s", s["dongs"], intent, axes, source)
        return {"sentences": sentences, "source": source}

    def finish(s: ChatState) -> dict:
        sentences = s.get("sentences") or []
        if s.get("note"):
            sentences = [*_sentences([s["note"]], "rule"), *sentences]
        return {
            "question": "",
            "sentences": sentences,
            "turns": [{"intents": s.get("intents") or [], "dongs": s["dongs"]}],
        }

    g = StateGraph(ChatState)
    for name, fn in (
        ("resolve_dongs", resolve_dongs),
        ("find_intent", find_intent),
        ("guide", guide),
        ("single_answer", single_answer),
        ("compare_answer", compare_answer),
        ("finish", finish),
    ):
        g.add_node(name, fn)
    g.add_edge(START, "resolve_dongs")
    g.add_conditional_edges(
        "resolve_dongs",
        lambda s: "finish" if s.get("guide") else "find_intent",
        ["finish", "find_intent"],
    )
    g.add_conditional_edges("find_intent", route, ["guide", "single_answer", "compare_answer"])
    for name in ("guide", "single_answer", "compare_answer"):
        g.add_edge(name, "finish")
    g.add_edge("finish", END)
    return g


_saver = None
_lock = threading.Lock()


def saver():
    """REDIS_URL 이 있으면 RedisSaver(redis:8), 없으면 메모리. 처음 쓸 때 한 번 만들고 setup 한다.
    Redis 가 죽어 있으면 RedisError 가 나고, 다음 요청에서 다시 만든다."""
    global _saver
    with _lock:
        if _saver is None:
            if settings.REDIS_URL:
                from langgraph.checkpoint.redis import RedisSaver

                redis_saver = RedisSaver(
                    redis_url=settings.REDIS_URL,
                    ttl={"default_ttl": TTL_MINUTES, "refresh_on_read": True},
                )
                redis_saver.setup()
                _saver = redis_saver
            else:
                _saver = InMemorySaver()
        return _saver


def run(
    graph: StateGraph, conversation_id: str | None, panel: str, question: str, criteria: dict
) -> tuple[str, dict]:
    """(대화 id, 끝난 상태). 없는 대화이거나 패널이 다르면 새 id.
    체크포인터(Redis) 오류면 기억 없이 한 번 더 실행한다 (스펙 §7-1)."""
    inp = {"question": question, "panel": panel, "criteria": criteria}
    try:
        app = graph.compile(checkpointer=saver())
        cid = conversation_id
        if cid:
            prev = app.get_state({"configurable": {"thread_id": cid}}).values
            if prev.get("panel") != panel:
                cid = None
        cid = cid or uuid.uuid4().hex
        state = app.invoke(inp, {"configurable": {"thread_id": cid}}, durability="exit")
        return cid, state
    except RedisError as ex:
        logger.warning("채팅 대화 기억(Redis) 실패 → 기억 없이 답함: %s", ex)
        return conversation_id or uuid.uuid4().hex, graph.compile().invoke(inp)


def respond(conversation_id: str, state: dict, names: list[DongName]) -> dict:
    """스펙 §8 응답 본문."""
    by_code = {n.code: n.name for n in names}
    intents = state.get("intents") or ["unknown_other"]
    dongs = state.get("dongs") or []
    if len(dongs) >= 2 and not state.get("guide"):
        examples = list(COMPARE_EXAMPLES)
    else:
        examples = ask.examples_for(intents)
    return {
        "conversationId": conversation_id,
        "dongs": [{"id": c, "name": by_code.get(c, c)} for c in dongs],
        "intent": intents[0],
        "sentences": [
            {"text": s["text"], "evidence": s["evidence"], "source": s["source"]}
            for s in state.get("sentences") or []
        ],
        "guide": state.get("guide"),
        "examples": examples,
    }
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_explore_chat_flow.py -q`
Expected: PASS (10 passed)

`test_question_is_not_checkpointed` 가 실패하면(입력 쓰기가 남는 경우) `durability="exit"` 가 `invoke` 에 들어갔는지, `finish` 가 `question` 을 비우는지 확인한다. 그래도 남으면 그 사실을 기록하고 사용자에게 알린다(스펙 §7-2-1 결정에 영향).

- [ ] **Step 6: 커밋**

```bash
uv run ruff check explore tests && uv run ruff format --check explore tests
git add pyproject.toml uv.lock compose.yaml explore/chat/flow.py tests/test_explore_chat_flow.py
git commit -m "feat: 탐색 v7 채팅 LangGraph 흐름 · Redis 체크포인터 (redis:8, 질문 원문 저장 안 함)"
```

---

### Task 7: `POST /api/explore/chat`

**Files:**
- Modify: `explore/views.py`
- Modify: `explore/urls.py`
- Test: `tests/test_explore_chat_api.py`

**Interfaces:**
- Consumes: Task 5 `chat_data.*`, Task 6 `flow.build` · `flow.run` · `flow.respond` · `flow.Deps`, Task 2 `arrange.clean_criteria`, Task 3 `llm.enabled` · `llm.generate`, 기존 `ask_match.find_intents` · `ask.answer` · `AskBurstThrottle` · `AskDayThrottle`
- Produces:
  - `views.answer_one(dong_id: str, raw: dict, intents: list[str], question: str) -> tuple[list[str], str]` (기존 `DongAsk._answer` 를 옮긴 것)
  - `views.ExploreChat` (APIView), URL 이름 `explore-chat`
  - 오류 코드: `QUESTION_INVALID` · `PANEL_INVALID` · `CRITERIA_INVALID`(400), `DONG_NOT_FOUND`(404), `TOO_MANY_REQUESTS`(429), `GRAPH_UNAVAILABLE`(503)

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/test_explore_chat_api.py`:

```python
"""POST /api/explore/chat (스펙 §8). 읽기 · 의도 · 생성을 바꿔 끼워 Redis · Neo4j · HCX 없이 본다."""

import pytest

from explore import ask_match, sources, views
from explore.chat import arrange, flow, llm
from explore.chat import data as chat_data
from explore.graphdb import GraphUnavailable
from tests.explore_chat_samples import HYOJA, NAMES, RAWS, YEOUI

URL = "/api/explore/chat"
INTENTS = {"여의동이랑 비교하면?": ["compare_summary"], "주말엔?": ["weekend_hours"]}


@pytest.fixture(autouse=True)
def world(settings, monkeypatch):
    settings.REDIS_URL = ""
    monkeypatch.setattr(flow, "_saver", None)
    monkeypatch.setattr(chat_data, "dong_names", lambda: NAMES)
    monkeypatch.setattr(chat_data, "dong_raw", RAWS.get)
    vecs = {HYOJA: ((0.73, 0.56, 0.79, 0.7), False), YEOUI: ((0.57, 0.79, 0.96, 0.6), False)}
    monkeypatch.setattr(chat_data, "metric_vecs", lambda: vecs)
    monkeypatch.setattr(chat_data, "pairs", lambda: arrange.pair_distances(vecs))
    monkeypatch.setattr(
        ask_match, "find_intents", lambda q: (INTENTS.get(q, ["unknown_other"]), 0.9)
    )
    monkeypatch.setattr(views, "answer_one", lambda *a: (["한 동 답이에요."], "llm"))
    monkeypatch.setattr(llm, "enabled", lambda: False)

    def no_log(*a):
        raise AssertionError("채팅은 질문 원문을 기록하지 않는다")

    monkeypatch.setattr(sources, "log_question", no_log)


def post(client, question="여의동이랑 비교하면?", **body):
    payload = {"panel": HYOJA, "question": question, **body}
    return client.post(URL, payload, content_type="application/json")


def test_compare_answer(client):
    res = post(client, criteria={"chips": ["quiet"]})
    assert res.status_code == 200
    body = res.json()
    assert len(body["conversationId"]) == 32
    assert body["dongs"] == [{"id": HYOJA, "name": "효자2동"}, {"id": YEOUI, "name": "여의동"}]
    assert body["intent"] == "compare_summary"
    assert body["sentences"][0] == {
        "text": "조용함을 원한다면 효자2동이 맞아요.",
        "evidence": "조용함: 효자2동 S(73점) · 여의동 A(57점)",
        "source": "rule",
    }
    assert body["guide"] is None
    assert len(body["examples"]) == 3


def test_conversation_continues(client):
    cid = post(client).json()["conversationId"]
    body = post(client, "주말엔?", conversationId=cid).json()
    assert body["conversationId"] == cid
    assert [d["id"] for d in body["dongs"]] == [HYOJA, YEOUI]
    assert body["intent"] == "weekend_hours"


def test_unknown_question_guides(client):
    body = post(client, "이 동네 어때?").json()
    assert body["dongs"] == [{"id": HYOJA, "name": "효자2동"}]
    assert body["intent"] == "unknown_other"
    assert body["guide"] is not None


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"panel": HYOJA, "question": ""}, "QUESTION_INVALID"),
        ({"panel": HYOJA, "question": "가" * 101}, "QUESTION_INVALID"),
        ({"question": "주말엔?"}, "PANEL_INVALID"),
        ({"panel": HYOJA, "question": "주말엔?", "criteria": {"chips": ["cheap"]}}, "CRITERIA_INVALID"),
    ],
)
def test_bad_request(client, payload, code):
    res = client.post(URL, payload, content_type="application/json")
    assert res.status_code == 400
    assert res.json()["error"]["code"] == code


def test_unknown_panel(client):
    res = post(client, panel="0000000000")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DONG_NOT_FOUND"


def test_graph_unavailable(client, monkeypatch):
    def down(code):
        raise GraphUnavailable("neo4j")

    monkeypatch.setattr(chat_data, "dong_raw", down)
    res = post(client)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "GRAPH_UNAVAILABLE"


def test_burst_throttle(client):
    for _ in range(10):
        assert post(client).status_code == 200
    res = post(client)
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert int(res["Retry-After"]) > 0
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_chat_api.py -q`
Expected: FAIL — 404 (`/api/explore/chat` 경로 없음)

- [ ] **Step 3: 뷰 구현**

`explore/views.py` 를 고친다.

1) import 에 추가:

```python
from explore.chat import arrange, flow, llm
```

(`from explore.chat import data as chat_data` 는 Task 5 에서 넣었다.)

2) `DongAsk._answer` 정적 메서드를 모듈 함수 `answer_one` 으로 옮긴다(본문 그대로). `DongAsk.post` 의 `self._answer(dong_id, raw, intents, question)` → `answer_one(dong_id, raw, intents, question)`.

```python
def answer_one(dong_id: str, raw: dict, intents: list[str], question: str) -> tuple[list[str], str]:
    """동 1개 답: Redis → dong_answer → 생성·저장 (v6.1 스펙 §4 ⑥~⑧). 채팅도 같이 쓴다."""
    joined = "+".join(intents)
    digest = ask.prompt_hash(ask.facts_for(raw, intents, question), question)
    key = f"explore:ask:a:{dong_id}:{joined}:{digest}"
    hit = cache.get(key)
    if hit is not None:
        return hit[0], hit[1]
    stored = sources.load_answer(dong_id, joined, digest)
    if stored is None:
        result = ask.answer(raw, intents, _chat if clova.enabled() else None, question=question)
        sources.save_answer(
            dong_id, joined, result.facts_hash, result.sentences, result.source, result.model
        )
        stored = (result.sentences, result.source)
    cache.set(key, list(stored), ANSWER_TTL)
    return stored
```

3) 429 처리를 공통 부모로 올린다:

```python
class AskView(APIView):
    """동네에 물어보기 · 채팅 공통: throttle 과 429 를 공통 오류 모양으로."""

    throttle_classes = [AskBurstThrottle, AskDayThrottle]

    def handle_exception(self, exc):
        if isinstance(exc, Throttled):
            res = error(429, "TOO_MANY_REQUESTS", "잠시 후 다시 물어봐 주세요.")
            if exc.wait is not None:
                res["Retry-After"] = str(int(exc.wait))
            return res
        return super().handle_exception(exc)
```

`class DongAsk(APIView)` → `class DongAsk(AskView)`, `DongAsk` 의 `throttle_classes` · `handle_exception` 은 지운다.

4) 파일 끝에 채팅 뷰를 더한다:

```python
# ───────────────────────── 동 패널 채팅 (v7) ─────────────────────────


def _chat_deps() -> flow.Deps:
    """요청마다 만든다(테스트에서 바꿔 끼운 함수를 그대로 쓰게)."""
    return flow.Deps(
        names=chat_data.dong_names,
        raw=chat_data.dong_raw,
        find_intents=ask_match.find_intents,
        single=lambda *a: answer_one(*a),  # 테스트가 views.answer_one 을 바꿔 끼운다
        generate=llm.generate if llm.enabled() else None,
        vecs=chat_data.metric_vecs,
        pairs=chat_data.pairs,
    )


class ExploreChat(AskView):
    """POST /api/explore/chat {conversationId, panel, question, criteria} (v7 스펙 §8).
    질문 원문은 어디에도 기록하지 않는다(로그에는 동네 · 의도 · source 만)."""

    def post(self, request):
        try:
            body = request.data if isinstance(request.data, dict) else {}
        except ParseError:
            body = {}
        question = body.get("question")
        question = question.strip() if isinstance(question, str) else ""
        if not 1 <= len(question) <= MAX_QUESTION:
            return error(400, "QUESTION_INVALID", "질문은 1~100자로 적어 주세요.")
        panel = body.get("panel")
        if not isinstance(panel, str) or not panel:
            return error(400, "PANEL_INVALID", "동네를 먼저 골라 주세요.")
        try:
            criteria = arrange.clean_criteria(body.get("criteria"))
        except ValueError:
            return error(400, "CRITERIA_INVALID", "비교 기준이 올바르지 않아요.")
        cid = body.get("conversationId")
        cid = cid if isinstance(cid, str) and cid else None

        names = chat_data.dong_names()
        try:
            if all(n.code != panel for n in names) or chat_data.dong_raw(panel) is None:
                return error(404, "DONG_NOT_FOUND", "동네를 찾을 수 없어요.")
            cid, state = flow.run(flow.build(_chat_deps()), cid, panel, question, criteria)
        except GraphUnavailable as ex:
            logger.warning("탐색 그래프 조회 실패 chat %s: %s", panel, ex)
            return error(503, "GRAPH_UNAVAILABLE", "탐색 데이터를 불러오지 못했어요.")
        logger.info(
            "동 패널 채팅 dongs=%s intents=%s source=%s",
            state.get("dongs"),
            state.get("intents"),
            state.get("source"),
        )
        return Response(flow.respond(cid, state, names))
```

5) `explore/urls.py` 에 경로를 더하고 docstring 을 고친다:

```python
"""탐색 API (접두사 /api/explore/). 계약: 백엔드·웹 v6 스펙 §6, v6.1 §5, v7 채팅 §8"""

    path("chat", views.ExploreChat.as_view(), name="explore-chat"),
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_explore_chat_api.py tests/test_explore_ask_api.py -q`
Expected: PASS

- [ ] **Step 5: 전체 테스트 · 린트**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: 전부 통과 (Neo4j 통합 테스트는 `NEO4J_TEST_URI` 가 없으면 건너뜀)

- [ ] **Step 6: 커밋**

```bash
git add explore/views.py explore/urls.py tests/test_explore_chat_api.py
git commit -m "feat: POST /api/explore/chat 동 패널 채팅 API (같은 시군구 비교 · 대화 이어받기)"
```

---

### Task 8: 실제 환경 확인 · 운영 준비

**Files:**
- Create: `_notes/2026-11-16-compare-summary/bench/chat_smoke.md` (작업 디렉터리 바깥 `_notes`, 커밋 대상 아님)
- 배포 DB: `ask_vocab` 임베딩 갱신 (SQL 실행)

**Interfaces:**
- Consumes: Task 1~7 전부

- [ ] **Step 1: Redis 8 로 띄우기**

```bash
docker compose up -d --wait redis
docker compose exec redis redis-cli MODULE LIST | grep -E "search|ReJSON"
```
Expected: `search` 와 `ReJSON` 이 보인다. 기존 redis:7 컨테이너가 있으면 `docker compose up -d --force-recreate redis` 로 다시 만든다(캐시만 들어 있어 지워져도 된다).

- [ ] **Step 2: `ask_vocab` 임베딩 갱신 (CLOVA 키 필요)**

`db/tools/build_embeddings.py` 머리 주석의 절차대로, 이미 임베딩된 표현을 내려받고 새 표현만 임베딩한다:

```bash
python3 db/tools/build_embeddings.py ask db/seed/ask_vocab.csv /tmp/ask_vocab <임베딩된 표현.csv>
```
생성된 `/tmp/ask_vocab_*.sql` 을 Supabase SQL 편집기(또는 `psql`)로 순서대로 실행한다. 사전 버전(`max(updated_at)`)이 바뀌어 질문 → 의도 캐시는 자동으로 새로 찾는다. **배포 DB 에 쓰는 단계라 실행 전에 사용자에게 확인받는다.**

- [ ] **Step 3: 서버로 실제 대화 해 보기**

`.env` 에 `REDIS_URL=redis://localhost:6379/0` · `CLOVA_API_KEY` · Neo4j 설정이 있는 상태에서 서버를 띄우고(이미 떠 있으면 재시작), 같은 시군구 두 동으로 세 턴을 보낸다. 예: 전주시 효자2동(`4511164500`) · 여의동(`4511163500`).

```bash
B=http://localhost:8000/api/explore/chat
R1=$(curl -s $B -H 'content-type: application/json' -d '{"panel":"4511164500","question":"이 동네 조용해?"}')
CID=$(echo "$R1" | python3 -c 'import json,sys; print(json.load(sys.stdin)["conversationId"])')
curl -s $B -H 'content-type: application/json' -d "{\"conversationId\":\"$CID\",\"panel\":\"4511164500\",\"question\":\"여의동이랑 비교하면?\",\"criteria\":{\"chips\":[\"quiet\",\"evening\"]}}" | python3 -m json.tool
curl -s $B -H 'content-type: application/json' -d "{\"conversationId\":\"$CID\",\"panel\":\"4511164500\",\"question\":\"주말엔?\"}" | python3 -m json.tool
```
Expected: 두 번째 답 `dongs` 2개, `source` 가 `llm` 또는 `mixed`, 문장 끝마다 `evidence`. 세 번째 답이 두 동을 이어받는다.

- [ ] **Step 4: 체크포인트 크기 · 원문 없음 확인**

```bash
uv run python - "$CID" <<'EOF'
import sys, redis
r = redis.from_url("redis://localhost:6379/0")
cid = sys.argv[1]
keys = list(r.scan_iter(f"*{cid}*"))
size = sum(r.memory_usage(k) or 0 for k in keys)
found = False
for k in keys:
    t = r.type(k)
    v = r.json().get(k) if t == b"ReJSON-RL" else r.get(k) if t == b"string" else None
    found |= v is not None and "조용해" in str(v)
print(f"키 {len(keys)}개 · {size} bytes · TTL {r.ttl(keys[0]) if keys else '-'}s · 원문 {'있음' if found else '없음'}")
EOF
```
Expected: `원문 없음`, TTL 이 1800초 이하. 키 수 · 바이트 합을 `chat_smoke.md` 에 적는다(스펙 §11 "체크포인트 크기 미측정" 해소). 값이 직렬화돼 있어 글자 검색이 못 볼 수도 있으니, 원문 없음의 주 근거는 Task 6 의 단위 테스트다.

- [ ] **Step 5: Redis 장애 확인**

```bash
docker compose stop redis
curl -s $B -H 'content-type: application/json' -d '{"panel":"4511164500","question":"여의동이랑 비교하면?"}' | python3 -m json.tool
docker compose start redis
```
Expected: 200 으로 답한다(서버 로그에 "채팅 대화 기억(Redis) 실패 → 기억 없이 답함").

- [ ] **Step 6: 기록과 마무리**

`_notes/2026-11-16-compare-summary/bench/chat_smoke.md` 에 세 턴 응답, 체크포인트 크기, 장애 결과, HCX-007 응답 시간을 적는다. 스펙 §11 의 "같은 시군구 조합 30개로 다시 재기"는 별도 작업으로 남긴다.

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
git log --oneline docs/explore-chat-spec..HEAD
```

푸시 · PR 은 사용자에게 물은 뒤에 한다.

---

## Self-Review 결과

- 스펙 대응: §1 완료 기준(Task 6·7 테스트) · §2 결정(Global Constraints) · §3 흐름(Task 6) · §4(Task 1) · §5-1(Task 4·6) · §5-2·§5-3(Task 2) · §6(Task 3) · §7-1(Task 6·8) · §7-2·§7-2-1(Task 3·6) · §7-3(Task 7) · §7-4(Task 3·6) · §8(Task 6·7) · §12(각 Task 테스트). §9 화면은 웹 짝 문서 범위라 이 계획에 없다. §10 은 미룬 것.
- 이름 일치: `Order.top` · `Dong.values` · `compose(generate, user, dongs, orders)` · `Deps.single(code, raw, intents, question)` · `views.answer_one` 을 Task 사이에서 같은 이름으로 쓴다.
