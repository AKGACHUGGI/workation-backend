"""자연어 검색 (F-09).
흐름은 workation-docs 설계/시스템-아키텍처 · 명세/api 의 /search/nl 설명과 같다.

① 질문 분해   HyperCLOVA X 가 문장을 {month, pieces} JSON 으로. 실패하면 규칙 파서
② 조건 매칭   조각마다 임베딩 → tag_vocab 최근접 표현. 유사도 THRESHOLD 미만은 unmatched
             임베딩을 못 쓰면 조각에 사전 표현이 그대로 들어 있는지로 대신 찾는다
③ 순위        종합 = 태그 만족 비율 × 100 과 조건 지표 평균을 반씩. 같으면 소개문 유사도
④ 추천 사유   DB 값만 쓰는 규칙 템플릿 (명세 v0.3: MVP 사유 문장은 규칙 템플릿)

THRESHOLD 근거 (db/README.md): 사전에 없는 표현 25개 중 24개 정답, 정답 유사도 최소 0.73,
무관한 문장 최고 0.62.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from django.db import connection

from core import clova
from metrics.scoring import LABELS, REGION_ONLY
from regions.photos import dong_photos
from regions.views import TAG_LABELS

logger = logging.getLogger(__name__)

THRESHOLD = 0.68
TOP_N = 5
SEASON = {"봄": 4, "여름": 8, "가을": 10, "겨울": 1}

PARSE_PROMPT = """너는 워케이션 지역 검색 질문을 조건으로 나누는 파서다.
JSON 한 줄만 출력한다. 설명 금지.
형식: {"month": 1~12 또는 null, "pieces": ["조건 조각", ...]}
규칙:
- month: 질문에 나온 달. "가을"=10, "여름"=8, "겨울"=1, "봄"=4. 없으면 null.
- pieces: 장소의 성격을 나타내는 표현을 하나씩 짧게 쪼갠다. 원문 표현을 최대한 살린다.
- 달, 기간("한 달", "일주일"), 인원, "추천해줘" 같은 말은 pieces 에 넣지 않는다.
  단 "한 달 살기 숙소"처럼 숙소 성격이면 넣는다.
