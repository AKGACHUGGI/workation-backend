"""행정동 경계(vuski/admdongkor) → dong.geom·adm_code·area_km2 갱신 SQL 생성

데이터가 2025년 1~12월이라 같은 시점의 경계(ver20251231)를 쓴다.
  - 2025-09 금암1·2동 → 금암동 통합이 반영돼 있고
  - 전라남도가 아직 통합(전남광주통합특별시) 전 이름이라 DB 의 region.sido_name 과 같다.

매칭: (시도명, 시군구명, 읍면동명) 이름 3개. 경계 파일의 '전주시완산구'·'전주시덕진구' 는 '전주시' 로 묶는다.
고성군 수동면은 경계 파일에 없다(민통선 북쪽). DB 에서 이미 is_excluded=true 라 geom 을 비워 둔다.

출력 SQL 이 하는 일 (한 트랜잭션)
  1. postgis 확장 + dong.geom 컬럼 + GIST 인덱스 (workation-backend db/02_postgis.sql 과 동일, 재실행 안전)
  2. dong.adm_code(행정기관코드 10자리), dong.geom(MultiPolygon, WGS84), dong.area_km2 갱신
  3. region.sigungu_code(5자리) 갱신. 전주시는 시 코드 52110

사용 (pandas, pyarrow 필요. workation-data 의 uv 환경에서 실행하면 편하다)
  python3 db/tools/build_boundary_sql.py <경계.geojson> <dong_year_summary.parquet> <출력.sql>
  경계: https://github.com/vuski/admdongkor/tree/master/ver20251231
  parquet: workation-data preprocess.py 출력
  생성된 SQL(약 2 MB)은 커밋하지 않고 SQL Editor 나 psql 로 실행한다
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd


# 경계 파일에 없는 것으로 확인된 동 (이유와 함께). 이 목록 밖의 미매칭은 오류로 멈춘다.
KNOWN_MISSING = {("강원특별자치도", "고성군", "수동면"): "민통선 북쪽, 경계 자료 없음. DB 에서 is_excluded"}

# 시군구 코드. 경계 파일의 sgg 를 쓰되, 구가 나뉜 전주시는 시 코드로 둔다.
SGG_OVERRIDE = {"전주시": "52110"}


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main(src: Path, ours_path: Path, out: Path) -> None:
    features = json.loads(src.read_text())["features"]
    rows = []
    for f in features:
        p = f["properties"]
        sgg_key = "전주시" if p["sggnm"].startswith("전주시") else p["sggnm"]
        rows.append({
            "sido": p["sidonm"], "sgg": sgg_key, "dong": p["adm_nm"].split(" ")[-1],
            "adm_code": p["adm_cd2"], "sgg_code": SGG_OVERRIDE.get(sgg_key, p["sgg"]),
            "geom": json.dumps(f["geometry"], separators=(",", ":")),
        })
    b = pd.DataFrame(rows)

    ours = pd.read_parquet(ours_path)[["ctdo_nm", "ccw_nm", "adng_nm"]].drop_duplicates()
    m = ours.merge(b, left_on=["ctdo_nm", "ccw_nm", "adng_nm"], right_on=["sido", "sgg", "dong"], how="left")
    if len(m) != len(ours):
        sys.exit(f"이름이 겹치는 경계가 있습니다: {len(m) - len(ours)}건")
    missing = m[m.adm_code.isna()]
    unexpected = [k for k in map(tuple, missing[["ctdo_nm", "ccw_nm", "adng_nm"]].values) if k not in KNOWN_MISSING]
    if unexpected:
        sys.exit(f"예상 밖 미매칭: {unexpected}")
    matched = m[m.adm_code.notna()]

    lines = [
        "-- 행정동 경계 적재 (db/tools/build_boundary_sql.py 로 생성, 직접 고치지 말 것)",
        f"-- 원본: vuski/admdongkor {src.name} · 매칭 {len(matched)}/{len(ours)}",
        "-- 미매칭: " + ", ".join(f"{k[1]} {k[2]}({v})" for k, v in KNOWN_MISSING.items()),
        "BEGIN;",
        "SET LOCAL search_path = public, extensions;",
        "CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;",
        "ALTER TABLE dong ADD COLUMN IF NOT EXISTS geom extensions.geometry(MultiPolygon, 4326);",
        "COMMENT ON COLUMN dong.geom IS '경계 폴리곤 (WGS84). 행정동 경계 shp 에서 adm_code 로 매핑해 채움';",
        "CREATE INDEX IF NOT EXISTS dong_geom_idx ON dong USING gist (geom);",
        "",
    ]
    for r in matched.itertuples():
        geom = f"ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON({sql_str(r.geom)}), 4326))"
        lines.append(
            "UPDATE dong d SET adm_code = {code}, geom = g.geom, "
            "area_km2 = round((ST_Area(g.geom::geography) / 1e6)::numeric, 3) "
            "FROM (SELECT {geom} AS geom) g, region r "
            "WHERE d.region_id = r.region_id AND r.sido_name = {sido} AND r.sigungu_name = {sgg} "
            "AND d.dong_name = {dong};".format(
                code=sql_str(r.adm_code), geom=geom, sido=sql_str(r.ctdo_nm),
                sgg=sql_str(r.ccw_nm), dong=sql_str(r.adng_nm)))
    lines.append("")
    for (sido, sgg), code in matched.groupby(["ctdo_nm", "ccw_nm"]).sgg_code.first().items():
        lines.append(f"UPDATE region SET sigungu_code = {sql_str(code)} "
                     f"WHERE sido_name = {sql_str(sido)} AND sigungu_name = {sql_str(sgg)};")
    lines += ["", "COMMIT;", ""]
    out.write_text("\n".join(lines))
    print(f"매칭 {len(matched)}/{len(ours)}, 시군구 {matched.ccw_nm.nunique()}개 → {out} ({out.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
