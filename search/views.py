"""자연어 검색 POST /search/nl. 흐름은 search/engine.py 머리말."""

import time

from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.errors import bad_request
from search import engine


@api_view(["POST"])
def search_nl(request):
    query = (request.data or {}).get("query") if isinstance(request.data, dict) else None
    if not isinstance(query, str) or not query.strip() or len(query) > 200:
        return bad_request("query 는 1~200자 문자열입니다.")
    started = time.monotonic()
    cond = engine.understand(query.strip())
    found = bool(cond.tags or cond.metrics or cond.month)
    results = engine.rank(cond, query.strip()) if (cond.tags or cond.metrics) else []
    return Response(
        {
            "query": query,
            "conditions": {
                "month": cond.month,
                "tags": cond.tags,
                "metrics": [{"key": k, "direction": d} for k, d in cond.metrics],
            }
            if found
            else {"tags": []},
            "chips": engine.chips(cond),
            "rankQuery": engine.rank_query(cond),
            "unmatched": cond.unmatched,
            "results": results,
            "meta": {
                "parser": cond.parser,
                "elapsedMs": round((time.monotonic() - started) * 1000),
            },
        }
    )
