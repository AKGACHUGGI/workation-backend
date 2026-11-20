"""체감 투표 (스펙 4-5)."""

import pytest

from tests.supabase_tokens import supabase  # noqa: F401
from tests.test_api import data  # noqa: F401
from tests.test_me_api import GEOJIN, GOSEONG, OTHER_ID, auth, me, run  # noqa: F401
from webapi import poll

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    ("month", "name", "preset", "text"),
    [
        (10, "고성군", "quiet", "10월 고성군, 실제로 조용했나요?"),
        (3, "고성군", "lively", "3월 고성군, 실제로 활기 있었나요?"),
        (None, "고성군", "custom", "연간 고성군, 어땠나요?"),
        (10, "거진읍", "", "10월 거진읍, 어땠나요?"),
    ],
)
def test_question(month, name, preset, text):
    assert poll.question(month, name, preset) == text


def vote(client, kind, item_id, choice, month="2025-10", preset="quiet"):
    payload = {"month": month, "choice": choice}
    if preset is not None:
        payload["preset"] = preset
    return client.post(
        f"/api/{kind}/{item_id}/poll/vote", payload, content_type="application/json", **auth()
    )


def other_votes(scope, unit, month, preset, choice):
    run(
        "INSERT INTO poll_vote (user_id, scope, unit_id, month, preset, choice)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        [OTHER_ID, scope, unit, month, preset, choice],
    )


def test_get_anonymous(client, me):  # noqa: F811
    other_votes("region", 1, 10, "quiet", False)
    body = client.get(f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=quiet").json()
    assert body == {
        "question": "10월 고성군, 실제로 조용했나요?",
        "yes": 0,
        "no": 1,
        "total": 1,
        "photos": [],
        "myVote": None,
    }


def test_vote_change_cancel(client, me):  # noqa: F811
    other_votes("region", 1, 10, "quiet", True)
    r = vote(client, "regions", GOSEONG, "yes")
    assert r.status_code == 200
    assert (r.json()["yes"], r.json()["no"], r.json()["myVote"]) == (2, 0, "yes")
    r = vote(client, "regions", GOSEONG, "no")
    assert (r.json()["yes"], r.json()["no"], r.json()["myVote"]) == (1, 1, "no")
    got = client.get(f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=quiet", **auth()).json()
    assert got == r.json()
    r = vote(client, "regions", GOSEONG, None)
    assert (r.json()["total"], r.json()["myVote"]) == (1, None)
    assert vote(client, "regions", GOSEONG, None).status_code == 200  # 없어도 200


def test_votes_are_per_question(client, me):  # noqa: F811
    vote(client, "regions", GOSEONG, "yes", preset="quiet")
    lively = client.get(f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=lively").json()
    annual = client.get(f"/api/regions/{GOSEONG}/poll?month=annual&preset=quiet").json()
    assert lively["total"] == annual["total"] == 0


def test_dong_ignores_preset(client, me):  # noqa: F811
    r = vote(client, "dongs", GEOJIN, "yes", month="annual", preset="lively")
    assert r.status_code == 200 and r.json()["question"] == "연간 거진읍, 어땠나요?"
    assert run("SELECT scope, unit_id, month, preset FROM poll_vote") == [("dong", 101, 0, "")]
    assert client.get(f"/api/dongs/{GEOJIN}/poll?month=annual").json()["total"] == 1


def test_poll_photos(client, me):  # noqa: F811
    tip = run(
        "INSERT INTO tip (user_id, region_id, body) VALUES (%s, 1, '팁') RETURNING tip_id", [me]
    )[0][0]
    rows = [  # (month, source, user_id, credit, status, tip_id)
        (10, "user", me, None, "approved", None),
        (10, "tourapi", None, "한국관광공사 포토코리아 / 갑", "approved", None),
        (7, "user", me, None, "approved", None),  # 다른 달
        (10, "user", me, None, "pending", None),  # 가려짐
        (10, "user", me, None, "approved", tip),  # 노트 사진
    ]
    ids = []
    for i, (month, source, user, credit, status, tip_id) in enumerate(rows):
        ids.append(
            run(
                "INSERT INTO photo (scope, unit_id, month, url, source, user_id, credit, status,"
                " tip_id, created_at) VALUES ('region', 1, %s, %s, %s, %s, %s, %s, %s,"
                " now() - make_interval(mins => %s)) RETURNING photo_id",
                [month, f"https://r2.example/{i}.webp", source, user, credit, status, tip_id, i],
            )[0][0]
        )
    body = client.get(f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=quiet").json()
    assert body["photos"] == [  # 최신순, 업로더 닉네임 또는 출처
        {"id": str(ids[0]), "url": "https://r2.example/0.webp", "author": "두이"},
        {
            "id": str(ids[1]),
            "url": "https://r2.example/1.webp",
            "author": "한국관광공사 포토코리아 / 갑",
        },
    ]
    annual = client.get(f"/api/regions/{GOSEONG}/poll?month=annual&preset=quiet").json()
    assert len(annual["photos"]) == 3  # 연간은 달을 가리지 않는다


@pytest.mark.parametrize(
    "url",
    [
        f"/api/regions/{GOSEONG}/poll?month=2025-10",
        f"/api/regions/{GOSEONG}/poll?month=2025-10&preset=calm",
        f"/api/regions/{GOSEONG}/poll?month=10&preset=quiet",
    ],
)
def test_get_bad_400(client, me, url):  # noqa: F811
    assert client.get(url).status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"month": "2025-10", "preset": "quiet"},  # choice 없음
        {"month": "2025-10", "preset": "quiet", "choice": "maybe"},
        {"month": "2025-10", "choice": "yes"},  # region 은 preset 필수
        {"preset": "quiet", "choice": "yes"},  # month 없음
        {"month": "2025-13", "preset": "quiet", "choice": "yes"},
    ],
)
def test_vote_bad_400(client, me, payload):  # noqa: F811
    r = client.post(
        f"/api/regions/{GOSEONG}/poll/vote", payload, content_type="application/json", **auth()
    )
    assert r.status_code == 400
    assert run("SELECT count(*) FROM poll_vote") == [(0,)]


def test_poll_404_and_vote_401(client, me):  # noqa: F811
    assert client.get("/api/regions/99999/poll?month=2025-10&preset=quiet").status_code == 404
    assert client.get("/api/dongs/0000000000/poll?month=2025-10").status_code == 404
    r = client.post(
        f"/api/regions/{GOSEONG}/poll/vote",
        {"month": "2025-10", "preset": "quiet", "choice": "yes"},
        content_type="application/json",
    )
    assert r.status_code == 401
