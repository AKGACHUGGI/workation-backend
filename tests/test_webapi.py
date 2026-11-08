"""웹 화면 계약 API (/api/...). 계약: workation-web src/features/*/types.ts"""

from search import engine
from tests.test_api import data, photos  # noqa: F401 (픽스처 재사용)


def test_tier_sigungu_contract(client, data):  # noqa: F811
    body = client.get("/api/tier?month=2025-10&scope=sigungu").json()
    assert body["month"] == "2025-10" and body["scope"] == "sigungu"
    item = next(i for i in body["items"] if i["id"] == "51820")
    assert item["scores"] == {"quiet": 80, "crowd": 90, "peer": 40, "overnight": 82, "vibe": 30}
    assert item["province"] == "강원특별자치도"
    assert item["inherited"] == [] and item["terrain"] == ["coast"]


def test_tier_dong_inherits_region_metrics(client, data):  # noqa: F811
    body = client.get("/api/tier?month=2025-10&scope=dong").json()
    ids = [i["id"] for i in body["items"]]
    assert "5182025300" in ids and len(ids) == 2  # 수동면(집계 제외)은 빠짐
    item = next(i for i in body["items"] if i["id"] == "5182025300")
    assert item["sigungu"] == {"id": "51820", "name": "고성군"}
    assert item["scores"]["crowd"] == 90 and item["scores"]["quiet"] == 95
    assert item["inherited"] == ["crowd", "overnight"]


def test_month_param_rejected(client, data):  # noqa: F811
    assert client.get("/api/tier?month=10&scope=sigungu").status_code == 400


def test_popular_contract(client, data):  # noqa: F811
    body = client.get("/api/regions/popular?month=annual").json()
    assert body["month"] == "annual" and len(body["items"]) == 2
    assert {"favoriteCount", "viewCount", "tags", "terrain", "scores"} <= set(body["items"][0])


def test_region_detail_contract(client, photos):  # noqa: F811
    body = client.get("/api/regions/51820?month=2025-10").json()
    assert body["stats"]["crowdIndex"] == 67 and body["stats"]["overnightCoef"] == 0.91
    assert body["bestMonths"] == [2, 3] and body["avoidMonths"] == [7, 8]
    assert len(body["months"]) == 12 and body["seasonType"] in {
        "summer_beach",
        "spring_autumn",
        "second_half_rise",
        "flat",
    }
    assert body["excludedDongs"] == [{"name": "수동면", "reason": "special_facility"}]
    assert body["photos"][0]["url"].startswith("https://r2.example/")
    assert [d["id"] for d in body["dongs"]] == ["5182025300"]


def test_dong_detail_contract(client, photos):  # noqa: F811
    body = client.get("/api/dongs/5182025300?month=2025-10").json()
    assert body["sigungu"] == {"id": "51820", "name": "고성군"}
    assert len(body["hourly"]["weekday"]) == 24
    assert body["peaks"]["weekday"]["peakHour"] == 17
    assert sum(body["activityMix"].values()) == 100
    assert body["stats"]["localLife"] == 80
    assert body["quieterTwins"] == []  # 테스트 DB 에는 dong_feature 가 없다
    assert body["kind"] == "living"


def test_notes_poll_view_and_auth(client, data):  # noqa: F811
    assert client.get("/api/regions/51820/notes?sort=helpful").json() == {"total": 0, "items": []}
    assert client.get("/api/dongs/5182025300/poll?month=2025-10").json()["total"] == 0
    assert client.post("/api/regions/51820/view").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/me/favorites/ids").status_code == 401


def test_search_parse_maps_conditions(client, monkeypatch):
    cond = engine.Conditions(month=10, tags=["beach", "long_stay"], metrics=[("quiet", 1)])
    monkeypatch.setattr(engine, "understand", lambda q: cond)
    body = client.post(
        "/api/search/parse", {"text": "10월 바닷가 조용한 곳"}, content_type="application/json"
    ).json()
    assert body["month"] == "2025-10" and body["preset"] == "quiet"
    assert body["terrain"] == ["coast"] and body["stay"] == "month"
    assert [t["axis"] for t in body["tags"]] == ["month", "preset", "terrain", "stay"]
