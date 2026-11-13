"""탐색 v6 파생 표 만들기 (월 배치 ②). 지표 적재(03_metrics.sql) 다음에 실행한다.

uv run python manage.py build_explore
"""

from django.core.management.base import BaseCommand

from explore import derive, sources


class Command(BaseCommand):
    help = "dong_type_score · dong_hour_stat · dong_similar 를 지우고 다시 계산 (한 트랜잭션)"

    def handle(self, *args, **options):
        inp = sources.load_derive_input()
        types = derive.type_scores(inp.dong_metrics, inp.region_metrics, inp.morning, inp.region_of)
        year = sources.data_year()
        hours = {}
        for dong_id, days in sources.load_hourly(year).items():
            if dong_id not in inp.dong_metrics:
                continue
            hours[dong_id] = {
                day: (driv, *derive.hour_stat(driv, day))
                for day, driv in days.items()
                if any(v is not None for v in driv)
            }
        vecs = {
            d: derive.metric_vec(m["quiet"], m["peer"], m["evening"], m["local_life"])
            for d, m in inp.dong_metrics.items()
        }
        coastal = {d: derive.is_coastal(inp.coast_km.get(d)) for d in vecs}
        similar = derive.similar_dongs(vecs, coastal)
        sources.write_explore(types, hours, similar)
        n_hours = sum(len(days) for days in hours.values())
        n_similar = sum(len(rows) for rows in similar.values())
        self.stdout.write(
            f"{year}년 · 동 {len(types)} → dong_type_score {4 * len(types)}"
            f" · dong_hour_stat {n_hours} · dong_similar {n_similar}"
        )
