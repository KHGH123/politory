"""FastAPI 진입점. 레포 루트에서 `uvicorn backend.main:app --reload`로 실행한다."""
import json
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
from sse_starlette.sse import EventSourceResponse

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
    """화면2 선택 카드용 — 동명이인 특정 또는 정책 기반 의원 추천, 두 경우 모두 재사용."""

    name: str
    legislator_id: str | None = None
    party: str | None = None
    district: str | None = None
    image_url: str | None = None


class ClassifyResponse(BaseModel):
    sufficient: bool
    member_name: str | None = None
    legislator_id: str | None = None
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
    # 질문에 의원 이름이 없을 때, 관련 있을 법한 상임위원회 이름 하나(예: "국토교통위원회").
    # 이것도 DB 검증 없이 그대로 응답에 노출하면 안 되므로(위원회 자체는 지어낼 수 없는
    # 고정된 목록이라 hallucination 우려는 적지만, 오타/변형 표기 가능성은 있음) classify()가
    # 이 값으로 BigQuery를 조회해 실제 소속 의원만 member_candidates로 반환한다.
    committee_guess: str | None = None


# ---- MP(국회의원) BigQuery 조회 공용 모델 ----

class SnsLink(BaseModel):
    platform: str
    url: str


class MemberProfile(BaseModel):
    """MP(국회의원) BigQuery 테이블 조회 결과 — 화면3 상단 약력 카드용."""

    name: str
    legislator_id: str | None = None
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
        legislator_id=row.legislator_id,
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
        f"SELECT name, legislator_id, party, district, image_url "
        f"FROM `{_MEMBERS_TABLE}` WHERE name = @name",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name)]
        ),
    )
    return [
        MemberCandidate(
            name=row.name,
            legislator_id=row.legislator_id,
            party=row.party,
            district=row.district,
            image_url=row.image_url,
        )
        for row in job.result()
    ]


def _find_members_by_committee(committee_guess: str, limit: int = 3) -> list[MemberCandidate]:
    """상임위원회 이름으로 소속 의원을 찾는다 (정책 질문에서 인물을 역으로 추천할 때 사용).

    committee 컬럼은 "연금개혁 특별위원회, 보건복지위원회"처럼 여러 위원회가
    콤마로 붙어있을 수 있어 LIKE로 부분일치한다.
    """
    job = _bq_client.query(
        f"SELECT name, legislator_id, party, district, image_url FROM `{_MEMBERS_TABLE}` "
        f"WHERE committee LIKE @pattern ORDER BY term_count DESC, name LIMIT @limit",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("pattern", "STRING", f"%{committee_guess}%"),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
            ]
        ),
    )
    return [
        MemberCandidate(
            name=row.name,
            legislator_id=row.legislator_id,
            party=row.party,
            district=row.district,
            image_url=row.image_url,
        )
        for row in job.result()
    ]


def _get_member_profile(
    name: str,
    party: str | None = None,
    legislator_id: str | None = None,
) -> MemberProfile | None:
    """약력 카드용 필드를 조회. 없으면 None.

    동명이인이 있을 수 있어 party가 주어지면 그것까지 같이 필터링해 특정한다.
    """
    query = f"SELECT * FROM `{_MEMBERS_TABLE}` WHERE name = @name"
    params = [bigquery.ScalarQueryParameter("name", "STRING", name)]
    if legislator_id:
        query += " AND legislator_id = @legislator_id"
        params.append(
            bigquery.ScalarQueryParameter(
                "legislator_id", "STRING", legislator_id
            )
        )
    elif party:
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
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="질문을 입력해주세요.")

    prompt = f"""사용자 질문: "{request.question}"

이 질문에서 국회의원 이름으로 보이는 고유명사가 있으면 정당명·직함(대표, 원내대표 등)을
제외하고 member_name에 채워라 (있을 때만, 확신이 없어도 일단 채워라).

이 질문이 특정 국회의원 이름과 구체적인 정책/이슈를 모두 포함해서
바로 의정활동을 조회할 수 있을 만큼 충분히 구체적인지 판단해라.

- 충분하면 sufficient=true로 하라.
- 불충분하고 member_name이 있으면, 그 의원과 관련될 만한 정책 키워드를
  최대 3개까지 추천해라(keywords). 각 키워드에는 왜 이 키워드를 추천하는지
  20자 이내로 짧게 이유를 적어라. 없는 사실을 지어내지 마라.
- 불충분하고 member_name이 없으면(정책/이슈만 있고 특정 인물이 없는 질문),
  keywords는 비워두고 대신 이 정책/이슈를 주로 다루는 대한민국 국회 상임위원회
  이름 하나를 committee_guess에 채워라(예: "국토교통위원회", "보건복지위원회",
  "기획재정위원회" 등 실제 상임위 명칭 그대로). 확신이 없어도 가장 가까운 걸로 채워라."""

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
    member_not_found = False
    if result.member_name:
        candidates = _find_members_by_name(result.member_name)
        if len(candidates) == 0:
            # DB에 없으면 무조건 화면2(정책/키워드 확인)로 보낸다. 이때 keywords는
            # 반드시 비워야 한다 — LLM이 member_name을 실존 의원이라고 착각한
            # 상태에서 만든 키워드라(예: "윤석열이 뭐하는 사람이야" -> 키워드로
            # "의대 정원 확대" 등 정부 정책을 추천), 존재하지 않는 인물과 관련된
            # 것처럼 보이는 키워드를 그대로 두면 사용자가 그걸 눌러 인물 미확정
            # 상태로 조회가 나가는 문제가 있었다(실사용 중 발견, 2026-08-23).
            result.sufficient = False
            result.member_name = None
            result.keywords = []
            member_not_found = True
        elif len(candidates) > 1:
            # 동명이인 — 어느 쪽인지 특정 안 되니 화면2에서 사용자가 직접 고르게 한다.
            result.sufficient = False
            result.member_name = None
            result.legislator_id = None
            result.member_candidates = candidates
        else:
            result.legislator_id = candidates[0].legislator_id
    elif llm_result.committee_guess:
        # 인물 없이 정책만 있는 질문 — "검색축은 인물"이라는 원칙에 따라 정책 키워드
        # 대신 그 정책을 다루는 상임위 소속 실제 의원을 추천 카드로 보여준다.
        # committee_guess 자체는 DB에 없을 수도 있으니(오타/변형 표기), 매칭되는
        # 의원이 없으면 원래 하던 대로 정책 키워드로 폴백한다.
        committee_matches = _find_members_by_committee(llm_result.committee_guess)
        if committee_matches:
            result.member_candidates = committee_matches
            result.keywords = []

    if not result.member_name and not result.member_candidates and llm_result.committee_guess:
        # 인물이 없거나(정책만 있는 질문) DB에 없는 인물이면, "검색축은 인물"이라는
        # 원칙에 따라 정책 키워드 대신 그 정책을 다루는 상임위 소속 실제 의원을
        # 추천 카드로 보여준다. committee_guess 자체는 DB에 없을 수도 있으니
        # (오타/변형 표기), 매칭되는 의원이 없으면 정책 키워드로 폴백한다 —
        # 단, member_not_found(실존하지 않는 인물을 지칭한 경우)에는 이미 위에서
        # keywords를 비웠으니 그대로 빈 채로 둔다(엉뚱한 사람 이름에 낚여
        # 상임위를 추천하는 것도 부적절하므로 committee_guess 자체를 쓰지 않는다).
        if not member_not_found:
            committee_matches = _find_members_by_committee(llm_result.committee_guess)
            if committee_matches:
                result.member_candidates = committee_matches
                result.keywords = []

    # keywords는 "인물은 확정됐고 주제만 좁히면 되는" 경우에만 의미가 있다.
    # 프롬프트로 "member_name 없으면 keywords 비워두라"고 지시했지만 LLM이
    # 그 지시를 어기고 member_name=null인데 keywords를 채우는 경우가 실측으로
    # 확인됐다(예: "윤석열이 뭐하는 사람이야" -> member_name=null인데
    # keywords로 "국정운영" 등 채워 넣음 — 위의 member_not_found 분기가 막는
    # 경로와 달리 애초에 LLM이 member_name을 null로 낸 경우라 그 분기를 타지
    # 않고 새어나갔다). LLM 판단에 기대지 않고 여기서 무조건 강제한다.
    if not result.member_name:
        result.keywords = []

    return result


