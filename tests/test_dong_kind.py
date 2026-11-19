"""같은 동의 kind(living · tourist)는 어느 응답에서나 같다 (#32).
연간 값과 그 달 값이 0.7 경계를 사이에 두고 갈리는 동으로 확인한다."""

import pytest

from metrics.models import Metric
from regions.models import Dong
from tests.supabase_tokens import make_token, supabase  # noqa: F401
from tests.test_api import data  # noqa: F401 (픽스처 재사용)

pytestmark = pytest.mark.django_db

# (dong_id, adm_code, 이름, 그 달 local_life, 연간 local_life, 기대 kind)
CASES = [
    (301, "5115031000", "연곡면", 0.6, 0.75, "living"),  # 달 값은 관광지, 연간은 생활권
    (302, "5115032000", "연동", 0.8, 0.65, "tourist"),  # 달 값은 생활권, 연간은 관광지
    (303, "5115033000", "경계동", 0.5, 0.7, "living"),  # 연간이 딱 0.7
    (304, "5115034000", "값없는동", 0.9, None, "tourist"),  # 연간 값이 없으면 관광지
]


@pytest.fixture
def boundary(data):  # noqa: F811
    region = Dong.objects.get(dong_id=201).region
    for dong_id, code, name, month_val, annual_val, _ in CASES:
        Dong.objects.create(dong_id=dong_id, region=region, dong_name=name, adm_code=code, tags=[])
        for month, val in ((10, month_val), (0, annual_val)):
            if val is not None:
                Metric.objects.create(
                    scope="dong", unit_id=dong_id, month=month, metric_def_id="local_life",
                    raw_value=val,
                )  # fmt: skip


def test_region_and_dong_detail_agree(client, boundary):
    region = client.get("/api/regions/51150?month=2025-10").json()
    kinds = {d["id"]: d["kind"] for d in region["dongs"]}
    for _, code, name, *_, expected in CASES:
        assert kinds[code] == expected, name
        assert client.get(f"/api/dongs/{code}?month=2025-10").json()["kind"] == expected, name
        # 구 계약(/api/v1)의 lifeType 도 같은 판정
        v1 = client.get(f"/api/v1/dongs/{code}?month=10").json()
        assert v1["lifeType"] == ("local" if expected == "living" else "tourist"), name


def test_favorites_agree(client, boundary, supabase):  # noqa: F811
    from tests.test_me_api import auth

    assert client.get("/api/me", **auth()).status_code == 200
    for _, code, *_ in CASES:
        assert client.put(f"/api/me/favorites/dongs/{code}", **auth()).status_code == 204
    items = client.get("/api/me/favorites?month=2025-10", **auth()).json()["dongs"]
    kinds = {d["id"]: d["kind"] for d in items}
    assert kinds == {code: expected for _, code, *_, expected in CASES}
