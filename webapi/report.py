"""W-06 지자체 진단 리포트 (/api/reports/{sggId} · /pdf).

계약: workation-web src/features/report/types.ts 의 ReportResponse.
연간(2025) 기준이고 preset 은 연간 순위의 프리셋만 바꾼다 (custom 은 지원하지 않는다).
진단 문장은 규칙 템플릿이다. 화면과 PDF 가 같은 문장을 쓰도록 응답에 넣는다.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from statistics import mean

from django.http import HttpResponse
from django.utils.http import content_disposition_header
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import bad_request, not_found
from metrics import scoring
from webapi import data

PRESETS = ("quiet", "lively")
# 웹 화면의 이름 (features/metric/constants.ts)
PRESET_LABEL = {"quiet": "조용한 곳", "lively": "활기 있는 곳"}
METRIC_LABEL = {
    "quiet": "동네 조용함",
    "crowd": "시기 여유",
    "peer": "또래 밀도",
    "overnight": "숙박 성향",
    "vibe": "저녁 활기",
}
SEASON_HEAD = {
    "summer_beach": "{top}에 방문이 집중되는 여름 해변형입니다.",
    "spring_autumn": "{top}에 방문이 몰리는 봄·가을형입니다.",
    "second_half_rise": "{top}에 방문이 많고 상반기보다 하반기 방문이 많은 하반기 상승형입니다.",
    "flat": "{top}에 방문이 가장 많지만 달마다 차이가 작은 연중 평탄형입니다.",
}
LOW_CROWD = 50  # 연중 최대의 절반 이하인 달을 비수기로 본다


def _round(x: float) -> int:
    """0.5 는 올린다 (scoring.weighted 와 같은 규칙)."""
    return math.floor(x + 0.5)


def _grade(score: int) -> str:
    return "S" if score >= 75 else "A" if score >= 50 else "B" if score >= 25 else "C"


def months_text(months: list[int]) -> str:
    """[2, 3, 4, 12] → '2~4월·12월', [7, 8, 10] → '7·8·10월'. 셋 이상 이어지면 범위로 묶는다."""
    runs: list[list[int]] = []
    for m in sorted(months):
        if runs and m == runs[-1][-1] + 1:
            runs[-1].append(m)
        else:
            runs.append([m])
    parts: list[str] = []
    ranged = False
    for run in runs:
        if len(run) >= 3:
            parts.append(f"{run[0]}~{run[-1]}")
            ranged = True
        else:
            parts.extend(str(m) for m in run)
    if not parts:
        return ""
    return "·".join(f"{p}월" for p in parts) if ranged else "·".join(parts) + "월"


def _josa(word: str, with_final: str, without_final: str) -> str:
    """받침에 따라 조사를 붙인다 ('숙박 성향' → 이, '저녁 활기' → 가)."""
    code = ord(word[-1]) - 0xAC00
    has_final = 0 <= code < 11172 and code % 28 != 0
    return word + (with_final if has_final else without_final)


def _ro(number: str) -> str:
    """숫자 뒤의 '로/으로' (0 · 3 · 6 으로 끝나면 '으로')."""
    return number + ("으로" if number[-1] in "036" else "로")


def _overnight_kind(coef: float) -> str:
    if coef >= 0.9:
        return "방문객 대부분이 숙박하는"
    if coef >= 0.7:
        return "숙박과 당일 방문이 섞인"
    return "당일 방문 위주의"


def overnight_copy(coef: float) -> str:
    if coef >= 0.9:
        return "방문객 대부분 숙박"
    if coef >= 0.7:
        return "숙박·당일 혼합"
    return "당일 방문 위주"


def _top_months(crowd: dict[int, float]) -> list[int]:
    return sorted(sorted(crowd, key=lambda m: (-crowd[m], m))[:3])


def _peak_share(crowd: dict[int, float]) -> float:
    """상위 3개월 방문 ÷ 연간 방문 (%). 혼잡 지수가 방문인구에 비례하므로 지수로 계산한다."""
    total = sum(crowd.values())
    return 100 * sum(crowd[m] for m in _top_months(crowd)) / total if total else 0.0


def _annual_ranking(preset: str) -> list[tuple[int, float]]:
    """[(region_id, 월별 프리셋 종합 점수의 12개월 평균)] 내림차순."""
    weights = scoring.PRESETS[preset]
    sums: dict[int, list[int]] = {}
    for m in range(1, 13):
        for rid, value in scoring.region_values(m, weights).items():
            if value is not None:
                sums.setdefault(rid, []).append(value)
    return sorted(((rid, mean(v)) for rid, v in sums.items()), key=lambda x: (-x[1], x[0]))


def _quiet_dongs(region_id: int, limit: int = 2) -> list[str]:
    """그 시군구에서 연간 동네 조용함 점수가 높은 동 이름."""
    scores = scoring.unit_scores("dong", None)
    mine = [
        (scores.get(d.dong_id, {}).get("quiet", {}).get("score"), d.dong_name)
        for d in data.active_dongs()
        if d.region_id == region_id
    ]
    ranked = sorted(((s, n) for s, n in mine if s is not None), key=lambda x: (-x[0], x[1]))
    return [n for _, n in ranked[:limit]]


def _narrative(season: str, crowd: dict[int, float]) -> tuple[str, list[int]]:
    low = [m for m in sorted(crowd) if crowd[m] <= LOW_CROWD]
    head = SEASON_HEAD[season].format(top=months_text(_top_months(crowd)))
    tail = (
        f"{months_text(low)}은 연중 최대의 절반 이하로, 비수기 체류 지원에 적합합니다."
        if low
        else "방문이 고르게 분포해 특정 비수기가 뚜렷하지 않습니다."
    )
    return f"{head} {tail}", low


def _position(rank: int, total: int) -> str:
    if rank <= total / 4:
        return "상위권"
    if rank <= total / 2:
        return "중상위권"
    if rank <= total * 3 / 4:
        return "중하위권"
    return "하위권"


def _diagnosis(report: dict, low: list[int], quiet_dongs: list[str]) -> dict:
    ranks = report["metricRanks"]
    total = report["ranking"]["total"]
    # 순위가 같으면 METRIC_LABEL 순서의 앞쪽 (웹 copy.ts 의 strongestMetric · weakestMetric 과 같게)
    strong = min(METRIC_LABEL, key=lambda k: ranks[k]["rank"])
    weak = max(METRIC_LABEL, key=lambda k: ranks[k]["rank"])
    coef = report["overnight"]["coef"]
    pc = report["peakConcentration"]
    if pc["value"] > pc["average"]:
        peak = f"보다 높아 {months_text(pc['topMonths'])}에 방문이 쏠립니다"
    elif pc["value"] < pc["average"]:
        peak = "보다 낮아 성수기 쏠림이 작습니다"
    else:
        peak = "와 같은 수준입니다"
    when = f"{months_text(low)} 비수기에 " if low else ""
    where = (
        f"{'·'.join(quiet_dongs)} 등 지역 안에서 조용한 동네를 중심으로 한 " if quiet_dongs else ""
    )
    return {
        "strength": (
            f"{METRIC_LABEL[strong]} {ranks[strong]['rank']}위로 {total}곳 중"
            f" {_position(ranks[strong]['rank'], total)}이며,"
            f" 숙박 계수 {_ro(f'{coef:.2f}')} {_overnight_kind(coef)} 지역입니다."
        ),
        "weakness": (
            f"{_josa(METRIC_LABEL[weak], '이', '가')} {ranks[weak]['rank']}위로 가장 약하고,"
            f" 성수기 집중도 {pc['value']}%는 {total}곳 평균 {pc['average']}%{peak}."
        ),
        "suggestion": (
            f"{when}{where}체류형 워케이션 프로그램이 적합하며,"
            f" {METRIC_LABEL[weak]} 보완 시 연간 순위 상승 여지가 있습니다."
        ),
    }


def build_report(sgg_id: str, preset: str) -> dict | None:
    """ReportResponse. 시군구가 없으면 None."""
    regions = data.regions_by_id()
    region = next((r for r in regions.values() if r.sigungu_code == sgg_id), None)
    if region is None:
        return None
    rid = region.region_id
    total = len(regions)

    crowds = {r: data.monthly_crowd(r) for r in regions}
    crowd = crowds[rid]
    shares = {r: _peak_share(c) for r, c in crowds.items()}

    annual = scoring.unit_scores("region", None)
    overnight = annual.get(rid, {}).get("overnight", {})
    overnight_score = overnight.get("score") or 0

    metric_ranks = {}
    for web, db in data.WEB_TO_DB.items():
        values = {r: annual.get(r, {}).get(db, {}).get("score") for r in regions}
        metric_ranks[web] = {
            "rank": scoring.rank_of(values, rid) or total,
            "score": values[rid] or 0,
        }

    ranking = _annual_ranking(preset)
    order = [r for r, _ in ranking]
    season = data.season_type(crowd)
    narrative, low = _narrative(season, crowd)

    report = {
        "id": region.sigungu_code,
        "name": region.sigungu_name,
        "province": region.sido_name,
        "year": data.DATA_YEAR,
        "preset": preset,
        "peakConcentration": {
            "value": _round(shares[rid]),
            "topMonths": _top_months(crowd),
            "average": _round(mean(shares.values())),
            "rank": scoring.rank_of(shares, rid),
            "total": total,
        },
        "overnight": {
            "coef": round(overnight.get("raw") or 0, 2),
            "score": overnight_score,
            "grade": overnight.get("grade") or _grade(overnight_score),
        },
        "ranking": {
            "rank": order.index(rid) + 1 if rid in order else total,
            "total": total,
            "items": [
                {
                    "id": regions[r].sigungu_code,
                    "name": regions[r].sigungu_name,
                    "score": _round(score),
                }
                for r, score in ranking
            ],
        },
        "metricRanks": metric_ranks,
        "months": [{"month": m, "crowd": _round(crowd[m])} for m in sorted(crowd)],
        "seasonType": season,
        "seasonNarrative": narrative,
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }
    report["diagnosis"] = _diagnosis(report, low, _quiet_dongs(rid))
    return report


def _load(request, sgg_id: str):
    """(report, None) 또는 (None, 오류 응답)."""
    year = request.query_params.get("year") or str(data.DATA_YEAR)
    if year != str(data.DATA_YEAR):
        return None, bad_request(f"year 는 {data.DATA_YEAR} 입니다.")
    preset = request.query_params.get("preset") or "quiet"
    if preset not in PRESETS:
        return None, bad_request("preset 은 quiet 또는 lively 입니다.")
    report = build_report(sgg_id, preset)
    if report is None:
        return None, not_found("시군구를 찾을 수 없습니다.")
    return report, None


@api_view(["GET"])
def report(request, sgg_id: str):
    body, failure = _load(request, sgg_id)
    return failure or Response(body)


@api_view(["GET"])
def report_pdf(request, sgg_id: str):
    body, failure = _load(request, sgg_id)
    if failure:
        return failure
    from webapi import report_pdf as pdf  # reportlab 은 PDF 를 만들 때만 읽는다

    res = HttpResponse(pdf.render(body), content_type="application/pdf")
    filename = f"{body['name']}_진단리포트_{body['year']}.pdf"
    res["Content-Disposition"] = content_disposition_header(True, filename)
    return res
