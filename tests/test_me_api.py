"""마이페이지 · 즐겨찾기 · 노트 읽기 (/api/me/* · /api/{regions|dongs}/{id}/notes).

계약: workation-web src/features/me/types.ts · src/features/region/types.ts (Note).
"""

import pytest
from django.db import connection

from tests.supabase_tokens import USER_ID, make_token, supabase  # noqa: F401
from tests.test_api import data, photos  # noqa: F401 (픽스처 재사용)
from webapi import community

pytestmark = pytest.mark.django_db

OTHER_ID = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
GOSEONG, GANGNEUNG = "51820", "51150"
GEOJIN, GYEONGPO = "5182025300", "5115060500"  # 거진읍(고성군) · 경포동(강릉시)


def auth(token=None):
    return {"HTTP_AUTHORIZATION": f"Bearer {token or make_token()}"}


def run(sql, params=None):
    with connection.cursor() as cur:
        cur.execute(sql, params or [])
        return cur.fetchall() if cur.description else None


def add_user(user_id, nickname):
    run(
        "INSERT INTO app_user (user_id, provider, provider_uid, nickname)"
        " VALUES (%s, 'kakao', %s, %s)",
        [user_id, user_id, nickname],
    )


def add_tip(user_id, region_id, dong_id=None, body="팁", minutes_ago=0, **cols):
    """팁 한 줄을 넣고 tip_id 를 돌려준다. minutes_ago 가 클수록 오래된 팁."""
    cols = {"visit_month": None, "stay_days": None, "status": "public"} | cols
    return run(
        "INSERT INTO tip (user_id, region_id, dong_id, body, visit_month, stay_days, status,"
        " created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, now() - make_interval(mins => %s))"
        " RETURNING tip_id",
        [
            user_id,
            region_id,
            dong_id,
            body,
            cols["visit_month"],
            cols["stay_days"],
            cols["status"],
            minutes_ago,
        ],
    )[0][0]


def add_helpful(tip_id, *user_ids):
    for user_id in user_ids:
        run("INSERT INTO tip_reaction (tip_id, user_id) VALUES (%s, %s)", [tip_id, user_id])


@pytest.fixture
def me(client, supabase, data):  # noqa: F811
    """로그인한 사용자(두이)와 다른 사용자(바다)."""
    assert client.get("/api/me", **auth()).status_code == 200
    add_user(OTHER_ID, "바다개발자")
    return USER_ID


# ───────────────────────── 로그인 ─────────────────────────

ME_PATHS = [
    ("get", "/api/me/favorites/ids"),
    ("get", "/api/me/favorites?month=2025-10"),
    ("put", f"/api/me/favorites/regions/{GOSEONG}"),
    ("delete", f"/api/me/favorites/regions/{GOSEONG}"),
    ("get", "/api/me/summary"),
    ("get", "/api/me/tips"),
    ("delete", "/api/me/tips/1"),
    ("get", "/api/me/photos"),
]


@pytest.mark.parametrize(("method", "path"), ME_PATHS)
def test_me_paths_need_login(client, supabase, data, method, path):  # noqa: F811
    """토큰이 없거나, 토큰은 맞아도 app_user 가 아직 없으면(첫 GET /api/me 전) 401."""
    for headers in ({}, auth()):
        r = getattr(client, method)(path, **headers)
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "UNAUTHORIZED"


# ───────────────────────── 즐겨찾기 ─────────────────────────


def put_favorite(client, kind, item_id):
    return client.put(f"/api/me/favorites/{kind}/{item_id}", **auth())


def save_in_order(client, *items):
    """(kind, id) 를 차례로 즐겨찾기하고 저장 시각을 1분씩 벌린다.
    테스트는 한 트랜잭션이라 now() 가 모두 같아서, 순서를 보려면 시각을 직접 넣어야 한다."""
    for i, (kind, item_id) in enumerate(items):
        assert put_favorite(client, kind, item_id).status_code == 204
        run(
            "UPDATE favorite SET created_at = now() - make_interval(mins => %s)"
            " WHERE scope = %s AND unit_id = %s",
            [
                len(items) - i,
                community.KIND_SCOPE[kind],
                community.unit_id(community.KIND_SCOPE[kind], item_id),
            ],
        )


