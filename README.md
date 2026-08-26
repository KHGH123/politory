# Politory(의정기록)

국회의원의 발의·표결·발언을 **인물 단위로 엮어 시간순으로 보여주는** AI 기반 정치 행적 검색 서비스.
Ajou PBL 2차 Team 프로젝트.

**서비스 바로 써보기**: https://politory-951524423893.us-central1.run.app/

## 왜 만들었나

22대 국회 전반기 2년(2024.05.30 ~ 2026.06.10) 동안 발의된 법안은 18,658건으로, 21대 국회 같은 기간보다 21% 늘었습니다([출처](https://www.hankyung.com/article/2026061236821)). 발의 법안·표결 결과·회의록은 각각 공개돼 있지만 서로 연결되어 있지 않아서, "이 의원이 이 정책에 대해 예전엔 뭐라고 했고 지금은 어떻게 생각할까?"에 답하려면 사람이 여러 시스템(의안정보시스템·표결정보시스템·회의록시스템)을 일일이 뒤져 시간순으로 직접 엮어야 합니다. 특히 표결·의안은 정형 데이터로 제공되지만 **발언은 비정형 회의록에 묻혀 의원 단위 조회가 사실상 불가능**합니다.

- **A씨** — 어떤 정치인의 부동산 정책이 예전 발언과 같은지 알고 싶지만, 보도가 여기저기 흩어져 있어 스스로 시간순으로 엮어봐야 한다.
- **B씨** — 부동산 정책에 관심이 많지만 어떤 의원이 이 사안을 다루는지 모른다. 정책만 검색해도 관련 의원을 보여주는 서비스가 없다.

Politory는 이 두 질문에 인물 축으로 답합니다.

## 프로젝트 차별점

| | | |
|---|---|---|
| **인물 중심 시점별 추적** | **맥락은 제공, 판단은 사용자 몫** | **근거 기반 말과 행동 비교** |
| 한 사람의 발언을 시간순으로 제시 | AI가 "입장이 바뀌었다"를 판단하지 않고 배경과 근거만 제시 | 발언·법안·표결까지 원문 출처로 대조 |

검색축은 정책 키워드가 아니라 **인물(의원)** — 키워드는 타임라인을 좁히는 필터일 뿐입니다. 1차 출처(회의록 원문·법안·표결)와 2차 출처(뉴스 보도)는 응답에서 신뢰도를 구분해 표시합니다.

## 서비스 동작 흐름

| 단계 | 이름 | 설명 |
|---|---|---|
| ① | 사용자 질문 | 정치인이나 정책에 대해 질문 입력 |
| ② | 키워드/인물 추천 | 질문이 충분히 구체적이지 않으면, 정치인 관련 정책 또는 정책 관련 정치인을 추천(구체적이면 이 단계는 건너뜀) |
| ③ | 결과 생성 | 회의록·표결·뉴스 검색 결과를 요약 + 타임라인 형식으로 생성 |

## 시스템 아키텍처

| 계층 | 구성 | GCP 프로젝트 |
|---|---|---|
| Frontend | React + Vite | aj11 |
| Backend | FastAPI | aj11 |
| Agent | Google ADK | aj11 |
| MCP Server | FastMCP | aj36 |
| Data | BigQuery, Vertex AI Search | aj04 |
| Secret 관리 | Secret Manager | aj36 |

| 연결 관계 | 설명 |
|---|---|
| Frontend ↔ Backend | 검색창 입력·결과 렌더링 |
| Backend ↔ Agent | `/api/query`가 root_agent 실행 |
| Backend → BigQuery | 약력 카드·동명이인/지역구 후보 직접 조회 |
| Agent ↔ MCP Server | speech_agent/action_agent가 MCPToolset(HTTP)으로 호출 |
| MCP Server → BigQuery, Vertex AI Search | 발언/표결 원문 조회·시맨틱 검색 |

| CI/CD 단계 | 도구 |
|---|---|
| 코드 푸시 | GitHub(`main` push) |
| 인프라 프로비저닝 | Terraform(IaC) |
| 이미지 빌드 | Cloud Build |
| 배포 | Cloud Run(자동 배포) |

팀원마다 GCP 프로젝트가 나뉘어 있습니다 — `aj11`(Vertex AI/Gemini, 서비스 실행), `aj04`(BigQuery/Vertex AI Search 데이터), `aj36`(MCP Server). `config.py`의 `BIGQUERY_PROJECT`가 이 분리를 흡수합니다.

## 멀티 에이전트 구조

`agent/agent.py`의 `root_agent`(SequentialAgent)가 아래 순서로 실행됩니다. `fetch` 단계의 speech/action/context 3갈래는 동시에(ParallelAgent) 실행됩니다.

| 순서 | 단계 | 역할 |
|---|---|---|
| 1 | `classify`(backend) | 질문 충분 여부 판단 · 이름/정책 추출 · 키워드 카드 추천 |
| 2 | `query_processing` | action/speech/context 필요 여부(T·F) 판단 |
| 3 | `fetch` → `speech_agent` → `speech_verifier` | 회의록에서 근거 조회 → 근거가 실제 도구 결과에 기반하는지 검증(불분명하면 재검색) |
| 3 | `fetch` → `action_agent` → `action_verifier` | 찬반 표결에서 근거 조회 → 검증 |
| 3 | `fetch` → `context_agent` → `context_verifier` | 네이버 뉴스에서 근거 조회 → 검증 |
| 4 | `merge` | 근거 종합 · answer+sources 구조화 · 1차/2차 출처 구분 · reference id 부여 |
| 5 | `guardrail` | 해석적 판단 문장 제거 · hallucination 제거 · url 누락 검사 · 악성 스크립트 검사 |
| 6 | 최종 응답 | answer + sources(타임라인) |

각 소스 단계는 `LoopAgent(max_iterations=2)`로 구성되어, verifier가 통과시키면 즉시 종료(`exit_loop`)하고, 근거가 불분명하면(hallucination 의심) 재검색 지시(`retry_hint`)를 남겨 같은 단계를 한 번 더 돕니다. `source_verification`(사실 근거 검사)과 `guardrail`(해석적 판단 검사)은 서로 다른 계층에서 서로 다른 문제를 검사합니다.

| MCP Server 툴 | 역할 |
|---|---|
| `resolve_legislator` | 의원 이름 → ID·기본정보 조회 |
| `search_speeches` | Vertex AI Search로 발언 검색 |
| `search_votes` | 본회의 표결 검색 + 회의록 출처 |
| `get_utterances` | 발언 ID로 전체 원문 조회 |
| `retrieve_speech_evidence` | 검색 + 중복·짧은 발언 제거 |

## 사용 방법

### 서비스로 바로 쓰기

1. https://politory-951524423893.us-central1.run.app/ 접속
2. 검색창에 **인물**("이재명 의원 부동산 정책") 또는 **정책**("교통비 완화 정책")을 입력
3. 질문이 충분히 구체적이지 않으면 추천 카드(관련 키워드 또는 관련 의원)가 뜨고, 선택하면 자동으로 조회가 이어집니다
4. 결과 화면에서 요약된 답변과, 그 답변이 인용한 회의록·표결(1차)·뉴스(2차) 출처를 시간순 타임라인으로 확인

### 로컬 개발 환경

```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

프론트엔드:

```bash
cd frontend
npm install
npm run dev
```

Docker로 한 번에:

```bash
docker compose up --build
```

API 요청/응답 형식은 [API.md](API.md), 아키텍처와 각 모듈의 세부 동작·트러블슈팅 기록은 [CLAUDE.md](CLAUDE.md)를 참고하세요.

## 데이터 소스

| 데이터 | 수집/검색 방식 |
|---|---|
| 열린국회정보 Open API | 국회의원 인적사항, 회의 메타데이터 |
| 국회 공식 회의록 PDF | GCS에 원문 보존 후 `pdftotext -raw`로 직접 텍스트 추출 |
| BigQuery | 위 데이터를 정형 테이블로 통합, 화자-의원 매칭(`speaker_identity_map`) |
| Vertex AI Search | BigQuery 발언/표결 데이터를 색인한 시맨틱 검색 엔진 |
| 네이버 뉴스 검색 API | 2차 출처(보도) 실시간 조회 |

수집 파이프라인 실행 순서와 옵션은 [pipeline/README.md](pipeline/README.md) 참고.

## 프로젝트 구조

```
backend/     FastAPI 서버
agent/       ADK 에이전트, 가드레일 (툴은 mcp_server/ 참고)
mcp_server/  rag/ 함수를 MCP(HTTP) 서버로 노출
pipeline/    회의록 PDF 수집/추출 -> BigQuery/Vertex AI Search 적재 (1회성 스크립트)
rag/         Vertex AI Search 시맨틱 검색 + BigQuery 원문 하이드레이션
eval/        평가셋 + DeepEval
scripts/     Day 1 데이터소스 검증 스크립트
infra/       Terraform (CI/CD: GitHub push -> Cloud Build -> Cloud Run)
frontend/    React (Vite)
```

## 검증 결과

DeepEval 기반 자동 평가(judge: Vertex AI Gemini)로 핵심 지표를 검증했습니다.

| 지표 | 목표 | 결과 |
|---|---|---|
| 과업 성공률 | 80% 이상 | 85.7% (7종 중 6종) |
| 응답 지연·안정성 | 평균 120초 이내, 오류율 10% 미만 | 평균 72.88초, 오류율 7.7% |
| 사실성(Faithfulness) | 0.8 이상 | 1.0 |
| 관련성(Answer Relevancy) | 0.8 이상 | 1.0 |
| 안전성(근거 없는 응답 차단) | 100% 통과 | 100% 통과 |

동명이인 식별, 존재하지 않는 의원·프롬프트 인젝션 방어, 발언·표결 통합 검색 등 세부 시나리오 검증은 `eval/qa_dataset.jsonl`과 `python -m eval.run_eval` 결과(`eval/eval_report.json`)로 확인할 수 있습니다.

## 추후 계획

- 용어 설명 챗봇 구현
- 의원 및 데이터 범위 확대(현재는 22대 국회의원 한정)
- 자연어 질의 대응 확대(현재는 정책·정치인 입력 중심, 추후 포괄적 질문 처리)

## 팀

| 학과 | 이름 |
|---|---|
| 소프트웨어학과 | 이기훈 |
| 소프트웨어학과 | 최환희 |
| 사이버보안학과 | 배동준 |
| 디지털미디어학과 | 안현식 |

| 담당 | 역할 | 산출물 |
|---|---|---|
| A | 인프라/저장 | `infra/`, `pipeline/bigquery_schema.sql` |
| B | 데이터 전처리 | `pipeline/` |
| C | 에이전트+툴 | `agent/` |
| D | RAG 설계+평가 | `rag/`, `eval/` |
