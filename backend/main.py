"""FastAPI 진입점. 레포 루트에서 `uvicorn backend.main:app --reload`로 실행한다."""
import re
import uuid
from datetime import date
from pathlib import Path

from dateutil import parser as dateutil_parser
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google import genai
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.cloud import bigquery
from google.genai import types as genai_types
from pydantic import BaseModel

from agent.agent import root_agent
from agent.subagent.evidence_synthesis import AgentResponse
from agent.subagent.evidence_synthesis import Source as AgentSource
from config import settings

app = FastAPI(title="의정기록 API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict:
    return {"status": "ok"}


# ---- POST /api/classify ----

class ClassifyRequest(BaseModel):
    question: str


class KeywordSuggestion(BaseModel):
    title: str
    reason: str


class MemberCandidate(BaseModel):
    """동명이인 판별용 — 이름만으로 특정이 안 될 때 화면2에서 선택 카드로 보여줌."""

    name: str
    party: str | None = None
    image_url: str | None = None


class ClassifyResponse(BaseModel):
    sufficient: bool
    member_name: str | None = None
    keywords: list[KeywordSuggestion] = []
    member_candidates: list[MemberCandidate] = []


class _ClassifyLLMOutput(BaseModel):
    """Gemini에게 실제로 채우게 하는 필드만 담는다.

    ClassifyResponse에 member_candidates까지 그대로 response_schema로 넘기면,
    프롬프트가 그 필드를 채우라고 지시한 적이 없는데도 구조화 출력이 스키마의
    모든 필드를 채우려 들어서 LLM이 존재하지도 않는 동명이인 후보를 지어내
    반환하는 문제가 있었다(예: "이재명" 단독 검색에도 candidates 1건이 새어나옴).
    member_candidates는 오직 아래 classify()의 DB 조회 결과로만 채운다.
    """

    sufficient: bool
    member_name: str | None = None
    keywords: list[KeywordSuggestion] = []


# ---- MP(국회의원) BigQuery 조회 공용 모델 ----

class SnsLink(BaseModel):
    platform: str
    url: str


class MemberProfile(BaseModel):
    """MP(국회의원) BigQuery 테이블 조회 결과 — 화면3 상단 약력 카드용."""

    name: str
    age: int | None = None
    party: str | None = None
    gender: str | None = None
    image_url: str | None = None
    military: str | None = None
    criminal: str | None = None
    committee: str | None = None
    district: str | None = None
    term_count: int | None = None
    status: str | None = None
    sns: list[SnsLink] = []


_genai_client = genai.Client(
    vertexai=settings.GOOGLE_GENAI_USE_VERTEXAI,
    project=settings.GOOGLE_CLOUD_PROJECT,
    location=settings.GOOGLE_CLOUD_LOCATION,
)

_bq_client = bigquery.Client(project=settings.BIGQUERY_PROJECT)
_MEMBERS_TABLE = f"{settings.BIGQUERY_PROJECT}.{settings.BIGQUERY_DATASET}.{settings.BIGQUERY_MEMBERS_TABLE}"


def _row_to_profile(row: bigquery.table.Row) -> MemberProfile:
    return MemberProfile(
        name=row.name,
        age=row.age,
        party=row.party,
        gender=row.gender,
        image_url=row.image_url,
        military=row.military,
        criminal=row.criminal,
        committee=row.committee,
        district=row.district,
        term_count=row.term_count,
        status=row.status,
        sns=[SnsLink(platform=s["platform"], url=s["url"]) for s in (row.sns or [])],
    )


def _find_members_by_name(name: str) -> list[MemberCandidate]:
    """이름으로 의원 후보를 찾는다. 동명이인이면 여러 건 반환.

    LLM이 스스로 '이 사람이 진짜 의원인지'를 판단하면 신뢰도가 들쭉날쭉해서
    (예: 유명인은 맞히고 아니면 틀림) DB 조회로 확정한다.
    """
    job = _bq_client.query(
        f"SELECT name, party, image_url FROM `{_MEMBERS_TABLE}` WHERE name = @name",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name)]
        ),
    )
    return [
        MemberCandidate(name=row.name, party=row.party, image_url=row.image_url)
        for row in job.result()
    ]


def _get_member_profile(name: str, party: str | None = None) -> MemberProfile | None:
    """약력 카드용 필드를 조회. 없으면 None.

    동명이인이 있을 수 있어 party가 주어지면 그것까지 같이 필터링해 특정한다.
    """
    query = f"SELECT * FROM `{_MEMBERS_TABLE}` WHERE name = @name"
    params = [bigquery.ScalarQueryParameter("name", "STRING", name)]
    if party:
        query += " AND party = @party"
        params.append(bigquery.ScalarQueryParameter("party", "STRING", party))
    query += " LIMIT 1"

    job = _bq_client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params))
    rows = list(job.result())
    return _row_to_profile(rows[0]) if rows else None