def test_favorites_put_ids_delete(client, me):
    assert client.get("/api/me/favorites/ids", **auth()).json() == {"regions": [], "dongs": []}
    r = put_favorite(client, "regions", GANGNEUNG)
    assert r.status_code == 204 and r.content == b""
    save_in_order(client, ("regions", GANGNEUNG), ("regions", GOSEONG), ("dongs", GEOJIN))
    assert put_favorite(client, "regions", GOSEONG).status_code == 204  # 이미 있어도 204
    assert run("SELECT count(*) FROM favorite")[0][0] == 3
    # 저장한 순서
    assert client.get("/api/me/favorites/ids", **auth()).json() == {
        "regions": [GANGNEUNG, GOSEONG],
        "dongs": [GEOJIN],
    }
    for _ in range(2):  # 없어도 204
        assert client.delete(f"/api/me/favorites/regions/{GOSEONG}", **auth()).status_code == 204
    assert client.get("/api/me/favorites/ids", **auth()).json() == {
        "regions": [GANGNEUNG],
        "dongs": [GEOJIN],
    }


def test_favorites_are_per_user(client, me):
    run("INSERT INTO favorite (user_id, scope, unit_id) VALUES (%s, 'region', 1)", [OTHER_ID])
    assert client.get("/api/me/favorites/ids", **auth()).json()["regions"] == []
    client.delete(f"/api/me/favorites/regions/{GOSEONG}", **auth())
    assert run("SELECT count(*) FROM favorite WHERE user_id = %s", [OTHER_ID])[0][0] == 1


def test_favorite_unknown_target_404(client, me):
    for kind, item_id in (("regions", "99999"), ("dongs", "9999999999"), ("towns", GOSEONG)):
        r = put_favorite(client, kind, item_id)
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert client.delete("/api/me/favorites/regions/99999", **auth()).status_code == 204
    assert client.delete(f"/api/me/favorites/towns/{GOSEONG}", **auth()).status_code == 404
    assert run("SELECT count(*) FROM favorite")[0][0] == 0


def test_favorite_limit_409_per_kind(client, me, monkeypatch):
    monkeypatch.setattr(community, "FAVORITE_LIMIT", 1)
    assert put_favorite(client, "regions", GOSEONG).status_code == 204
    r = put_favorite(client, "regions", GANGNEUNG)
    assert r.status_code == 409 and r.json()["error"]["code"] == "FAVORITE_LIMIT"
    assert put_favorite(client, "regions", GOSEONG).status_code == 204  # 이미 있는 것은 그대로
    assert put_favorite(client, "dongs", GEOJIN).status_code == 204  # 동네는 따로 센다
    assert client.get("/api/me/favorites/ids", **auth()).json() == {
        "regions": [GOSEONG],
        "dongs": [GEOJIN],
    }


def test_favorites_list_contract(client, me, photos):  # noqa: F811
    save_in_order(client, ("regions", GOSEONG), ("regions", GANGNEUNG), ("dongs", GEOJIN))
    r = client.get("/api/me/favorites?month=2025-10", **auth())
    assert r.status_code == 200
    body = r.json()
    assert body["month"] == "2025-10"
    assert [x["id"] for x in body["regions"]] == [GANGNEUNG, GOSEONG]  # 최근에 저장한 것부터
    region = body["regions"][1]
    assert set(region) == {"id", "name", "province", "photoUrl", "scores", "tags", "addedAt"}
    assert region["name"] == "고성군" and region["province"] == "강원특별자치도"
    assert region["photoUrl"].startswith("https://r2.example/")
    assert region["tags"] == ["해안", "해수욕장"]
    assert region["addedAt"].endswith("+09:00")
    dong = body["dongs"][0]
    assert set(dong) == {"id", "name", "kind", "sigungu", "scores", "addedAt"}
    assert dong["name"] == "거진읍" and dong["kind"] == "living"
    assert dong["sigungu"] == {"id": GOSEONG, "name": "고성군"}
    # scores 는 /api/tier 의 같은 항목과 같다 (동의 crowd · overnight 는 시군구 값)
    tier = lambda scope: client.get(f"/api/tier?month=2025-10&scope={scope}").json()["items"]  # noqa: E731
    assert region["scores"] == next(i for i in tier("sigungu") if i["id"] == GOSEONG)["scores"]
    assert dong["scores"] == next(i for i in tier("dong") if i["id"] == GEOJIN)["scores"]
    assert dong["scores"]["crowd"] == 90 and dong["scores"]["quiet"] == 95


