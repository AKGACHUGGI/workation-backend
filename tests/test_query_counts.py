"""화면 API 의 DB 왕복 수 상한. DB 가 멀면(도쿄 리전 기준 왕복 약 44ms) 쿼리 수가 곧 응답 시간이라
목록 · 상세 화면이 단위마다 쿼리를 다시 부르는 회귀(N+1)를 막는다.
테스트 DB 에는 PostGIS · pgvector 가 없어 bbox · 비슷한 동네 쿼리는 실패하지만
왕복 수에는 들어간다."""

import pytest

from tests.test_api import data, photos  # noqa: F401 (픽스처 재사용)

# 테스트 DB 에서 bbox · 비슷한 동네는 실패해 캐시되지 않고 세이브포인트 왕복(3번씩)이 붙는다.
# 실제 DB 에서는 캐시돼 동네 프로필이 4~7번이다
CASES = [
    ("/api/regions/51820?month=2025-10", 4),
    ("/api/dongs/5182025300?month=2025-10", 12),
    ("/api/tier?month=2025-10&scope=dong", 2),
    ("/api/regions/popular?month=2025-10", 5),
    ("/api/v1/tiers?metric=value&month=10", 1),
    ("/api/v1/regions/51820?month=10", 2),
    ("/api/v1/dongs/5182025300?month=10", 8),
]


@pytest.mark.parametrize(("url", "limit"), CASES)
def test_query_count(client, photos, django_assert_max_num_queries, url, limit):  # noqa: F811
    """정적 데이터 캐시가 찬 뒤(두 번째 요청)의 왕복 수."""
    assert client.get(url).status_code == 200
    with django_assert_max_num_queries(limit):
        assert client.get(url).status_code == 200
