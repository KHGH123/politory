"""FastAPI 진입점. 레포 루트에서 `uvicorn backend.main:app --reload`로 실행한다."""
from typing import Annotated, Literal, Union

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.cloud import bigquery
from google.genai import types as genai_types
from pydantic import BaseModel, Field

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

_bq_client = bigquery.Client(project=settings.GOOGLE_CLOUD_PROJECT)


def _find_members_by_name(name: str) -> list[MemberCandidate]:
    """이름으로 BigQuery MP 테이블 조회. 동명이인이면 여러 건 반환.

    LLM이 스스로 '이 사람이 진짜 의원인지'를 판단하면 신뢰도가 들쭉날쭉해서
    (예: 유명인은 맞히고 아니면 틀림) DB 조회로 확정한다.
    """
    query = f"""
        SELECT name, party, image_url
        FROM `{settings.GOOGLE_CLOUD_PROJECT}.{settings.BIGQUERY_DATASET}.{settings.BIGQUERY_MEMBERS_TABLE}`
        WHERE name = @name
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("name", "STRING", name)]
    )
    rows = list(_bq_client.query(query, job_config=job_config).result())
    return [
        MemberCandidate(name=row["name"], party=row["party"], image_url=row["image_url"])
        for row in rows
    ]


def _get_member_profile(name: str, party: str | None = None) -> MemberProfile | None:
    """BigQuery MP 테이블에서 약력 카드용 필드를 조회. 없으면 None.

    동명이인이 있을 수 있어 party가 주어지면 그것까지 같이 필터링해 특정한다.
    """
    where = "WHERE name = @name"
    params = [bigquery.ScalarQueryParameter("name", "STRING", name)]
    if party:
        where += " AND party = @party"
        params.append(bigquery.ScalarQueryParameter("party", "STRING", party))

    query = f"""
        SELECT name, age, party, gender, image_url, military, criminal,
               committee, district, term_count, status, sns
        FROM `{settings.GOOGLE_CLOUD_PROJECT}.{settings.BIGQUERY_DATASET}.{settings.BIGQUERY_MEMBERS_TABLE}`
        {where}
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(query_parameters=params)
    rows = list(_bq_client.query(query, job_config=job_config).result())
    if not rows:
        return None

    row = rows[0]
    return MemberProfile(
        name=row["name"],
        age=row["age"],
        party=row["party"],
        gender=row["gender"],
        image_url=row["image_url"],
        military=row["military"],
        criminal=row["criminal"],
        committee=row["committee"],
        district=row["district"],
        term_count=row["term_count"],
        status=row["status"],
        sns=[SnsLink(**link) for link in (row["sns"] or [])],
    )


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


class SpeechSource(BaseModel):
    """발언 원문 — 실선 카드 + 타임라인 점 마커로 렌더링."""

    category: Literal["speech"] = "speech"
    type: Literal["primary", "secondary"]
    meeting: str | None = None
    quote: str
    url: str | None = None
    date: str | None = None


class BillSource(BaseModel):
    """법안 발의/표결 이벤트 — 점선 노트로 렌더링 (타임라인 점 마커 없음)."""

    category: Literal["bill"] = "bill"
    date: str | None = None
    title: str
    proposer: str | None = None
    url: str | None = None


Source = Annotated[Union[SpeechSource, BillSource], Field(discriminator="category")]


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source] = []
    member_profile: MemberProfile | None = None


# TODO: agent/agent.py의 root_agent가 main에 머지되면 answer/sources를 ADK Runner로 교체.
# 구현 방법은 API.md의 "POST /api/query > 구현 방법" 참고 (Runner/InMemorySessionService 패턴).
# member_profile은 에이전트 파이프라인과 무관하게 BigQuery MP 테이블에서 바로 조회한다.
@app.post("/api/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    profile = (
        _get_member_profile(request.member_name, request.party) if request.member_name else None
    )

    # classify를 거치지 않고 화면2에서 자유 입력으로 바로 넘어온 경우까지 대비해,
    # member_name이 있는데 DB에 없으면 여기서도 막는다 — 검색 자체를 진행시키지 않는다.
    if request.member_name and profile is None:
        raise HTTPException(status_code=404, detail="등록된 국회의원이 아닙니다.")

    return QueryResponse(answer="TODO", sources=[], member_profile=profile)
