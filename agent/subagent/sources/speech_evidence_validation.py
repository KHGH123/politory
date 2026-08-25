"""Speech Agent 결과를 MCP 원문과 결정적으로 대조하는 순수 함수."""

from __future__ import annotations

import json
from typing import Any


EVIDENCE_FIELDS = (
    "utterance_id",
    "speaker_name",
    "legislator_id",
    "meeting_date",
    "meeting_title",
    "quote",
    "page_start",
    "page_end",
    "source_pdf_url",
)


def parse_speech_info(value: Any) -> dict[str, Any] | None:
    """Agent가 출력한 JSON 객체를 파싱한다. 마크다운 코드펜스도 허용한다."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def collect_tool_utterances(tool_response: Any) -> list[dict[str, Any]]:
    """FastMCP/ADK 응답 포장 여부와 관계없이 utterances 배열을 꺼낸다."""
    response = tool_response
    if isinstance(response, dict) and isinstance(response.get("result"), dict):
        response = response["result"]
    if not isinstance(response, dict):
        return []
    utterances = response.get("utterances")
    if not isinstance(utterances, list):
        return []
    return [item for item in utterances if isinstance(item, dict)]


def validate_speech_info(
    speech_info: Any,
    source_utterances: dict[str, dict[str, Any]],
) -> tuple[bool, str]:
    """모든 인용과 메타데이터가 실제 MCP 원문과 일치하는지 검사한다."""
    parsed = parse_speech_info(speech_info)
    if parsed is None:
        return False, "speech_info를 지정된 JSON 객체 형식으로만 다시 출력하라."

    if set(parsed) != {"evidence"}:
        return False, "해석이나 설명을 넣지 말고 evidence 배열만 출력하라."

    evidence = parsed.get("evidence")
    if not isinstance(evidence, list):
        return False, "evidence는 배열이어야 한다."
    if not evidence:
        return False, "검증 가능한 회의록 근거가 없다. 검색어를 바꿔 다시 검색하라."

    seen: set[str] = set()
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict) or set(item) != set(EVIDENCE_FIELDS):
            return False, f"근거 {index}의 필드가 지정된 형식과 다르다."

        utterance_id = item.get("utterance_id")
        if not isinstance(utterance_id, str) or utterance_id in seen:
            return False, f"근거 {index}의 발언 ID가 없거나 중복됐다."
        seen.add(utterance_id)

        source = source_utterances.get(utterance_id)
        if source is None:
            return False, f"근거 {index}의 발언 ID가 실제 MCP 조회 결과에 없다."

        # legislator_id가 없는 발언(국회의원이 아닌 진술인·정부 관계자 등)을
        # 이름만 비슷해 보인다고 채택하면 동명이인 오검증으로 이어진다(실측 확인:
        # "김민수 의원"으로 물었을 때 대한의사협회 진술인 "김민수"의 발언을
        # 근거로 쓴 사례). 회의록 화자 식별이 안 된 발언은 결과적으로 어떤
        # 국회의원 발언인지 보장할 수 없으므로 여기서 결정적으로 차단한다.
        legislator_id = source.get("legislator_id")
        if not isinstance(legislator_id, str) or not legislator_id.strip():
            return False, (
                f"근거 {index}({item.get('speaker_name')})는 legislator_id가 없어 "
                "국회의원 발언인지 확인되지 않는다. 국회의원이 아닌 진술인·정부"
                " 관계자 발언일 수 있으니 제외하고, legislator_id가 채워진"
                " 결과만 다시 채택하라."
            )

        quote = item.get("quote")
        source_text = source.get("utterance_text")
        if not isinstance(quote, str) or not quote.strip():
            return False, f"근거 {index}의 인용문이 비어 있다."
        if not isinstance(source_text, str) or quote not in source_text:
            return False, f"근거 {index}의 인용문이 해당 전체 발언 원문에 없다."

        for field in EVIDENCE_FIELDS:
            if field in {"quote", "utterance_id"}:
                continue
            source_value = source.get(field)
            if item.get(field) != source_value:
                return False, f"근거 {index}의 {field} 값이 MCP 원본과 다르다."

    return True, ""
