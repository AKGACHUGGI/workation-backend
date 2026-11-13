#!/usr/bin/env bash
# 탐색 화면(/api/explore/*)용 로컬 Neo4j 를 띄우고 Supabase 파생 표에서 그래프를 채운다.
# Supabase 는 읽기만 한다. 공용 표를 다시 만드는 build_explore · build_briefs 는 월 배치 담당만 돌린다.
# 처음 한 번, 그리고 Supabase 파생 표가 바뀌었을 때(새 달 데이터) 다시 실행한다.
set -euo pipefail
cd "$(dirname "$0")/.."

# .env 에 값이 없어도 이 스크립트는 compose 기본값으로 붙는다 (.env 에 값이 있으면 그쪽이 먼저)
export NEO4J_URI="${NEO4J_URI:-bolt://localhost:7687}"
export NEO4J_PASSWORD="${NEO4J_PASSWORD:-localpassword}"

docker compose up -d --wait neo4j
uv run python manage.py sync_graph

if ! grep -qE '^NEO4J_URI=.+' .env 2>/dev/null; then
  echo
  echo "runserver 가 Neo4j 에 붙으려면 .env 에 아래 두 줄을 넣으세요 (.env.example 과 같음):"
  echo "  NEO4J_URI=bolt://localhost:7687"
  echo "  NEO4J_PASSWORD=localpassword"
fi
