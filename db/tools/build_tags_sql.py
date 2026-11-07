"""태그 입력 시트(xlsx) → dong.tags · region.tags 의 city · mountain 갱신 SQL 생성

시트: '태그 입력' 탭의 dong_id, city, mountain 열. 값이 'Y'(대소문자 무관)면 태그를 붙인다.
coast 태그는 build_coast_sql.py 가 관리하므로 건드리지 않는다.
시군구 태그는 소속 행정동 태그의 합집합으로 다시 계산한다 (coast 포함).

사용 (openpyxl 필요)
  python3 db/tools/build_tags_sql.py <dong_tags_sheet.xlsx> <출력.sql>
"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import load_workbook

MANAGED = ("city", "mountain")


def main(sheet: Path, out: Path) -> None:
    ws = load_workbook(sheet, read_only=True, data_only=True)["태그 입력"]
    rows = ws.iter_rows(values_only=True)
    head = [str(h).strip() if h is not None else "" for h in next(rows)]
    col = {name: head.index(name) for name in ("dong_id", *MANAGED)}

    marked: dict[str, list[int]] = {t: [] for t in MANAGED}
    seen = 0
    for r in rows:
        if r[col["dong_id"]] is None:
            continue
        seen += 1
        dong_id = int(r[col["dong_id"]])
        for t in MANAGED:
            v = r[col[t]]
            if v is not None and str(v).strip().upper() == "Y":
                marked[t].append(dong_id)
            elif v not in (None, ""):
                sys.exit(f"dong_id {dong_id} 의 {t} 값이 'Y' 나 빈칸이 아닙니다: {v!r}")
    if seen != 204:
        sys.exit(f"행정동이 204행이 아닙니다: {seen}행")

    lines = [
        "-- city · mountain 태그 (db/tools/build_tags_sql.py 로 생성, 직접 고치지 말 것)",
        f"-- 시트: {sheet.name} · " + " · ".join(f"{t} {len(v)}곳" for t, v in marked.items()),
        "BEGIN;",
    ]
    for t in MANAGED:
        lines.append(f"UPDATE dong SET tags = array_remove(tags, '{t}');")
        if marked[t]:
            ids = ", ".join(map(str, sorted(marked[t])))
            lines.append(f"UPDATE dong SET tags = array_append(tags, '{t}') WHERE dong_id IN ({ids});")
    lines += [
        "-- 시군구 태그 = 소속 행정동 태그의 합집합",
        "UPDATE region r SET tags = coalesce((",
        "  SELECT array_agg(DISTINCT t ORDER BY t) FROM dong d, unnest(d.tags) t WHERE d.region_id = r.region_id",
        "), '{}');",
        "COMMIT;",
        "",
    ]
    out.write_text("\n".join(lines))
    print(f"{seen}행 읽음 · " + " · ".join(f"{t} {len(v)}곳" for t, v in marked.items()) + f" → {out}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
