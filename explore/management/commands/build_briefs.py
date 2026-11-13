"""동네 브리핑 3문장 생성 (월 배치 ④). sync_graph 다음에 실행한다.
끝에 60일 지난 동네에 물어보기 답(dong_answer)을 지운다 (그래프가 바뀌어 더는 안 쓰이는 행).

uv run python manage.py build_briefs [--force]
CLOVA_API_KEY 가 없으면 규칙 문장만 만든다(로컬 개발).
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from core import clova
from explore import brief, queries, sources
from explore.graphdb import GraphUnavailable

CHAT_TIMEOUT = 20  # 초 (스펙 §5-4)
RULE_WARN = 0.2


def chat(system: str, user: str) -> str:
    return clova.chat(system, user, max_tokens=400, temperature=0.3, timeout=CHAT_TIMEOUT)


class Command(BaseCommand):
    help = "Neo4j 사실 → HyperCLOVA 3문장 → 코드 검증 → dong_brief (사실이 같으면 건너뜀)"

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="사실이 같아도 다시 만든다")

    def handle(self, *args, **options):
        use_chat = chat if clova.enabled() else None
        if use_chat is None:
            self.stdout.write(self.style.WARNING("CLOVA_API_KEY 없음: 규칙 문장만 만든다"))
        saved = sources.load_brief_hashes()
        counts: Counter[str] = Counter()
        for dong_id, code in sources.load_brief_targets():
            try:
                raw = queries.fetch_dong(code)
            except GraphUnavailable as ex:
                raise CommandError(f"Neo4j 조회 실패 (sync_graph 먼저): {ex}") from ex
            if raw is None:
                self.stdout.write(self.style.WARNING(f"그래프에 없는 동 {code} 건너뜀"))
                continue
            facts = brief.collect_facts(raw)
            digest = brief.facts_hash(facts)
            if saved.get(dong_id) == digest and not options["force"]:
                counts["skip"] += 1
                continue
            result = brief.generate(raw, facts, use_chat)
            sources.save_brief(dong_id, result.sentences, result.source, result.model, digest)
            counts[result.source] += 1
        made = counts["llm"] + counts["mixed"] + counts["rule"]
        self.stdout.write(
            f"llm {counts['llm']} · mixed {counts['mixed']} · rule {counts['rule']}"
            f" · 건너뜀 {counts['skip']}"
        )
        self.stdout.write(f"60일 지난 질문 답 {sources.delete_old_answers()}개 지움")
        if made and counts["rule"] / made > RULE_WARN:
            self.stdout.write(
                self.style.WARNING(f"규칙 문장만 쓴 동이 {counts['rule']}/{made} (20% 초과)")
            )