# ---- POST /api/query ----

class QueryRequest(BaseModel):
    question: str
    member_name: str | None = None
    legislator_id: str | None = None
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


# event.author(ADK가 각 leaf LlmAgent 실행 시 이벤트에 채우는 이름) ->
# 프론트에 보여줄 "지금 시작함" 문구. LoopAgent/ParallelAgent/SequentialAgent
# 자체는 이벤트를 발생시키지 않고 내부 leaf 에이전트만 author로 잡힌다
# (프로파일링 스크립트로 실측 확인 — agent/subagent/*.py의 name= 값과 일치).
_START_LABELS: dict[str, str] = {
    "query_processing": "질문 분석 중",
    "speech_agent": "회의록 발언 조회 중",
    "action_agent": "법안·표결 기록 조회 중",
    "context_agent": "관련 뉴스 조회 중",
    "merge": "근거 종합 중",
    "guardrail": "답변 검증 중",
}

# verifier 3종(speech/action/context)의 착수 문구. 원래는 이 문구를 아예 안
# 내기로 설계했었다("검증 중"이라는 내부 사정까지 노출할 실익이 적다는
# 판단) — 그런데 실측해보니 소스 에이전트의 "N건 확인" 완료 문구가 뜬 뒤에도
# verifier가 조용히 수 초(실측: context_verifier 6.14초) 더 실행되다가 끝나야
# fetch 전체가 끝나서, 그 사이 화면에 아무 신호가 없어 "레인이 다 끝났는데
# 근거 종합 전까지 멈춘 것 같다"는 피드백으로 이어졌다. 완료 문구(_START_LABELS와
# 별개로 관리되는 attempt_pending과는 무관하게) 없이 착수 문구만 짧게 내서
# "지금 확인하는 중"이라는 최소한의 신호만 준다 — 재검색 루프에서 소스
# 에이전트가 다시 뜨면 자연히 다음 문구로 교체되므로 verifier 자체의 완료
# 시점을 별도로 추적할 필요는 없다.
_VERIFIER_START_LABELS: dict[str, str] = {
    "speech_verifier": "회의록 발언 검증 중",
    "action_verifier": "법안·표결 기록 검증 중",
    "context_verifier": "관련 뉴스 검증 중",
}

# verifier가 통과했을 때 낼 완료 문구. "조회"는 소스 에이전트, "검증"은
# verifier로 동사가 분리돼 있으니("verify는 검증이라고 하는 게" 피드백),
# 검증 통과 시에도 소스의 조회 완료 문구를 재활용하지 않고 "OO 검증 완료"를
# 따로 낸다 — 조회와 검증이 각자 자기만의 착수/완료 쌍을 갖게 된다
# (조회 중 -> 조회 완료, 검증 중 -> 검증 완료).
_VERIFIER_COMPLETE_LABELS: dict[str, str] = {
    "speech_verifier": "회의록 발언 검증 완료",
    "action_verifier": "법안·표결 기록 검증 완료",
    "context_verifier": "관련 뉴스 검증 완료",
}

