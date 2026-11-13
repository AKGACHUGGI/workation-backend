# 탐색 워케이션 유형 3개 · 대표 유형 기준 설계 (백엔드)

- 작성일: 2026-11-14
- 상태: 승인됨 (브레인스토밍 결과, 2026-11-13~14)
- 고치는 문서: [v6 설계](./2026-11-13-explore-graph-v6-design.md) §2 · §3 · §4-1 · §5-4 · §6. 이 문서에 없는 내용은 v6 그대로다
- 웹 짝 문서: workation-web `docs/superpowers/specs/2026-11-14-W-09-방사형-그래프-design.md` (브랜치 `docs/W-09-radial-graph`)
- 분석 스크립트: 세션 scratchpad `feas.py`(동 단위 장기체류 가능성), `thresh.py`(기준 점수). Supabase 읽기 전용으로 실행, 2025년 데이터

## 1. 목표

- 워케이션 유형에서 **장기체류형을 뺀다**. 서비스 취지가 "일할 자리 찾기"이고, 장기체류형은 동 데이터가 아니다.
- 1위 유형 점수가 낮은 동은 **대표 유형 없음**으로 둔다. 23점짜리 "몰입형" 같은 약한 1위를 보여 주지 않는다.

완료 기준:

- `dong_type_score`에 유형 3개(`immersion` · `networking` · `local`), 순위 1~3만 있다.
- 핀 API는 1위 점수가 40 미만인 동의 `topType` · `topScore`를 `null`로 준다.
- 동 상세 API의 `types`는 3개 막대를 그대로 준다(기준 미만이어도).
- 유형 없는 동의 브리핑 3번째 문장과 "어떤 워케이션에 잘 맞아?" 답이 유형을 지어내지 않는다.

## 2. 왜 장기체류형을 빼나 (근거)

| 근거 | 내용 |
| --- | --- |
| 시군구 데이터 | 점수식 재료 `overnight` · `ease`가 `metric.scope = 'region'`에만 있다. 같은 시군구 동은 전부 같은 점수(강릉 21동 모두 61) |
| 외곽 쏠림 | 다른 유형이 약한 읍·면이 61점으로 1위가 된다. 강릉 장기체류형 1위 8곳 전부 읍·면 |
| 동 단위로 내려도 안 맞음 | 동 월별 방문(`vist_total`)으로 한산한 달을 세면 축제 달 하나 때문에 11달이 "한산"이 되는 동이 27곳. 시뮬레이션하면 장기체류형 1위가 58 → 73동으로 더 쏠린다 |
| 취지 | 워케이션의 질문은 "어디서 일하기 좋은가". 머무는 기간은 유형이 아니라 사용자의 조건이다 |

한산한 달(F3, 동 상세 `months`, 브리핑 2번째 문장)은 시군구 정보로 그대로 둔다. 이건 `derive.quiet_months`(raw 하위 3달)이고, 장기체류형만 쓰던 `derive.easy_months`(raw ≤ 80 개수)와 다르다.

## 3. 대표 유형 기준 40점 (근거)

장기체류형을 뺀 203동의 1위 점수 분포:

```
 20~39 ███████████████████████████████████████ 39   ← 낮은 무리
 40~59 ████████████████████████████████ 32          ← 골짜기
 60~99 ████…  156                                   ← 대부분
```

| 기준 | 유형 없음 | 강릉 | 판단 |
| --- | --- | --- | --- |
| 35 | 23동 (11%) | 3 | 낮은 무리 중간을 자른다 |
| **40** | **39동 (19%)** | **5** | **낮은 무리와 골짜기 경계. 채택** |
| 50 | 56동 (28%) | 6 | "평균 이상 유형 없음"이라 뜻은 깔끔하나 골짜기 중간 |
| 60 | 71동 (35%) | 7 | 너무 많다 |

- 점수는 203동 백분위 기반이라 40 미만 = 세 유형 모두 하위 40%.
- 세 점수가 무관한 무작위라면 1위 < 40은 6.4%다. 실제 19%는 "모든 지표가 낮은 동"이 한 무리로 있다는 뜻이다(주로 인구 적은 읍·면).
- 세 유형의 점수 평균이 모두 약 50(몰입 49.7 · 네트워킹 50 · 로컬 50.2)이라 한 기준을 같이 써도 공평하다.
- 알려진 결과: 고흥군 16동 중 10동, 부안군 13동 중 6동이 유형 없음. 전국 기준이라 정확한 값이다. 시군구 안 상대 순위로 바꾸지 않는다(시군구마다 유형의 뜻이 달라진다).

기준 상수는 `explore/derive.py`의 `TYPE_MIN = 40` 하나다.

## 4. 바뀌는 것

### 4-1. 유형 점수 (`explore/derive.py`)

- `TYPES = ("immersion", "networking", "local")`, `TYPE_KO`에서 `long_stay` 제거.
- `type_scores`의 `long_stay` 항목 제거. 인자 `region_metrics`는 장기체류형만 썼으므로 뺀다: `type_scores(dong_metrics, morning)`. `region_of`도 장기체류형 전용이면 뺀다.
- `easy_months`는 장기체류형 전용이라 삭제(테스트 포함).
- `explore/sources.py`: `region_metrics`(overnight 있는 시군구만 거름)와, 시군구에 overnight가 없으면 동을 빼던 조건(91행 `region_of.get(d) in region_metrics`)을 없앤다. `DeriveInput`에서 필드 제거. `build_explore.py` 호출부도 맞춘다.
- 동점 순서: 몰입형 → 네트워킹형 → 로컬살이형.
- 추가: `TYPE_MIN = 40`. 그래프 degree · Cypher · 브리핑 · 질문 답이 이 값 하나를 본다.

