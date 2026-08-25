"""Action Agent 결과를 MCP 표결 원본과 결정적으로 대조하는 순수 함수."""

from __future__ import annotations

import json
from typing import Any


EVIDENCE_FIELDS = (
    "document_id",
    "vote_id",
    "document_type",
    "member_name",
    "legislator_id",
    "identity_status",
    "vote_title",
    "vote_date",
    "meeting_id",
    "meeting_title",
    "choice",
    "choice_ko",
    "total_count",
    "yes_count",
    "no_count",
    "abstain_count",
    "content",
    "page_start",
    "page_end",
    "source_pdf_url",
)

_MEMBER_DOCUMENT = "assembly_vote_member"
_SUMMARY_DOCUMENT = "assembly_vote_summary"
_CHOICES = {"YES": "찬성", "NO": "반대", "ABSTAIN": "기권"}


def parse_action_info(value: Any) -> dict[str, Any] | None:
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


def collect_tool_votes(tool_response: Any) -> list[dict[str, Any]]:
    """FastMCP/ADK 응답 포장 여부와 관계없이 표결 문서 배열을 꺼낸다."""
    response = tool_response
    if isinstance(response, dict):
        structured = response.get("structuredContent")
        if structured is None:
            structured = response.get("structured_content")
        if structured is not None:
            response = structured
    if isinstance(response, dict) and "result" in response:
        response = response["result"]
    if not isinstance(response, list):
        return []
    return [item for item in response if isinstance(item, dict)]


def validate_action_info(
    action_info: Any,
    source_votes: dict[str, dict[str, Any]],
) -> tuple[bool, str]:
    """모든 표결 필드가 실제 MCP search_votes 결과와 일치하는지 검사한다."""
    parsed = parse_action_info(action_info)
    if parsed is None:
        return False, "action_info를 지정된 JSON 객체 형식으로만 다시 출력하라."

    if set(parsed) != {"evidence"}:
        return False, "해석이나 설명을 넣지 말고 evidence 배열만 출력하라."

    evidence = parsed.get("evidence")
    if not isinstance(evidence, list):
        return False, "evidence는 배열이어야 한다."
    if not evidence:
        return False, "검증 가능한 공식 전자투표 근거가 없다. 검색어를 바꿔 다시 검색하라."

    seen: set[str] = set()
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict) or set(item) != set(EVIDENCE_FIELDS):
            return False, f"근거 {index}의 필드가 지정된 형식과 다르다."

        document_id = item.get("document_id")
        if not isinstance(document_id, str) or not document_id or document_id in seen:
            return False, f"근거 {index}의 문서 ID가 없거나 중복됐다."
        seen.add(document_id)

        source = source_votes.get(document_id)
        if source is None:
            return False, f"근거 {index}의 문서 ID가 실제 MCP 조회 결과에 없다."

        document_type = source.get("document_type")
        if document_type not in {_MEMBER_DOCUMENT, _SUMMARY_DOCUMENT}:
            return False, f"근거 {index}의 표결 문서 유형을 확인할 수 없다."

        if document_type == _MEMBER_DOCUMENT:
            if source.get("identity_status") != "MATCHED":
                return False, f"근거 {index}의 의원 신원이 MATCHED로 확인되지 않았다."
            choice = source.get("choice")
            if choice not in _CHOICES or source.get("choice_ko") != _CHOICES[choice]:
                return False, f"근거 {index}의 공식 표결 선택 값이 올바르지 않다."
            for field in ("member_name", "legislator_id"):
                value = source.get(field)
                if not isinstance(value, str) or not value.strip():
                    return False, f"근거 {index}의 {field} 값이 없어 의원을 확인할 수 없다."
        else:
            for field in (
                "member_name",
                "legislator_id",
                "identity_status",
                "choice",
                "choice_ko",
            ):
                if item.get(field) is not None:
                    return False, f"안건 요약 근거 {index}의 {field}는 null이어야 한다."

        for field in EVIDENCE_FIELDS:
            if item.get(field) != source.get(field):
                return False, f"근거 {index}의 {field} 값이 MCP 원본과 다르다."

    return True, ""