# verifier가 exit_loop()을 호출해 검증을 통과시키면(=재검색 없이 그대로
# 끝) 그 소스 에이전트는 다시 실행되지 않아 output_key 이벤트(speech_info/
# action_info/context_info)가 다시 발생하지 않는다 — "검증 중" 착수 문구
# 뒤에 아무 신호도 안 와서 로그가 그 상태로 멈춰 보이는 버그를 실측으로
# 확인했다("뉴스는 왜 조회 중에서 끝나"라는 피드백. 최종 응답 자체는 정상이라
# 순수 진행 로그 표시 문제였다). ADK가 exit_loop 도구 실행 결과를 반영한
# 이벤트에서 event.actions.escalate=True를 세팅하는 걸 실측으로 확인했다
# (실제로는 exit_loop 호출 이벤트 다음 이벤트에서 escalate=True가 뜬다) —
# 이 신호로 "검증 통과로 루프가 끝났다"를 감지해 _VERIFIER_COMPLETE_LABELS를
# yield한다.
_VERIFIER_TO_STAGE: dict[str, str] = {
    "speech_verifier": "speech",
    "action_verifier": "action",
    "context_verifier": "context",
}

# speech_verified_loop/action_verified_loop(max_iterations=2)가 재검색을
# 다 써버려도 끝까지 근거 불분명 판정이면 exit_loop()이 한 번도 안 불려
# escalate=True가 안 뜬 채 루프가 조용히 끝난다 — 이 경우 화면엔 "검증
# 완료"가 아니라 "검증 실패로 재검색 종료"임을 알려야 "검증 중"에서 로그가
# 멈춘 것처럼 보이는 문제가 안 생긴다(아래 stage_verified 주석 참고).
# context는 max_iterations=1로 재검색 자체가 없어 이 경로를 안 탄다.
_VERIFIER_EXHAUSTED_LABELS: dict[str, str] = {
    "speech": "회의록 발언 검증 보류 (근거 불충분)",
    "action": "법안·표결 기록 검증 보류 (근거 불충분)",
}


def _count_json_evidence(info_text: str | None) -> int | None:
    """speech_agent/action_agent가 output_key로 내는 {"evidence": [...]}
    텍스트에서 건수를 센다. 파싱 실패(마크다운 코드펜스, 형식 오류 등)나
    evidence 키가 없으면 None — 호출부가 "몇 건" 문구 대신 "조회 완료"로
    폴백하게 한다(사용자에게 잘못된 숫자를 보여주는 것보다 안전).
    """
    if not info_text:
        return None
    text = info_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    evidence = parsed.get("evidence") if isinstance(parsed, dict) else None
    return len(evidence) if isinstance(evidence, list) else None