def test_favorites_list_month(client, me):
    assert client.get("/api/me/favorites", **auth()).json() == {
        "month": "annual",
        "regions": [],
        "dongs": [],
    }
    assert client.get("/api/me/favorites?month=10", **auth()).status_code == 400


def popular_favorite_counts(client):
    items = client.get("/api/regions/popular").json()["items"]
    return {it["id"]: it["favoriteCount"] for it in items}


def test_popular_favorite_count(client, me):
    assert set(popular_favorite_counts(client).values()) == {0}
    put_favorite(client, "regions", GOSEONG)
    put_favorite(client, "regions", GANGNEUNG)
    run(  # 두 번째 사용자도 고성을 저장
        "INSERT INTO favorite (user_id, scope, unit_id) VALUES (%s, 'region', %s)",
        [OTHER_ID, community.unit_id("region", GOSEONG)],
    )
    put_favorite(client, "dongs", GEOJIN)  # 동 즐겨찾기는 세지 않는다
    counts = popular_favorite_counts(client)
    assert counts[GOSEONG] == 2
    assert counts[GANGNEUNG] == 1
    assert sum(counts.values()) == 3  # 즐겨찾기 없는 지역은 0
    assert client.delete(f"/api/me/favorites/regions/{GOSEONG}", **auth()).status_code == 204
    assert popular_favorite_counts(client)[GOSEONG] == 1


# ───────────────────────── 노트 읽기 ─────────────────────────


def test_region_notes_contract(client, me):
    first = add_tip(
        me,
        1,
        101,
        "거진항 카페는 평일 오전이 조용해요",
        minutes_ago=30,
        visit_month=10,
        stay_days=14,
    )
    second = add_tip(OTHER_ID, 1, None, "속초에서 버스로 40분", minutes_ago=10)
    add_tip(OTHER_ID, 1, None, "숨긴 팁", status="hidden")
    add_tip(OTHER_ID, 2, 201, "강릉 팁")
    add_helpful(first, me, OTHER_ID)
    run("INSERT INTO tip_reply (tip_id, user_id, body) VALUES (%s, %s, '답글')", [first, OTHER_ID])
    run(
        "INSERT INTO tip_reply (tip_id, user_id, body, status) VALUES (%s, %s, '숨김', 'hidden')",
        [first, OTHER_ID],
    )

    body = client.get(f"/api/regions/{GOSEONG}/notes?sort=helpful").json()  # 로그인 없이 읽는다
    assert body["total"] == 2 and "nextCursor" not in body
    assert [n["id"] for n in body["items"]] == [str(first), str(second)]
    note = body["items"][0]
    assert note == {
        "id": str(first),
        "author": {"nickname": "두이", "initial": "두"},
        "createdAt": note["createdAt"],
        "dongName": "거진읍",
        "stayLabel": "2주 체류",
        "visitMonth": "2025-10",
        "body": "거진항 카페는 평일 오전이 조용해요",
        "helpful": 2,
        "replies": 1,
        "photos": [],
    }
    assert note["createdAt"].endswith("+09:00")
    # 없는 값은 키를 뺀다 (웹 타입의 선택 필드)
    assert set(body["items"][1]) == {
        "id",
        "author",
        "createdAt",
        "body",
        "helpful",
        "replies",
        "photos",
    }

    recent = client.get(f"/api/regions/{GOSEONG}/notes?sort=recent").json()
    assert [n["id"] for n in recent["items"]] == [str(second), str(first)]
    default = client.get(f"/api/regions/{GOSEONG}/notes").json()
    assert [n["id"] for n in default["items"]] == [str(first), str(second)]


