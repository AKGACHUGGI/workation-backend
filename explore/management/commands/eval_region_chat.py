"""시군구 채팅 품질 확인 표 (시군구 채팅 스펙 §9).

질문 묶음을 실제 흐름(의도 사전 · Neo4j · HCX)에 넣고 문장 · 근거 줄을 찍는다.
사람이 문장과 근거 줄을 대조해 오류를 센다(통과 문장 오류 0, LLM 문장 비율 90% 안팎).
    uv run python manage.py eval_region_chat 52110 51150 51820
"""

from django.core.management.base import BaseCommand

from explore import views
from explore.chat import arrange, flow

QUESTIONS = (
    "조용한 동네 어디야?",
    "한적한 동네 알려 줘",
    "또래가 많은 동네는?",
    "젊은 사람 많은 동네 어디야?",
    "저녁에 활기 있는 데는?",
    "밤에 북적이는 동네는?",
    "집중해서 일하기 좋은 동네는?",
    "어떤 유형 동네가 있어?",
    "바다 가까운 동네 있어?",
    "해변 근처 동네 알려 줘",
    "관광지 말고 생활권 동네는?",
    "관광지 동네는 어디야?",
    "조용하면서 바다 가까운 데",
    "몇 월에 가면 한산해?",
    "여긴 어떤 곳이야?",
    "몇 시에 붐벼?",
)


class Command(BaseCommand):
    help = "시군구 채팅 질문 묶음의 답을 표로 찍는다 (사람이 근거 줄과 대조)"

    def add_arguments(self, parser):
        parser.add_argument("sigungu", nargs="+", help="시군구 코드 5자리")

    def handle(self, *args, sigungu, **options):
        graph = flow.build(views._chat_deps())
        total = llm_count = 0
        for sgg in sigungu:
            for question in QUESTIONS:
                inp = {
                    "surface": "region",
                    "panel": None,
                    "sigungu": sgg,
                    "month": None,
                    "walk": False,
                    "question": question,
                    "criteria": arrange.clean_criteria(None),
                }
                _, state = flow.run(graph, None, inp)
                if state.get("guide"):
                    self.stdout.write(f"{sgg}\t{question}\tguide\t{state['guide']}\t")
                for s in state.get("sentences") or []:
                    total += 1
                    llm_count += s["source"] == "llm"
                    self.stdout.write(
                        f"{sgg}\t{question}\t{s['source']}\t{s['text']}\t{s['evidence'] or ''}"
                    )
        self.stdout.write(f"LLM 문장 {llm_count}/{total}")