async def _run_agent_stream(
    question: str,
    member_name: str | None,
    keyword: str | None,
    legislator_id: str | None = None,
    diagnostics_out: dict | None = None,
):
    """root_agent를 한 번 실행하며 (진행 문구, 최종 AgentResponse|None)을 순서대로 yield한다.

    각 서브에이전트가 시작될 때 "OO 조회 중..." 착수 문구를, 그 소스 에이전트가
    실제로 결과를 내면(output_key 텍스트가 담긴 이벤트) "OO N건 찾음" 완료
    문구를 추가로 내보낸다 — 프론트에 "무엇을 실행 중인지"뿐 아니라 "무엇을
    찾았는지"까지 보여주기 위함. 파이프라인이 끝나면 마지막으로
    (None, AgentResponse)를 한 번 낸다 — 호출부가 "진행 중" 이벤트와 "완료"
    이벤트를 같은 루프에서 구분해 처리하게 하기 위한 2-튜플 프로토콜.
    query_processing이 자유 텍스트 하나만 받으므로, member_name/keyword를
    질문 문장에 조합해 넣는다(요청 스키마 question/member_name/keyword는
    아직 agent 쪽에 구조화된 입력으로 반영되지 않은 상태 — CLAUDE.md
    "다음 할 일" 참고).
    """
    parts = [question]
    if member_name:
        parts.append(f"(대상 의원: {member_name})")
    if legislator_id:
        parts.append(f"(확정된 의원 ID: {legislator_id})")
    if keyword:
        parts.append(f"(키워드: {keyword})")
    combined_question = " ".join(parts)

    session_id = str(uuid.uuid4())
    await _session_service.create_session(
        app_name="politory_agent",
        user_id="backend",
        session_id=session_id,
        state={"requested_legislator_id": legislator_id or ""},
    )

    # author(action_agent/speech_agent/context_agent)별 착수 횟수. 재검색
    # 루프(LoopAgent, max_iterations=2)로 같은 소스 에이전트가 여러 번 다시
    # 실행될 수 있는데, 기존에는 착수 문구를 author당 최초 1회만 내보내
    # "지금 몇 번째 시도인지" 알 수 없었다 — "발언이나 법안은 두 번씩 하는데
    # 차수를 적어달라"는 피드백.
    #
    # author가 매번 딱 한 이벤트만 내는 게 아니다 — 도구 호출/도구 응답/
    # 최종 텍스트 등 하나의 실제 시도 안에서 같은 author로 여러 이벤트가
    # 온다(실측: action_agent 이벤트가 10회 넘게 잡힘, 근데 실제 재검색은
    # max_iterations=2가 한계). 그래서 "이 author 이벤트를 볼 때마다
    # 카운터+1"로 짰다가 시도 하나가 여러 번 카운트되는 버그가 났다 — 대신
    # "이 시도가 완료(output_key 텍스트 이벤트)되기 전까지는 착수 이벤트를
    # 다시 봐도 새 시도로 세지 않는다"로 고쳤다. pending 플래그가 그 상태를
    # 추적한다: 착수 시 True로 세팅하고 그동안의 추가 착수 이벤트는 무시,
    # 완료 이벤트가 오면 False로 되돌려 다음 착수를 새 시도로 받아들인다.
    attempt_count: dict[str, int] = {}
    attempt_pending: dict[str, bool] = {}
    # context_search_urls는 context_agent의 최종 출력이 아니라 도구 호출
    # 직후(중간 이벤트)에 잡히는 값이라, "완료" 판정과는 별개로 최신값만
    # 계속 추적해뒀다가 진짜 완료 시점(context_info 이벤트)에 건수만
    # 꺼내 쓴다 — 아래 완료 분기 주석 참고.
    latest_context_url_count = 0
    # stage("action"/"speech"/"context") -> 그 stage가 마지막으로 낸 완료
    # 문구. verifier가 exit_loop()으로 통과시켜 소스 에이전트가 재실행되지
    # 않는 경우(_VERIFIER_TO_STAGE 주석 참고), 이 저장값을 그대로 다시
    # yield해 "확인 중"에서 멈춰 보이던 로그를 마무리 짓는다.
    latest_stage_complete_label: dict[str, str] = {}
    # stage("action"/"speech"/"context") -> 그 stage의 verifier가 이미
    # "검증 완료/종료" 문구를 냈는지. LoopAgent(max_iterations=2인
    # speech/action)가 재검색을 다 써버려도 exit_loop()으로 통과하지
    # 못하면(=끝까지 근거 불분명) escalate가 한 번도 True로 안 뜬 채 루프가
    # 조용히 끝난다 — ADK가 "max_iterations 도달로 종료"라는 별도 이벤트를
    # 안 내기 때문에, 이 경우를 감지할 지점이 원래 없었다("speech랑
    # action쪽은 검증 로직이 있을 텐데 왜 검증 완료가 없지?"라는 피드백으로
    # 발견 — "검증 중"에서 로그가 멈춘 것처럼 보였다). fetch(ParallelAgent)
    # 전체가 끝나야 merge가 시작되므로, merge 착수 시점에 아직 여기 기록이
    # 없는 stage는 "검증 실패로 재검색을 다 쓰고 끝났다"로 확정할 수 있다.
    stage_verified: set[str] = set()
    # "근거 종합 중"(merge 착수 문구)을 실제 merge author 이벤트로 내면
    # 너무 늦다 — merge는 도구 호출 없이 순수 LLM 추론(구조화 출력)만
    # 하므로 ADK가 착수~완료 사이 이벤트를 하나도 안 낸다. 실측: fetch
    # 전체가 실제로 끝난 시점(가장 늦은 검증 루프의 종료 확인 시점)부터
    # merge가 draft_response를 실제로 내놓는 시점까지 12.21초 공백이
    # 있었다 — 즉 merge author의 첫(=유일한) 이벤트가 사실상 "이미 다 끝난
    # 순간"이라 "근거 종합 중"이 뜨자마자 바로 다음 문구로 넘어가는 것처럼
    # 보인다("세 병렬처리 다 끝나고 병합 중이 왜 늦게 떠"라는 피드백으로
    # 발견 — context_verifier에서 겪었던 것과 정확히 같은 패턴).
    # route(어떤 stage가 실행되는지)를 알고 있으면, 그 stage들이 전부
    # "끝났다"고 확정되는 시점(완료/검증완료/검증보류/애초에 라우팅
    # 안 됨)에 merge의 실제 이벤트를 기다리지 않고 바로 착수 문구를 낸다.
    route_stages: dict[str, bool] | None = None
    merge_started = False
    async for event in _agent_runner.run_async(
        user_id="backend",
        session_id=session_id,
        new_message=genai_types.Content(
            role="user", parts=[genai_types.Part(text=combined_question)]
        ),
    ):
        # 최종 결과 자체는 기존과 동일하게 아래에서 세션 state를 직접 읽는다
        # (event.content로 못 읽는 이유는 이전 주석 참고 — 그 제약은 그대로
        # 유지). 여기서는 이벤트를 진행 문구 트리거로만 쓴다.
        author = getattr(event, "author", None)
        delta = event.actions.state_delta if event.actions else {}

        # 착수 문구: _START_LABELS에 매핑된 author(=6개 leaf 에이전트)만
        # 낸다. attempt_pending이 이미 True면(이 시도가 아직 완료 안 됨)
        # 같은 시도 안에서 온 중복 착수 이벤트이므로 무시한다 — 안 그러면
        # 시도 하나가 여러 줄로 찍힌다.
        if author in _START_LABELS and not attempt_pending.get(author, False):
            attempt_count[author] = attempt_count.get(author, 0) + 1
            attempt_pending[author] = True
            label = _START_LABELS[author]
            # context_agent는 차수를 안 붙인다 — instruction상 search_news
            # 호출 자체는 "딱 한 번만"이라 명시돼 있어(agent/subagent/sources/
            # context_agent.py) 사용자에게는 "뉴스는 한 번만 검색한다"는 게
            # 맞는 이해다. 그런데도 (N차)가 붙으면 재검색을 여러 번 하는
            # 것처럼 오해를 준다 — 실제로 여기 찍히는 반복은 context_verifier가
            # 가짜 URL 등으로 불통과 판정해 LoopAgent가 context_agent 전체를
            # 다시 실행시키는 것(도구 호출은 여전히 매 실행마다 1회)이라,
            # "검색을 여러 번 한다"는 인상 자체가 부정확하다.
            if author in ("speech_agent", "action_agent"):
                label = f"{label} ({attempt_count[author]}차)"
            # merge는 조기 발행(아래 escalate 분기의 _maybe_start_merge)이
            # 이미 착수 문구를 냈을 수 있다 — 그 경우 attempt_pending["merge"]가
            # 이미 True라 이 if 블록 자체에 안 들어온다(바깥 조건 참고).
            # 즉 이 yield는 "조기 발행이 아직 안 된 채로 merge 이벤트가
            # 먼저 온 경우"(=한 stage 이상이 exhausted로 끝난 경우, 아래
            # merge_started 주석 참고)에만 실행된다.
            yield label, None
            if author == "merge":
                merge_started = True
                # fetch(ParallelAgent) 전체가 끝나야 merge가 시작된다 — 즉
                # 이 시점에 stage_verified에 없는 stage(_VERIFIER_EXHAUSTED_
                # LABELS에 있는 speech/action만 해당)는 재검색을
                # max_iterations까지 다 쓰고도 끝내 근거 불충분 판정을
                # 벗어나지 못한 채 조용히 끝난 것이다(escalate가 한 번도
                # True로 안 뜸 — 위 _VERIFIER_EXHAUSTED_LABELS 주석 참고).
                # "검증 중"에서 로그가 멈춘 것처럼 보이지 않도록 보정
                # 문구를 낸다. 이건 조기 발행 여부와 무관하게 실제 merge
                # 이벤트에서 항상 실행돼야 한다 — exhausted는 사전에 알 수
                # 없어 조기 발행 대상이 아니기 때문이다(아래 escalate 분기
                # 주석 참고).
                for stage, exhausted_label in _VERIFIER_EXHAUSTED_LABELS.items():
                    if stage not in stage_verified and stage in latest_stage_complete_label:
                        yield exhausted_label, None
        # (verifier 착수 문구는 더 이상 여기서 안 낸다 — 아래 소스 완료
        # 분기 참고. LoopAgent(sub_agents=[소스_agent, verifier])는 순서
        # 실행이라 소스 agent가 output_key를 낸 직후 곧바로 verifier가
        # 시작되는데, ADK는 LLM이 도구 호출 없이 판단만 하는 동안 이벤트를
        # 안 낸다 — 그래서 verifier의 첫 이벤트가 사실상 "판단이 이미 끝나
        # exit_loop을 부르는 순간"이라, 여기서 착수 문구를 내면 그 즉시
        # 완료 문구가 뒤따라 "검증 중"이 실측 5.91초짜리 검증인데도 화면엔
        # 0.3초만 떠 있다가 사라지는 문제가 있었다("뉴스 검증할 때 검증
        # 중은 매우 짧던데" -> "context verifier가 무겁지 않냐" -> "뉴스
        # 조회 한 다음 곧바로 context_verifier 타는거야?"로 이어진 피드백
        # 끝에 실측으로 확인). 대신 소스 완료 시점(=verifier가 실제로
        # 시작하는 시점)에 착수 문구를 함께 낸다.

        if "route" in delta:
            # query_processing의 완료 시점(output_key="route"가 담긴 이벤트,
            # agent/subagent/query_processing.py 참고). 원래는 착수 문구
            # ("질문 분석 중")만 있고 완료 문구가 없어서, fetch(병렬 레인)가
            # 이미 다 진행 중인데도 프론트 로그에는 "질문 분석 중"이 그대로
            # 남아 있었다 — "질문 분석 완료했으면 중에서 완료로"라는 피드백.
            # query_processing은 재시도 루프가 없는 1회성 단계라 차수는
            # 안 붙인다.
            yield "질문 분석 완료", None
            # route_stages: {"action": bool, "speech": bool, "context": bool}
            # 그대로 저장해둔다 — merge 조기 착수 판정(아래 _maybe_start_merge)에
            # "이 stage가 애초에 실행되는지"를 확인하는 데 쓴다. False인
            # stage는 fetch.py의 skip_if_not_routed로 아예 안 돌아서 진행
            # 이벤트 자체가 없으므로, stage_finished에 못 들어가도 "안
            # 끝난 것"으로 착각하면 안 된다.
            route_stages = dict(delta["route"]) if delta["route"] else {}

        # 완료(건수) 문구: 소스 에이전트가 output_key 텍스트를 낸 이벤트에서만
        # 판단한다 — 도구 호출/응답 등 중간 이벤트에는 이 키가 없다(실측
        # 확인: agent/subagent/sources/*.py의 output_key와 동일한 키가
        # state_delta에 잡히는 이벤트가 그 에이전트의 마지막 이벤트).
        # 재검색 루프로 여러 번 결과를 낼 수 있으므로 착수 문구와 달리
        # 매번 내보낸다 — 재시도 후 건수가 바뀌었을 수 있어 최신값을 보여준다.
        # 차수는 바로 위에서 센 attempt_count를 그대로 붙여 착수 줄과 짝을
        # 맞추고, attempt_pending을 내려 다음 착수를 새 시도로 받아들이게 한다.
        # author를 speech_agent/action_agent로 한정한다. action_verifier/
        # speech_verifier의 before_agent_callback(source_verification.py의
        # _check_action_against_mcp/_check_speech_against_mcp)이 검증 실패
        # 시 callback_context.state["action_info"/"speech_info"]를 직접
        # 덮어쓰는데(빈 evidence로 초기화), 이것도 action_info/speech_info
        # state_delta를 가진 이벤트를 만들어낸다 — 다만 이때 author는
        # action_verifier/speech_verifier다. author 체크 없이 델타 키만
        # 보면 검증 단계에서도 "조회 완료 + 검증 착수" 문구가 또 한 번
        # 찍히는 중복이 실측으로 확인됐다(검증 착수 문구를 조회 완료
        # 시점에서 내기 시작한 뒤 새로 드러남 — 이전엔 완료 문구끼리만
        # 연달아 왔었고 App.jsx가 "직전과 동일하면 스킵"으로 우연히
        # 가려주고 있었다).
        #
        # attempt_pending 가드(추가): author를 speech_agent/action_agent로
        # 한정해도 중복이 실측으로 또 발견됐다 — 소스 에이전트 하나의 "한
        # 번의 실제 시도"가 도구 호출/응답/최종 텍스트 등 여러 이벤트로
        # 쪼개져 오는데(위쪽 착수 문구 주석의 "실측: action_agent 이벤트가
        # 10회 넘게 잡힘" 참고), 그중 speech_info/action_info state_delta를
        # 가진 이벤트가 같은 시도 안에서 두 번 온 사례가 실제 브라우저
        # 콘솔로 확인됐다(예: "법안·표결 기록 0건 조회 (1차)"가 연달아 두
        # 번 발행됨). 이전엔 완료 문구만 냈으니 두 번째 완료 문구가 dedup에
        # 걸려 화면엔 안 보였지만, 지금은 그 사이에 "검증 중" 착수 문구를
        # 끼워 넣으므로 두 번째 완료 문구가 splitProgressLog에서 그 "검증
        # 중"을 착수 문구로 오인해 pop해버려 화면에서 사라지는 버그로
        # 이어졌다. attempt_pending이 이미 False(=이번 시도의 완료를 이미
        # 한 번 처리함)면 재실행하지 않는다 — True일 때만(=이번 시도의
        # 첫 완료 이벤트) 문구를 내고 False로 내린다.
        if "speech_info" in delta and author == "speech_agent" and attempt_pending.get("speech_agent"):
            n = _count_json_evidence(delta["speech_info"])
            attempt_pending["speech_agent"] = False
            attempt = attempt_count.get("speech_agent", 1)
            # "확인"은 verifier(검증) 전용 동사로 남기고, 소스 에이전트의
            # 조회 완료는 "조회"로 부른다 — 둘 다 "확인"을 쓰면 "N건 확인"
            # (조회 완료)과 "확인 중"(검증 착수)이 같은 단어로 겹쳐 헷갈린다는
            # 피드백("확인은 verify 아니야?"). _VERIFIER_START_LABELS도 같은
            # 이유로 "검증 중"으로 통일했다.
            base = f"회의록 발언 {n}건 조회" if n is not None else "회의록 조회 완료"
            label = f"{base} ({attempt}차)"
            latest_stage_complete_label["speech"] = label
            yield label, None
            # LoopAgent(sub_agents=[speech_agent, speech_verifier])는 순서
            # 실행이라 speech_info가 나온 직후가 곧 speech_verifier의 실제
            # 착수 시점이다(위 481번째 줄 부근 주석 참고) — 여기서 바로
            # 착수 문구를 낸다.
            yield _VERIFIER_START_LABELS["speech_verifier"], None
        elif "action_info" in delta and author == "action_agent" and attempt_pending.get("action_agent"):
            n = _count_json_evidence(delta["action_info"])
            attempt_pending["action_agent"] = False
            attempt = attempt_count.get("action_agent", 1)
            base = f"법안·표결 기록 {n}건 조회" if n is not None else "법안·표결 조회 완료"
            label = f"{base} ({attempt}차)"
            latest_stage_complete_label["action"] = label
            yield label, None
            yield _VERIFIER_START_LABELS["action_verifier"], None
        if "context_search_urls" in delta:
            # context_search_urls는 context_agent의 최종 출력이 아니라
            # search_news 도구 응답 직후(중간 이벤트)에 잡히는 값이다 — 이걸
            # 완료 판정에 썼다가 버그가 났다: context_agent가 아직 텍스트
            # 생성 중인데도 "완료"로 처리돼 attempt_pending이 너무 일찍
            # 풀리고, 뒤이어 오는 (같은 실행의) 다른 이벤트가 "새 착수"로
            # 오인돼 "관련 뉴스 조회 중"이 한 실행 안에서 두 번 찍히는 게
            # 실측됐다(사용자 피드백으로 발견). 여기서는 최신 건수만
            # 기록해두고, 완료 문구 자체는 아래 context_info 분기(진짜
            # 최종 출력)에서만 낸다.
            latest_context_url_count = len(delta["context_search_urls"])
        # context_verifier의 before_agent_callback(_make_url_check_callback)은
        # 검증 실패 시 state["context_info"]를 직접 덮어쓰지 않고 Content를
        # 반환해 output_key(context_retry_hint)로만 저장한다(위 action_info/
        # speech_info와 달리 이 필드는 안전) — 그래도 author를 명시적으로
        # 맞춰 위와 같은 패턴을 유지한다.
        if "context_info" in delta and author == "context_agent" and attempt_pending.get("context_agent"):
            # context_agent의 진짜 완료 시점(output_key가 담긴 이벤트) —
            # action_info/speech_info와 같은 패턴. context_agent는 evidence
            # 스키마가 아니라 자유 텍스트로 응답하므로 건수를 셀 수 없어,
            # 위에서 추적해둔 latest_context_url_count(search_news가 실제로
            # 반환한 후보 기사 수)를 대신 보여준다 — "채택된" 건수가 아니라
            # "훑어본" 후보 수라는 차이가 있지만, 사용자에게는 "뉴스를 몇 건
            # 살펴봤는지"로 충분히 유의미하다. 차수는 안 붙인다 — 착수 문구
            # 분기 주석 참고(search_news 자체는 매번 1회만 호출됨).
            # attempt_pending 가드: speech_info/action_info와 같은 이유
            # (위 538번째 줄 주석 참고) — 같은 시도 안에서 context_info
            # state_delta가 여러 번 잡히는 경우를 대비한다.
            attempt_pending["context_agent"] = False
            # "확인"은 verifier(검증) 전용 동사로 남긴다 — 위 speech_info/
            # action_info 분기 주석 참고.
            label = f"관련 뉴스 {latest_context_url_count}건 조회"
            latest_stage_complete_label["context"] = label
            yield label, None
            yield _VERIFIER_START_LABELS["context_verifier"], None

        # verifier가 exit_loop()으로 검증을 통과시켜 루프가 끝나면(=그
        # stage의 소스 에이전트가 다시 실행되지 않음) event.actions.escalate
        # 가 True로 뜬다(실측 확인 — _VERIFIER_TO_STAGE 위 주석 참고). 이
        # 시점에 "OO 검증 완료"를 yield해 "검증 중"에서 로그가 멈춰 보이던
        # 문제를 해소한다. latest_stage_complete_label에 그 stage 값이
        # 아직 없으면(소스 에이전트가 한 번도 완료 문구를 못 낸 이례적인
        # 경우) 검증 완료를 말할 근거 자체가 없으니 건너뛴다.
        if author in _VERIFIER_TO_STAGE and getattr(event.actions, "escalate", False):
            stage = _VERIFIER_TO_STAGE[author]
            if stage in latest_stage_complete_label:
                yield _VERIFIER_COMPLETE_LABELS[author], None
                # 이 stage는 검증을 통과해 끝났다는 걸 표시해둔다 — merge
                # 착수 시점에 아직 이 표시가 없는 stage만 "재검색을 다 쓰고
                # 끝내 통과 못함"으로 보정 처리한다(위 착수 분기의 merge
                # 처리, _VERIFIER_EXHAUSTED_LABELS 주석 참고).
                stage_verified.add(stage)

                # merge 착수 조기 발행: route로 실행되는 모든 stage가 지금
                # 이 순간 전부 escalate(검증 통과)로 끝났다면, fetch
                # 전체가 실제로 끝난 시점을 100% 확실하게 알 수 있다 —
                # 이게 곧 merge가 시작되는 시점(SequentialAgent가 fetch
                # 완전 종료 후에만 다음으로 넘어감)이므로, merge의 실제
                # 이벤트(도구 호출 없는 순수 LLM 추론이라 착수 이벤트 자체가
                # 없음 — 위 route_stages 주석 참고)를 기다리지 않고 바로
                # "근거 종합 중"을 낸다. 재검색을 다 쓰고도 통과 못한 채
                # 조용히 끝나는 경우(exhausted)는 그 사실 자체를 사전에
                # 알려주는 이벤트가 없어 조기 발행 대상에서 제외한다 —
                # 그 경우는 착수 분기의 merge 처리(위)가 실제 merge
                # 이벤트가 왔을 때 그대로 처리한다(사실과 다른 타이밍을
                # 지어내지 않기 위해, 알 수 없는 채로 둔다).
                if (
                    route_stages is not None
                    and not merge_started
                    and not attempt_pending.get("merge", False)
                    and all(
                        stage_key in stage_verified
                        for stage_key, routed in route_stages.items()
                        if routed
                    )
                ):
                    attempt_pending["merge"] = True
                    merge_started = True
                    yield _START_LABELS["merge"], None

    # guardrail이 직접 생성한 이벤트(event.content)가 아니라 실행이 끝난 뒤
    # 세션을 다시 조회해서 state["final_answer"]를 읽는다. guardrail의
    # after_agent_callback(_verify_excerpts, _resolve_footnotes)이 state를
    # 고쳐도 반환값은 None이라, ADK가 그 상태 변경만으로 만드는 이벤트는
    # content=None이다(google/adk/agents/base_agent.py의
    # _handle_after_agent_callback 확인) — event.content로 걸러 읽으면 그
    # 상태 변경 이전의 guardrail 원본 LLM 출력을 그대로 쓰게 되어, excerpt
    # 정합성 검증과 각주 재계산이 실제 API 응답에 반영되지 않는 버그가 있었다.
    # 세션을 다시 읽으면 콜백이 반영된 최종 state를 확실히 얻는다.
    session = await _session_service.get_session(
        app_name="politory_agent", user_id="backend", session_id=session_id
    )
    state = session.state if session else {}
    final_state_answer = state.get("final_answer")
    route = state.get("route", {}) or {}
    if hasattr(route, "model_dump"):
        route = route.model_dump()
    diagnostics = {
        "route": dict(route) if isinstance(route, dict) else {},
        "executed_agents": {
            "speech": bool(state.get("speech_tool_called", False)),
            "action": bool(state.get("action_tool_called", False)),
            "context": bool(state.get("context_tool_called", False)),
        },
    }
    if diagnostics_out is not None:
        diagnostics_out.clear()
        diagnostics_out.update(diagnostics)

    if final_state_answer is None:
        # 파이프라인이 끝까지 돌았는데 guardrail 출력이 안 잡힌 비정상 케이스 —
        # 사용자에게는 원인불명 500 대신 "답변을 만들지 못했다"로 명확히 알린다.
        yield None, AgentResponse(answer="답변을 생성하지 못했습니다. 다시 시도해주세요.", sources=[])
        return

    final_answer = (
        final_state_answer
        if isinstance(final_state_answer, AgentResponse)
        else AgentResponse.model_validate(final_state_answer)
    )

    # merge instruction이 answer는 "시간순으로 나열하라"고 지시하지만 sources
    # 배열 자체의 정렬은 강제하지 않아서, 실제로 날짜가 뒤섞여 나오는 걸
    # 확인했다(예: 08-20, 08-11, 08-12, 08-20 순). LLM 출력 순서에 기대지 않고
    # 여기서 date 기준으로 확실하게 정렬한다.
    #
    # 이 정렬을 각주 번호 계산(_resolve_footnote_numbers)보다 반드시 먼저
    # 해야 한다 — evidence_synthesis.py의 _resolve_footnotes는 더 이상 sources를
    # 재정렬하거나 번호를 확정하지 않는다(그 단계에서 확정해버리면, 이후 여기서
    # sources만 재정렬될 때 answer에 이미 박힌 [1][2][3] 텍스트와 배열 순서가
    # 다시 어긋나는 버그가 배포에서 실측됐다 — evidence_synthesis.py 주석 참고).
    # "sources를 어떤 순서로 배치할지"와 "그 순서로 번호를 매기는 것"을 이
    # 함수 안에서 순서대로 실행해 정렬 기준이 한 곳에만 있게 한다.
    final_answer.sources = sorted(final_answer.sources, key=_source_sort_key)
    _resolve_footnote_numbers(final_answer)
    yield None, final_answer