def test_dong_notes_only_that_dong(client, me):
    mine = add_tip(me, 1, 101, "거진읍 팁")
    add_tip(me, 1, None, "고성군 팁")
    body = client.get(f"/api/dongs/{GEOJIN}/notes?sort=helpful").json()
    assert body["total"] == 1 and [n["id"] for n in body["items"]] == [str(mine)]
    assert client.get(f"/api/dongs/{GYEONGPO}/notes").json() == {"total": 0, "items": []}


def test_notes_cursor(client, me, monkeypatch):
    monkeypatch.setattr(community, "PAGE_SIZE", 2)
    ids = [str(add_tip(me, 1, body=f"팁 {i}", minutes_ago=i)) for i in range(5)]  # 새 것부터
    url = f"/api/regions/{GOSEONG}/notes?sort=recent"
    seen, cursor = [], None
    for _ in range(3):
        body = client.get(url + (f"&cursor={cursor}" if cursor else "")).json()
        assert body["total"] == 5
        seen += [n["id"] for n in body["items"]]
        cursor = body.get("nextCursor")
    assert seen == ids and cursor is None
    # 끝을 지난 커서도 total 은 맞다
    assert client.get(url + "&cursor=10").json() == {"total": 5, "items": []}


def test_notes_bad_params(client, me):
    assert client.get(f"/api/regions/{GOSEONG}/notes?sort=old").status_code == 400
    assert client.get(f"/api/regions/{GOSEONG}/notes?cursor=abc").status_code == 400
    assert client.get("/api/regions/99999/notes").status_code == 404
    assert client.get("/api/dongs/9999999999/notes").status_code == 404


@pytest.mark.parametrize(
    ("days", "label"),
    [
        (None, None),
        (0, None),
        (3, "3일 체류"),
        (7, "1주 체류"),
        (14, "2주 체류"),
        (27, "3주 체류"),
        (30, "한 달 체류"),
        (60, "2개월 체류"),
        (95, "3개월 체류"),
    ],
)
def test_stay_label(days, label):
    assert community.stay_label(days) == label


# ───────────────────────── MY ─────────────────────────


def test_summary_contract(client, me, photos):  # noqa: F811
    r = client.get("/api/me/summary", **auth())
    assert r.status_code == 200
    body = r.json()
    assert body.pop("createdAt").endswith("+09:00")
    assert body == {"stays": [], "tipCount": 0, "photoCount": 0, "favoriteCount": 0, "voteCount": 0}

    add_tip(me, 1, 101)
    add_tip(me, 2, 201)
    add_tip(me, 2)
    add_tip(me, 2, status="hidden")  # 숨긴 팁 · 남의 팁은 세지 않는다
    add_tip(OTHER_ID, 1)
    for status in ("approved", "pending", "hidden"):
        run(
            "INSERT INTO user_photo (scope, unit_id, url, user_id, status)"
            " VALUES ('region', 1, 'https://r2.example/mine.jpg', %s, %s)",
            [me, status],
        )
    put_favorite(client, "regions", GOSEONG)
    put_favorite(client, "dongs", GEOJIN)
    tip_id = add_tip(me, 1, status="hidden")  # 숨긴 팁이라 stays · tipCount 는 그대로
    run(  # 노트 사진은 이달의 사진 수에 넣지 않는다
        "INSERT INTO user_photo (scope, unit_id, url, user_id, status, tip_id)"
        " VALUES ('region', 1, 'https://r2.example/note.jpg', %s, 'approved', %s)",
        [me, tip_id],
    )
    run(
        "INSERT INTO poll_vote (user_id, scope, unit_id, month, preset, choice)"
        " VALUES (%s, 'region', 1, 10, 'quiet', true)",
        [me],
    )
    body = client.get("/api/me/summary", **auth()).json()
    assert body["stays"] == [
        {"regionName": "강릉", "count": 2},
        {"regionName": "고성", "count": 1},
    ]
    assert (body["tipCount"], body["photoCount"]) == (3, 2)
    assert (body["favoriteCount"], body["voteCount"]) == (2, 1)


