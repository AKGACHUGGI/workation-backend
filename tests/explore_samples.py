"""탐색 v6 테스트 공용 재료. DB·Neo4j 없이 쓴다.

- sample_graph_input(): 시군구 3 · 동 4 (주문진읍·연곡면·노학동 바닷가, 금암동 내륙)
- jumunjin_raw(): queries.fetch_dong("5115025000") 이 돌려줄 값. 주문진읍 실측(2025)과
  203동 기준 degree (유형은 2026-11-14 Supabase 실측, 기준 40 이상 1위만)
"""

import dataclasses

from explore.derive import TypeScore
from explore.graph import DongRow, GraphInput, RegionRow

GANGNEUNG_RAW = dict(
    enumerate([80.3, 69.8, 78.4, 70.0, 81.9, 78.0, 89.4, 100.0, 70.5, 85.9, 72.5, 74.9], 1)
)
JEONJU_RAW = dict(
    enumerate([79.6, 74.8, 94.4, 84.3, 86.9, 82.1, 83.0, 79.1, 93.1, 100.0, 87.8, 86.0], 1)
)
SOKCHO_RAW = {m: (100.0 if m in (7, 8) else 70.0) for m in range(1, 13)}
JUMUNJIN_WEEKDAY = [
    637, 511, 478, 471, 497, 601, 782, 1129, 1576, 1573, 1712, 1969,
    2064, 2042, 2036, 2024, 1969, 1920, 1666, 1321, 1091, 941, 793, 680,
]  # fmt: skip
JUMUNJIN_WEEKEND = [
    790, 638, 593, 569, 580, 669, 828, 1078, 1408, 1769, 2203, 2623,
    2808, 2824, 2833, 2766, 2548, 2353, 2010, 1659, 1369, 1122, 912, 788,
]  # fmt: skip


def curve(peak: int, quiet: int) -> list[float]:
    """peak 시각 200, quiet 시각 50, 나머지 100 인 24시간 값."""
    out = [100.0] * 24
    out[peak], out[quiet] = 200.0, 50.0
    return out


def sample_graph_input() -> GraphInput:
    regions = [
        RegionRow(1, "51150", "강릉시", "강원특별자치도"),
        RegionRow(4, "51210", "속초시", "강원특별자치도"),
        RegionRow(9, "52110", "전주시", "전북특별자치도"),
    ]
    dongs = [
        DongRow(
            116, "5115025000", "주문진읍", 1, 37.88818, 128.75354, 0.0,
            ["beach", "cafe", "food", "market", "park", "resort", "coast"], 0.7365,
        ),
        DongRow(
            112, "5115037000", "연곡면", 1, 37.85, 128.8, 0.0,
            ["beach", "cafe", "food", "coast"], 0.65,
        ),
        DongRow(
            403, "5121057000", "노학동", 4, 38.18, 128.55, 0.887,
            ["cafe", "food", "park", "resort", "coast"], 0.75,
        ),
        DongRow(901, "5211357500", "금암동", 9, 35.84, 127.13, None, [], 0.7218),
    ]  # fmt: skip
    grades = {
        116: {"quiet": ("B", 31, 0.8721), "peer": ("B", 39, 0.3735), "evening": ("A", 62, 0.278)},
        112: {"quiet": ("A", 55, 0.80), "peer": ("C", 20, 0.30), "evening": ("B", 40, 0.20)},
        403: {"quiet": ("B", 39, 0.85), "peer": ("A", 52, 0.40), "evening": ("B", 47, 0.22)},
        901: {"quiet": ("C", 21, 0.95), "peer": ("S", 97, 0.60), "evening": ("S", 94, 0.45)},
    }
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
    geumam_weekday = curve(17, 10)
    geumam_weekday[0] = None  # 값 없는 시각 → Neo4j 에는 -1
    hours = {
        116: {
            "weekday": (JUMUNJIN_WEEKDAY, 12, 9),
            "weekend": (JUMUNJIN_WEEKEND, 14, 3),
        },
        112: {"weekday": (curve(13, 9), 13, 9), "weekend": (curve(15, 3), 15, 3)},
        403: {"weekday": (curve(17, 9), 17, 9), "weekend": (curve(14, 3), 14, 3)},
        901: {"weekday": (geumam_weekday, 17, 10), "weekend": (curve(13, 5), 13, 5)},
    }
    similar = {
        116: [(403, 0.2141), (112, 0.3)],
        112: [(116, 0.3), (403, 0.31)],
        403: [(116, 0.2141), (112, 0.31)],
        901: [],
    }
    ease = {1: GANGNEUNG_RAW, 4: SOKCHO_RAW, 9: JEONJU_RAW}
    return GraphInput(regions, dongs, grades, types, hours, similar, ease, 2025)