async def _run_agent_with_diagnostics(
    question: str,
    member_name: str | None,
    keyword: str | None,
    legislator_id: str | None = None,
) -> tuple[AgentResponse, dict]:
    """평가용 실행 경로. API 응답과 별도로 라우팅·도구 호출 정보를 반환한다."""
    final_answer: AgentResponse | None = None
    diagnostics: dict = {}
    async for _label, response in _run_agent_stream(
        question,
        member_name,
        keyword,
        legislator_id,
        diagnostics_out=diagnostics,
    ):
        if response is not None:
            final_answer = response
    assert final_answer is not None
    return final_answer, diagnostics


async def _run_agent(
    question: str,
    member_name: str | None,
    keyword: str | None,
    legislator_id: str | None = None,
) -> AgentResponse:
    """root_agent를 한 번 실행해 evidence_synthesis의 최종 응답(AgentResponse)을 얻는다.

    /api/query(비스트리밍)가 쓰는 얇은 래퍼 — 진행 이벤트는 버리고 마지막
    AgentResponse만 취한다. 진행 이벤트까지 프론트에 전달하려면
    /api/query/stream(_run_agent_stream)을 쓴다.
    """
    final_answer: AgentResponse | None = None
    async for _label, response in _run_agent_stream(
        question, member_name, keyword, legislator_id
    ):
        if response is not None:
            final_answer = response
    assert final_answer is not None  # _run_agent_stream은 항상 마지막에 응답을 낸다
    return final_answer


