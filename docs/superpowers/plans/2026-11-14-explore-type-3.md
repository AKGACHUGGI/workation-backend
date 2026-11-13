# 탐색 워케이션 유형 3개 · 대표 유형 기준 40점 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 워케이션 유형에서 장기체류형을 빼고, 1위 점수가 40 미만인 동은 "대표 유형 없음"으로 내려 준다.

**Architecture:** 유형 계산(`explore/derive.py`)을 3개로 줄이고 기준 상수 `TYPE_MIN = 40`을 둔다. Postgres `dong_type_score`는 3유형만 받도록 CHECK를 바꾼다. Neo4j `FITS`는 동마다 3개를 그대로 두되, 핀·공통 사실 Cypher와 `Type.degree`는 1위 점수가 기준 이상일 때만 유형으로 센다. 브리핑 규칙 문장과 질문 답 규칙 문장은 유형이 없는 동에서 유형을 지어내지 않는다.

**Tech Stack:** Python 3 · Django · pytest/pytest-django (테스트 DB = `db/schema.sql`) · Neo4j(Cypher) · Supabase Postgres

**설계:** `docs/superpowers/specs/2026-11-14-explore-type-3-design.md` (이 계획에 없는 내용은 그 문서와 v6 설계를 따른다)

## Global Constraints

- 유형은 `immersion` · `networking` · `local` 3개, 동점 순서도 이 순서. 한국어 이름 `몰입형` · `네트워킹형` · `로컬살이형`.
- 대표 유형 기준은 `explore/derive.py`의 `TYPE_MIN = 40` 하나. 다른 곳에 40을 직접 쓰지 않는다(테스트 기대값 제외).
- 1위 점수 < 40 → 핀 API `topType` · `topScore` = `null`. 동 상세 `types`는 기준과 관계없이 3개 막대를 준다.
- 관광공사 태그 `dong.tags`의 `long_stay`(장기 숙소)는 **다른 것**이다. `graph.POI_TAGS` · `webapi` · `tag_vocab` · `05_dong_doc.sql` · `build_poi_tags_sql.py`는 건드리지 않는다.
- 한산한 달(`derive.quiet_months`, F3, 동 상세 `months`)은 그대로 둔다. 삭제는 `derive.easy_months` · `EASY_RAW`만.
- Supabase 쓰기(마이그레이션 적용 · `build_explore` · `sync_graph` · `build_briefs` · `clear_answers`)는 Task 6에서 **사용자 확인을 받은 뒤에만** 실행한다. `build_briefs`는 HyperCLOVA 호출 비용이 든다.
- 테스트 실행: `uv run pytest <경로> -v` (기본 테스트는 로컬 테스트 DB 사용). Neo4j 통합 테스트는 `NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest -m neo4j -v`, 환경변수가 없으면 건너뛴다.
- 커밋 메시지는 한국어 `feat:` / `test:` / `chore:` 형식(기존 로그와 같게), 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 작업 브랜치: `docs/explore-type-3`에서 시작해 `feat/explore-type-3`를 만든다(`git switch -c feat/explore-type-3`).

## File Structure

| 파일 | 책임 | Task |
| --- | --- | --- |
| `explore/derive.py` | 유형 3개 점수, `TYPE_MIN` | 1 |
| `explore/sources.py` | `DeriveInput`에서 시군구 재료 제거 | 1 |
| `explore/management/commands/build_explore.py` | 새 `type_scores` 호출, 출력 개수 | 1 |
| `tests/explore_samples.py` | 표본 3유형, 기준 미만 표본 두 개 | 1, 3, 4 |
| `db/migrations/20261114_explore-type-3.sql` (새) · `db/schema.sql` · `db/README.md` | CHECK 3유형 · 순위 1~3 | 2 |
| `explore/graph.py` | `Type.degree`는 기준 이상 1위만 | 3 |
| `explore/queries.py` | `PINS` · `FACTS` 의 `top`에 `$type_min` 조건 | 3 |
| `explore/brief.py` | F4 사실 · 규칙 문장 3 | 4 |
| `explore/ask.py` | 의도 `type` 규칙 답 | 5 |

---

### Task 1: 유형 3개로 계산 (장기체류형 삭제)

장기체류형을 계산·표본·기대값에서 모두 뺀다. 이 Task가 끝나면 전체 테스트가 초록이어야 한다(아직 40점 기준은 없다).

**Files:**
- Modify: `explore/derive.py` (12–18행 상수, 19행 `EASY_RAW`, 43–44행 `easy_months`, 58–98행 `type_scores`)
- Modify: `explore/sources.py` (41–92행 `DeriveInput` · `load_derive_input`)
- Modify: `explore/management/commands/build_explore.py:16,37`
- Modify: `tests/explore_samples.py`
- Test: `tests/test_explore_derive.py`, `tests/test_explore_build.py`, `tests/test_explore_graph.py`, `tests/test_explore_queries.py`, `tests/test_explore_api.py`, `tests/test_explore_brief.py`, `tests/test_explore_ask.py`, `tests/integration/test_neo4j_sync.py`

**Interfaces:**
- Produces: `derive.TYPES = ("immersion", "networking", "local")`, `derive.TYPE_KO` 3개, `derive.TYPE_MIN = 40`, `derive.type_scores(dong_metrics: dict[int, dict[str, float]], morning: dict[int, float]) -> dict[int, list[TypeScore]]`, `sources.DeriveInput(dong_metrics, morning, coast_km)`.

- [ ] **Step 1: 브랜치 만들기**

```bash
git switch -c feat/explore-type-3
```

- [ ] **Step 2: derive 테스트를 3유형 기대값으로 바꾼다 (실패 확인용)**

`tests/test_explore_derive.py`에서 `THREE_EASY_RAW` 상수와 `four_dongs` ~ `test_months_use_raw_values`를 아래로 바꾼다. 나머지 테스트(`test_quiet_months_tie_takes_earlier_month` 이후)는 그대로 둔다.

