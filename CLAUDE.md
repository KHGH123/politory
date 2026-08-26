# CLAUDE.md

이 파일은 이 저장소에서 작업하는 Claude Code(claude.ai/code)에게 제공하는 가이드입니다.

## 프로젝트 개요

Politory(의정기록) — 정치인의 특정 정책·사회 이슈에 대한 과거 발언과 정치 활동을 시간순으로 수집·분석하여, 사용자가 **입장 변화와 그 근거를 한눈에 확인**할 수 있도록 제공하는 AI 기반 정치 행적 검색 서비스. Ajou PBL 2차 Team 프로젝트(4인 팀)이며, 현재 대부분 모듈이 `raise NotImplementedError` 형태의 스텁으로 인터페이스만 잡혀 있고 실제 구현은 안 된 상태입니다.

**해결하려는 문제**: 의원의 발의·표결·발언은 의안정보시스템·표결정보시스템·회의록시스템에 각각 흩어져 있어, 특정 의원이 한 이슈에 대해 무엇을 해왔는지 알려면 여러 곳을 수작업으로 엮어야 한다. 특히 표결·의안은 정형 데이터로 제공되지만 **발언은 비정형 회의록에 묻혀 의원 단위 조회가 사실상 불가능**하다.

핵심 제약(PBL 제출 문서 참고):
- 검색축은 정책 키워드가 아니라 **인물(의원)** — 키워드는 타임라인을 좁히는 필터일 뿐이다.
- 같은 의원의 시간차 발언을 병치할 때 **"입장이 바뀌었다" 같은 해석적 판단을 AI가 직접 생성하지 않는다.** 정권 교체·여야 지위·사회적 상황 등 맥락을 근거와 함께 제공하고, 입장 변화가 합리적인지 판단하는 건 어디까지나 사용자 몫이다. `agent/subagent/evidence_synthesis.py`의 `guardrail`이 응답 생성 후 이 위반 여부를 검사한다(실제 해석적 판단 문장을 탐지·제거하는 것까지 테스트로 확인됨). 이와 별개로 `agent/subagent/source_verification.py`는 각 소스의 진술이 실제 도구 결과에 근거하는지(hallucination 여부)를 검사한다 — "해석" 문제와 "사실 근거" 문제는 서로 다른 계층에서 다룬다.
- 1차 출처(회의록 원문, 법안, 표결)와 2차 출처(뉴스 보도)는 응답에서 **신뢰도를 구분해서 표시**해야 한다.
- 핵심 기능 우선순위(PBL 문서 SECTION 04): **F-01(정치인·주제 기반 행적 검색)**, **F-02(발언 타임라인 및 맥락 제공)**가 Must, F-03(입장 변화 분석·요약)이 Should, F-04(발언·정치활동 비교)는 Could.

## 커맨드

모든 명령은 레포 루트에서 실행한다고 가정합니다.

```bash
# 초기 설정
cp .env.example .env
pip install -r requirements.txt

# 백엔드 실행
uvicorn backend.main:app --reload

# Docker로 실행
docker compose up --build

# Day 1 데이터소스 검증 (열린국회정보 API 실제 응답 구조 확인)
python scripts/verify_data_source.py --api-id <API_ID>

# 1회성 데이터 수집 (현재 미구현 스텁)
python -m pipeline.collect_assembly_api

# 평가 (Ragas/DeepEval, 현재 미구현)
python -m eval.run_eval
```

테스트 스위트나 린터, 프론트엔드 스캐폴드는 아직 없습니다. `frontend/`는 의도적으로 전담자가 없으며("각자 자기 파트 화면을 바이브코딩으로 붙인다"), 새로 시작할 때는 `npm create vite@latest . -- --template react`로 부트스트랩합니다.

## 아키텍처

요청 흐름: `frontend` → `backend/main.py` (FastAPI, `POST /api/query` 단일 엔드포인트) → `agent/agent.py`의 `root_agent` → `query_processing`(라우팅) → `fetch`(speech/action/context를 각각 검증 루프로 감싸 병렬 실행) → `evidence_synthesis`(merge → guardrail) → 출처 라벨링된 응답.

역할 분담: **오케스트레이션 담당**(query_processing, fetch의 조립 뼈대, source_verification,
evidence_synthesis, agent.py)과 **소스 에이전트 담당**(agent/subagent/sources/의
speech_agent·action_agent·context_agent 각각의 instruction·tool 연결)이 분리되어 있다.
fetch.py/source_verification.py는 라우팅·검증 뼈대만 갖고 개별 에이전트 정의는 sources/
아래 파일로 나뉘어 있어, 두 그룹이 같은 파일을 동시에 건드리지 않는다.

