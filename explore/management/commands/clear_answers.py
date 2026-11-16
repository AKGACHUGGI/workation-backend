"""동네에 물어보기 저장 답 지우기. CLOVA 키를 나중에 넣었거나 프롬프트를 바꿨을 때 쓴다.

uv run python manage.py clear_answers [--source rule]
Postgres dong_answer 행과 Redis 의 답 캐시(explore:ask:a:*)를 지운다. 다음 질문에서 새로 만든다.
"""

from django.core.cache import cache
from django.core.management.base import BaseCommand

from explore import sources

ANSWER_KEYS = "explore:ask:a:*"


class Command(BaseCommand):
    help = "dong_answer 와 Redis 답 캐시를 지운다 (--source 를 줘도 캐시는 답 전부)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--source", choices=["llm", "mixed", "rule"], help="이 출처의 답만 지운다"
        )

    def handle(self, *args, **options):
        rows = sources.clear_answers(options["source"])
        delete_pattern = getattr(cache, "delete_pattern", None)
        if delete_pattern is None:
            self.stdout.write(
                self.style.WARNING(
                    "Redis 가 아니라서 서버 메모리의 답 캐시는 못 지움 (재시작하면 비워짐)"
                )
            )
            keys = 0
        else:
            keys = delete_pattern(ANSWER_KEYS)
        self.stdout.write(f"dong_answer {rows}행 · 답 캐시 {keys}개 지움")