- 장소 조건이 없는 질문이면 pieces 는 빈 배열이다.
예시
질문: 10월에 바다 근처 조용한 동네 추천해줘
{"month": 10, "pieces": ["바다 근처", "조용한"]}
질문: 카페 많고 젊은 사람 많은 도심, 저녁에 놀 데도 있으면 좋겠어
{"month": null, "pieces": ["카페 많은", "젊은 사람 많은", "도심", "저녁에 놀 데"]}"""


@dataclass
class Conditions:
    month: int | None = None
    tags: list[str] = field(default_factory=list)
    metrics: list[tuple[str, int]] = field(default_factory=list)  # (key, direction)
    unmatched: list[dict] = field(default_factory=list)
    parser: str = "llm"


# ───────────────────────── ① 질문 분해 ─────────────────────────


def parse_rule(question: str) -> dict:
    m = re.search(r"(1[0-2]|[1-9])\s*월", question)
    month = int(m.group(1)) if m else next((v for k, v in SEASON.items() if k in question), None)
    text = re.sub(
        r"((1[0-2]|[1-9])\s*월|봄|여름|가을|겨울)(에|의|에는)?|추천해\s*줘|추천|알려\s*줘|좋겠어|싶어",
        " ",
        question,
    )
    pieces = [
        p.strip()
        for p in re.split(r"[,.]|그리고|\s(?:하고|이고|있고|많고|좋고)\s", text)
        if len(p.strip()) > 1
    ]
    return {"month": month, "pieces": pieces}


def parse(question: str) -> tuple[dict, str]:
    try:
        out = clova.chat(PARSE_PROMPT, "질문: " + question)
        found = re.search(r"\{.*\}", out, re.S)
        data = json.loads(found.group(0)) if found else None
        if not isinstance(data, dict) or not isinstance(data.get("pieces"), list):
            raise ValueError("형식 오류")
        month = data.get("month")
        month = int(month) if str(month).isdigit() and 1 <= int(month) <= 12 else None
        pieces = [str(p).strip() for p in data["pieces"] if str(p).strip()]
        return {"month": month, "pieces": pieces}, "llm"
    except (clova.ClovaError, ValueError) as ex:  # 시연 중 LLM 이 실패해도 검색은 동작해야 한다
        logger.warning("LLM 파싱 실패 → 규칙 파서: %s", ex)
        return parse_rule(question), "rule"


# ───────────────────────── ② 조건 매칭 ─────────────────────────


def _vec(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def match(pieces: list[str]) -> list[dict]:
    """조각마다 가장 가까운 사전 표현. [{piece, kind, key, direction, phrase, sim}]"""
    if not pieces:
        return []
    try:
        vectors = [clova.embed(p) for p in pieces]
    except clova.ClovaError as ex:
        logger.warning("임베딩 실패 → 문자열 매칭: %s", ex)
        return _match_text(pieces)
    out = []
    with connection.cursor() as cur:
        for piece, v in zip(pieces, vectors, strict=True):
            cur.execute(
                """SELECT target_kind, target_key, direction, phrase,
                          1 - (embedding <=> %s::vector) AS sim
                     FROM tag_vocab WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector LIMIT 1""",
                [_vec(v), _vec(v)],
            )
            row = cur.fetchone()
            if row:
                kind, key, direction, phrase, sim = row
                out.append(
                    {
                        "piece": piece,
                        "kind": kind,
                        "key": key,
                        "direction": direction,
                        "phrase": phrase,
                        "sim": float(sim),
                    }
                )
    return out


def _match_text(pieces: list[str]) -> list[dict]:
    with connection.cursor() as cur:
        cur.execute("SELECT target_kind, target_key, direction, phrase FROM tag_vocab")
        vocab = sorted(cur.fetchall(), key=lambda r: -len(r[3]))  # 긴 표현 먼저
    out = []
    for piece in pieces:
        hit = next((r for r in vocab if r[3] in piece), None)
        if hit:
            out.append(
                {
                    "piece": piece,
                    "kind": hit[0],
                    "key": hit[1],
                    "direction": hit[2],
                    "phrase": hit[3],
                    "sim": 1.0,
                }
            )
        else:
            out.append(
                {
                    "piece": piece,
                    "kind": None,
                    "key": None,
                    "direction": 1,
                    "phrase": None,
                    "sim": 0.0,
                }
            )
    return out


def understand(question: str) -> Conditions:
    parsed, parser = parse(question)
    cond = Conditions(month=parsed["month"], parser=parser)
    for r in match(parsed["pieces"]):
        if r["key"] is None or r["sim"] < THRESHOLD:
            cond.unmatched.append({"text": r["piece"], "similarity": round(r["sim"], 2)})
            continue
        if r["kind"] == "tag":
            if r["key"] not in cond.tags:
                cond.tags.append(r["key"])
        elif all(k != r["key"] for k, _ in cond.metrics):
            cond.metrics.append((r["key"], int(r["direction"])))
    return cond


# ───────────────────────── ③ 순위 · ④ 사유 ─────────────────────────


def rank(cond: Conditions, question: str) -> list[dict]:
    """행정동 상위 TOP_N. 월 없이 시기 여유를 물으면 점수에서 빼고 사유에 한산한 달을 붙인다."""
    month = cond.month or 0
    metrics = [(k, d) for k, d in cond.metrics if not (k == "ease" and month == 0)]
    terms, params = [], []
    for key, d in metrics:
        if key in REGION_ONLY:
            src = (
                "(SELECT score FROM metric WHERE scope = 'region' AND unit_id = c.region_id"
                " AND metric_key = %s AND month = %s)"
            )
            params += [key, 0 if key == "overnight" else month]
        else:
            src = (
                "(SELECT score FROM metric WHERE scope = 'dong' AND unit_id = c.dong_id"
                " AND metric_key = %s AND month = %s)"
            )
            params += [key, month]
        terms.append(src if d == 1 else f"(100 - {src})")
    metric_cols = ", ".join(f"{t} AS m{i}" for i, t in enumerate(terms)) or "NULL AS m_none"
    n_tags, n_metrics = len(cond.tags), len(terms)
    m_avg = " + ".join(f"coalesce(m{i}, 0)" for i in range(n_metrics))
    if n_tags and n_metrics:
        total = f"(tag_hit * 100.0 / {n_tags} + ({m_avg}) / {n_metrics}.0) / 2"
    elif n_tags:
        total = f"tag_hit * 100.0 / {n_tags}"
    elif n_metrics:
        total = f"({m_avg}) / {n_metrics}.0"
    else:
        total = "0"

    try:
        qvec = _vec(clova.embed(question))
        doc_sim = "1 - (doc.embedding <=> %s::vector)"
        doc_params = [qvec]
    except clova.ClovaError:
        doc_sim, doc_params = "0", []

    sql = f"""
        WITH c AS (
          SELECT d.dong_id, d.region_id, d.adm_code, d.dong_name, d.tags, d.coast_dist_km,
                 r.sigungu_name, r.sigungu_code
            FROM dong d JOIN region r USING (region_id)
           WHERE NOT d.is_excluded AND d.adm_code IS NOT NULL
        ), s AS (
          SELECT c.*, doc.body,
                 cardinality(ARRAY(SELECT unnest(%s::text[])
                                   INTERSECT SELECT unnest(c.tags))) AS tag_hit,
                 {doc_sim} AS doc_sim,
                 {metric_cols}
            FROM c JOIN dong_doc doc USING (dong_id)
        )
        SELECT *, {total} AS total FROM s
         ORDER BY total DESC, doc_sim DESC
         LIMIT {TOP_N}"""
    with connection.cursor() as cur:
        cur.execute(sql, [cond.tags, *doc_params, *params])
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

    when = f"{cond.month}월" if cond.month else "연간"
    ease_hint = month == 0 and any(k == "ease" for k, _ in cond.metrics)
    results = []
    for i, r in enumerate(rows, 1):
        facts = []
        for j, (key, d) in enumerate(metrics):
            v = r.get(f"m{j}")
            if v is not None:
                score = v if d == 1 else 100 - v
                facts.append(
                    f"{when} {LABELS[key]} {score}점"
                    + ("" if d == 1 else " (낮을수록 조건에 맞음)")
                )
        have = r["tags"] or []
        for t in cond.tags:
            if t in have:
                if t == "coast" and r["coast_dist_km"] is not None:
                    facts.append(f"바닷가(해안선 {float(r['coast_dist_km']):.1f} km)")
                else:
                    facts.append(TAG_LABELS.get(t, t))
        if ease_hint:
            calm = re.search(r"한산한 달 ([\d·]+월)", r["body"] or "")
            if calm:
                facts.append(f"시군구 한산한 달 {calm.group(1)}")
        extra = [TAG_LABELS[t] for t in have if t not in cond.tags and t in TAG_LABELS]
        if extra:
            facts.append("그 밖에 " + "·".join(extra[:2]))
        results.append(
            {
                "rank": i,
                "dongId": r["adm_code"],
                "name": r["dong_name"],
                "regionId": r["sigungu_code"],
                "regionName": r["sigungu_name"],
                "score": round(float(r["total"]), 1),
                "reason": " · ".join(facts),
                "photo": next(
                    iter(dong_photos(r["dong_id"], r["region_id"], cond.month, 1)["featured"]), None
                ),
            }
        )
    return results


def chips(cond: Conditions) -> list[dict]:
    out = []
    if cond.month:
        out.append({"key": "month", "label": f"{cond.month}월"})
    for t in cond.tags:
        out.append({"key": f"tag:{t}", "label": TAG_LABELS.get(t, t)})
    for k, d in cond.metrics:
        if k == "ease" and not cond.month:
            out.append({"key": "metric:ease", "label": "한산한 달 안내 (월 미지정)"})
        else:
            out.append(
                {"key": f"metric:{k}", "label": f"{LABELS[k]} {'높은' if d == 1 else '낮은'} 곳"}
            )
    return out


def rank_query(cond: Conditions) -> str:
    parts = []
    if cond.month:
        parts.append(f"month={cond.month}")
    for k, d in cond.metrics:
        if d == 1:
            parts.append(f"w_{k}=1")
    if cond.tags:
        parts.append("tags=" + ",".join(cond.tags))
    return "&".join(parts)
