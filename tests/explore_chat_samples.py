"""탐색 v7 채팅 테스트 공용 재료. 전주시 동 3개(효자2동 · 여의동 · 교1동)와 이름 사전.

chat_raw() 는 queries.fetch_dong 이 돌려주는 모양을 채팅이 쓰는 필드만 채워 만든다.
점수는 축별 순서를 확인하기 좋게 고른 값이다 (실측 아님).
"""

from explore.chat.resolve import DongName
from explore.graph import METRICS

HYOJA, YEOUI, GYO = "4511164500", "4511163500", "4511152000"
SONG = "4511153000"  # 송천1동 (NAMES 에 있음)
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
    """scores · grades = (조용함, 또래 밀도, 저녁 활기).
    types = (로컬살이형, 네트워킹형, 몰입형) 점수. near = 해안선까지 km (None 이면 내륙).
    similar = [(순위, 코드, 이름, 시군구)]."""
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


def region_dong(code, name, grades, types, *, near=(), life=("생활권", 0.8)):
    """REGION Cypher 의 dongs 한 칸. grades = [(지표, 등급, 점수, 시군구 순위)] — 시군구 동 4곳."""
    return {
        "code": code,
        "name": name,
        "grades": [[m, g, s, r, 4] for m, g, s, r in grades],
        "types": [[i + 1, n, s] for i, (n, s) in enumerate(types)],
        "near": list(near),
        "life": [list(life)],
    }


# 시군구 채팅 원재료 (queries.fetch_region 모양). 점수는 순서를 확인하기 좋게 고른 값 (실측 아님)
REGION_RAW = {
    "region": {"code": JEONJU[0], "name": JEONJU[1]},
    "sido": "전북특별자치도",
    "months": [[m, 80.0 + m, m in (1, 2, 8)] for m in range(1, 13)],
    "dongs": [
        region_dong(
            HYOJA, "효자2동",
            [("quiet", "S", 90, 1), ("peer", "B", 40, 3), ("evening", "C", 20, 4)],
            [("몰입형", 70), ("로컬살이형", 50), ("네트워킹형", 30)],
            life=("생활권", 0.9),
        ),
        region_dong(
            YEOUI, "여의동",
            [("quiet", "A", 70, 2), ("peer", "A", 60, 2), ("evening", "S", 95, 1)],
            [("네트워킹형", 65), ("로컬살이형", 45), ("몰입형", 20)],
            life=("관광지", 0.3),
        ),
        region_dong(
            GYO, "교1동",
            [("quiet", "A", 60, 3), ("peer", "S", 88, 1), ("evening", "A", 70, 2)],
            [("로컬살이형", 60), ("네트워킹형", 55), ("몰입형", 40)],
            near=(3.2,),
            life=("생활권", 0.8),
        ),
        region_dong(
            SONG, "송천1동",
            [("quiet", "C", 10, 4), ("peer", "C", 20, 4), ("evening", "B", 50, 3)],
            [("로컬살이형", 45), ("몰입형", 25), ("네트워킹형", 10)],
            life=("생활권", 0.6),
        ),
    ],
}  # fmt: skip
