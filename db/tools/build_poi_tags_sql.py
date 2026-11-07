"""TourAPI 장소 → 행정동 장소 태그 SQL 생성

장소: 한국관광공사 국문 관광정보(KorService2/areaBasedList2)의 12개 시군구 전체(약 5천 곳).
분류: 관광공사 신분류 3단계 코드(lclsSystm3). 장소 좌표가 행정동 경계 안에 있으면 그 동에 센다.
태그: 아래 TAGS 의 코드에 해당하는 장소가 기준 개수 이상인 동에 붙인다.
  cafe 와 food 는 "많은 곳"이라 기준이 3 · 10 이다. 장소가 있는 동 가운데 상위 약 20% (2026-11-07 분포).
  나머지는 1곳 이상.
mountain 은 이 스크립트가 붙이지 않는다. 태그 시트(build_tags_sql.py)로 팀이 확정하고,
  preview 결과의 mountain_hint 를 시트의 제안값으로 쓴다.
시군구 태그는 소속 행정동 태그의 합집합으로 다시 계산한다.

사용 (표준 라이브러리만. 키는 TOURAPI_KEY 환경변수, 없으면 현재 폴더 .env 의 TOURAPI_KEY)
  python3 db/tools/build_poi_tags_sql.py preview <출력.sql>   # 동별 태그 개수 SELECT (DB 변경 없음)
  python3 db/tools/build_poi_tags_sql.py apply <출력.sql>     # dong.tags · region.tags 갱신
  migrations/20261107_dong-tags-coast.sql 을 먼저 적용해야 한다. 출력 SQL 은 커밋하지 않는다.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# (태그, 기준 개수, 관광공사 신분류 3단계 코드)
TAGS = [
    ("beach",     1,  ["NA020900"]),                          # 해변·해수욕장
    ("island",    1,  ["NA020500"]),                          # 섬
    ("surf",      1,  ["LS020100"]),                          # 윈드서핑·제트스키 (서핑 스쿨 포함)
    ("cafe",      3,  ["FD050100"]),                          # 카페
    ("food",      10, None),                                  # 음식점(contenttypeid 39) 중 카페 제외
    ("library",   1,  ["VE090300"]),                          # 도서관
    ("long_stay", 1,  ["AC020100", "AC020200", "AC060200"]),  # 콘도, 레지던스, 게스트하우스
    ("park",      1,  ["VE030100", "VE030400", "VE030500"]),  # 시민·근린·주제공원
    ("market",    1,  ["SH060100", "SH060200"]),              # 비상설·상설시장
    ("resort",    1,  ["VE050200"]),                          # 리조트
]
# 태그 시트의 mountain 제안값용 (DB 에 쓰지 않음)
MOUNTAIN_HINT = ["NA010100", "NA010200", "NA010300", "NA010400", "NA040600"]  # 산·오름, 숲, 폭포, 계곡, 자연휴양림
MANAGED = [t for t, _, _ in TAGS]

# (시군구, 법정동 시도코드, 시군구코드 목록). collect_tourapi 와 같은 2026년 TourAPI 기준
TARGETS = [
    ("강릉시", "51", ["150"]), ("고성군", "51", ["820"]), ("동해시", "51", ["170"]),
    ("속초시", "51", ["210"]), ("양양군", "51", ["830"]), ("고흥군", "12", ["740"]),
    ("순천시", "12", ["150"]), ("부안군", "52", ["800"]), ("전주시", "52", ["110", "111", "113"]),
    ("정읍시", "52", ["180"]), ("서귀포시", "50", ["130"]), ("제주시", "50", ["110"]),
]


def load_key() -> str:
    if os.environ.get("TOURAPI_KEY"):
        return os.environ["TOURAPI_KEY"]
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("TOURAPI_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("TOURAPI_KEY 환경변수나 .env 가 필요합니다")


def fetch_places(key: str) -> dict[str, dict]:
    places: dict[str, dict] = {}
    for name, regn, codes in TARGETS:
        for code in codes:
            page = 1
            while True:
                q = urllib.parse.urlencode({
                    "serviceKey": key, "MobileOS": "ETC", "MobileApp": "workation", "_type": "json",
                    "numOfRows": 1000, "pageNo": page, "lDongRegnCd": regn, "lDongSignguCd": code})
                body = json.loads(urllib.request.urlopen(
                    f"https://apis.data.go.kr/B551011/KorService2/areaBasedList2?{q}", timeout=30).read())["response"]["body"]
                items = body["items"]["item"] if body.get("items") else []
                for it in [items] if isinstance(items, dict) else items:
                    if it.get("mapx") and it.get("mapy"):
                        places[it["contentid"]] = it
                if page * 1000 >= int(body.get("totalCount") or 0):
                    break
                page += 1
                time.sleep(0.3)
        print(f"  {name} 누적 {len(places)}곳")
    return places


def tag_of(it: dict) -> list[str]:
    code = it.get("lclsSystm3", "")
    out = [t for t, _, codes in TAGS if codes and code in codes]
    if it.get("contenttypeid") == "39" and code != "FD050100":
        out.append("food")
    if code in MOUNTAIN_HINT:
        out.append("mountain_hint")
    return out


def build(mode: str, places: dict[str, dict]) -> str:
    rows = [f"('{t}', {float(it['mapx']):.6f}, {float(it['mapy']):.6f})"
            for it in places.values() for t in tag_of(it)]
    head = [
        "-- 장소 태그 (db/tools/build_poi_tags_sql.py 로 생성, 직접 고치지 말 것)",
        f"-- 장소: 한국관광공사 TourAPI {len(places)}곳 중 태그 대상 {len(rows)}건",
        "BEGIN;",
        "SET LOCAL search_path = public, extensions;",
        "CREATE TEMP TABLE poi (tag text, x float8, y float8) ON COMMIT DROP;",
        "INSERT INTO poi VALUES\n" + ",\n".join(rows) + ";",
        "CREATE TEMP TABLE cnt ON COMMIT DROP AS",
        "SELECT d.dong_id, p.tag, count(*) AS n FROM poi p",
        "  JOIN dong d ON ST_Contains(d.geom, ST_SetSRID(ST_MakePoint(p.x, p.y), 4326))",
        "  GROUP BY d.dong_id, p.tag;",
    ]
    if mode == "preview":  # 이 API 는 마지막 문장 결과만 돌려주므로 SELECT 로 끝낸다. 임시 테이블뿐이라 남는 것 없음
        return "\n".join(head + ["SELECT dong_id, tag, n FROM cnt ORDER BY dong_id, tag;"]) + "\n"
    managed = "ARRAY[" + ", ".join(f"'{t}'" for t in MANAGED) + "]"
    need = "CASE c.tag " + " ".join(f"WHEN '{t}' THEN {k}" for t, k, _ in TAGS) + " END"
    body = [
        f"UPDATE dong SET tags = coalesce((SELECT array_agg(t) FROM unnest(tags) t WHERE t <> ALL({managed})), '{{}}');",
        "UPDATE dong d SET tags = d.tags || ARRAY(",
        f"  SELECT c.tag FROM cnt c WHERE c.dong_id = d.dong_id AND c.tag = ANY({managed}) AND c.n >= {need}",
        "  ORDER BY c.tag);",
        "-- 시군구 태그 = 소속 행정동 태그의 합집합",
        "UPDATE region r SET tags = coalesce((",
        "  SELECT array_agg(DISTINCT t ORDER BY t) FROM dong d, unnest(d.tags) t WHERE d.region_id = r.region_id",
        "), '{}');",
        "COMMIT;",
    ]
    return "\n".join(head + body) + "\n"


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("preview", "apply"):
        sys.exit(__doc__)
    print("TourAPI 장소 받는 중")
    places = fetch_places(load_key())
    out = Path(sys.argv[2])
    out.write_text(build(sys.argv[1], places))
    print(f"→ {out} ({out.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