_REF_MARKER_PATTERN = re.compile(r"⟦(s\d+)⟧")


def _resolve_footnote_numbers(response: AgentResponse) -> None:
    """정렬이 끝난 response.sources 순서를 기준으로 answer의 ⟦sN⟧ 라벨을
    최종 각주 번호 "[1]", "[2]"...로 확정하고, 내부 처리용 필드였던 ref_id를
    응답에서 제거한다(evidence_synthesis.py의 구 _resolve_footnotes가 하던 일을
    여기로 옮긴 것 — 옮긴 이유는 위 호출부 주석 참고). 반드시 sources 정렬
    *이후*에 호출해야 번호와 배열 인덱스가 일치한다.
    """
    index_by_ref: dict[str, int] = {}
    for i, source in enumerate(response.sources, start=1):
        if source.ref_id:
            index_by_ref.setdefault(source.ref_id, i)

    response.answer = _REF_MARKER_PATTERN.sub(
        lambda m: (f"[{index_by_ref[m.group(1)]}]" if m.group(1) in index_by_ref else ""),
        response.answer,
    )

    for source in response.sources:
        source.ref_id = ""


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


def _validate_query_request(request: QueryRequest) -> MemberProfile | None:
    """/api/query와 /api/query/stream이 공유하는 입력 검증.

    통과하면 member_profile(없으면 None)을 반환하고, 실패하면 HTTPException을
    던진다.
    """
    # 프론트는 빈 질문을 제출 전에 막지만(App.jsx classifyAndRoute), 이
    # 엔드포인트는 그 검증을 거치지 않고 직접 호출될 수 있다(예: 화면2에서
    # /api/classify 없이 바로 여기로 오는 경로, 또는 API 직접 호출). 빈
    # question을 그대로 에이전트에 넘기면 query_processing/context_agent가
    # 질문 없이도 아무 정치 이슈나 지어내 답하는 걸 실측으로 확인했다(P-04).
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="질문을 입력해주세요.")

    profile = (
        _get_member_profile(request.member_name, request.party, request.legislator_id)
        if request.member_name
        else None
    )

    # classify를 거치지 않고 화면2에서 자유 입력으로 바로 넘어온 경우까지 대비해,
    # member_name이 있는데 DB에 없으면 여기서도 막는다 — 검색 자체를 진행시키지 않는다.
    if request.member_name and profile is None:
        raise HTTPException(status_code=404, detail="등록된 국회의원이 아닙니다.")

    return profile


