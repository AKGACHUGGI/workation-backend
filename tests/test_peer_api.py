"""또래 밀도 연령대 개인화 API.
보는 사람의 연령대로 scores.peer · peerShare · peerOffpeakMonths 가 바뀐다.
설계: docs/superpowers/specs/2026-11-23-peer-personalization-design.md 5-2, 5-3절"""

import pytest

from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import auth, run
from tests.test_peer_personalization import peer_data  # noqa: F401

pytestmark = pytest.mark.django_db

TIER = "/api/tier?month=2025-10&scope=sigungu"
TIER_DONG = "/api/tier?month=2025-10&scope=dong"


@pytest.fixture
def viewer(client, supabase, peer_data):  # noqa: F811
    """로그인 사용자 1명을 만들고, set_age(age) 로 연령대를 바꾼다."""
    assert client.get("/api/me", **auth()).status_code == 200

    def set_age(age):
        run("UPDATE app_user SET age_group = %s", [age])

    return set_age


def peer(body, unit_id):
    return next(i for i in body["items"] if i["id"] == unit_id)["scores"]["peer"]


def test_tier_anonymous_default(client, peer_data):  # noqa: F811
    body = client.get(TIER).json()
    assert peer(body, "51820") == 40
    assert peer(body, "51150") == 70


def test_tier_30s_viewer(client, viewer):
    viewer("30s")
    body = client.get(TIER, **auth()).json()
    assert peer(body, "51820") == 10
    assert peer(body, "51150") == 90


def test_tier_viewer_without_age_default(client, viewer):
    body = client.get(TIER, **auth()).json()
    assert peer(body, "51820") == 40


def test_tier_bad_token_default(client, peer_data):  # noqa: F811
    r = client.get(TIER, HTTP_AUTHORIZATION="Bearer garbage")
    assert r.status_code == 200
    assert peer(r.json(), "51820") == 40


def test_tier_dong_scope_bucket(client, viewer):
    viewer("30s")
    body = client.get(TIER_DONG, **auth()).json()
    assert peer(body, "5182025300") == 15
    assert peer(body, "5115060500") == 85


def test_tier_missing_bucket_zero(client, viewer):
    viewer("20s")
    body = client.get(TIER, **auth()).json()
    assert peer(body, "51820") == 0


def test_tier_70s_maps_to_60plus(client, viewer):
    viewer("70s")
    body = client.get(TIER, **auth()).json()
    assert peer(body, "51820") == 95
    assert peer(body, "51150") == 5


def test_popular_personalized(client, viewer):
    viewer("30s")
    url = "/api/regions/popular?month=2025-10"
    assert peer(client.get(url, **auth()).json(), "51820") == 10
    assert peer(client.get(url).json(), "51820") == 40


def test_dong_detail_personalized(client, viewer):
    url = "/api/dongs/5182025300?month=2025-10"
    body = client.get(url).json()
    assert body["stats"]["peerShare"] == 40
    assert body["scores"]["peer"] == 40
    viewer("30s")
    body = client.get(url, **auth()).json()
    assert body["stats"]["peerShare"] == 5
    assert body["scores"]["peer"] == 15


def test_region_detail_peer_share(client, viewer):
    url = "/api/regions/51820?month=2025-10"
    assert client.get(url).json()["stats"]["peerShare"] == 30
    viewer("30s")
    assert client.get(url, **auth()).json()["stats"]["peerShare"] == 5


def test_region_detail_reads_bucket_monthly(client, viewer, monkeypatch):
    calls = []

    def fake_region_monthly(region_id, keys):
        calls.append(keys)
        return {
            keys[0]: {m: 50.0 for m in range(1, 13)},
            keys[1]: {m: (0.9 if m == 3 else 0.1) for m in range(1, 13)},
        }

    monkeypatch.setattr("webapi.data.region_monthly", fake_region_monthly)
    url = "/api/regions/51820?month=2025-10"
    viewer("30s")
    r = client.get(url, **auth())
    assert r.status_code == 200
    assert calls[-1] == ("ease", "peer_30s")
    assert 3 in r.json()["peerOffpeakMonths"]
    client.get(url)
    assert calls[-1] == ("ease", "peer")


def test_favorites_personalized(client, viewer):
    viewer("30s")
    client.put("/api/me/favorites/regions/51820", **auth())
    body = client.get("/api/me/favorites?month=2025-10", **auth()).json()
    assert body["regions"][0]["scores"]["peer"] == 10