@app.post("/api/classify", response_model=ClassifyResponse)
def classify(request: ClassifyRequest) -> ClassifyResponse:
    """질문이 (의원명+정책) 정도로 충분히 구체적인지 판단.

    부족하면 관련 키워드 후보 최대 3개를 이유와 함께 추천해서
    프론트가 화면2(키워드 선택)를 보여줄 수 있게 한다.
    """
    prompt = f"""사용자 질문: "{request.question}"

이 질문에서 국회의원 이름으로 보이는 고유명사가 있으면 정당명·직함(대표, 원내대표 등)을
제외하고 member_name에 채워라 (있을 때만, 확신이 없어도 일단 채워라).

이 질문이 특정 국회의원 이름과 구체적인 정책/이슈를 모두 포함해서
바로 의정활동을 조회할 수 있을 만큼 충분히 구체적인지 판단해라.

- 충분하면 sufficient=true로 하라.
- 불충분하면 sufficient=false로 하고, 이 질문과 관련될 만한 정책 키워드를
  최대 3개까지 추천해라. 각 키워드에는 왜 이 키워드를 추천하는지
  20자 이내로 짧게 이유를 적어라. 없는 사실을 지어내지 마라."""

    response = _genai_client.models.generate_content(
        model=settings.MODEL,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_ClassifyLLMOutput,
        ),
    )
    llm_result = _ClassifyLLMOutput.model_validate_json(response.text)
    result = ClassifyResponse(
        sufficient=llm_result.sufficient,
        member_name=llm_result.member_name,
        keywords=llm_result.keywords,
    )

    # LLM이 "이 사람이 실존 의원인지"를 자체 판단하게 두지 않고, BigQuery MP
    # 테이블 조회로 확정한다.
    if result.member_name:
        candidates = _find_members_by_name(result.member_name)
        if len(candidates) == 0:
            # DB에 없으면 무조건 화면2(정책/키워드 확인)로 보낸다.
            result.sufficient = False
            result.member_name = None
        elif len(candidates) > 1:
            # 동명이인 — 어느 쪽인지 특정 안 되니 화면2에서 사용자가 직접 고르게 한다.
            result.sufficient = False
            result.member_name = None
            result.member_candidates = candidates

    return result


# ---- POST /api/query ----

class QueryRequest(BaseModel):
    question: str
    member_name: str | None = None
    party: str | None = None  # 동명이인 특정용. 화면2 후보 선택 시 프론트가 채워 보낸다.
    keyword: str | None = None


# agent/subagent/evidence_synthesis.py의 Source(type/title/url/date)를 그대로
# 재사용한다 — 원래 SpeechSource/BillSource(category로 구분되는 discriminated
# union, quote/meeting/proposer 등 더 풍부한 필드)를 선언해뒀었지만, 프론트
# (ResultsScreen.jsx)가 실제로는 title/type/url/date만 읽고 있어서 agent가 주는
# 형태를 그대로 QueryResponse.sources 타입으로 쓴다. 나중에 프론트가
# quote/meeting/proposer까지 렌더링하게 되면 그때 다시 세분화를 검토한다.
Source = AgentSource


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source] = []
    member_profile: MemberProfile | None = None


_session_service = InMemorySessionService()
_agent_runner = Runner(
    agent=root_agent, app_name="politory_agent", session_service=_session_service
)


async def _run_agent(question: str, member_name: str | None, keyword: str | None) -> AgentResponse:
    """root_agent를 한 번 실행해 evidence_synthesis의 최종 응답(AgentResponse)을 얻는다.

    query_processing이 자유 텍스트 하나만 받으므로, member_name/keyword를 질문
    문장에 조합해 넣는다(요청 스키마 question/member_name/keyword는 아직 agent
    쪽에 구조화된 입력으로 반영되지 않은 상태 — CLAUDE.md "다음 할 일" 참고).
    """
    parts = [question]
    if member_name:
        parts.append(f"(대상 의원: {member_name})")
    if keyword:
        parts.append(f"(키워드: {keyword})")
    combined_question = " ".join(parts)

    session_id = str(uuid.uuid4())
    await _session_service.create_session(
        app_name="politory_agent", user_id="backend", session_id=session_id
    )

    final_answer: AgentResponse | None = None
    async for event in _agent_runner.run_async(
        user_id="backend",
        session_id=session_id,
        new_message=genai_types.Content(
            role="user", parts=[genai_types.Part(text=combined_question)]
        ),
    ):
        if event.author == "guardrail" and event.content and event.content.parts:
            text = "".join(p.text or "" for p in event.content.parts)
            if text:
                final_answer = AgentResponse.model_validate_json(text)

    if final_answer is None:
        # 파이프라인이 끝까지 돌았는데 guardrail 출력이 안 잡힌 비정상 케이스 —
        # 사용자에게는 원인불명 500 대신 "답변을 만들지 못했다"로 명확히 알린다.
        return AgentResponse(answer="답변을 생성하지 못했습니다. 다시 시도해주세요.", sources=[])

    # merge instruction이 answer는 "시간순으로 나열하라"고 지시하지만 sources
    # 배열 자체의 정렬은 강제하지 않아서, 실제로 날짜가 뒤섞여 나오는 걸
    # 확인했다(예: 08-20, 08-11, 08-12, 08-20 순). LLM 출력 순서에 기대지 않고
    # 여기서 date 기준으로 확실하게 정렬한다.
    final_answer.sources = sorted(final_answer.sources, key=_source_sort_key)
    return final_answer


