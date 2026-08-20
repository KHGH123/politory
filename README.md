# 의정기록

국회의원의 입법·표결·발언 활동을 의원 단위로 엮어 시간순으로 보여주는 RAG 기반 의정활동 조회 서비스. 4일 해커톤 MVP.

- 검색축은 정책 키워드가 아니라 **인물(의원)**. 키워드는 타임라인을 좁히는 필터일 뿐.
- 시간차 발언을 병치할 때 "입장이 바뀌었다" 같은 해석적 판단은 생성하지 않는다 (가드레일로 강제).
- 회의록 원문(1차)과 뉴스 보도(2차)는 신뢰도를 구분해서 표시한다.

## 구조

```
backend/     FastAPI 서버
agent/       ADK 에이전트, 가드레일 (툴은 mcp_server/ 참고)
mcp_server/  agent 툴을 MCP 서버로 노출 (C 담당)
pipeline/    회의록 수집/파싱/청킹 (1회성 스크립트)
rag/         임베딩/벡터 검색
eval/        평가셋 + Ragas/DeepEval
db/          SQLite 스키마
scripts/     setup/verify 스크립트
infra/       Terraform (CI/CD: GitHub push -> Cloud Build -> Cloud Run)
frontend/    React (Vite)
docs/        아키텍처/정책 문서
```

## 팀 역할

| 담당 | 역할 | 산출물 |
|---|---|---|
| A | 인프라/저장 | `db/`, `scripts/setup_*` |
| B | 데이터 전처리 | `pipeline/` |
| C | 에이전트+툴 | `agent/` |
| D | RAG 설계+평가 | `rag/`, `eval/` |

## 실행

```
cp .env.example .env
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Docker:
```
docker compose up --build
```

CI/CD (선택, GCP 배포 시): `infra/README.md` 참고.

## Day 1 확인사항

```
python scripts/verify_data_source.py --api-id <API_ID>
```