```python
def four_dongs():
    """손으로 계산 가능한 4동. 오전 보행 지수 백분위 50 · 100 · 75 · 25."""
    dong_metrics = {
        1: {"quiet": 80, "peer": 20, "evening": 40, "local_life": 0.9},
        2: {"quiet": 40, "peer": 60, "evening": 80, "local_life": 0.5},
        3: {"quiet": 20, "peer": 90, "evening": 70, "local_life": 0.75},
        4: {"quiet": 60, "peer": 40, "evening": 20, "local_life": 0.6},
    }
    morning = {1: 0.4, 2: 0.8, 3: 0.6, 4: 0.2}
    return dong_metrics, morning


def by_type(scores):
    return {s.type: s for s in scores}


def test_pct_counts_values_at_or_below():
    assert derive.pct([0.4, 0.8, 0.6, 0.2], 0.6) == 75
    assert derive.pct([1, 1, 2], 1) == pytest.approx(66.667, abs=1e-3)


def test_types_are_three_with_threshold():
    assert derive.TYPES == ("immersion", "networking", "local")
    assert derive.TYPE_KO == {"immersion": "몰입형", "networking": "네트워킹형", "local": "로컬살이형"}
    assert derive.TYPE_MIN == 40


def test_type_scores_four_dongs():
    out = derive.type_scores(*four_dongs())
    # 몰입형 = 0.6·quiet + 0.4·(100 − 오전 백분위), 네트워킹형 = 0.5·peer + 0.5·evening,
    # 로컬살이형 = 생활권 지수 백분위
    assert {t: s.score for t, s in by_type(out[1]).items()} == {
        "immersion": 68, "networking": 30, "local": 100,
    }  # fmt: skip
    assert {t: s.score for t, s in by_type(out[2]).items()} == {
        "immersion": 24, "networking": 70, "local": 25,
    }  # fmt: skip
    assert {t: s.score for t, s in by_type(out[3]).items()} == {
        "immersion": 22, "networking": 80, "local": 75,
    }  # fmt: skip
    assert [s.type for s in out[1]] == ["local", "immersion", "networking"]
    assert [s.type for s in out[3]] == ["networking", "local", "immersion"]
    assert [s.rank for s in out[4]] == [1, 2, 3]
    assert [s.type for s in out[4]] == ["immersion", "local", "networking"]


def test_type_reasons():
    t = by_type(derive.type_scores(*four_dongs())[1])
    assert t["immersion"].reason == "조용함 80 · 오전 한산 50"
    assert t["networking"].reason == "또래 밀도 20 · 저녁 활기 40"
    assert t["local"].reason == "생활권 지수 0.90"


def test_networking_rounds_half_to_even():
    # 주문진읍 또래 39 · 저녁 62 → 50.5 → 50 (스펙 값)
    dong_metrics = {1: {"quiet": 31, "peer": 39, "evening": 62, "local_life": 0.7365}}
    out = derive.type_scores(dong_metrics, {1: 0.5})
    assert by_type(out[1])["networking"].score == 50


def test_ties_follow_type_order():
    # A: 몰입형 0.6·50 + 0.4·50 = 50, 네트워킹형 50, 로컬살이형 백분위 50 → 셋 동점
    dong_metrics = {
        "A": {"quiet": 50, "peer": 50, "evening": 50, "local_life": 0.3},
        "B": {"quiet": 90, "peer": 90, "evening": 90, "local_life": 0.8},
    }
    out = derive.type_scores(dong_metrics, {"A": 0.1, "B": 0.9})
    assert [(s.type, s.score, s.rank) for s in out["A"]] == [
        ("immersion", 50, 1),
        ("networking", 50, 2),
        ("local", 50, 3),
    ]


def test_months_use_raw_values():
    assert derive.quiet_months(GANGNEUNG_RAW) == [2, 4, 9]
    assert derive.busiest_month(GANGNEUNG_RAW) == 8
```

파일 맨 위 docstring은 `"""explore/derive.py 순수 계산. 손으로 계산 가능한 4동 + 강릉·주문진읍 실측 일부."""` 그대로 둔다.

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_derive.py -v`
Expected: FAIL — `test_types_are_three_with_threshold`(`AttributeError: TYPE_MIN`), `test_type_scores_four_dongs`(`TypeError: type_scores() missing 2 required positional arguments`) 등.

- [ ] **Step 4: derive.py 구현**

`explore/derive.py`의 상수 블록(11–19행)을 아래로 바꾼다.

```python
# 동점이면 이 순서가 앞선다 (스펙 §4-1 표 순서). 장기체류형은 2026-11-14 삭제 (유형 3개 스펙 §2)
TYPES = ("immersion", "networking", "local")
TYPE_KO = {"immersion": "몰입형", "networking": "네트워킹형", "local": "로컬살이형"}
# 대표 유형 기준: 1위 점수가 이보다 낮으면 "뚜렷한 유형 없음" (유형 3개 스펙 §3)
TYPE_MIN = 40
COAST_KM = 1.0
```

`easy_months` 함수(43–44행)를 지운다. `type_scores`(58–98행)를 아래로 바꾼다.

```python
def type_scores(
    dong_metrics: dict[int, dict[str, float]], morning: dict[int, float]
) -> dict[int, list[TypeScore]]:
    """동마다 유형 3개 점수·순위·이유 (스펙 §4-1).

    dong_metrics: {dong_id: {"quiet", "peer", "evening": 점수, "local_life": 원값}}
    morning: {dong_id: 평일 9~11시 보행 지수 평균 (0~1)}
    """
    mornings = [morning[d] for d in dong_metrics]
    lives = [m["local_life"] for m in dong_metrics.values()]
    out = {}
    for d, m in dong_metrics.items():
        calm_am = 100 - pct(mornings, morning[d])
        raw = {
            "immersion": (
                0.6 * m["quiet"] + 0.4 * calm_am,
                f"조용함 {m['quiet']} · 오전 한산 {round(calm_am)}",
            ),
            "networking": (
                0.5 * m["peer"] + 0.5 * m["evening"],
                f"또래 밀도 {m['peer']} · 저녁 활기 {m['evening']}",
            ),
            "local": (pct(lives, m["local_life"]), f"생활권 지수 {m['local_life']:.2f}"),
        }
        # round() 는 반올림 짝수: 0.5·39 + 0.5·62 = 50.5 → 50 (실측 주문진읍 네트워킹형과 같음)
        scored = sorted(TYPES, key=lambda t: (-round(raw[t][0]), TYPES.index(t)))
        out[d] = [
            TypeScore(t, round(raw[t][0]), i, raw[t][1]) for i, t in enumerate(scored, start=1)
        ]
    return out
