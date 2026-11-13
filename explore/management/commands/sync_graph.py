"""Postgres → Neo4j 탐색 그래프 동기화 (월 배치 ③). build_explore 다음에 실행한다.

uv run python manage.py sync_graph
"""

import uuid

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from neo4j.exceptions import DriverError, Neo4jError

from explore import graph, graph_write, graphdb, sources


class Command(BaseCommand):
    help = "Postgres 파생 표 → Neo4j 그래프 (MERGE 후 지난 동기화분 삭제, 개수 비교)"

    def handle(self, *args, **options):
        g = graph.build_graph(sources.load_graph_input())
        sync_id = uuid.uuid4().hex
        synced_at = timezone.localtime().isoformat(timespec="seconds")
        try:
            driver = graphdb.get_driver()
            graph_write.ensure_constraints(driver)
            graph_write.write_graph(driver, g, sync_id, synced_at)
            actual = graph_write.count_graph(driver)
        except (graphdb.GraphUnavailable, DriverError, Neo4jError) as ex:
            raise CommandError(f"Neo4j 쓰기 실패: {ex}") from ex
        expected = graph.expected_counts(g)
        self.stdout.write(f"동기화 {synced_at} (sync_id {sync_id})")
        for kind in ("nodes", "rels"):
            for key, n in sorted(actual[kind].items()):
                self.stdout.write(f"  {key} {n}")
        diffs = graph_write.compare_counts(expected, actual)
        if diffs:
            raise CommandError("Postgres 기준 개수와 다름:\n  " + "\n  ".join(diffs))
        self.stdout.write(self.style.SUCCESS("개수 일치"))
