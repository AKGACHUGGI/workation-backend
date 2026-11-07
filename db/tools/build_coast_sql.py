"""OpenStreetMap 해안선 → dong.coast_dist_km · 해안 태그 SQL 생성

해안선: OpenStreetMap natural=coastline (© OpenStreetMap contributors, ODbL). Overpass API 로
12개 시군구 경계 범위를 0.25° 넓혀 받는다. 해안선은 DB 에 저장하지 않고 SQL 안의 임시 테이블로만 쓴다.

거리: 행정동 경계 폴리곤과 가장 가까운 해안선 사이의 실제 거리(km, geography). 경계가 해안에 닿으면 0.
  0.2°(약 18~22 km) 안에 해안선이 없으면 NULL. 받은 범위 밖의 해안까지 재지 않기 위해서다.
  즉 NULL = 내륙(해안에서 약 18 km 이상) 또는 경계 없음(수동면).

사용
  python3 db/tools/build_coast_sql.py preview <출력.sql>        # 거리만 SELECT (DB 변경 없음)
  python3 db/tools/build_coast_sql.py apply <출력.sql> 1         # coast_dist_km 저장 + 1 km 이내면 tags 에 'coast'
  표준 라이브러리만 쓴다. 출력 SQL(약 1 MB)은 커밋하지 않는다
  migrations/20261107_dong-tags-coast.sql 을 먼저 적용해야 한다
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import tempfile
import urllib.request
from pathlib import Path

# 시군구별 경계 범위 (DB: ST_Extent(dong.geom), 2026-11-07). (서, 남, 동, 북)
EXTENTS = {
    "강릉시": (128.580, 37.503, 129.073, 37.918), "고성군": (128.204, 38.189, 128.590, 38.617),
    "동해시": (128.958, 37.427, 129.161, 37.606), "속초시": (128.420, 38.119, 128.615, 38.231),
    "양양군": (128.404, 37.862, 128.810, 38.162), "고흥군": (127.081, 34.379, 127.547, 34.828),
    "순천시": (127.175, 34.831, 127.594, 35.189), "부안군": (126.099, 35.567, 126.840, 35.800),
    "전주시": (126.997, 35.728, 127.236, 35.901), "정읍시": (126.718, 35.461, 127.117, 35.758),
    "서귀포시": (126.168, 33.112, 126.948, 33.484), "제주시": (126.144, 33.273, 126.973, 34.013),
}
PAD = 0.25          # 받는 범위 여유 (°)
SEARCH_DEG = 0.2    # 거리를 재는 최대 반경 (°). PAD 보다 작아야 범위 밖 해안을 놓치지 않는다
OVERPASS = "https://overpass-api.de/api/interpreter"
SIMPLIFY_DEG = 0.0001  # 선 단순화 허용 오차 (°, 약 10 m). SQL 크기를 API 한도(약 2 MB) 아래로
CACHE = Path(tempfile.gettempdir()) / "workation_coastline_cache.json"  # Overpass 재호출 방지. 지우면 다시 받는다


def simplify(pts: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker (반복형). 끝점은 항상 남긴다."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5
        best, idx = -1.0, -1
        for i in range(a + 1, b):
            x, y = pts[i]
            d = abs(dy * (x - x1) - dx * (y - y1)) / norm if norm else ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
            if d > best:
                best, idx = d, i
        if best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(pts, keep) if k]


def near_our_area(pts: list[tuple[float, float]]) -> bool:
    """거리를 재는 반경(SEARCH_DEG) 안에 들어오는 선만 남긴다."""
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return any(min(xs) <= e + SEARCH_DEG and max(xs) >= w - SEARCH_DEG and
               min(ys) <= n + SEARCH_DEG and max(ys) >= s - SEARCH_DEG
               for w, s, e, n in EXTENTS.values())


def fetch_coastline() -> dict[int, list[tuple[float, float]]]:
    if CACHE.exists():
        print(f"  캐시 사용: {CACHE}")
        return {int(k): [tuple(p) for p in v] for k, v in json.loads(CACHE.read_text()).items()}
    ways: dict[int, list[tuple[float, float]]] = {}
    for name, (w, s, e, n) in EXTENTS.items():
        q = f'[out:json][timeout:90];way["natural"="coastline"]({s-PAD},{w-PAD},{n+PAD},{e+PAD});out geom;'
        req = urllib.request.Request(OVERPASS, data=urllib.parse.urlencode({"data": q}).encode(),
                                     headers={"User-Agent": "workation-capstone/0.1"})
        for attempt in range(3):
            try:
                data = json.loads(urllib.request.urlopen(req, timeout=120).read())
                break
            except Exception as ex:
                if attempt == 2:
                    sys.exit(f"{name} 해안선 받기 실패: {type(ex).__name__}")
                time.sleep(10)
        got = [el for el in data["elements"] if el["type"] == "way" and len(el.get("geometry", [])) >= 2]
        for el in got:
            ways[el["id"]] = [(p["lon"], p["lat"]) for p in el["geometry"]]
        print(f"  {name:5s} {len(got):4d}개 선")
        time.sleep(2)  # Overpass 공용 서버 예의
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(ways))
    return ways


def build(mode: str, ways: dict, km: float | None) -> str:
    rows = ",\n".join(
        f"({wid}, ST_GeomFromText('LINESTRING({', '.join(f'{x:.5f} {y:.5f}' for x, y in pts)})', 4326))"
        for wid, pts in ways.items())
    head = [
        "-- 해안 거리·해안 태그 (db/tools/build_coast_sql.py 로 생성, 직접 고치지 말 것)",
        f"-- 해안선: © OpenStreetMap contributors (ODbL), natural=coastline {len(ways)}개 선",
        "BEGIN;",
        "SET LOCAL search_path = public, extensions;",
        "CREATE TEMP TABLE coast (id bigint PRIMARY KEY, geom geometry(LineString, 4326)) ON COMMIT DROP;",
        f"INSERT INTO coast VALUES\n{rows};",
        "CREATE INDEX ON coast USING gist (geom);",
        "CREATE TEMP TABLE dist ON COMMIT DROP AS",
        "SELECT d.dong_id,",
        "       (SELECT round((min(ST_Distance(d.geom::geography, c.geom::geography)) / 1000)::numeric, 3)",
        f"          FROM coast c WHERE ST_DWithin(d.geom, c.geom, {SEARCH_DEG})) AS km",
        "  FROM dong d WHERE d.geom IS NOT NULL;",
    ]
    if mode == "preview":
        # 이 API 는 마지막 문장 결과만 돌려주므로 SELECT 로 끝낸다. 임시 테이블뿐이라 남는 것 없음
        body = ["SELECT r.sigungu_name, d.dong_name, t.km FROM dist t JOIN dong d USING (dong_id)",
                "  JOIN region r USING (region_id) ORDER BY t.km NULLS LAST;"]
    else:
        body = [
            "UPDATE dong d SET coast_dist_km = t.km FROM dist t WHERE d.dong_id = t.dong_id;",
            "-- 해안 태그: 다른 태그는 두고 'coast' 만 다시 계산",
            "UPDATE dong SET tags = array_remove(coalesce(tags, '{}'), 'coast');",
            f"UPDATE dong SET tags = array_append(tags, 'coast') WHERE coast_dist_km <= {km};",
            "UPDATE region r SET tags = array_remove(coalesce(r.tags, '{}'), 'coast');",
            "UPDATE region r SET tags = array_append(r.tags, 'coast')",
            "  WHERE EXISTS (SELECT 1 FROM dong d WHERE d.region_id = r.region_id AND 'coast' = ANY(d.tags));",
            "COMMIT;",
        ]
    return "\n".join(head + body) + "\n"


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] not in ("preview", "apply") or (sys.argv[1] == "apply" and len(sys.argv) != 4):
        sys.exit(__doc__)
    mode, out = sys.argv[1], Path(sys.argv[2])
    km = float(sys.argv[3]) if mode == "apply" else None
    print("해안선 받는 중 (Overpass)")
    raw = fetch_coastline()
    ways = {wid: simplify(pts, SIMPLIFY_DEG) for wid, pts in raw.items() if near_our_area(pts)}
    print(f"  반경 안 선 {len(ways)}/{len(raw)}개, 점 {sum(map(len, raw.values()))} → {sum(map(len, ways.values()))}")
    out.write_text(build(mode, ways, km))
    print(f"해안선 {len(ways)}개 선 → {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