```

- [ ] **Step 5: derive 테스트 통과 확인**

Run: `uv run pytest tests/test_explore_derive.py -v`
Expected: PASS (전부)

- [ ] **Step 6: sources.py · build_explore.py 맞추기**

`explore/sources.py`의 `DeriveInput`과 `load_derive_input`을 아래로 바꾼다(시군구 숙박 성향·ease 읽기, `region_metrics`, `region_of` 삭제. 시군구에 숙박 성향이 없어도 동을 빼지 않는다).

```python
@dataclass(frozen=True)
class DeriveInput:
    dong_metrics: dict[int, dict[str, float]]  # derive.type_scores 입력
    morning: dict[int, float]
    coast_km: dict[int, float | None]


def load_derive_input() -> DeriveInput:
    """연간(month 0) 지표 4개와 평일 오전 보행 지수가 모두 있는 동만."""
    metrics: dict[int, dict] = defaultdict(dict)
    for dong_id, key, score, raw in _rows(
        f"""SELECT m.unit_id, m.metric_key, m.score, m.raw_value
              FROM metric m JOIN dong d ON d.dong_id = m.unit_id
             WHERE m.scope = 'dong' AND m.month = 0 AND {DONG_OK}
               AND m.metric_key IN ('quiet', 'peer', 'evening', 'local_life')"""
    ):
        value = raw if key == "local_life" else score
        if value is not None:
            metrics[dong_id][key] = value
    morning = {
        dong_id: float(v)
        for dong_id, v in _rows(
            """SELECT dong_id, avg(index_100) / 100.0 FROM dong_hourly
                WHERE month = 0 AND day_group = 'weekday' AND hour BETWEEN 9 AND 11
                  AND index_100 IS NOT NULL
                GROUP BY dong_id"""
        )
    }
    coast = {
        dong_id: _float(km)
        for dong_id, km in _rows(f"SELECT dong_id, coast_dist_km FROM dong d WHERE {DONG_OK}")
    }
    complete = {d: m for d, m in metrics.items() if len(m) == 4 and d in morning and d in coast}
    return DeriveInput(complete, morning, coast)
```

`explore/management/commands/build_explore.py` 16행과 출력(37행 근처)을 바꾼다.

```python
        types = derive.type_scores(inp.dong_metrics, inp.morning)
```

```python
        self.stdout.write(
            f"{year}년 · 동 {len(types)} → dong_type_score {len(derive.TYPES) * len(types)}"
            f" · dong_hour_stat {n_hours} · dong_similar {n_similar}"
        )
```

- [ ] **Step 7: 표본(explore_samples.py)을 3유형으로**

`tests/explore_samples.py`에서:
1. `STAY = ...` 줄을 지운다.
2. `sample_graph_input()`의 `types`를 아래로 바꾼다(장기체류형 행을 빼고 순위를 당김).

```python
    types = {
        116: [
            TypeScore("local", 60, 1, "생활권 지수 0.74"),
            TypeScore("networking", 50, 2, "또래 밀도 39 · 저녁 활기 62"),
            TypeScore("immersion", 36, 3, "조용함 31 · 오전 한산 44"),
        ],
        112: [
            TypeScore("immersion", 50, 1, "조용함 55 · 오전 한산 42"),
            TypeScore("local", 40, 2, "생활권 지수 0.65"),
            TypeScore("networking", 30, 3, "또래 밀도 20 · 저녁 활기 40"),
        ],
        403: [
            TypeScore("local", 65, 1, "생활권 지수 0.75"),
            TypeScore("networking", 50, 2, "또래 밀도 52 · 저녁 활기 47"),
            TypeScore("immersion", 40, 3, "조용함 39 · 오전 한산 41"),
        ],
        901: [
            TypeScore("networking", 96, 1, "또래 밀도 97 · 저녁 활기 94"),
            TypeScore("local", 53, 2, "생활권 지수 0.72"),
            TypeScore("immersion", 18, 3, "조용함 21 · 오전 한산 15"),
        ],
    }
```

3. `jumunjin_raw()`에서 `stay_top = ("long_stay", "장기체류형", 58)`을 `local_top = ("local", "로컬살이형", 72)`로 바꾸고, 쓰는 두 곳(주문진읍 · 노학동 `_facts_row`)도 `local_top`으로 바꾼다. 대정읍 · 영천동의 `("networking", "네트워킹형", 49)`는 `("networking", "네트워킹형", 56)`으로 바꾼다. 72 · 56은 2025년 203동에서 1위이면서 40점 이상인 동 수(Supabase 실측, 유형 3개 스펙 §3 분포)다. docstring의 "graph_data/facts.json 으로 센 203동 기준 degree"는 "203동 기준 degree (유형은 2026-11-14 Supabase 실측, 기준 40 이상 1위만)"로 고친다.
4. `"types"`를 3행으로:

```python
            "types": [
                [1, "로컬살이형", 60, "생활권 지수 0.74"],
                [2, "네트워킹형", 50, "또래 밀도 39 · 저녁 활기 62"],
                [3, "몰입형", 36, "조용함 31 · 오전 한산 44"],
            ],
