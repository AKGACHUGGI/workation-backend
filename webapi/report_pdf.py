"""W-06 지자체 진단 리포트 PDF (A4 1장). 입력은 화면과 같은 ReportResponse (webapi/report.py).

구성은 웹 화면과 같다: 요약 카드 3개 · 월별 방문 지수 막대 · 순위표 · 진단 요약 · 출처.
글꼴은 webapi/fonts 의 나눔고딕(OFL)을 PDF 에 넣는다 (서버에 한글 글꼴이 없어도 된다).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from webapi.report import METRIC_LABEL, PRESET_LABEL, months_text, overnight_copy

FONT_DIR = Path(__file__).resolve().parent / "fonts"
REGULAR, BOLD = "NanumGothic", "NanumGothic-Bold"

INK = HexColor("#222222")
MUTED = HexColor("#6a6a6a")
LINE = HexColor("#dddddd")
BAR = HexColor("#d4d4d4")
BRAND = HexColor("#ff385c")
SELECTED = HexColor("#fff1f3")
GRADE_COLOR = {
    "S": HexColor("#00a699"),
    "A": HexColor("#008a05"),
    "B": HexColor("#fc642d"),
    "C": HexColor("#767676"),
}

W, H = A4
MARGIN = 40
GAP = 12
INNER = W - 2 * MARGIN
PAD = 12

SOURCE = (
    "출처: LG U+ 유동인구·서비스인구 2025.1~12 (행정동 단위) · "
    "원본 인구수는 표시하지 않고 지수·등급·상대순위만 제공합니다"
)


def _fonts() -> None:
    if REGULAR not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(REGULAR, FONT_DIR / "NanumGothic-Regular.ttf"))
        pdfmetrics.registerFont(TTFont(BOLD, FONT_DIR / "NanumGothic-Bold.ttf"))


def wrap(text: str, font: str, size: float, width: float) -> list[str]:
    """띄어쓰기에서 줄을 바꾸고, 한 낱말이 폭을 넘으면 글자에서 자른다."""
    lines: list[str] = []
    line = ""
    for word in text.split(" "):
        trial = f"{line} {word}" if line else word
        if pdfmetrics.stringWidth(trial, font, size) <= width:
            line = trial
            continue
        if line:
            lines.append(line)
        line = word
        while pdfmetrics.stringWidth(line, font, size) > width:
            cut = len(line) - 1
            while cut > 1 and pdfmetrics.stringWidth(line[:cut], font, size) > width:
                cut -= 1
            lines.append(line[:cut])
            line = line[cut:]
    if line:
        lines.append(line)
    return lines


def _text(c: Canvas, x, y, text, font=REGULAR, size=9, color=INK) -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    c.drawString(x, y, text)


def _para(c: Canvas, x, y, text, width, font=REGULAR, size=8.5, color=MUTED, leading=13) -> float:
    """여러 줄 글. 마지막 줄 아래의 y 를 돌려준다."""
    for line in wrap(text, font, size, width):
        _text(c, x, y, line, font, size, color)
        y -= leading
    return y


def _box(c: Canvas, x, y, w, h) -> None:
    """(x, y) 가 왼쪽 위인 둥근 테두리 상자."""
    c.setStrokeColor(LINE)
    c.setLineWidth(0.8)
    c.roundRect(x, y - h, w, h, 8, stroke=1, fill=0)


def _badge(c: Canvas, x, y, grade: str, size=16) -> None:
    c.setFillColor(GRADE_COLOR.get(grade, MUTED))
    c.roundRect(x, y, size, size, 4, stroke=0, fill=1)
    c.setFont(BOLD, size * 0.6)
    c.setFillColor(white)
    c.drawCentredString(x + size / 2, y + size * 0.28, grade)


def _strongest(ranks: dict) -> str:
    return min(METRIC_LABEL, key=lambda k: ranks[k]["rank"])


def _weakest(ranks: dict) -> str:
    return max(METRIC_LABEL, key=lambda k: ranks[k]["rank"])


def _rank_copy(score: int) -> str:
    return (
        "상위권"
        if score >= 75
        else "중상위"
        if score >= 50
        else "중하위"
        if score >= 25
        else "하위권"
    )


def _cards(c: Canvas, r: dict, top: float) -> float:
    pc, ov, rk, ranks = r["peakConcentration"], r["overnight"], r["ranking"], r["metricRanks"]
    strong, weak = _strongest(ranks), _weakest(ranks)
    direction = "높음" if pc["value"] >= pc["average"] else "낮음"
    cards = [
        (
            "성수기 집중도",
            str(pc["value"]),
            "%",
            None,
            f"상위 3개월({months_text(pc['topMonths'])}) 방문인구 ÷ 연간 방문인구."
            f" {pc['total']}곳 평균 {pc['average']}%보다 집중도 {direction} ({pc['rank']}위)",
        ),
        (
            "숙박 성향",
            str(ov["score"]),
            "점",
            ov["grade"],
            f"숙박 계수 {ov['coef']:.2f} · 방문객 1명당 밤 체류 증가분 · 연간 기준."
            f" {overnight_copy(ov['coef'])}",
        ),
        (
            "대상 지역 대비 위치",
            str(rk["rank"]),
            f"위 / {rk['total']}",
            None,
            f"{PRESET_LABEL[r['preset']]} 프리셋 연간 순위."
            f" {METRIC_LABEL[strong]} {ranks[strong]['rank']}위 ·"
            f" {METRIC_LABEL[weak]} {_rank_copy(ranks[weak]['score'])}",
        ),
    ]
    w, h = (INNER - 2 * GAP) / 3, 118
    for i, (title, value, unit, grade, sub) in enumerate(cards):
        x = MARGIN + i * (w + GAP)
        _box(c, x, top, w, h)
        _text(c, x + PAD, top - 22, title, BOLD, 9.5)
        _text(c, x + PAD, top - 56, value, BOLD, 28)
        vx = x + PAD + pdfmetrics.stringWidth(value, BOLD, 28) + 4
        _text(c, vx, top - 56, unit, REGULAR, 11, MUTED)
        if grade:
            _badge(c, vx + pdfmetrics.stringWidth(unit, REGULAR, 11) + 8, top - 58, grade)
        _para(c, x + PAD, top - 76, sub, w - 2 * PAD, size=7.5, leading=11)
    return top - h


def _chart(c: Canvas, r: dict, x: float, top: float, w: float, h: float) -> None:
    _box(c, x, top, w, h)
    _text(c, x + PAD, top - 22, "월별 방문인구 지수", BOLD, 10.5)
    _text(
        c,
        x + PAD,
        top - 36,
        "연중 최대 = 100 · 붉은 막대는 성수기 상위 3개월",
        size=7.5,
        color=MUTED,
    )
    lines = wrap(r["seasonNarrative"], REGULAR, 8, w - 2 * PAD)
    text_h = len(lines) * 12
    base = top - h + PAD + text_h + 22  # 막대 바닥
    area = (top - 62) - base  # 값 100 의 높이
    months = r["months"]
    slot = (w - 2 * PAD) / max(len(months), 1)
    bar_w = slot * 0.62
    tops = set(r["peakConcentration"]["topMonths"])
    for i, m in enumerate(months):
        bx = x + PAD + i * slot + (slot - bar_w) / 2
        bh = max(area * m["crowd"] / 100, 1)
        c.setFillColor(BRAND if m["month"] in tops else BAR)
        c.roundRect(bx, base, bar_w, bh, 2, stroke=0, fill=1)
        c.setFont(REGULAR, 7)
        c.setFillColor(INK)
        c.drawCentredString(bx + bar_w / 2, base + bh + 3, str(m["crowd"]))
        c.setFillColor(MUTED)
        c.drawCentredString(bx + bar_w / 2, base - 11, f"{m['month']}월")
    y = top - h + PAD + text_h - 9
    for line in lines:
        _text(c, x + PAD, y, line, size=8, color=MUTED)
        y -= 12


def _ranking(c: Canvas, r: dict, x: float, top: float, w: float, h: float) -> None:
    _box(c, x, top, w, h)
    _text(c, x + PAD, top - 22, "대상 지역 대비 위치", BOLD, 10.5)
    _text(
        c,
        x + PAD,
        top - 36,
        f"종합 점수 · {PRESET_LABEL[r['preset']]} 프리셋 · 연간",
        size=7.5,
        color=MUTED,
    )
    items = r["ranking"]["items"]
    row = min((h - 48 - PAD) / max(len(items), 1), 20)
    name_x, bar_x = x + PAD + 16, x + PAD + 70
    bar_w = w - 2 * PAD - 70 - 24
    y = top - 48
    for i, item in enumerate(items, 1):
        mine = item["id"] == r["id"]
        if mine:
            c.setFillColor(SELECTED)
            c.roundRect(x + 5, y - row + 2, w - 10, row - 1, 4, stroke=0, fill=1)
        ty = y - row / 2 - 2.5
        font = BOLD if mine else REGULAR
        _text(c, x + PAD, ty, str(i), font, 8, MUTED)
        _text(c, name_x, ty, item["name"], font, 8.5)
        c.setFillColor(HexColor("#f2f2f2"))
        c.roundRect(bar_x, ty, bar_w, 6, 3, stroke=0, fill=1)
        c.setFillColor(BRAND if mine else HexColor("#b0b0b0"))
        c.roundRect(bar_x, ty, max(bar_w * item["score"] / 100, 6), 6, 3, stroke=0, fill=1)
        c.setFont(font, 8.5)
        c.setFillColor(INK)
        c.drawRightString(x + w - PAD, ty, str(item["score"]))
        y -= row


def _diagnosis(c: Canvas, r: dict, top: float) -> float:
    d = r["diagnosis"]
    cols = [
        ("S", "강점", d["strength"]),
        ("C", "약점", d["weakness"]),
        ("S", "제안", d["suggestion"]),
    ]
    col_w = (INNER - 2 * PAD - 2 * GAP) / 3
    longest = max(len(wrap(text, REGULAR, 8.5, col_w)) for _, _, text in cols)
    h = 66 + longest * 13
    _box(c, MARGIN, top, INNER, h)
    _text(c, MARGIN + PAD, top - 22, "진단 요약", BOLD, 10.5)
    _text(c, MARGIN + PAD + 52, top - 22, "규칙 기반 템플릿", size=7.5, color=MUTED)
    for i, (grade, title, text) in enumerate(cols):
        x = MARGIN + PAD + i * (col_w + GAP)
        _badge(c, x, top - 48, grade, 14)
        _text(c, x + 20, top - 45, title, BOLD, 9.5)
        _para(c, x, top - 64, text, col_w, color=INK)
    return top - h


def render(report: dict) -> bytes:
    """ReportResponse → PDF 바이트 (A4 1장)."""
    _fonts()
    out = BytesIO()
    c = Canvas(out, pagesize=A4)
    title = f"{report['name']} 진단 리포트 {report['year']}"
    c.setTitle(title)
    c.setAuthor("워케이션맵")

    y = H - MARGIN - 18
    _text(c, MARGIN, y, "지자체 진단 리포트", BOLD, 20)
    place = f"{report['province']} {report['name']}"
    c.setFont(BOLD, 13)
    c.setFillColor(BRAND)
    c.drawRightString(W - MARGIN, y + 2, place)
    y -= 20
    _text(
        c,
        MARGIN,
        y,
        "시군구별 성수기 집중도, 숙박 성향, 대상 지역 대비 위치를 한 장으로 정리합니다."
        f" {report['year']}년 연간 데이터 기준.",
        size=8.5,
        color=MUTED,
    )

    y = _cards(c, report, y - 18) - GAP
    mid_h = 300
    left = INNER * 0.58
    _chart(c, report, MARGIN, y, left, mid_h)
    _ranking(c, report, MARGIN + left + GAP, y, INNER - left - GAP, mid_h)
    y = _diagnosis(c, report, y - mid_h - GAP)

    foot = MARGIN
    for i, line in enumerate(reversed(wrap(SOURCE, REGULAR, 7.5, INNER))):
        _text(c, MARGIN, foot + i * 10, line, size=7.5, color=MUTED)
    c.setFont(REGULAR, 7.5)
    c.setFillColor(MUTED)
    c.drawRightString(W - MARGIN, foot - 12, f"워케이션맵 · 생성 {report['generatedAt'][:10]}")

    c.showPage()
    c.save()
    return out.getvalue()
