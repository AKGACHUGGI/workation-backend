"""문장 임베딩 → tag_vocab · dong_doc · ask_vocab 의 embedding 채우는 SQL 생성

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
  python3 db/tools/build_embeddings.py ask db/seed/ask_vocab.csv <출력 접두어> [임베딩된 표현.csv]
      동네에 물어보기 의도 사전. CSV 를 검사(의도 14개, 의도마다 5~8개, 대표 질문)하고 전체를 넣는다.
      CSV 에서 빠진 표현은 DB 에서도 지운다. 네 번째 인자를 주면 거기 있는 표현은 임베딩을 다시
      만들지 않는다 (새 표현만 CLOVA 호출). 그 CSV 는 아래 결과를 받는다.
        SELECT phrase FROM ask_vocab WHERE embedding IS NOT NULL AND model = 'clova-embedding-v2';
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


# 동네에 물어보기 의도. 앞 10개는 답할 수 있는 의도(대표 질문 1개), 뒤 4개는 고정 안내 (스펙 §3)
ASK_ANSWERABLE = ("weekday_hours", "weekend_hours", "months", "quiet", "peer", "evening",
                  "type", "coast", "life", "similar")
ASK_UNKNOWN = ("unknown_place", "unknown_stay", "unknown_transport", "unknown_other")


def read_ask(src: Path) -> list[tuple[str, str, bool]]:
    """ask_vocab.csv → [(intent, phrase, canonical)]. 규칙에 어긋나면 이유를 찍고 끝낸다."""
    items = list(csv.DictReader(src.open(encoding="utf-8")))
    out, seen = [], set()
    for it in items:
        intent, phrase, flag = it["intent"], it["phrase"].strip(), it["canonical"]
        if intent not in ASK_ANSWERABLE + ASK_UNKNOWN:
            sys.exit(f"모르는 의도: {intent}")
        if not 1 <= len(phrase) <= 100:
            sys.exit(f"표현은 1~100자: {phrase!r}")
        if phrase in seen:
            sys.exit(f"중복 표현: {phrase}")
        if flag not in ("true", "false"):
            sys.exit(f"canonical 은 true/false: {phrase}")
        seen.add(phrase)
        out.append((intent, phrase, flag == "true"))
    for intent in ASK_ANSWERABLE + ASK_UNKNOWN:
        n = sum(1 for i, _, _ in out if i == intent)
        canon = sum(1 for i, _, c in out if i == intent and c)
        if not 5 <= n <= 8:
            sys.exit(f"{intent} 표현은 5~8개: {n}개")
        if canon != (1 if intent in ASK_ANSWERABLE else 0):
            sys.exit(f"{intent} 대표 질문(canonical) 수가 틀림: {canon}개")
    return out


def ask(src: Path, prefix: str, have: Path | None = None) -> None:
    items = read_ask(src)
    done = {r["phrase"] for r in csv.DictReader(have.open(encoding="utf-8"))} if have else set()
    todo = [it for it in items if it[1] not in done]
    key = load_key() if todo else ""
    rows = []
    for intent, phrase, canon in items:
        flag = "true" if canon else "false"
        if phrase in done:
            rows.append(
                f"INSERT INTO ask_vocab (intent, phrase, canonical) VALUES ({q(intent)}, {q(phrase)}, {flag}) "
                "ON CONFLICT (phrase) DO UPDATE SET intent = EXCLUDED.intent, "
                "canonical = EXCLUDED.canonical, updated_at = now();")
            continue
        v = embed(key, phrase)
        rows.append(
            "INSERT INTO ask_vocab (intent, phrase, canonical, embedding, model) VALUES "
            f"({q(intent)}, {q(phrase)}, {flag}, {vec_sql(v)}, {q(MODEL)}) "
            "ON CONFLICT (phrase) DO UPDATE SET intent = EXCLUDED.intent, canonical = EXCLUDED.canonical, "
            "embedding = EXCLUDED.embedding, model = EXCLUDED.model, updated_at = now();")
    keep = ",\n  ".join(q(p) for _, p, _ in items)
    first = ["-- 대표 질문이 다른 표현으로 옮겨 가도 의도당 1개 제약(ask_vocab_canonical)에 걸리지 않게",
             "UPDATE ask_vocab SET canonical = false WHERE canonical;",
             "-- CSV 에서 빠진 표현 지우기",
             "DELETE FROM ask_vocab WHERE phrase NOT IN (\n  " + keep + ");"]
    head = ["-- 동네에 물어보기 의도 사전 (db/tools/build_embeddings.py ask 로 생성, 직접 고치지 말 것)",
            f"-- 원본 {src.name} {len(items)}개 표현 · 새로 임베딩 {len(todo)}개 · 모델 {MODEL}"]
    write_chunks(prefix, head, rows, first)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "ask" and len(sys.argv) in (4, 5):
        print(f"ask 임베딩 ({MODEL})")
        ask(Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4]) if len(sys.argv) == 5 else None)
        return
    if len(sys.argv) != 4 or mode not in ("vocab", "doc"):
        sys.exit(__doc__)
    src, prefix = Path(sys.argv[2]), sys.argv[3]
    print(f"{mode} 임베딩 ({MODEL})")
    (vocab if mode == "vocab" else doc)(src, prefix)


if __name__ == "__main__":
    main()