def test_my_tips_contract(client, me):
    dong_tip = add_tip(me, 1, 101, "거진읍 팁", minutes_ago=5, visit_month=10, stay_days=30)
    region_tip = add_tip(me, 2, None, "강릉 팁", minutes_ago=1)
    add_tip(OTHER_ID, 1, 101, "남의 팁")
    add_helpful(dong_tip, me, OTHER_ID)
    add_helpful(region_tip, OTHER_ID)
    body = client.get("/api/me/tips", **auth()).json()
    assert body["helpfulSum"] == 3 and "nextCursor" not in body
    assert [t["id"] for t in body["items"]] == [str(region_tip), str(dong_tip)]  # 최근 것부터
    assert body["items"][0]["target"] == {"kind": "region", "id": GANGNEUNG, "name": "강릉시"}
    tip = body["items"][1]
    assert tip["target"] == {"kind": "dong", "id": GEOJIN, "name": "거진읍"}
    assert tip["author"] == {"nickname": "두이", "initial": "두"}
    assert (tip["dongName"], tip["stayLabel"], tip["visitMonth"]) == (
        "거진읍",
        "한 달 체류",
        "2025-10",
    )
    assert (tip["helpful"], tip["replies"], tip["photos"]) == (2, 0, [])


def test_my_tips_cursor_keeps_helpful_sum(client, me, monkeypatch):
    monkeypatch.setattr(community, "PAGE_SIZE", 2)
    ids = [add_tip(me, 1, body=f"팁 {i}", minutes_ago=i) for i in range(3)]
    for tip_id in ids:
        add_helpful(tip_id, OTHER_ID)
    first = client.get("/api/me/tips", **auth()).json()
    assert len(first["items"]) == 2 and first["helpfulSum"] == 3  # 합계는 전체 팁 기준
    second = client.get(f"/api/me/tips?cursor={first['nextCursor']}", **auth()).json()
    assert [t["id"] for t in second["items"]] == [str(ids[2])]
    assert second["helpfulSum"] == 3 and "nextCursor" not in second
    assert client.get("/api/me/tips?cursor=-1", **auth()).status_code == 400


def test_delete_my_tip(client, me):
    mine = add_tip(me, 1, 101)
    others = add_tip(OTHER_ID, 1, 101)
    add_helpful(mine, OTHER_ID)
    for tip_id in (others, 999999, "abc", "99999999999999999999"):  # 남의 팁 · 없는 팁
        r = client.delete(f"/api/me/tips/{tip_id}", **auth())
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = client.delete(f"/api/me/tips/{mine}", **auth())
    assert r.status_code == 204 and r.content == b""
    assert [row[0] for row in run("SELECT tip_id FROM tip")] == [others]
    assert run("SELECT count(*) FROM tip_reaction")[0][0] == 0  # 도움돼요도 함께 지워진다
    assert client.delete(f"/api/me/tips/{mine}", **auth()).status_code == 404


def test_my_photos_contract(client, me):
    def add_photo(scope, unit_id, month, status, user_id=me):
        return run(
            "INSERT INTO user_photo (scope, unit_id, month, url, user_id, status)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING user_photo_id",
            [scope, unit_id, month, f"https://r2.example/{scope}-{status}.jpg", user_id, status],
        )[0][0]

    approved = add_photo("region", 1, 10, "approved")
    pending = add_photo("dong", 201, None, "pending")
    add_photo("region", 1, 10, "hidden")
    add_photo("region", 1, 10, "approved", user_id=OTHER_ID)
    note = run(
        "INSERT INTO tip (user_id, region_id, body) VALUES (%s, 1, '팁') RETURNING tip_id", [me]
    )[0][0]
    run(
        "INSERT INTO user_photo (scope, unit_id, url, user_id, status, tip_id)"
        " VALUES ('region', 1, 'https://r2.example/note.jpg', %s, 'approved', %s)",
        [me, note],
    )
    body = client.get("/api/me/photos", **auth()).json()
    assert body == {
        "items": [  # 최근 것부터. 동네 사진의 regionName 은 소속 시군구
            {
                "id": str(pending),
                "url": "https://r2.example/dong-pending.jpg",
                "month": "annual",
                "regionName": "강릉시",
                "status": "pending",
            },
            {
                "id": str(approved),
                "url": "https://r2.example/region-approved.jpg",
                "month": "2025-10",
                "regionName": "고성군",
                "status": "approved",
            },
        ]
    }


