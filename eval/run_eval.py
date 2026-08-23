"""DeepEval 기반 RAG 정량 평가 실행.

평가셋(질문-정답 10~20개)은 D가 구축하는 eval/qa_dataset.jsonl(JSONL, 한 줄에
객체 하나)에서 읽는다. 각 행 스키마:
  {"question": str,               # 필수. 사용자가 입력했을 법한 질문
   "member_name": str|null,       # 선택. 화면2에서 확정된 인물명(=/api/query의 member_name)
   "keyword": str|null,           # 선택. 화면2에서 고른 키워드
   "ground_truth": str|null}      # 선택. 사람이 직접 검증한 정답(있으면 정밀도/재현율까지 채점)

실제 root_agent 파이프라인(backend.main._run_agent, /api/query가 쓰는 것과 동일한
함수)을 그대로 호출해 answer/sources를 얻은 뒤, DeepEval 지표로 채점한다.
Judge 모델은 이 프로젝트가 이미 쓰는 Vertex AI Gemini로 통일한다(DeepEval
기본값은 OpenAI라 별도 API 키가 필요해서 그대로 못 씀).

- Faithfulness, AnswerRelevancy: ground_truth 없이도 채점 가능 — 모든 행에서 계산.
  answer가 검색된 근거(sources)에 실제로 기반하는지/질문과 관련 있는지를 본다.
- ContextualPrecision, ContextualRecall: ground_truth가 있는 행에서만 계산 —
  검색된 근거가 정답을 뒷받침하기에 충분하고 불필요한 근거가 적은지를 본다.

실행: python -m eval.run_eval
결과: eval/eval_report.json에 상세 저장, 터미널에 지표별 평균 출력.

주의: 실제 speech_agent 경로는 MCP 세션 생성 등으로 질문 1건당 수 분이 걸릴 수
있다(2026-08-23 실측 최대 2~3분). 문항 수를 무리하게 늘리면 전체 실행이 오래
걸리니, qa_dataset.jsonl을 늘릴 때 이 점을 감안할 것.
"""
import asyncio
import json
from pathlib import Path

from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    FaithfulnessMetric,
)
from deepeval.models.base_model import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from backend.main import _run_agent
from config import settings

_DATASET_PATH = Path(__file__).parent / "qa_dataset.jsonl"
_REPORT_PATH = Path(__file__).parent / "eval_report.json"


class GeminiJudge(DeepEvalBaseLLM):
    """DeepEval 지표가 채점에 쓰는 judge 모델을 프로젝트가 이미 쓰는 Vertex AI
    Gemini로 맞춘다 — DeepEval 기본 judge는 OpenAI라 이 프로젝트엔 안 맞는다.
    """

    def __init__(self, model: str | None = None):
        self._client = genai.Client(
            vertexai=settings.GOOGLE_GENAI_USE_VERTEXAI,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        super().__init__(model_name=model or settings.MODEL)

    def load_model(self, *args, **kwargs):
        return self._client

    # DeepEval의 각 지표는 판정 결과를 정형화해서 받으려고 프롬프트마다
    # 자체 Pydantic schema(예: Claims, Verdicts)를 함께 넘긴다(schema kwarg).
    # 이 schema가 오면 반드시 그 타입으로 파싱된 객체를 돌려줘야 한다 —
    # 문자열만 돌려주면 지표 코드가 res.claims처럼 속성 접근을 하다 그대로
    # AttributeError로 죽는다(실측 확인). schema가 없으면 그냥 텍스트만 쓰는
    # 호출(예: FaithfulnessMetric의 reason 생성)이므로 원문 텍스트를 돌려준다.
    def _build_config(self, schema: type[BaseModel] | None) -> genai_types.GenerateContentConfig:
        if schema is None:
            return genai_types.GenerateContentConfig()
        return genai_types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema
        )

    def generate(self, prompt: str, schema: type[BaseModel] | None = None, *args, **kwargs):
        response = self._client.models.generate_content(
            model=self.model_name, contents=prompt, config=self._build_config(schema)
        )
        if schema is not None:
            return schema.model_validate_json(response.text)
        return response.text or ""

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None, *args, **kwargs):
        response = await self._client.aio.models.generate_content(
            model=self.model_name, contents=prompt, config=self._build_config(schema)
        )
        if schema is not None:
            return schema.model_validate_json(response.text)
        return response.text or ""

    def get_model_name(self, *args, **kwargs) -> str:
        return self.model_name


def _load_dataset() -> list[dict]:
    if not _DATASET_PATH.exists() or _DATASET_PATH.stat().st_size == 0:
        raise FileNotFoundError(
            f"{_DATASET_PATH}가 비어 있습니다. 질문-정답 쌍을 JSONL로 채운 뒤 다시 실행하세요."
        )
    rows = []
    with _DATASET_PATH.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{_DATASET_PATH}:{line_no} JSON 파싱 실패: {exc}") from exc
    return rows


def _sources_to_contexts(sources) -> list[str]:
    """Source(excerpt/description)를 DeepEval의 retrieval_context 문자열 목록으로 변환.

    excerpt(원문 그대로)가 있으면 우선 쓰고, 없으면 description(요약)으로
    폴백한다 — merge/guardrail이 이미 근거 없는 항목은 걸러내므로 여기선
    있는 그대로 옮기기만 한다.
    """
    contexts = []
    for s in sources:
        text = s.excerpt or s.description or ""
        if not text:
            continue
        contexts.append(f"[{s.type}] {s.title} ({s.date or '날짜 미상'}): {text}")
    return contexts


async def _run_case(row: dict, judge: GeminiJudge) -> dict:
    question = row["question"]
    agent_response = await _run_agent(question, row.get("member_name"), row.get("keyword"))
    contexts = _sources_to_contexts(agent_response.sources)

    test_case = LLMTestCase(
        input=question,
        actual_output=agent_response.answer,
        retrieval_context=contexts or ["(검색된 근거 없음)"],
        expected_output=row.get("ground_truth"),
    )

    metrics = [
        FaithfulnessMetric(model=judge, include_reason=True),
        AnswerRelevancyMetric(model=judge, include_reason=True),
    ]
    if row.get("ground_truth"):
        metrics += [
            ContextualPrecisionMetric(model=judge, include_reason=True),
            ContextualRecallMetric(model=judge, include_reason=True),
        ]

    scores: dict[str, dict] = {}
    for metric in metrics:
        await metric.a_measure(test_case)
        scores[metric.__class__.__name__] = {"score": metric.score, "reason": metric.reason}

    return {
        "question": question,
        "answer": agent_response.answer,
        "source_count": len(agent_response.sources),
        "scores": scores,
    }


async def _run_all(rows: list[dict]) -> list[dict]:
    judge = GeminiJudge()
    results = []
    for i, row in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {row['question']}")
        result = await _run_case(row, judge)
        for metric_name, detail in result["scores"].items():
            print(f"  {metric_name}: {detail['score']:.2f}")
        results.append(result)
    return results


def main() -> None:
    rows = _load_dataset()
    results = asyncio.run(_run_all(rows))

    _REPORT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 요약 ===")
    metric_names = sorted({name for r in results for name in r["scores"]})
    for name in metric_names:
        vals = [r["scores"][name]["score"] for r in results if name in r["scores"]]
        avg = sum(vals) / len(vals) if vals else 0.0
        print(f"{name}: 평균 {avg:.2f} ({len(vals)}건)")
    print(f"\n상세 결과: {_REPORT_PATH}")


if __name__ == "__main__":
    main()