_KOREAN_DATE_RE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일")


def _parse_source_date(date_str: str | None) -> date | None:
    """sources[].date는 LLM이 채우므로 "2026-08-20", "2024년 7월 10일" 같은
    형식이 섞여 나온다(원문에 명시된 표기를 그대로 옮기라고 지시했기 때문).
    dateutil은 한글 "년/월/일" 형식을 못 읽어서 먼저 정규식으로 시도하고,
    안 되면 dateutil로 폴백한다. 둘 다 실패하면 None(정렬 시 맨 뒤로 보냄).
    """
    if not date_str:
        return None
    m = _KOREAN_DATE_RE.search(date_str)
    if m:
        year, month, day = (int(g) for g in m.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    try:
        return dateutil_parser.parse(date_str, fuzzy=True).date()
    except (ValueError, OverflowError):
        return None


def _source_sort_key(source: AgentSource) -> tuple[int, date]:
    """date 오름차순(과거 -> 최근) 정렬 키. 날짜를 못 읽은 항목은 맨 뒤로 보낸다
    (0/1 플래그로 우선 그룹을 나누고, 그룹 내에서 실제 날짜로 2차 정렬)."""
    parsed = _parse_source_date(source.date)
    if parsed is None:
        return (1, date.min)
    return (0, parsed)


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    profile = (
        _get_member_profile(request.member_name, request.party) if request.member_name else None
    )

    # classify를 거치지 않고 화면2에서 자유 입력으로 바로 넘어온 경우까지 대비해,
    # member_name이 있는데 DB에 없으면 여기서도 막는다 — 검색 자체를 진행시키지 않는다.
    if request.member_name and profile is None:
        raise HTTPException(status_code=404, detail="등록된 국회의원이 아닙니다.")

    agent_response = await _run_agent(request.question, request.member_name, request.keyword)

    return QueryResponse(
        answer=agent_response.answer, sources=agent_response.sources, member_profile=profile
    )


# ---- 프론트엔드 정적 서빙 ----
# Dockerfile이 frontend를 먼저 빌드해 backend/static/에 dist를 복사해둔다
# (politory 서비스 하나로 프론트+백엔드를 같이 서빙하기 위함 — 별도 Cloud Run
# 서비스로 분리했던 이전 시도 대신 이 방식으로 통합). 로컬에서 `uvicorn
# backend.main:app --reload`만 띄우는 경우엔 이 디렉터리가 없을 수 있으므로
# 존재할 때만 마운트한다 — 없어도 API 자체는 그대로 동작해야 한다.
_STATIC_DIR = Path(__file__).parent / "static"

if _STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC_DIR / "assets"), name="static-assets")

    # SPA fallback: /api/*, /health가 아닌 모든 GET 경로를 받는다. favicon.svg
    # 처럼 dist 최상위에 있는 파일(assets/ 밖)은 요청 경로 그대로 존재하면
    # 그 파일을 서빙하고, 없으면(클라이언트 라우팅 경로 등) index.html로
    # 폴백한다. 이 라우트를 맨 마지막에 등록해야 위의 /api/*, /health 라우트가
    # 먼저 매칭된다(FastAPI는 등록 순서대로 매칭 시도).
    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str) -> FileResponse:
        candidate = (_STATIC_DIR / full_path).resolve()
        # candidate가 static 디렉터리 밖으로 벗어나지 않는지 확인한다
        # (예: full_path="../../etc/passwd" 같은 경로 순회 방지).
        if (
            full_path
            and candidate.is_relative_to(_STATIC_DIR.resolve())
            and candidate.is_file()
        ):
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")