# ───────────────────────── DB 왕복 수 ─────────────────────────

# 토큰 주인(app_user) 조회 1번 포함 (노트 읽기도 토큰이 있으면 mine 을 위해 조회한다).
# 정적 데이터 캐시가 찬 뒤 기준
QUERY_CASES = [
    ("/api/me/favorites/ids", 2),
    ("/api/me/favorites?month=2025-10", 3),
    ("/api/me/summary", 2),
    ("/api/me/tips", 2),
    ("/api/me/photos", 2),
    (f"/api/regions/{GOSEONG}/notes?sort=helpful", 2),
    (f"/api/dongs/{GEOJIN}/notes?sort=helpful", 3),
]


@pytest.mark.parametrize(("url", "limit"), QUERY_CASES)
def test_query_count(client, me, photos, django_assert_max_num_queries, url, limit):  # noqa: F811
    """목록이 길어져도 왕복 수는 그대로다 (항목마다 다시 묻지 않는다)."""
    for i in range(3):
        add_tip(me, 1, 101, f"팁 {i}")
    put_favorite(client, "regions", GOSEONG)
    put_favorite(client, "regions", GANGNEUNG)
    put_favorite(client, "dongs", GEOJIN)
    put_favorite(client, "dongs", GYEONGPO)
    assert client.get(url, **auth()).status_code == 200
    with django_assert_max_num_queries(limit):
        assert client.get(url, **auth()).status_code == 200


def test_favorite_put_is_one_statement(client, me, django_assert_max_num_queries):
    put_favorite(client, "regions", GOSEONG)  # 시군구 목록 캐시를 채운다
    with django_assert_max_num_queries(2):  # app_user 조회 + 세기 · 넣기 한 문장
        assert put_favorite(client, "regions", GANGNEUNG).status_code == 204


# ───────────────────────── 팁 쓰기 ─────────────────────────


def post_tip(client, kind, item_id, payload, token=None):
    path = f"/api/{kind}/{item_id}/notes"
    return client.post(path, payload, content_type="application/json", **auth(token))


def test_post_region_tip_contract(client, me):
    r = post_tip(
        client, "regions", GOSEONG, {"body": "  속초에서 버스로 40분  ", "visitMonth": "2025-10"}
    )
    assert r.status_code == 201
    note = r.json()
    assert note == {
        "id": note["id"],
        "author": {"nickname": "두이", "initial": "두"},
        "createdAt": note["createdAt"],
        "visitMonth": "2025-10",
        "body": "속초에서 버스로 40분",
        "helpful": 0,
        "replies": 0,
        "photos": [],
        "mine": True,
    }
    assert note["createdAt"].endswith("+09:00")
    row = run("SELECT user_id, region_id, dong_id, visit_month, stay_days, body, status FROM tip")
    assert [tuple(map(str, r)) for r in row] == [
        (me, "1", "None", "10", "None", "속초에서 버스로 40분", "public")
    ]
    assert str(row[0][0]) == str(me)


def test_post_dong_tip_stores_region_and_dong(client, me):
    r = post_tip(client, "dongs", GEOJIN, {"body": "거진항 카페"})
    assert r.status_code == 201
    note = r.json()
    assert note["dongName"] == "거진읍" and "visitMonth" not in note
    assert run(
        "SELECT region_id, dong_id, visit_month FROM tip WHERE tip_id = %s", [note["id"]]
    ) == [(1, 101, None)]
    # 시군구 읽기에도 보인다 (dongName 으로 구분)
    region = client.get(f"/api/regions/{GOSEONG}/notes").json()
    assert [n["id"] for n in region["items"]] == [note["id"]]


