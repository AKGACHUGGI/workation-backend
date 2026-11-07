"""문장 임베딩 → tag_vocab · dong_doc 의 embedding 채우는 SQL 생성

모델: CLOVA Studio 임베딩 v2 (1024차원). 검색할 때 질문도 같은 모델로 바꿔야 한다.
키: CLOVA_API_KEY 환경변수, 없으면 현재 폴더 .env 의 CLOVA_API_KEY. 키는 출력 SQL 에 들어가지 않는다.
출력 SQL 은 API 요청 한도(약 2 MB)를 넘지 않게 <접두어>_1.sql, _2.sql … 로 나눈다. 순서대로 실행한다.
출력 SQL 은 커밋하지 않는다.

사용 (표준 라이브러리만)
  python3 db/tools/build_embeddings.py vocab db/seed/tag_vocab.csv <출력 접두어>
      표현 사전 전체를 임베딩. CSV 에서 빠진 표현은 DB 에서도 지운다.
  python3 db/tools/build_embeddings.py doc <소개문.csv> <출력 접두어>
      소개문 CSV(dong_id, body)를 임베딩. 05_dong_doc.sql 실행 뒤 아래 결과를 CSV 로 받아 넣는다.
        SELECT dong_id, body FROM dong_doc WHERE embedding IS NULL ORDER BY dong_id;
      UPDATE 는 body 가 그대로일 때만 적용되므로, 그 사이 문장이 바뀐 행은 비어 있게 남는다.
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

URL = "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"
MODEL = "clova-embedding-v2"
DIM = 1024
ROWS_PER_FILE = 80  # 1행 약 12 KB → 파일 약 1 MB
INTERVAL = 1.05     # CLOVA Studio 기본 한도 분당 60회 (응답 헤더 x-ratelimit-limit-requests)


def load_key() -> str:
    if os.environ.get("CLOVA_API_KEY"):
        return os.environ["CLOVA_API_KEY"]
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("CLOVA_API_KEY="):
                return line.split("=", 1)[1].strip()
    sys.exit("CLOVA_API_KEY 환경변수나 .env 가 필요합니다")


def embed(key: str, text: str) -> list[float]:
    body = json.dumps({"text": text}).encode()
    for attempt in range(5):
        req = urllib.request.Request(URL, data=body, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4())})
        try:
            res = json.loads(urllib.request.urlopen(req, timeout=30).read())
            vec = res["result"]["embedding"]
            if len(vec) != DIM:
                sys.exit(f"차원이 {DIM} 이 아닙니다: {len(vec)}")
            time.sleep(INTERVAL)
            return vec
        except urllib.error.HTTPError as ex:
            if ex.code in (429, 500, 502, 503) and attempt < 4:
                reset = (ex.headers.get("x-ratelimit-reset-requests") or "").rstrip("s")
                time.sleep(float(reset) + 1 if reset.replace(".", "", 1).isdigit() else 2 ** (attempt + 2))
                continue
            sys.exit(f"임베딩 실패 HTTP {ex.code}: {ex.read()[:200].decode(errors='ignore')}")
    raise AssertionError


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def vec_sql(v: list[float]) -> str:
    return "'[" + ",".join(f"{x:.6f}" for x in v) + "]'"


def write_chunks(prefix: str, head: list[str], rows: list[str], tail_first: list[str]) -> None:
    chunks = [rows[i:i + ROWS_PER_FILE] for i in range(0, len(rows), ROWS_PER_FILE)] or [[]]
    for n, chunk in enumerate(chunks, 1):
        lines = head + ["BEGIN;", "SET LOCAL search_path = public, extensions;"]
        if n == 1:
            lines += tail_first
        lines += chunk + ["COMMIT;", ""]
        out = Path(f"{prefix}_{n}.sql")
        out.write_text("\n".join(lines))
        print(f"  → {out} ({len(chunk)}행, {out.stat().st_size / 1e6:.2f} MB)")


def vocab(src: Path, prefix: str) -> None:
    items = list(csv.DictReader(src.open(encoding="utf-8")))
    keys = set()
    for it in items:
        k = (it["target_kind"], it["target_key"], int(it["direction"]), it["phrase"].strip())
        if k in keys:
            sys.exit(f"중복 표현: {k}")
        keys.add(k)
    key = load_key()
    rows = []
    for i, (kind, tkey, d, phrase) in enumerate(sorted(keys), 1):
        v = embed(key, phrase)
        rows.append(
            "INSERT INTO tag_vocab (target_kind, target_key, direction, phrase, embedding, model) VALUES "
            f"({q(kind)}, {q(tkey)}, {d}, {q(phrase)}, {vec_sql(v)}, {q(MODEL)}) "
            "ON CONFLICT (target_kind, target_key, direction, phrase) DO UPDATE "
            "SET embedding = EXCLUDED.embedding, model = EXCLUDED.model, updated_at = now();")
        if i % 40 == 0:
            print(f"  {i}/{len(keys)}")
    keep = ",\n  ".join(f"({q(k)}, {q(t)}, {d}, {q(p)})" for k, t, d, p in sorted(keys))
    delete = ["-- CSV 에서 빠진 표현 지우기",
              "DELETE FROM tag_vocab WHERE (target_kind, target_key, direction, phrase) NOT IN (VALUES\n  "
              + keep + ");"]
    head = ["-- 표현 사전 임베딩 (db/tools/build_embeddings.py vocab 로 생성, 직접 고치지 말 것)",
            f"-- 원본 {src.name} {len(keys)}개 표현 · 모델 {MODEL}"]
    write_chunks(prefix, head, rows, delete)


def doc(src: Path, prefix: str) -> None:
    items = list(csv.DictReader(src.open(encoding="utf-8")))
    key = load_key()
    rows = []
    for i, it in enumerate(items, 1):
        body = it["body"]
        v = embed(key, body)
        rows.append(f"UPDATE dong_doc SET embedding = {vec_sql(v)}, model = {q(MODEL)}, updated_at = now() "
                    f"WHERE dong_id = {int(it['dong_id'])} AND md5(body) = md5({q(body)});")
        if i % 40 == 0:
            print(f"  {i}/{len(items)}")
    head = ["-- 동네 소개문 임베딩 (db/tools/build_embeddings.py doc 으로 생성, 직접 고치지 말 것)",
            f"-- {len(items)}개 · 모델 {MODEL}"]
    write_chunks(prefix, head, rows, [])


def main() -> None:
    if len(sys.argv) != 4 or sys.argv[1] not in ("vocab", "doc"):
        sys.exit(__doc__)
    mode, src, prefix = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    print(f"{mode} 임베딩 ({MODEL})")
    (vocab if mode == "vocab" else doc)(src, prefix)


if __name__ == "__main__":
    main()