@app.post("/api/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    profile = _validate_query_request(request)

    agent_response = await _run_agent(
        request.question,
        request.member_name,
        request.keyword,
        request.legislator_id,
    )

    return QueryResponse(
        answer=agent_response.answer, sources=agent_response.sources, member_profile=profile
    )


# ---- POST /api/query/stream (SSE) ----
#
# /api/query와 같은 파이프라인을 실행하지만, 각 서브에이전트가 실행되는
# 시점마다 진행 문구를 SSE 이벤트로 먼저 흘려보낸다(프론트가 "조회 중..."
# 고정 문구 대신 "회의록 발언 조회 중..." 같은 실제 단계를 보여줄 수 있게).
# fetch 단계는 병렬(speech/action/context 동시 실행)이라 progress 이벤트가
# 발생 순서와 무관하게 완료 순서대로 도착할 수 있다 — 프론트는 이를 단순
# 텍스트 치환으로만 처리하고 진행률 바 등 순서에 의존하는 UI를 만들지 않는다.
#
# 이벤트 타입:
#   {"type": "progress", "label": "..."}  — 진행 문구
#   {"type": "result", "data": QueryResponse}  — 최종 결과(한 번만, 마지막)
#   {"type": "error", "detail": "..."}  — 파이프라인 도중 에러(파이프라인
#     내부 예외 한정 — 입력 검증 실패는 스트림 시작 전에 HTTPException으로
#     즉시 응답하므로 이 이벤트로 오지 않는다)
@app.post("/api/query/stream")
async def query_stream(request: QueryRequest) -> EventSourceResponse:
    # 검증은 스트림을 열기 전에 수행한다 — HTTPException을 그대로 던지면
    # 평소처럼 4xx 상태 코드로 응답하고, SSE 본문 안에 에러를 섞어 200으로
    # 감출 필요가 없다(스트림이 아직 시작 전이라 부작용이 없다).
    profile = _validate_query_request(request)

    async def event_generator():
        try:
            async for label, response in _run_agent_stream(
                request.question,
                request.member_name,
                request.keyword,
                request.legislator_id,
            ):
                if response is not None:
                    yield {
                        "event": "result",
                        "data": QueryResponse(
                            answer=response.answer,
                            sources=response.sources,
                            member_profile=profile,
                        ).model_dump_json(),
                    }
                else:
                    yield {"event": "progress", "data": label}
        except Exception as exc:  # noqa: BLE001 - 스트림 도중 예외를 SSE 이벤트로 알린다
            yield {
                "event": "error",
                "data": f"처리 중 오류가 발생했습니다: {exc}",
            }

    return EventSourceResponse(event_generator())


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
