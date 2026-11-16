"""질문 속 동 이름 → 비교 동네 (스펙 §4)."""

from explore.chat import resolve as r
from tests.explore_chat_samples import GYO, HYOJA, NAMES, YEOUI

PANEL = NAMES[0]


def go(question, prev=()):
    return r.resolve(question, PANEL, list(prev), NAMES)


def test_same_sigungu_name_is_added():
    got = go("여의동이랑 비교하면?")
    assert got == r.Resolved([HYOJA, YEOUI], named=True)


def test_spaced_digit_name():
    assert go("교 1동은 어때?").dongs == [HYOJA, GYO]


def test_longest_name_wins():
    # '송천1동' 안의 '송천'(송천동 약칭)이 따로 잡히지 않는다
    assert go("송천1동이랑 비교").dongs == [HYOJA, "4511153000"]


def test_same_name_prefers_panel_sigungu():
    assert go("송정동은?").dongs == [HYOJA, "4511199000"]


def test_other_sigungu_dong_guides():
    got = go("경포동이랑 비교해 줘")
    assert got.dongs == [HYOJA]
    assert got.guide.startswith("지금은 같은 시군구 안의 동네끼리만 비교할 수 있어요.")
    assert "전주시에서는 교1동, 송정동, 송천1동 등과" in got.guide


def test_other_sigungu_name_only_asks_for_dong():
    assert go("강릉시는 어때?").guide == r.ASK_DONG


def test_own_sigungu_name_is_not_a_guide():
    assert go("전주시 1년 중 언제 한산해?") == r.Resolved([HYOJA])


def test_no_name_inherits_previous():
    assert go("주말엔?", [HYOJA, YEOUI]) == r.Resolved([HYOJA, YEOUI])


def test_new_name_replaces_previous():
    assert go("그럼 교1동은?", [HYOJA, YEOUI]).dongs == [HYOJA, GYO]


def test_together_keeps_previous():
    assert go("셋 다 비교하면? 교1동도", [HYOJA, YEOUI]).dongs == [HYOJA, YEOUI, GYO]


def test_more_than_three_keeps_first_three():
    got = go("여의동, 교1동, 송천1동 다 비교해 줘")
    assert got.dongs == [HYOJA, YEOUI, GYO]
    assert got.note == r.TOO_MANY


def test_panel_name_only_goes_back_to_one():
    assert go("효자2동 조용해?", [HYOJA, YEOUI]) == r.Resolved([HYOJA])


def test_previous_of_other_panel_is_ignored():
    assert go("주말엔?", ["x", "y"]).dongs == [HYOJA]


def test_aliases():
    assert r.aliases("효자2동") == {"효자2동", "효자 2동"}
    assert r.aliases("주문진읍") == {"주문진읍", "주문진"}
    assert r.aliases("교동") == {"교동"}
