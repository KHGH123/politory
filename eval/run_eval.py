"""DeepEval 기반 RAG 정량 평가 실행.

평가셋(질문-정답 10~20개)은 eval/qa_dataset.jsonl(JSONL, 한 줄에 객체 하나)에서
읽는다. 기존 question/member_name/keyword/ground_truth 필드에 더해 다음 검증
조건을 선택적으로 지정할 수 있다.

  {
    "id": "speech-01",
    "category": "speech",
    "question": "...",
    "member_name": "...",
    "keyword": "...",
    "ground_truth": null,
    "expected_source_types": ["primary"],
    "min_sources": 1,
    "max_sources": 8,
    "expect_no_evidence": false,
    "forbidden_phrases": ["입장을 바꿨다"]
  }

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
import os
import re
import time
from pathlib import Path
from typing import Literal

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
from pydantic import BaseModel, Field

from backend.main import _run_agent_with_diagnostics
from config import settings

_DATASET_PATH = Path(__file__).parent / "qa_dataset.jsonl"
_REPORT_PATH = Path(__file__).parent / "eval_report.json"

_FOOTNOTE_RE = re.compile(r"\[(\d+)\]")


class EvalRow(BaseModel):
    """평가 문항과 프로젝트 특화 결정적 검증 조건."""

    id: str | None = None
    category: str = "uncategorized"
    evaluation_mode: Literal["quality", "safety"] = "quality"
    question: str
    member_name: str | None = None
    keyword: str | None = None
    ground_truth: str | None = None
    expected_source_types: list[Literal["primary", "secondary"]] = Field(default_factory=list)
    min_sources: int = 0
    max_sources: int | None = None
    expect_no_evidence: bool = False
    forbidden_phrases: list[str] = Field(default_factory=list)
    required_answer_phrases: list[str] = Field(default_factory=list)
    expected_legislator_id: str | None = None
    required_agents: list[Literal["speech", "action", "context"]] = Field(
        default_factory=list
    )


class GeminiJudge(DeepEvalBaseLLM):
    """DeepEval 지표가 채점에 쓰는 judge 모델을 프로젝트가 이미 쓰는 Vertex AI
    Gemini로 맞춘다 — DeepEval 기본 judge는 OpenAI라 이 프로젝트엔 안 맞는다.
    """

    def __init__(self, model: str | None = None):
        self._model_name = model or settings.MODEL
        self._client = genai.Client(
            vertexai=settings.GOOGLE_GENAI_USE_VERTEXAI,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        # DeepEval 버전에 따라 BaseLLM이 model_name을 인스턴스 속성으로
        # 보존하지 않는 경우가 있어, 위의 _model_name을 직접 사용한다.
        super().__init__(model_name=self._model_name)

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
            model=self._model_name, contents=prompt, config=self._build_config(schema)
        )
        if schema is not None:
            return schema.model_validate_json(response.text)
        return response.text or ""

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None, *args, **kwargs):
        response = await self._client.aio.models.generate_content(
            model=self._model_name, contents=prompt, config=self._build_config(schema)
        )
        if schema is not None:
            return schema.model_validate_json(response.text)
        return response.text or ""

    def get_model_name(self, *args, **kwargs) -> str:
        return self._model_name


def _load_dataset() -> list[EvalRow]:
    if not _DATASET_PATH.exists() or _DATASET_PATH.stat().st_size == 0:
        raise FileNotFoundError(
            f"{_DATASET_PATH}가 비어 있습니다. 질문-정답 쌍을 JSONL로 채운 뒤 다시 실행하세요."
        )
    rows: list[EvalRow] = []
    with _DATASET_PATH.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                row = EvalRow.model_validate(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"{_DATASET_PATH}:{line_no} 문항 검증 실패: {exc}") from exc

            if not row.question.strip():
                raise ValueError(f"{_DATASET_PATH}:{line_no} question은 비어 있을 수 없습니다.")
            if row.min_sources < 0:
                raise ValueError(f"{_DATASET_PATH}:{line_no} min_sources는 0 이상이어야 합니다.")
            if row.max_sources is not None and row.max_sources < row.min_sources:
                raise ValueError(
                    f"{_DATASET_PATH}:{line_no} max_sources는 min_sources 이상이어야 합니다."
                )
            if row.expect_no_evidence and row.min_sources > 0:
                raise ValueError(
                    f"{_DATASET_PATH}:{line_no} expect_no_evidence=true이면 "
                    "min_sources는 0이어야 합니다."
                )
            rows.append(row)
    return rows


def _sources_to_contexts(sources, member_name: str | None) -> list[str]:
    """Source(description/excerpt)를 DeepEval의 retrieval_context 문자열 목록으로 변환.

    excerpt/description 둘 다 넘겨도 ContextualRecall이 여전히 0점을 주는 걸
    실측으로 확인했다 — merge의 description은 "~라고 밝혔다"처럼 매 조각마다
    화자 이름을 반복하지 않는다(실제 화면에서는 화면3 상단 프로필 카드로
    "누구 얘기인지"가 이미 확정돼 있어서 각 근거 조각이 매번 이름을
    반복할 필요가 없기 때문). eval은 근거 조각 하나만 뚝 떼어 판정하므로
    그 화면 맥락이 없다 — 실제 사용자가 갖는 것과 같은 맥락(조회 대상
    인물이 누구인지)을 각 조각 앞에 명시해줘야 공정하게 채점된다.
    """
    contexts = []
    for s in sources:
        parts = [p for p in (s.description, s.excerpt) if p]
        if not parts:
            continue
        text = " / ".join(parts)
        who = f"{member_name} 관련 " if member_name else ""
        contexts.append(f"[{s.type}] {who}{s.title} ({s.date or '날짜 미상'}): {text}")
    return contexts


def _run_deterministic_checks(
    row: EvalRow, answer: str, sources, diagnostics: dict | None = None
) -> dict[str, dict]:
    """LLM judge와 별개로 기계적으로 확정할 수 있는 품질 조건을 검사한다."""

    source_count = len(sources)
    checks: dict[str, dict] = {}

    def record(name: str, passed: bool, detail: str) -> None:
        checks[name] = {"passed": passed, "detail": detail}

    record(
        "min_sources",
        source_count >= row.min_sources,
        f"source_count={source_count}, required>={row.min_sources}",
    )

    if row.max_sources is not None:
        record(
            "max_sources",
            source_count <= row.max_sources,
            f"source_count={source_count}, required<={row.max_sources}",
        )

    if row.expect_no_evidence:
        record(
            "expect_no_evidence",
            source_count == 0,
            f"source_count={source_count}, required=0",
        )

    actual_types = {source.type for source in sources}
    expected_types = set(row.expected_source_types)
    if expected_types:
        missing_types = sorted(expected_types - actual_types)
        record(
            "expected_source_types",
            not missing_types,
            "missing=" + (", ".join(missing_types) if missing_types else "none"),
        )

    missing_url_indexes = [i for i, source in enumerate(sources, 1) if not source.url]
    record(
        "source_urls",
        not missing_url_indexes,
        "missing_url_indexes="
        + (", ".join(map(str, missing_url_indexes)) if missing_url_indexes else "none"),
    )

    footnotes = [int(value) for value in _FOOTNOTE_RE.findall(answer)]
    invalid_footnotes = sorted({value for value in footnotes if value < 1 or value > source_count})
    cited_indexes = set(footnotes)
    uncited_sources = [i for i in range(1, source_count + 1) if i not in cited_indexes]
    # 존재하지 않는 출처 번호를 인용하거나, 근거가 있는데 각주가 하나도 없는
    # 답변은 실패로 본다. 다만 에이전트가 유효한 후보 출처를 함께 반환했지만
    # 최종 답변에서 일부만 사용한 경우는 실패시키지 않고 상세 정보로만 남긴다.
    footnotes_passed = not invalid_footnotes and (source_count == 0 or bool(footnotes))
    if source_count == 0:
        footnotes_passed = not footnotes
    record(
        "footnote_source_alignment",
        footnotes_passed,
        f"invalid={invalid_footnotes or 'none'}, uncited_warning={uncited_sources or 'none'}",
    )

    found_phrases = [phrase for phrase in row.forbidden_phrases if phrase and phrase in answer]
    record(
        "forbidden_phrases",
        not found_phrases,
        "found=" + (", ".join(found_phrases) if found_phrases else "none"),
    )

    missing_required_phrases = [
        phrase for phrase in row.required_answer_phrases if phrase and phrase not in answer
    ]
    record(
        "required_answer_phrases",
        not missing_required_phrases,
        "missing="
        + (", ".join(missing_required_phrases) if missing_required_phrases else "none"),
    )

    if row.expected_legislator_id:
        primary_ids = {
            source.legislator_id
            for source in sources
            if source.type == "primary" and source.legislator_id
        }
        record(
            "expected_legislator_id",
            primary_ids == {row.expected_legislator_id},
            f"actual={sorted(primary_ids) or 'none'}, expected={row.expected_legislator_id}",
        )

    if row.required_agents:
        executed_agents = (diagnostics or {}).get("executed_agents", {})
        missing_agents = [
            agent_name
            for agent_name in row.required_agents
            if not executed_agents.get(agent_name, False)
        ]
        record(
            "required_agents_executed",
            not missing_agents,
            "missing=" + (", ".join(missing_agents) if missing_agents else "none"),
        )

    return checks


async def _run_case(row: EvalRow, judge: GeminiJudge) -> dict:
    question = row.question
    started_at = time.perf_counter()
    agent_response, diagnostics = await _run_agent_with_diagnostics(
        question, row.member_name, row.keyword, row.expected_legislator_id
    )
    contexts = _sources_to_contexts(agent_response.sources, row.member_name)

    test_case = LLMTestCase(
        input=question,
        actual_output=agent_response.answer,
        retrieval_context=contexts or ["(검색된 근거 없음)"],
        expected_output=row.ground_truth,
    )

    # 존재하지 않는 의원·근거 없음·프롬프트 인젝션은 정보를 만들지 않는 것이
    # 성공인데 AnswerRelevancy는 이를 0점으로 평가한다. safety 문항은 LLM
    # 관련성 평균에서 제외하고 결정적 조건만 검사한다.
    metrics = []
    if row.evaluation_mode == "quality":
        metrics = [
            FaithfulnessMetric(model=judge, include_reason=True),
            AnswerRelevancyMetric(model=judge, include_reason=True),
        ]
        if row.ground_truth:
            metrics += [
                ContextualPrecisionMetric(model=judge, include_reason=True),
                ContextualRecallMetric(model=judge, include_reason=True),
            ]

    scores: dict[str, dict] = {}
    for metric in metrics:
        await metric.a_measure(test_case)
        scores[metric.__class__.__name__] = {"score": metric.score, "reason": metric.reason}

    deterministic_checks = _run_deterministic_checks(
        row, agent_response.answer, agent_response.sources, diagnostics
    )
    deterministic_passed = all(check["passed"] for check in deterministic_checks.values())

    return {
        "id": row.id,
        "category": row.category,
        "evaluation_mode": row.evaluation_mode,
        "question": question,
        "answer": agent_response.answer,
        "source_count": len(agent_response.sources),
        "diagnostics": diagnostics,
        "scores": scores,
        "deterministic_checks": deterministic_checks,
        "deterministic_passed": deterministic_passed,
        "duration_seconds": round(time.perf_counter() - started_at, 3),
    }


async def _run_all(rows: list[EvalRow]) -> list[dict]:
    judge = GeminiJudge()
    results = []
    start_index = max(1, int(os.getenv("EVAL_START_INDEX", "1")))
    delay_seconds = max(0.0, float(os.getenv("EVAL_DELAY_SECONDS", "0")))
    selected_rows = rows[start_index - 1 :]

    # 재개 실행이면 이전 체크포인트에서 시작 인덱스 앞 문항만 복원한다.
    # 다른 부분 실행 결과나 오래된 문항은 섞지 않는다.
    if start_index > 1 and _REPORT_PATH.exists():
        prior_ids = {row.id for row in rows[: start_index - 1] if row.id}
        try:
            previous_results = json.loads(_REPORT_PATH.read_text(encoding="utf-8"))
            results = [result for result in previous_results if result.get("id") in prior_ids]
        except (json.JSONDecodeError, OSError):
            results = []

    for i, row in enumerate(selected_rows, start_index):
        label = row.id or f"case-{i:02d}"
        print(f"[{i}/{len(rows)}] {label} ({row.category}) - {row.question}")
        try:
            result = await _run_case(row, judge)
        except Exception as exc:
            result = {
                "id": row.id,
                "category": row.category,
                "evaluation_mode": row.evaluation_mode,
                "question": row.question,
                "answer": None,
                "source_count": None,
                "scores": {},
                "deterministic_checks": {},
                "deterministic_passed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "duration_seconds": None,
            }
            print(f"  ERROR: {type(exc).__name__}: {exc}")

        for metric_name, detail in result["scores"].items():
            print(f"  {metric_name}: {detail['score']:.2f}")
        if result["deterministic_checks"]:
            deterministic_status = "PASS" if result["deterministic_passed"] else "FAIL"
            print(f"  deterministic: {deterministic_status}")
            for check_name, detail in result["deterministic_checks"].items():
                if not detail["passed"]:
                    print(f"    - {check_name}: {detail['detail']}")
        results.append(result)
        # 장시간 평가 중 quota/네트워크 오류가 나도 완료된 문항은 보존한다.
        _REPORT_PATH.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if delay_seconds and i < len(rows):
            await asyncio.sleep(delay_seconds)
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
    passed_count = sum(1 for result in results if result["deterministic_passed"])
    print(f"결정적 검증: {passed_count}/{len(results)}건 통과")
    print(f"\n상세 결과: {_REPORT_PATH}")


if __name__ == "__main__":
    main()
