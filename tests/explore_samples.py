"""탐색 v6 테스트 공용 재료. DB·Neo4j 없이 쓴다.

- sample_graph_input(): 시군구 3 · 동 4 (주문진읍·연곡면·노학동 바닷가, 금암동 내륙)
- jumunjin_raw(): queries.fetch_dong("5115025000") 이 돌려줄 값. 주문진읍 실측(2025)과
  graph_data/facts.json 으로 센 203동 기준 degree
"""

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
STAY = "숙박 성향 64 · 한산한 달 7개(성수기보다 20% 이상 적은 달)"


def curve(peak: int, quiet: int) -> list[float]:
    """peak 시각 200, quiet 시각 50, 나머지 100 인 24시간 값."""
    out = [100.0] * 24
    out[peak], out[quiet] = 200.0, 50.0
    return out
