"""FastAPI 진입점. 레포 루트에서 `uvicorn backend.main:app --reload`로 실행한다."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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


class QueryRequest(BaseModel):
    question: str
    member_name: str | None = None
    keyword: str | None = None


class QueryResponse(BaseModel):
    answer: str
    sources: list[dict] = []


@app.post("/api/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    # TODO(C): agent.orchestrator.run(...) 연결
    return QueryResponse(answer="TODO", sources=[])