```

- [ ] **Step 8: 표본이 바뀐 만큼 기대값 바꾸기**

동작은 같고 값만 바뀐다. 아래를 그대로 고친다.

`tests/test_explore_build.py`
- import 줄에서 `STAY`를 뺀다.
- `test_build_explore_counts`: `"2025년 · 동 4 → dong_type_score 12 · dong_hour_stat 8 · dong_similar 4"`
- `test_type_scores_written`:

```python
def test_type_scores_written(explore_data):
    build()
    rows = fetch(
        "SELECT type, score, rank, reason FROM dong_type_score WHERE dong_id = 116 ORDER BY rank"
    )
    assert rows == [
        ("local", 100, 1, "생활권 지수 0.74"),
        ("networking", 50, 2, "또래 밀도 39 · 저녁 활기 62"),
        ("immersion", 39, 3, "조용함 31 · 오전 한산 50"),
    ]
    assert fetch("SELECT DISTINCT type FROM dong_type_score ORDER BY type") == [
        ("immersion",), ("local",), ("networking",),
    ]  # fmt: skip


def test_dongs_scored_without_region_overnight(explore_data):
    # 장기체류형이 쓰던 시군구 숙박 성향이 없어도 동을 빼지 않는다
    Metric.objects.filter(scope="region", metric_def_id="overnight").delete()
    assert "동 4 → dong_type_score 12" in build()
```

- `test_build_explore_rerun_replaces_rows`: `[(12,)]`
- `test_ease_quiet_months_use_raw_not_score`:

```python
def test_ease_quiet_months_use_raw_not_score(explore_data, monkeypatch):
    build()
    monkeypatch.setattr(sources, "load_centers", lambda: {})
    ease = sources.load_graph_input().ease[1]
    assert derive.quiet_months(ease) == [2, 4, 9]  # 점수 기준이면 1·4·10월
```

`tests/test_explore_graph.py`
- `test_label_counts`: `"Type": 3`, `"FITS": 12`.
- `test_fits_four_with_rank` → 이름을 `test_fits_three_with_rank`로:

```python
def test_fits_three_with_rank():
    fits = rels_of(build(), "FITS", "dong:5115025000")
    assert [(r.dst, r.props["score"], r.props["rank"]) for r in fits] == [
        ("type:local", 60, 1),
        ("type:networking", 50, 2),
        ("type:immersion", 36, 3),
    ]
    assert fits[0].props["reason"] == "생활권 지수 0.74"
```

- `test_degree_counts_connected_dongs`의 유형 두 줄:

```python
    assert n["type:local"].props["degree"] == 2  # 주문진읍·노학동. FITS rank 1 만 센다
    assert n["type:immersion"].props["degree"] == 1  # 연곡면
```

`tests/test_explore_queries.py`
- `PINS_REC`: 연곡면 `"top": [["몰입형", 50]]`, 주문진읍 `"top": [["로컬살이형", 60]]`.
- `test_shape_pins`: `"topType": "로컬살이형"`, `"topScore": 60`.
- `test_fact_list_labels_and_degrees`: `"type:long_stay": ("1위 장기체류형", 58)` → `"type:local": ("1위 로컬살이형", 72)`.
- `test_common_facts_rare_first`: `"1위 장기체류형"` → `"1위 로컬살이형"` (순서 그대로).
- `test_shape_dong_jumunjin`:

```python
    assert [(t["name"], t["score"]) for t in body["types"]] == [
        ("로컬살이형", 60), ("네트워킹형", 50), ("몰입형", 36),
    ]  # fmt: skip
    assert body["types"][0]["reason"] == "생활권 지수 0.74"
```

- `test_shape_dong_similar`: `similar[0]["commonTop"] == ["조용함 B", "1위 로컬살이형", "바닷가"]`.

`tests/test_explore_api.py`
- `test_pins`: `("로컬살이형", 60, True)`.
- `test_dong_detail_with_briefing`:

```python
    assert body["types"][0] == {"name": "로컬살이형", "score": 60, "reason": "생활권 지수 0.74"}
    assert body["months"]["quiet"] == [2, 4, 9]
    assert body["similar"][0]["commonTop"] == ["조용함 B", "1위 로컬살이형", "바닷가"]
```

`tests/test_explore_brief.py`
- `RULES[2]`: `"로컬살이형 60점이고, 강릉시 안 저녁 활기 1위예요."`
- `test_collect_facts_rare_first`: 목록에서 `"F4-4"`만 뺀다(나머지 순서 그대로. F4-1 흔함이 72/203 = 0.355로 0.3을 넘지만, 뒷무리 안에서도 F5-1(0.478)보다 앞이라 위치가 같다).
- `test_fact_texts_and_values`: `f["F4-1"].text == "로컬살이형 60점 (유형 1위)"`.
- `test_rule_third_without_sigungu_first_uses_best_grade`: `"로컬살이형 60점으로, 저녁 활기 A(62점)이에요."`

`tests/test_explore_ask.py`
- `FACT_IDS["type"]`: `["F4-1", "F4-2", "F4-3"]`
- `RULES["type"]`: `["로컬살이형 60점으로 가장 잘 맞고, 다음은 네트워킹형 50점이에요."]`
- `test_anchors`: `ask.anchors(facts["F4-1"]) == {"1", "60", "로컬살이형", "로컬살이"}`

`tests/integration/test_neo4j_sync.py`
- `test_pins_with_real_cypher`: `("로컬살이형", 60)`.
- `test_dong_with_real_cypher`:

```python
    assert [t["name"] for t in body["types"]] == ["로컬살이형", "네트워킹형", "몰입형"]
    ...
    nohak, yeongok = body["similar"]
    # 노학동: 조용함 B · 1위 로컬살이형 · 평일 9시 · 주말 3시 · 바닷가 · 생활권
    assert (nohak["name"], nohak["common"]) == ("노학동", 6)
    assert nohak["commonTop"] == ["조용함 B", "1위 로컬살이형", "생활권"]  # degree 2 → 3(key 순)
    assert nohak["diff"] == {"quiet": 8, "peer": 13, "evening": -15}
    # 연곡면: 평일 9시 · 주말 3시 · 바닷가 · 같은 강릉시 (1위 유형이 다름)
    assert (yeongok["name"], yeongok["common"]) == ("연곡면", 4)