```
backend/     FastAPI 앱. 로직 없이 얇은 레이어 — agent 호출 결과를 그대로 프론트에 전달.
agent/       ADK(google-adk) 기반 오케스트레이션 + 가드레일.
  agent.py          adk web/adk run이 찾는 실제 진입점. root_agent(SequentialAgent)를
                     정의하며 sub_agents=[query_processing, fetch, evidence_synthesis]로
                     구성. to_a2a()로 감싼 a2a_app도 함께 정의되어 있어
                     `uvicorn agent.agent:a2a_app`으로 A2A 서버로도 띄울 수 있다.
                     구 orchestrator.py(run() 스텁)와 구 guardrails.py는 이 방식으로
                     완전히 대체되어 삭제됨.
  subagent/
    query_processing.py  (구 router.py) 질문을 보고 action/speech/context 중
                          무엇이 필요한지 RouteDecision으로 분류.
    fetch.py              ParallelAgent 뼈대(담당: 오케스트레이션). query_processing
                          결과(session state의 "route")에 따라 speech_verified_loop/
                          action_verified_loop/context_verified_loop(각각 LoopAgent,
                          source_verification.py) 단위로 skip_if_not_routed 콜백을
                          걸어 조건부 실행. 개별 소스 에이전트 정의 자체는 sources/에 있음.
    source_verification.py  (담당: 오케스트레이션) 각 소스 에이전트 출력이 실제 도구
                          호출 결과에 근거하는지(hallucination 여부) 검증하는 계층.
                          evidence_synthesis의 guardrail(해석적 판단 문장 검사)과는
                          검사 대상이 다르다 — 이쪽은 사실 자체의 출처 근거를 본다.
                          speech_verified_loop/action_verified_loop/context_verified_loop
                          = LoopAgent(max_iterations=2, [source_agent, verifier]).
                          verifier가 근거를 확인하면 공식 exit_loop 툴을 호출해 즉시
                          종료하고, 불분명하면(hallucination 의심) exit_loop을 호출하지
                          않은 채 *_retry_hint(session state)에 재검색 지시를 남겨 다음
                          iteration의 source agent가 참고하게 한다. "정보 없음"을 명시한
                          정상 응답은 hallucination이 아니라 근거 있음으로 판단하도록
                          instruction에 명시(안 그러면 매번 재시도만 반복).
    sources/              (담당: 다른 팀원) 개별 소스 에이전트.
      speech_agent.py       (구 rag_search_agent) 발언 벡터 검색. output_key="speech_info".
      action_agent.py       (구 api_search_agent) 법안/표결/의원정보 API 조회.
                            output_key="action_info".
      context_agent.py      (구 news_search_agent) 뉴스/정치적 맥락 검색.
                            output_key="context_info".
                            각 파일의 tools=[]를 agent/tools/의 함수(mcp_server/ 경유)로
                            채우는 게 이 담당자의 몫. instruction에 이미
                            {speech_retry_hint?} 등 재검색 지시 참조가 최소한으로
                            반영되어 있으니 tools 연결 시 그대로 유지할 것.
                            알려진 이슈: tools=[]인 지금 상태에서 LLM이 간헐적으로
                            존재하지 않는 tool(예: "directory::list_tools")을 호출하려
                            시도해 `ValueError: Tool '...' not found`로 파이프라인이
                            죽는 경우가 있음(재현율 낮음, adk web에서 같은 질문
                            반복 실행 중 관찰). tools를 실제로 연결하면 LLM이 진짜
                            tool 목록을 보게 되므로 자연 해소될 것으로 예상 —
                            연결 후에도 재현되면 별도로 다룰 것.
    evidence_synthesis.py  (구 merge.py + guardrail.py 통합) SequentialAgent 2단계:
                          merge(근거 종합 + AgentResponse 스키마로 구조화, output_key=
                          "draft_response") -> guardrail(draft_response.answer의
                          해석적 판단 문장만 검사·제거, sources는 손대지 않고 그대로
                          통과, output_key="final_answer"). 응답 스키마는 아래
                          "a2a agent 입력 출력 형식" 절 참고 — 그 표의 answer/sources
                          구조를 그대로 pydantic AgentResponse로 구현한 것.
  tools/            각 데이터 소스별 비즈니스 로직. mcp_server/를 통해 MCP 툴로 감싸서
                     노출할 예정 — mcp_server/README.md의 FunctionTool/stdio 패턴과
                     agent/agent.py 쪽의 MCPToolset + StdioServerParameters 클라이언트
                     연결 참고. 현재 subagent/sources/의 각 소스 에이전트는
                     tools=[]로 비어 있음.
mcp_server/  agent/tools/의 함수들을 MCP(stdio)로 노출해 ADK 에이전트가 호출하게 함.
rag/         search_client.py(Vertex AI Search 시맨틱 검색), bigquery_client.py(검색
             결과 ID로 BigQuery에서 원문 하이드레이션), retriever.py(member_name/keyword로
             필터링 가능한 검색 조합).
pipeline/    1회성/수동 재실행 스크립트(스케줄링 없음)로 데이터를 수집·정규화:
               collect_assembly_api.py  열린국회정보 API -> data/raw/*.json
               parse_nanet.py           국회도서관 발언빅데이터 다운로드 -> 표준 발언 스키마
               parse_pdf.py             nanet 경로가 안 될 경우의 PDF 회의록 파싱 폴백
                                         (Day 1 go/no-go 결정 사항)
               chunk.py                 파싱된 발언을 임베딩용 청크로 분할
             표준 파싱 발언 형식: {"speaker", "content", "meeting_date",
             "committee", "source_url"}.
config.py    레포 전체가 공유하는 단일 Settings(pydantic-settings), 루트의 .env에서
             로드. 환경변수를 직접 읽지 말고 어디서든 `from config import settings`로 사용.
eval/        qa_dataset.jsonl(질문-정답 쌍) + run_eval.py(Ragas/DeepEval).
infra/       CI/CD 전용 Terraform(GitHub push -> Cloud Build -> Cloud Run). 빌드
             트리거/서비스 계정/IAM을 프로비저닝 — 애플리케이션 인프라가 아님.
             Cloud Build의 GitHub 호스트 연결을 콘솔에서 먼저 수동으로 만들어야 함
             (infra/README.md 참고).
```

