"""자주 읽는 시군구 · 행정동 목록 (정적 데이터 캐시, core/cache.py 의 static)."""

from core.cache import static
from regions.models import Dong, Region


def regions_by_id() -> dict[int, Region]:
    """시군구 12곳 {region_id: Region}."""
    return static("regions", lambda: {r.region_id: r for r in Region.objects.all()})


def active_dongs() -> list[Dong]:
    """화면에 나오는 행정동 (집계 제외 · 코드 없는 곳 빼고) dong_id 순."""
    return static(
        "active_dongs",
        lambda: list(
            Dong.objects.filter(is_excluded=False, adm_code__isnull=False).order_by("dong_id")
        ),
    )
