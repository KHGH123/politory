"""FastAPI 진입점. 레포 루트에서 `uvicorn backend.main:app --reload`로 실행한다."""
import re
import uuid
from datetime import date

from dateutil import parser as dateutil_parser
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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

# TEMP(더미 데이터 임시 전환)로 지금은 안 쓰지만, 실제 테이블 준비되면 아래 두 함수와
# 함께 이 클라이언트도 복원한다.
# _bq_client = bigquery.Client(project=settings.bigquery_project)

# TEMP(더미 데이터 임시 전환): BigQuery assembly 데이터셋의 실제 테이블은
# BIGQUERY_MEMBERS_TABLE(.env: "MP")이 아니라 "legislators"이고, 그 컬럼도
# (legislator_id, party_name, district, term_start/end 등) 아래 MemberProfile이
# 요구하는 필드(age, gender, image_url, military, criminal, committee, term_count,
# status, sns)와 겹치는 게 거의 없어 통합 테스트가 막힌 상태였다. 데이터 담당자가
# "약력 카드"용 실제 테이블을 채울 때까지, 함수 시그니처는 그대로 두고 내부만
# 더미 데이터로 임시 전환해서 backend/frontend 통합 흐름을 계속 검증할 수 있게 한다.
# 실제 테이블이 준비되면 이 두 함수 본문을 BigQuery 쿼리로 되돌리면 된다
# (git blame으로 이 커밋 이전 버전 참고).
_DUMMY_MEMBERS: dict[str, MemberProfile] = {
    "홍길동": MemberProfile(
        name="홍길동",
        age=55,
        party="더불어민주당",
        gender="남",
        image_url=None,
        military="예비역 병장",
        criminal="없음",
        committee="국토교통위원회",
        district="서울 강남구갑",
        term_count=3,
        status="현직",
        sns=[SnsLink(platform="twitter", url="https://twitter.com/example")],
    ),
    # 아래 두 명은 22대 국회 실존 의원(맹성규는 CLAUDE.md MVP 스코프인 국토교통위원회
    # 위원장, 서범수는 여야 균형을 위해 추가한 국민의힘 의원 — 행정안전위원회 소속).
    # 정당/지역구/위원회/선수/재직상태는 공개된 사실을 웹 검색으로 확인해 반영했고,
    # 나이·병역·전과처럼 개인을 특정해 부정확하면 문제가 될 수 있는 필드는 확인하지
    # 않은 채로 채우지 않고 None으로 비워둔다. speech/action_info는 여전히
    # speech_agent/action_agent(tools=[])가 만드는 값이라 이 두 실존 인물에
    # 대해서도 hallucination이 섞일 수 있다는 걸 감안하고 테스트할 것.
    "맹성규": MemberProfile(
        name="맹성규",
        age=None,
        party="더불어민주당",
        gender="남",
        image_url=None,
        military=None,
        criminal=None,
        committee="국토교통위원회(위원장)",
        district="인천 남동구갑",
        term_count=3,
        status="현직",
        sns=[],
    ),
    "서범수": MemberProfile(
        name="서범수",
        age=None,
        party="국민의힘",
        gender="남",
        image_url=None,
        military=None,
        criminal=None,
        committee="행정안전위원회",
        district="울산 울주군",
        term_count=2,
        status="현직",
        sns=[],
    ),
}


def _find_members_by_name(name: str) -> list[MemberCandidate]:
    """이름으로 의원 후보를 찾는다. 동명이인이면 여러 건 반환.

    LLM이 스스로 '이 사람이 진짜 의원인지'를 판단하면 신뢰도가 들쭉날쭉해서
    (예: 유명인은 맞히고 아니면 틀림) DB 조회로 확정한다.
    """
    profile = _DUMMY_MEMBERS.get(name)
    if not profile:
        return []
    return [MemberCandidate(name=profile.name, party=profile.party, image_url=profile.image_url)]


def _get_member_profile(name: str, party: str | None = None) -> MemberProfile | None:
    """약력 카드용 필드를 조회. 없으면 None.

    동명이인이 있을 수 있어 party가 주어지면 그것까지 같이 필터링해 특정한다.
    """
    profile = _DUMMY_MEMBERS.get(name)
    if not profile:
        return None
    if party and profile.party != party:
        return None
    return profile


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
            response_schema=ClassifyResponse,
        ),
    )
    result = ClassifyResponse.model_validate_json(response.text)

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