def test_post_tip_needs_login(client, supabase, data):  # noqa: F811
    for headers in ({}, auth()):  # 토큰 없음 · app_user 아직 없음
        r = client.post(
            f"/api/regions/{GOSEONG}/notes",
            {"body": "팁"},
            content_type="application/json",
            **headers,
        )
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    assert run("SELECT count(*) FROM tip") == [(0,)]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"body": ""},
        {"body": "   \n "},
        {"body": 123},
        {"body": None},
        {"body": "가" * 301},
        {"body": "  " + "가" * 301 + "  "},
        {"body": "팁", "visitMonth": "2025-13"},
        {"body": "팁", "visitMonth": "2025-00"},
        {"body": "팁", "visitMonth": "2025-1"},
        {"body": "팁", "visitMonth": "annual"},
        {"body": "팁", "visitMonth": "2024-10"},
        {"body": "팁", "visitMonth": 10},
        ["팁"],
    ],
)
def test_post_tip_bad_body_400(client, me, payload):
    r = post_tip(client, "regions", GOSEONG, payload)
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_REQUEST"
    assert run("SELECT count(*) FROM tip") == [(0,)]


def test_post_tip_300_chars_ok(client, me):
    body = "가" * 300
    r = post_tip(client, "regions", GOSEONG, {"body": f" {body} "})
    assert r.status_code == 201 and r.json()["body"] == body


def test_post_tip_unknown_or_excluded_404(client, me):
    from regions.models import Dong

    Dong.objects.create(
        dong_id=103,
        region_id=1,
        dong_name="제외동",
        adm_code="5182025400",
        is_excluded=True,
        tags=[],
    )
    for kind, item_id in [
        ("regions", "99999"),
        ("dongs", "0000000000"),
        ("dongs", "5182025400"),
    ]:
        r = post_tip(client, kind, item_id, {"body": "팁"})
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert run("SELECT count(*) FROM tip") == [(0,)]


def test_post_tip_burst_429(client, me):
    for i in range(community.TIP_BURST_LIMIT):
        assert post_tip(client, "regions", GOSEONG, {"body": f"팁 {i}"}).status_code == 201
    r = post_tip(client, "dongs", GEOJIN, {"body": "넷째"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "TIP_RATE_LIMIT"
    assert run("SELECT count(*) FROM tip") == [(community.TIP_BURST_LIMIT,)]


def test_post_tip_burst_counts_only_last_minute(client, me):
    for i in range(3):
        add_tip(me, 1, None, f"오래된 팁 {i}", minutes_ago=2)
    assert post_tip(client, "regions", GOSEONG, {"body": "새 팁"}).status_code == 201


def test_posted_tip_shows_up_in_reads(client, me):
    add_tip(OTHER_ID, 1, None, "먼저 쓴 팁", minutes_ago=10)
    tip_id = post_tip(client, "regions", GOSEONG, {"body": "방금 쓴 팁"}).json()["id"]
    notes = client.get(f"/api/regions/{GOSEONG}/notes?sort=recent").json()
    assert notes["total"] == 2 and notes["items"][0]["id"] == tip_id
    mine = client.get("/api/me/tips", **auth()).json()
    assert [n["id"] for n in mine["items"]] == [tip_id]
    assert mine["items"][0]["target"] == {"kind": "region", "id": GOSEONG, "name": "고성군"}
    summary = client.get("/api/me/summary", **auth()).json()
    assert summary["tipCount"] == 1 and summary["stays"] == [{"regionName": "고성", "count": 1}]


def test_post_tip_query_count(client, me, django_assert_max_num_queries):
    post_tip(client, "regions", GOSEONG, {"body": "시군구 목록 캐시를 채운다"})
    with django_assert_max_num_queries(2):  # app_user 조회 + 세기 · 넣기 한 문장
        assert post_tip(client, "regions", GOSEONG, {"body": "둘째"}).status_code == 201
    with django_assert_max_num_queries(3):  # + 행정동 조회
        assert post_tip(client, "dongs", GEOJIN, {"body": "셋째"}).status_code == 201