### 4-2. DB (`db/migrations/20261114_explore-type-3.sql` + `db/schema.sql`)

```sql
DELETE FROM dong_type_score WHERE type = 'long_stay';
-- CHECK 교체: type IN ('immersion','networking','local'), rank BETWEEN 1 AND 3
```

- 남은 행의 순위는 4-4 재계산 때 다시 쓴다(장기체류형이 1위였던 동은 순위가 당겨진다).
- `COMMENT ON COLUMN dong_type_score.rank`를 `1~3, 동점이면 immersion → networking → local`로.
- 태그 `dong.tags`의 `long_stay`(관광공사 장기 숙소)는 **다른 것이라 그대로 둔다.** `regions` · `webapi` · `tag_vocab` · `05_dong_doc.sql` · `build_poi_tags_sql.py` · `graph.py`의 `PoiTag` 목록은 손대지 않는다.

### 4-3. 그래프 (`explore/graph.py`, `explore/queries.py`)

- `Type` 노드 4 → 3. `FITS`는 모든 동 × 3 유형 그대로(점수·순위·이유). 상세 막대에 필요하다.
- `Type.degree`(브리핑 흔함 계산): 순위 1이면서 `TYPE_MIN` 이상인 동만 센다.
- 핀 조회: `FITS {rank: 1}` 중 `f.score >= $type_min`만 `top`으로. 없으면 `topType` · `topScore` = `null`. `$type_min`은 `derive.TYPE_MIN`을 넘긴다.
- 공통 사실 조회(`FACTS`의 `top`)도 같은 조건. 유형 없는 두 동이 `1위 몰입형`을 공통점으로 갖지 않게 한다.
- 동 상세 `types`: 그대로 3개.
- `sync_graph`의 기대 개수: `Type` 3, `FITS` 동 수 × 3.

### 4-4. 브리핑 (`explore/brief.py`)

- F4 사실: 1위가 `TYPE_MIN` 이상일 때만 `{이름} {점수}점 (유형 1위)`를 강점 사실로 넣는다. 미만이면 흔함 1.0으로 뒤에 두고 문구를 `{이름} {점수}점 (유형 1위, 기준 40점 미만)`으로 바꾼다(LLM이 강점으로 쓰지 않게).
- 규칙 문장 3:
  - 대표 유형 있음: v6 그대로 `{1위 유형} {점수}점으로, {가장 높은 등급 지표} {등급}({점수}점)이에요.`
  - 없음 + 시군구 1위 지표 있음: `{시군구} 안 {지표} 1위인 동네예요.`
  - 없음 + 등급만: `{가장 높은 등급 지표} {등급}({점수}점)이 이 동네의 가장 큰 특징이에요.`
  - 없음 + 등급도 없음: `뚜렷하게 맞는 워케이션 유형은 없어요.`
- 검증 규칙(§5-4)은 그대로. 유형 없는 동의 생성 문장에 유형 이름이 나오면 사실 목록에 없으므로 실패 → 규칙 문장.

### 4-5. 동네에 물어보기 (`explore/ask.py`)

- 의도 `type` 답: 대표 유형이 있으면 v6 그대로. 없으면 `뚜렷하게 맞는 유형은 없어요. 몰입형 {a}점 · 네트워킹형 {b}점 · 로컬살이형 {c}점이에요.`
- 224행 주석 예시를 `"로컬살이형 60점 (유형 1위)"`로.
- 저장된 답(`dong_answer`)은 재생성 때 `clear_answers`로 비운다.

### 4-6. API 응답 (v6 §6과 다른 점만)

```json
{ "topType": null, "topScore": null, "coastal": true, "life": "관광지", "grades": { "quiet": "B", "evening": "C" } }
```

## 5. 데이터 재생성 (Supabase 쓰기 · 별도 확인 후 실행)

1. 마이그레이션 `20261114_explore-type-3.sql` 적용
2. `manage.py build_explore` → `dong_type_score` 3유형 재계산
3. `manage.py sync_graph` → Neo4j
4. `manage.py build_briefs` → 1위 유형이 바뀐 동은 `facts_hash`가 달라져 다시 생성된다(HyperCLOVA 호출). 끝 출력의 `rule` 비율 확인
5. `manage.py clear_answers`

## 6. 테스트

- `test_explore_derive.py`: 유형 3개, 주문진읍 픽스처 → 로컬살이형 60 1위(`long_stay` 기대값 제거), 동점 순서.
- `test_explore_graph.py` · `test_explore_graph_write.py`: `Type` 3, `FITS` 3개/동, `degree`가 기준 미만 동을 세지 않음.
- `test_explore_queries.py` · `test_explore_api.py`: 기준 미만 동 핀 `topType`/`topScore` null, 상세 `types` 3개.
- `test_explore_brief.py`: 유형 없는 동 규칙 문장 3, F4 강점 사실 빠짐.
- `test_explore_ask.py`: 유형 없음 답 문장.
- `tests/explore_samples.py`: 장기체류형 행 제거, 기준 미만 표본 1동 추가.
- 통합 `tests/integration/test_neo4j_sync.py`: 기대 개수.

## 7. 범위 밖

- 한산한 달을 동 단위로 바꾸기(§2 이유로 보류).
- 태그 `long_stay`를 유형 점수에 섞기.
- 웹 그래프 모양 — 웹 짝 문서.