```

- [ ] **Step 9: 남은 장기체류 흔적 찾기**

Run: `grep -rn "long_stay\|장기체류\|easy_months\|EASY_RAW\|region_metrics\|region_of\|STAY" explore tests --include='*.py'`
Expected: 남는 것은 `explore/graph.py`의 `POI_TAGS` 안 `"long_stay"`(관광공사 태그), `tests/test_webapi.py`의 태그 `long_stay`, 그리고 `explore/graph.py`의 지역 순위 계산 지역변수 `region_of`뿐이다. `explore/ask.py:224` 주석(`"장기체류형 61점 (유형 1위)"`)은 Task 5에서 고친다.

- [ ] **Step 10: 전체 테스트**

Run: `uv run pytest -v`
Expected: PASS (neo4j 마크 테스트는 skip)

- [ ] **Step 11: 커밋**

```bash
git add explore/derive.py explore/sources.py explore/management/commands/build_explore.py tests/
git commit -m "feat: 탐색 워케이션 유형 3개 (장기체류형 삭제)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: DB CHECK 3유형 · 순위 1~3 (마이그레이션)

**Files:**
- Create: `db/migrations/20261114_explore-type-3.sql`
- Modify: `db/schema.sql:527-533` (dong_type_score CHECK · 주석)
- Modify: `db/README.md` 적용 이력 표 (51행 다음)
- Test: `tests/test_explore_build.py`

**Interfaces:**
- Consumes: Task 1의 `build_explore`(3유형 행).
- Produces: `dong_type_score_type_check` · `dong_type_score_rank_check` 제약 이름(Task 6 적용 확인에 쓴다).

- [ ] **Step 1: 실패 테스트 쓰기**

`tests/test_explore_build.py` 위쪽 import에 `from django.db import IntegrityError, transaction`을 더하고(이미 `connection`을 가져오는 줄에 합친다), 끝에 추가:

```python
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE dong_type_score SET type = 'long_stay' WHERE dong_id = 116 AND rank = 3",
        "UPDATE dong_type_score SET rank = 4 WHERE dong_id = 116 AND rank = 3",
    ],
)
def test_type_check_allows_three_types_and_ranks(explore_data, sql):
    build()
    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cur:
        cur.execute(sql)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_build.py::test_type_check_allows_three_types_and_ranks -v`
Expected: FAIL — `DID NOT RAISE <class 'django.db.utils.IntegrityError'>` (지금 CHECK는 4유형 · 1~4라 통과해 버린다)

- [ ] **Step 3: schema.sql 고치기**

`db/schema.sql`의 `dong_type_score` 정의에서:

```sql
  CHECK (type IN ('immersion', 'networking', 'local')),
  CHECK (score BETWEEN 0 AND 100),
  CHECK (rank BETWEEN 1 AND 3)
);
COMMENT ON TABLE  dong_type_score        IS '탐색 워케이션 유형 3개 점수 (연간). explore/derive.py type_scores. 장기체류형은 2026-11-14 삭제';
COMMENT ON COLUMN dong_type_score.rank   IS '그 동 안 순위 1~3. 동점이면 immersion → networking → local';
COMMENT ON COLUMN dong_type_score.reason IS '화면 이유 문구 (예: 생활권 지수 0.74)';
```

- [ ] **Step 4: 마이그레이션 파일 만들기**

`db/migrations/20261114_explore-type-3.sql`:

```sql
-- 탐색 워케이션 유형 3개 (장기체류형 삭제) · 2026-11-14
-- 기준: migrations/20261113_explore-v6.sql 까지 적용된 DB. 두 번 실행해도 안전.
-- 남은 행 순위를 1~3 으로 당긴 뒤 CHECK 를 바꾼다. 값은 이어서 manage.py build_explore 가 다시 쓴다.
-- 설계: docs/superpowers/specs/2026-11-14-explore-type-3-design.md §4-2
-- 관광공사 태그 dong.tags 의 long_stay(장기 숙소)는 다른 것이라 건드리지 않는다.

BEGIN;

DELETE FROM dong_type_score WHERE type = 'long_stay';

-- 순위 다시 매기기: 점수 높은 순, 같으면 immersion → networking → local (derive.TYPES 순서)
UPDATE dong_type_score t
   SET rank = r.new_rank
  FROM (SELECT dong_id, type,
               row_number() OVER (
                 PARTITION BY dong_id
                 ORDER BY score DESC,
                          array_position(ARRAY['immersion', 'networking', 'local'], type::text)
               ) AS new_rank
          FROM dong_type_score) r
 WHERE t.dong_id = r.dong_id AND t.type = r.type AND t.rank <> r.new_rank;

ALTER TABLE dong_type_score DROP CONSTRAINT IF EXISTS dong_type_score_type_check;
ALTER TABLE dong_type_score DROP CONSTRAINT IF EXISTS dong_type_score_rank_check;
ALTER TABLE dong_type_score
  ADD CONSTRAINT dong_type_score_type_check CHECK (type IN ('immersion', 'networking', 'local'));
ALTER TABLE dong_type_score
  ADD CONSTRAINT dong_type_score_rank_check CHECK (rank BETWEEN 1 AND 3);

COMMENT ON TABLE  dong_type_score        IS '탐색 워케이션 유형 3개 점수 (연간). explore/derive.py type_scores. 장기체류형은 2026-11-14 삭제';
COMMENT ON COLUMN dong_type_score.rank   IS '그 동 안 순위 1~3. 동점이면 immersion → networking → local';
COMMENT ON COLUMN dong_type_score.reason IS '화면 이유 문구 (예: 생활권 지수 0.74)';

COMMIT;
```

(이름 없는 표 CHECK의 Postgres 기본 이름은 `<표>_<첫 컬럼>_check`라 `dong_type_score_type_check` · `dong_type_score_rank_check`다. Task 6 Step 2에서 실제 이름을 먼저 확인한다.)