### ADK instruction의 `{key}`/`{key?}` 템플릿 주의사항

session.state 값을 LlmAgent 프롬프트에 넣으려면 `instruction` 문자열에
`{state_key}` 플레이스홀더가 실제로 있어야 한다 — "session state의 X를 봐라" 같은
서술만으로는 주입되지 않는다(실제 `KeyError`로 재현하고 고친 버그, `adk web`으로
직접 돌려보기 전엔 코드만 봐서 알아채기 어려웠음). route로 스킵된 소스는
`output_key`가 아예 채워지지 않으므로, 그 값을 참조하는 모든 곳(`{action_info}`,
`{speech_info}`, `{context_info}`, `{*_retry_hint}`)은 반드시 `{key?}`(옵셔널,
없으면 빈 문자열로 치환)로 써야 한다. `merge` 바로 다음에 오는 `{draft_answer}`처럼
항상 존재해야 정상인 값만 `?` 없이 그대로 쓴다.

### 데이터 소스와 신뢰도 등급

외부 데이터 소스 4개, 각각 `config.py`/`.env`에 API 키가 있습니다:
- **ASSEMBLY_API_KEY** — 열린국회정보 Open API(open.assembly.go.kr): 정형 의안/표결/의원 데이터. 무료 "sample" 키로 최대 10건 테스트 가능.
- **NANET_API_KEY** — 국회도서관 발언빅데이터(dataset.nanet.go.kr): 1차 출처 발언 원문(가입 필요). 우선 채택 경로 — 이 포맷이 되면 `pipeline/parse_pdf.py` 단계를 완전히 생략 가능.
- **DATA_GO_KR_API_KEY** — 공공데이터포털 국회사무처 회의록 API: 보조/폴백 정형 소스.
- **WEB_SEARCH_API_KEY** — 뉴스 검색, 회의록 원문과는 다른 신뢰도 등급의 2차 출처.

### MVP 스코프

`pipeline/collect_assembly_api.py`에 하드코딩: 22대 국회(`TARGET_ASSEMBLY = 22`), 국토교통위원회 상임위, 소수 대상 의원(`TARGET_MEMBERS`, 현재 빈 리스트 — 파이프라인을 돌리려면 먼저 대상 의원 3~5명을 채워 넣어야 함).

## 팀 역할 분담 (모듈 경계 참고용)

| 담당 | 역할 | 산출물 |
|---|---|---|
| A | 인프라/저장 | `infra/`, `pipeline/bigquery_schema.sql` |
| B | 데이터 전처리 | `pipeline/` |
| C | 에이전트+툴 | `agent/`, `mcp_server/` |
| D | RAG 설계+평가 | `rag/`, `eval/` |

`agent/` 내부는 다시 두 몫으로 나뉜다:
- **오케스트레이션**: `agent/agent.py`, `agent/subagent/query_processing.py`,
  `agent/subagent/fetch.py`(뼈대만), `agent/subagent/source_verification.py`,
  `agent/subagent/evidence_synthesis.py`
- **소스 에이전트**: `agent/subagent/sources/`(speech_agent/action_agent/context_agent)
  + `agent/tools/`, `mcp_server/`






# a2a agent 입력 출력 형식
  ### 요청

  | 필드 | 타입 | 필수 | 설명 |
  | --- | --- | --- | --- |
  | question | string | O | 질문 |
  | member_name | string | null | X | 의원명 |
  | keyword | string | null | X | 키워드 |

  ### 응답
  필드	타입	필수	설명
  answer	string	O	가드레일 통과한 최종 답변 텍스트
  sources	array	O	출처 목록. 없으면 빈 배열 (프론트가 섹션째로 숨김)
  sources[].type	"primary" | "secondary"	O	1차(회의록 원문)/2차(뉴스) 구분 — 이 값으로 배지 색 갈림
  sources[].title	string	O	출처 제목
  sources[].url	string | null	X	원문 링크
  sources[].date	string | null	X	날짜