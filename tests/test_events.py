"""개인화 랭킹 행동 기록 POST /api/events 와 인기 지역 viewCount 집계.
계약: workation-web docs/superpowers/plans/2026-11-15-개인화-랭킹.md "공유 계약" 서버 계약"""

import pytest
from django.core.cache import cache
from django.db import connection

from tests.test_api import data  # noqa: F401
from webapi.models import UserEvent
from webapi.views import EventsBurstThrottle

AID = "9b2f6c1e-3d4a-4c5b-8e7f-0a1b2c3d4e5f"
AID_B = "1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f"
AID_C = "2d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f6a"


@pytest.fixture(autouse=True)
def fresh_throttle():
    """한도 카운터(캐시)를 테스트마다 비워 다른 테스트의 요청 수에 묶이지 않게 한다."""
    cache.clear()
    yield
    cache.clear()


def post(client, body):
    return client.post("/api/events", body, content_type="application/json")


def view(anon_id=AID, target="51820"):
    return {
        "anonId": anon_id,
        "kind": "region_view",
        "target": target,
        "payload": {"sigunguId": None, "snapshot": None},
    }


def test_region_view_saved(client, data):  # noqa: F811
    r = post(client, view())
    assert r.status_code == 204
    row = UserEvent.objects.get()
    assert (str(row.anon_id), row.kind, row.target) == (AID, "region_view", "51820")
    assert row.payload == {"sigunguId": None, "snapshot": None} and row.user_id is None


def test_dong_view_ten_digits_ok(client, data):  # noqa: F811
    body = view() | {"kind": "dong_view", "target": "5182025300"}
    assert post(client, body).status_code == 204
    assert UserEvent.objects.get().target == "5182025300"


def test_search_target_must_be_null(client, data):  # noqa: F811
    body = {"anonId": AID, "kind": "search", "target": "51820", "payload": {"q": "바닷가"}}
    assert post(client, body).status_code == 400
    assert UserEvent.objects.count() == 0


def test_search_saved_with_null_target(client, data):  # noqa: F811
    body = {"anonId": AID, "kind": "search", "target": None, "payload": {"q": "바닷가"}}
    assert post(client, body).status_code == 204
    row = UserEvent.objects.get()
    assert (row.kind, row.target, row.payload) == ("search", None, {"q": "바닷가"})


def test_unknown_kind_rejected(client, data):  # noqa: F811
    assert post(client, view() | {"kind": "click"}).status_code == 400
    assert post(client, view() | {"kind": ["region_view"]}).status_code == 400
    assert UserEvent.objects.count() == 0


def test_bad_anon_id_rejected(client, data):  # noqa: F811
    assert post(client, view(anon_id="abc")).status_code == 400
    assert post(client, view(anon_id=AID.replace("-", ""))).status_code == 400
    assert post(client, view(anon_id="9b2f6c1e-3d4a-1c5b-8e7f-0a1b2c3d4e5f")).status_code == 400
    assert UserEvent.objects.count() == 0


def test_region_target_must_be_five_digits(client, data):  # noqa: F811
    assert post(client, view(target="5182")).status_code == 400
    assert post(client, view(target="5182a")).status_code == 400
    assert post(client, view(target="５１８２０")).status_code == 400  # 전각 숫자
    assert post(client, view(target=51820)).status_code == 400
    assert UserEvent.objects.count() == 0


def test_payload_too_large_rejected(client, data):  # noqa: F811
    body = {"anonId": AID, "kind": "search", "target": None, "payload": {"q": "가" * 1000}}
    assert post(client, body).status_code == 400
    assert UserEvent.objects.count() == 0


def test_payload_size_limit_is_inclusive(client, data):  # noqa: F811
    # {"q":"…"} 는 따옴표·중괄호 8바이트 + 본문. 본문 2040바이트면 정확히 2048
    ok = {"anonId": AID, "kind": "search", "target": None, "payload": {"q": "a" * 2040}}
    assert post(client, ok).status_code == 204
    over = ok | {"payload": {"q": "a" * 2041}}
    assert post(client, over).status_code == 400


def test_payload_nul_rejected(client, data):  # noqa: F811
    body = {"anonId": AID, "kind": "search", "target": None, "payload": {"q": "a\u0000b"}}
    assert post(client, body).status_code == 400
    assert UserEvent.objects.count() == 0


def test_payload_must_be_object_or_null(client, data):  # noqa: F811
    assert post(client, view() | {"payload": [1, 2]}).status_code == 400
    assert post(client, view() | {"payload": None}).status_code == 204
    assert UserEvent.objects.get().payload is None


def test_popular_view_count(client, data):  # noqa: F811
    for aid in (AID, AID, AID_B, AID_C):
        assert post(client, view(anon_id=aid)).status_code == 204
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE user_event SET created_at = now() - interval '31 days' WHERE anon_id = %s",
            [AID_C],
        )
    items = {it["id"]: it for it in client.get("/api/regions/popular").json()["items"]}
    assert items["51820"]["viewCount"] == 2
    assert all(it["viewCount"] == 0 for k, it in items.items() if k != "51820")


def test_events_throttled(client, data, monkeypatch):  # noqa: F811
    # DRF 는 한도 표를 클래스 로드 때 묶어 두므로 settings 대신 클래스 속성 rate 를 둔다
    # (SimpleRateThrottle 은 rate 가 있으면 scope 한도를 읽지 않는다)
    monkeypatch.setattr(EventsBurstThrottle, "rate", "2/min", raising=False)
    codes = [post(client, view()).status_code for _ in range(3)]
    assert codes == [204, 204, 429]