- [ ] **Step 5: README 적용 이력**

`db/README.md` 적용 이력 표의 `20261113_explore-v6.sql` 행 다음에:

```markdown
| `migrations/20261114_explore-type-3.sql` | 워케이션 유형 3개: `dong_type_score` 장기체류형 행 삭제, CHECK 3유형 · 순위 1~3 | (적용 전) |
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_explore_build.py tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add db/ tests/test_explore_build.py
git commit -m "feat: dong_type_score CHECK 3유형 · 순위 1~3 마이그레이션

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 대표 유형 기준 40 — 그래프 degree · 핀/공통 사실 Cypher

**Files:**
- Modify: `explore/graph.py:198-206` (degree)
- Modify: `explore/queries.py:8-9` (import), `PINS` · `FACTS`, `_pins_work`, `_dong_work`
- Modify: `tests/explore_samples.py` (표본 함수 추가)
- Test: `tests/test_explore_graph.py`, `tests/test_explore_queries.py`, `tests/integration/test_neo4j_sync.py`

**Interfaces:**
- Consumes: `derive.TYPE_MIN` (Task 1).
- Produces: `tests.explore_samples.weak_yeongok_input() -> GraphInput` (연곡면 1위 38). Cypher 매개변수 이름 `type_min`.

- [ ] **Step 1: 표본 함수 추가**

`tests/explore_samples.py` 맨 위 import에 `import dataclasses`를 더하고, `sample_graph_input` 아래에 추가:

```python
def weak_yeongok_input() -> GraphInput:
    """연곡면 1위가 기준(40) 미만인 표본. 대표 유형 없음 확인용."""
    inp = sample_graph_input()
    weak = [
        TypeScore("immersion", 38, 1, "조용함 55 · 오전 한산 13"),
        TypeScore("local", 35, 2, "생활권 지수 0.65"),
        TypeScore("networking", 30, 3, "또래 밀도 20 · 저녁 활기 40"),
    ]
    return dataclasses.replace(inp, types=inp.types | {112: weak})
```

- [ ] **Step 2: 실패 테스트 쓰기**

`tests/test_explore_graph.py` import를 `from tests.explore_samples import sample_graph_input, weak_yeongok_input`로 바꾸고 추가:

```python
def test_type_degree_counts_only_top_at_or_above_min():
    g = graph.build_graph(weak_yeongok_input())
    n = nodes_by_id(g)
    assert n["type:immersion"].props["degree"] == 0  # 연곡면 1위 38 < 40
    assert n["type:local"].props["degree"] == 2
    # 막대(동 상세 types)용 FITS 는 기준과 관계없이 3개
    assert len(rels_of(g, "FITS", "dong:5115037000")) == 3
```

`tests/test_explore_queries.py` 끝에 추가:

```python
def test_pins_and_facts_pass_type_min(monkeypatch):
    raw = jumunjin_raw()
    tx = FakeTx(
        {
            queries.META: [META_ROW],
            queries.PINS: [PINS_REC],
            queries.DONG: [raw["rec"]],
            queries.FACTS: list(raw["facts"].values()),
        }
    )
    monkeypatch.setattr(queries, "read", fake_read(tx))
    queries.fetch_pins("51150")
    queries.fetch_dong("5115025000")
    params = {q: p for q, p in tx.calls}
    assert params[queries.PINS]["type_min"] == 40
    assert params[queries.FACTS]["type_min"] == 40
```

`tests/integration/test_neo4j_sync.py` import에 `weak_yeongok_input`을 더하고 추가:

```python
def test_type_below_min_is_null_in_pins_and_facts(driver):
    sync(driver, weak_yeongok_input(), "first")
    yeongok = queries.fetch_pins("51150")["dongs"][0]
    assert yeongok["name"] == "연곡면"
    assert (yeongok["topType"], yeongok["topScore"]) == (None, None)
    raw = queries.fetch_dong("5115037000")
    assert raw["facts"]["5115037000"]["top"] == []
    assert [t["score"] for t in queries.shape_dong(raw, [])["types"]] == [38, 35, 30]
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_graph.py tests/test_explore_queries.py -v`
Expected: FAIL — `test_type_degree_counts_only_top_at_or_above_min`(degree 1 ≠ 0), `test_pins_and_facts_pass_type_min`(`KeyError: 'type_min'`)

- [ ] **Step 4: graph.py degree**

`explore/graph.py`의 degree 반복을 바꾼다.

```python
    # 사실 노드의 degree = 이어진 동 수. 공통점 정렬(스펙 §6-3)과 브리핑 흔함에 쓴다.
    # 유형은 대표 유형(1위이면서 TYPE_MIN 이상)인 동만 센다 (유형 3개 스펙 §4-3)
    degree: Counter[str] = Counter()
    for r in rels:
        if not r.src.startswith("dong:"):
            continue
        if r.type in ("IN", "HAS_GRADE", "NEAR", "LIFE") or (
            r.type == "FITS" and r.props["rank"] == 1 and r.props["score"] >= derive.TYPE_MIN
        ):
            degree[r.dst] += 1
        elif r.type == "QUIET_AT":
            degree[f"{r.dst}:{r.props['day']}"] += 1
```

- [ ] **Step 5: queries.py Cypher · 매개변수**

import에 `from explore import derive`를 더한다(`from explore.graph import ...` 위). `PINS`의 `top` 줄:

```
  top: [(d)-[f:FITS {rank: 1}]->(ty:Type) WHERE f.score >= $type_min | [ty.name, f.score]],
```

`FACTS`의 `top` 줄과 위 주석:

```python
# 공통점이 되는 사실 노드 (스펙 §6-3): 등급 · 대표 유형(1위 · 기준 이상) · 한산 시각(day 별) · 바닷가 · 생활 · 시군구
```

```
  [(d)-[f:FITS {rank: 1}]->(t:Type) WHERE f.score >= $type_min |
    {id: t.id, name: t.name, degree: t.degree}] AS top,