def weak_yeongok_input() -> GraphInput:
    """연곡면 1위가 기준(40) 미만인 표본. 대표 유형 없음 확인용."""
    inp = sample_graph_input()
    weak = [
        TypeScore("immersion", 38, 1, "조용함 55 · 오전 한산 13"),
        TypeScore("local", 35, 2, "생활권 지수 0.65"),
        TypeScore("networking", 30, 3, "또래 밀도 20 · 저녁 활기 40"),
    ]
    return dataclasses.replace(inp, types=inp.types | {112: weak})


def _facts_row(code, scores, grades, top, quiet, coastal, life, region):
    """FACTS Cypher 한 행. scores = (quiet, peer, evening) 점수, grades 는 (등급, degree) 3개.
    life = (key, 이름, degree, 생활권 지수)."""
    return {
        "code": code,
        "grades": [
            {"metric": m, "grade": g, "score": s, "degree": deg}
            for m, s, (g, deg) in zip(("quiet", "peer", "evening"), scores, grades, strict=True)
        ],
        "top": [{"id": f"type:{top[0]}", "name": top[1], "degree": top[2]}],
        "quiet": [{"day": day, "h": h, "degree": deg} for day, h, deg in quiet],
        "near": [{"id": "place:coast", "kind": "바닷가", "degree": 97}] if coastal else [],
        "life": [{"id": f"life:{life[0]}", "name": life[1], "degree": life[2], "value": life[3]}],
        "region": {"id": f"region:{region[0]}", "name": region[1], "degree": region[2]},
    }


def jumunjin_raw() -> dict:
    """주문진읍 실측. 비슷한 동 공통 수는 노학동 6 · 대정읍 4 · 영천동 5 (스펙 §6-3 규칙)."""
    local_top = ("local", "로컬살이형", 72)
    local = ("local", "생활권", 107, 0.7365)
    coast_quiet = [("weekday", 9, 108), ("weekend", 3, 102)]
    return {
        "rec": {
            "dong": {
                "code": "5115025000",
                "name": "주문진읍",
                "coast_km": 0.0,
                "driv_weekday": JUMUNJIN_WEEKDAY,
                "driv_weekend": JUMUNJIN_WEEKEND,
            },
            "region": {"code": "51150", "name": "강릉시"},
            "sido": "강원특별자치도",
            "types": [
                [1, "로컬살이형", 60, "생활권 지수 0.74"],
                [2, "네트워킹형", 50, "또래 밀도 39 · 저녁 활기 62"],
                [3, "몰입형", 36, "조용함 31 · 오전 한산 44"],
            ],
            "grades": [
                ["quiet", "B", 31, 140, 203, 12, 21],
                ["peer", "B", 39, 124, 203, 15, 21],
                ["evening", "A", 62, 78, 203, 1, 21],
            ],
            "hours": [
                ["QUIET_AT", "weekday", 9],
                ["PEAK_AT", "weekday", 12],
                ["QUIET_AT", "weekend", 3],
                ["PEAK_AT", "weekend", 14],
            ],
            "near": [0.0],
            "months": [[m, raw, m in (2, 4, 9)] for m, raw in sorted(GANGNEUNG_RAW.items())],
            "similar": [
                [1, "5121057000", "노학동", "속초시"],
                [2, "5013025000", "대정읍", "서귀포시"],
                [3, "5013056000", "영천동", "서귀포시"],
            ],
        },
        "facts": {
            "5115025000": _facts_row(
                "5115025000",
                (31, 39, 62),
                [("B", 50), ("B", 50), ("A", 51)],
                local_top,
                coast_quiet,
                True,
                local,
                ("51150", "강릉시", 21),
            ),
            "5121057000": _facts_row(
                "5121057000",
                (39, 52, 47),
                [("B", 50), ("A", 51), ("B", 50)],
                local_top,
                coast_quiet,
                True,
                ("local", "생활권", 107, 0.7228),
                ("51210", "속초시", 8),
            ),
            "5013025000": _facts_row(
                "5013025000",
                (50, 50, 59),
                [("A", 51), ("A", 51), ("A", 51)],
                ("networking", "네트워킹형", 56),
                coast_quiet,
                True,
                ("tourist", "관광지", 96, 0.6584),
                ("50130", "서귀포시", 17),
            ),
            "5013056000": _facts_row(
                "5013056000",
                (41, 59, 67),
                [("B", 50), ("A", 51), ("A", 51)],
                ("networking", "네트워킹형", 56),
                coast_quiet,
                True,
                ("tourist", "관광지", 96, 0.6387),
                ("50130", "서귀포시", 17),
            ),
        },
        "synced_at": "2026-11-13T10:00:00+09:00",
        "dong_count": 203,
        "region_count": 12,
    }