```

`_pins_work`와 `_dong_work`:

```python
def _pins_work(tx, code):
    _meta(tx)
    rec = tx.run(PINS, code=code, type_min=derive.TYPE_MIN).single()
    return None if rec is None else rec.data()
```

```python
    facts = {
        r["code"]: r.data()
        for r in tx.run(FACTS, codes=codes, type_min=derive.TYPE_MIN)
    }
```

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest -v`
Expected: PASS (neo4j 마크 skip). 로컬 Neo4j가 떠 있으면 `NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest -m neo4j -v`도 PASS. 안 떠 있으면 Task 6 Step 1에서 돌린다.

- [ ] **Step 7: 커밋**

```bash
git add explore/graph.py explore/queries.py tests/
git commit -m "feat: 대표 유형 기준 40 미만이면 핀 topType null · 공통 사실 제외

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 브리핑 — 대표 유형 없는 동

**Files:**
- Modify: `explore/brief.py:138-143` (F4 사실), `rule_sentences`의 셋째 문장(183–194행)
- Modify: `tests/explore_samples.py` (표본 함수 추가)
- Test: `tests/test_explore_brief.py`

**Interfaces:**
- Consumes: `derive.TYPE_MIN`.
- Produces: `tests.explore_samples.jumunjin_no_type_raw() -> dict` (Task 5도 쓴다). F4-1 기준 미만 문구 `"{이름} {점수}점 (유형 1위, 기준 40점 미만)"`.

- [ ] **Step 1: 표본 함수 추가**

`tests/explore_samples.py` 끝에:

```python
def jumunjin_no_type_raw() -> dict:
    """주문진읍 값에서 유형 점수만 모두 40 미만으로 낮춘 표본. 대표 유형 없음 확인용."""
    raw = jumunjin_raw()
    raw["rec"]["types"] = [
        [1, "로컬살이형", 39, "생활권 지수 0.60"],
        [2, "네트워킹형", 35, "또래 밀도 30 · 저녁 활기 40"],
        [3, "몰입형", 20, "조용함 31 · 오전 한산 4"],
    ]
    raw["facts"]["5115025000"]["top"] = []  # FACTS 는 기준 미만 1위를 top 으로 주지 않는다
    return raw
```

- [ ] **Step 2: 실패 테스트 쓰기**

`tests/test_explore_brief.py` import를 `from tests.explore_samples import jumunjin_no_type_raw, jumunjin_raw`로 바꾸고 추가:

```python
def test_no_type_top_fact_is_not_a_strength():
    f = {x.id: x for x in brief.collect_facts(jumunjin_no_type_raw())}
    assert f["F4-1"].text == "로컬살이형 39점 (유형 1위, 기준 40점 미만)"
    assert f["F4-1"].values == {"39", "1", "40"}
    assert f["F4-1"].common == 1.0
    assert f["F4-2"].text == "네트워킹형 35점 (유형 2위)"


def test_rule_third_without_type():
    raw = jumunjin_no_type_raw()
    third = lambda: brief.rule_sentences(raw, brief.collect_facts(raw))[2]  # noqa: E731
    assert third() == "강릉시 안 저녁 활기 1위인 동네예요."
    raw["rec"]["grades"][2][5] = 2  # 저녁 활기 강릉 2위로 → 시군구 1위 사실 없음
    assert third() == "저녁 활기 A(62점)이 이 동네의 가장 큰 특징이에요."
    raw["rec"]["grades"] = []
    assert third() == "뚜렷하게 맞는 워케이션 유형은 없어요."
```

- [ ] **Step 3: 실패 확인**

Run: `uv run pytest tests/test_explore_brief.py -v`
Expected: FAIL — 새 두 테스트 (`'로컬살이형 39점 (유형 1위)' != ...`, `'로컬살이형 39점이고, ...' != ...`)

- [ ] **Step 4: F4 사실 구현**

`explore/brief.py`의 F4 블록을 바꾼다.

```python
    top_degree = me["top"][0]["degree"] if me["top"] else n
    for rank, name, score, _ in sorted(rec["types"]):
        if rank == 1 and score >= derive.TYPE_MIN:
            facts.append(Fact("F4-1", f"{name} {score}점 (유형 1위)", _vals(score, rank), top_degree / n))
        elif rank == 1:
            # 1위가 기준 미만이면 대표 유형이 아니다 → 강점이 아니므로 뒤로, 문구에도 밝힌다
            text = f"{name} {score}점 (유형 1위, 기준 {derive.TYPE_MIN}점 미만)"
            facts.append(Fact("F4-1", text, _vals(score, rank, derive.TYPE_MIN), 1.0))
        else:
            # 1위가 아닌 유형은 강점이 아니므로 뒤로
            facts.append(Fact(f"F4-{rank}", f"{name} {score}점 (유형 {rank}위)", _vals(score, rank), 1.0))
```

- [ ] **Step 5: 규칙 문장 3 구현**

`rule_sentences`에서 `_, top_name, top_score, _ = sorted(rec["types"])[0]`부터 `return` 앞까지를 바꾼다.

```python
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
        third = f"{top_name} {top_score}점이에요." if has_type else "뚜렷하게 맞는 워케이션 유형은 없어요."
    return [first, second, third]
```

- [ ] **Step 6: 통과 확인 · 포맷**

Run: `uv run ruff format explore/brief.py tests/ && uv run ruff check explore tests && uv run pytest tests/test_explore_brief.py tests/test_explore_ask.py -v`
Expected: PASS, ruff 오류 없음

- [ ] **Step 7: 커밋**

```bash
git add explore/brief.py tests/
git commit -m "feat: 대표 유형 없는 동 브리핑 (유형 지어내지 않기)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 동네에 물어보기 — 유형 질문 답

**Files:**
- Modify: `explore/ask.py:224` (주석), `_intent_rule`의 `type` 분기
- Test: `tests/test_explore_ask.py`

**Interfaces:**
- Consumes: `derive.TYPE_MIN` · `derive.TYPES` · `derive.TYPE_KO`, `tests.explore_samples.jumunjin_no_type_raw` (Task 4).

- [ ] **Step 1: 실패 테스트 쓰기**

`tests/test_explore_ask.py` import를 `from tests.explore_samples import jumunjin_no_type_raw, jumunjin_raw`로 바꾸고 추가:

```python
def test_type_rule_without_representative_type():
    raw = jumunjin_no_type_raw()
    assert ask.rule_answer(raw, ["type"], ask.facts_for(raw, ["type"])) == [
        "뚜렷하게 맞는 유형은 없어요.",
        "몰입형 20점 · 네트워킹형 35점 · 로컬살이형 39점이에요.",
    ]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_explore_ask.py::test_type_rule_without_representative_type -v`
Expected: FAIL — `['로컬살이형 39점으로 가장 잘 맞고, 다음은 네트워킹형 35점이에요.'] != [...]`

- [ ] **Step 3: 구현**

`explore/ask.py` `_intent_rule`의 `type` 분기:

```python
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
```

224행 주석: `# "로컬살이형 60점 (유형 1위)"`

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`
Expected: PASS, 오류 없음

- [ ] **Step 5: 커밋**

```bash
git add explore/ask.py tests/test_explore_ask.py
git commit -m "feat: 동네에 물어보기 유형 답 — 대표 유형 없는 동

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 검증 · 데이터 재생성 (Supabase 쓰기 — 사용자 확인 후)

이 Task의 Step 2부터는 공용 Supabase와 HyperCLOVA를 쓴다. **각 단계 전에 사용자에게 무엇을 실행할지 말하고 확인을 받는다.** 확인이 없으면 Step 1까지만 하고 멈춘다.

**Files:**
- Modify: `db/README.md` (적용 날짜)

- [ ] **Step 1: 로컬 검증**

Run: `uv run pytest -v && uv run ruff check . && uv run ruff format --check .`
Expected: PASS

로컬 Neo4j(`scripts/explore-local.sh` 또는 `docker compose up -d neo4j`)가 있으면:
Run: `NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest -m neo4j -v`
Expected: PASS (그 Neo4j의 그래프는 테스트 그래프로 바뀌므로, 끝나면 Step 5의 `sync_graph`로 되돌린다)

- [ ] **Step 2: (확인 후) 제약 이름 확인 — 읽기 전용**

```bash
uv run python -I - .env <<'EOF'
import sys, environ, psycopg
env = environ.Env(); environ.Env.read_env(sys.argv[1])
c = psycopg.connect(host=env("DB_HOST"), port=env("DB_PORT"), dbname=env("DB_NAME"),
                    user=env("DB_USER"), password=env("DB_PASSWORD"), sslmode=env("DB_SSLMODE"))
c.read_only = True
print(c.execute("SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = 'dong_type_score'::regclass AND contype = 'c'").fetchall())
EOF
```

Expected: `dong_type_score_type_check` · `dong_type_score_rank_check` · `dong_type_score_score_check`. 이름이 다르면 마이그레이션의 `DROP CONSTRAINT` 이름을 맞춰 고치고 Task 2 커밋에 amend하지 말고 새 커밋으로 남긴다.

- [ ] **Step 3: (확인 후) 마이그레이션 적용**

```bash
uv run python -I - .env db/migrations/20261114_explore-type-3.sql <<'EOF'
import sys, environ, psycopg
env = environ.Env(); environ.Env.read_env(sys.argv[1])
sql = open(sys.argv[2]).read()
with psycopg.connect(host=env("DB_HOST"), port=env("DB_PORT"), dbname=env("DB_NAME"),
                     user=env("DB_USER"), password=env("DB_PASSWORD"),
                     sslmode=env("DB_SSLMODE"), autocommit=True) as c:
    c.execute(sql)
    print(c.execute("SELECT type, count(*), max(rank) FROM dong_type_score GROUP BY type ORDER BY type").fetchall())
EOF
```

Expected: `[('immersion', 203, 3), ('local', 203, 3), ('networking', 203, 3)]` (동 수는 203).

- [ ] **Step 4: (확인 후) 유형 점수 다시 만들기**

Run: `uv run python manage.py build_explore`
Expected: `2025년 · 동 203 → dong_type_score 609 · dong_hour_stat … · dong_similar …`

- [ ] **Step 5: (확인 후) Neo4j 동기화**

Run: `uv run python manage.py sync_graph`
Expected: 개수 비교 통과 (`Type` 3, `FITS` 609)

곧바로 확인: `curl -s localhost:8000/api/explore/sigungu/51150/pins | python3 -c "import sys,json; d=json.load(sys.stdin)['dongs']; print(sum(x['topType'] is None for x in d), {t: sum(x['topType']==t for x in d) for t in ('몰입형','네트워킹형','로컬살이형')})"` (서버가 떠 있을 때)
Expected: `5 {'몰입형': 2, '네트워킹형': 4, '로컬살이형': 10}`

- [ ] **Step 6: (확인 후 — HyperCLOVA 비용) 브리핑 다시 만들기**

Run: `uv run python manage.py build_briefs`
Expected: 1위 유형이 바뀐 동만 다시 생성. 끝 출력의 `rule` 비율을 사용자에게 보고한다.

- [ ] **Step 7: (확인 후) 저장된 질문 답 비우기**

Run: `uv run python manage.py clear_answers`

- [ ] **Step 8: README 적용 날짜 · 커밋**

`db/README.md`의 `20261114_explore-type-3.sql` 행 `(적용 전)`을 실제 적용 날짜로 바꾼다.

```bash
git add db/README.md
git commit -m "chore: 유형 3개 마이그레이션 Supabase 적용 기록

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

팀에 알린다(개발 가이드 §탐색 v6): "`build_explore` · `build_briefs` 다시 돌림 → 각자 `scripts/explore-local.sh` 다시 실행".
