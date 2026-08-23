#!/usr/bin/env python3
"""Build validated Vertex AI Search documents from roll-call appendices in PDFs."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from google.cloud import bigquery


PROJECT = "proj-aj04-211200020328"
DATASET = "assembly"
SCHEMA_VERSION = "assembly-vote-pdf-v1"
APPENDIX = "【전자투표 찬반 의원 성명】"
VOTE = re.compile(
    r"(?:^|\n)\s*◯(?P<title>.*?)\n\s*투표\s*의원\s*\(\s*(?P<total>\d+)\s*인\s*\)"
    r"(?P<body>.*?)(?=\n\s*◯|\Z)",
    re.DOTALL,
)
CHOICE = re.compile(r"(?P<label>찬성|반대|기권)\s*의원\s*\(\s*(?P<count>\d+)\s*인\s*\)")
PAGE_MARK = re.compile(r"\n\x00PAGE:(\d+)\x00\n")
PAGE_DATE = re.compile(r"\((\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\)")


def normalize(value: str | None) -> str:
    """PDF 표결 문자열을 Unicode NFKC와 단일 공백으로 정규화한다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def compact(value: str) -> str:
    """PDF에서 이름 사이에 삽입된 공백을 제거해 의원명 비교용으로 만든다."""
    return re.sub(r"\s+", "", normalize(value))


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def member_pattern(names: list[str]) -> re.Pattern[str]:
    """공식 의원명을 PDF의 선택별 명단에서 찾는 정규식을 만든다."""
    alternatives = []
    for name in sorted({compact(name) for name in names}, key=lambda item: (-len(item), item)):
        alternatives.append(r"\s*".join(map(re.escape, name)))
    return re.compile(rf"(?<![가-힣])({'|'.join(alternatives)})(?![가-힣])")


def extract_member_names(text: str, pattern: re.Pattern[str]) -> list[str]:
    """찬성·반대·기권 구간에서 공식 의원명만 원문 순서대로 추출한다."""
    # Parenthesized correction notes belong to metadata, not to the declared list.
    text = re.split(r"\n\s*\(", text, maxsplit=1)[0]
    return [compact(match.group(0)) for match in pattern.finditer(text)]


def page_at(position: int, starts: list[int], pages: list[int]) -> int:
    """연결된 전체 텍스트 위치를 원래 PDF 페이지 번호로 변환한다."""
    index = bisect.bisect_right(starts, position) - 1
    return pages[max(index, 0)]


def parse_meeting(
    meeting: dict[str, Any],
    pages: list[dict[str, Any]],
    names_to_ids: dict[str, list[str]],
    pattern: re.Pattern[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """회의 말미 전자투표 부록을 안건별로 파싱하고 인원수를 검증한다."""
    pieces: list[str] = []
    starts: list[int] = []
    page_numbers: list[int] = []
    page_dates: dict[int, date] = {}
    position = 0
    for page in pages:
        marker = f"\n\x00PAGE:{page['page_number']}\x00\n"
        pieces.append(marker)
        position += len(marker)
        starts.append(position)
        page_numbers.append(int(page["page_number"]))
        text = page["extracted_text"]
        date_match = PAGE_DATE.search(text)
        if date_match:
            page_dates[int(page["page_number"])] = date(
                int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
            )
        pieces.append(text)
        position += len(text)
    joined = "".join(pieces)
    appendix_at = joined.find(APPENDIX)
    if appendix_at < 0:
        return [], []
    # Hide page markers without changing string offsets so evidence page ranges
    # remain correct even when one vote list spans several PDF pages.
    appendix = PAGE_MARK.sub(lambda item: " " * len(item.group(0)), joined[appendix_at:])

    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for ordinal, match in enumerate(VOTE.finditer(appendix), start=1):
        title = normalize(match.group("title"))
        total = int(match.group("total"))
        body = match.group("body")
        choices: dict[str, dict[str, Any]] = {}
        markers = list(CHOICE.finditer(body))
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
            label = marker.group("label")
            declared = int(marker.group("count"))
            member_names = extract_member_names(body[marker.end():end], pattern)
            choices[label] = {"declared": declared, "names": member_names}

        reasons: list[str] = []
        if not title:
            reasons.append("empty_title")
        if "찬성" not in choices:
            reasons.append("missing_yes_list")
        for label, choice in choices.items():
            if choice["declared"] != len(choice["names"]):
                reasons.append(
                    f"{label}_count_mismatch:{choice['declared']}!={len(choice['names'])}"
                )
            duplicates = [name for name, count in Counter(choice["names"]).items() if count > 1]
            if duplicates:
                reasons.append(f"{label}_duplicate_names")
        declared_sum = sum(choice["declared"] for choice in choices.values())
        if declared_sum != total:
            reasons.append(f"total_mismatch:{total}!={declared_sum}")

        absolute_start = appendix_at + match.start()
        absolute_end = appendix_at + match.end()
        page_start = page_at(absolute_start, starts, page_numbers)
        page_end = page_at(absolute_end, starts, page_numbers)
        vote_key = f"{meeting['meeting_id']}:{ordinal}:{title}"
        vote_id = f"vote:{sha256(vote_key)[:24]}"
        record = {
            "vote_id": vote_id,
            "meeting_id": meeting["meeting_id"],
            "ordinal": ordinal,
            "title": title,
            "total_count": total,
            "yes_count": choices.get("찬성", {}).get("declared", 0),
            "no_count": choices.get("반대", {}).get("declared", 0),
            "abstain_count": choices.get("기권", {}).get("declared", 0),
            "choices": choices,
            "page_start": page_start,
            "page_end": page_end,
            "vote_date": page_dates.get(page_start, meeting["meeting_date"]),
            "source_text_sha256": sha256(normalize(match.group(0))),
            "reasons": reasons,
        }
        (rejected if reasons else valid).append(record)
    return valid, rejected


def documents_for_vote(
    vote: dict[str, Any], meeting: dict[str, Any], names_to_ids: dict[str, list[str]]
) -> list[dict[str, str]]:
    """표결 한 건을 요약 문서와 의원별 선택 문서로 변환한다."""
    common = {
        "schema_version": SCHEMA_VERSION,
        "vote_id": vote["vote_id"],
        "meeting_id": vote["meeting_id"],
        "meeting_date": meeting["meeting_date"].isoformat(),
        "vote_date": vote["vote_date"].isoformat(),
        "meeting_type": meeting["meeting_type"],
        "committee_name": meeting["committee_name"],
        "vote_title": vote["title"],
        "total_count": vote["total_count"],
        "yes_count": vote["yes_count"],
        "no_count": vote["no_count"],
        "abstain_count": vote["abstain_count"],
        "page_start": vote["page_start"],
        "page_end": vote["page_end"],
        "source_pdf_gcs_uri": meeting["raw_pdf_gcs_uri"],
        "source_text_sha256": vote["source_text_sha256"],
    }
    summary_content = (
        f"{vote['title']} 전자투표 결과: 투표 {vote['total_count']}인, "
        f"찬성 {vote['yes_count']}인, 반대 {vote['no_count']}인, "
        f"기권 {vote['abstain_count']}인."
    )
    summary = {
        **common,
        "document_type": "assembly_vote_summary",
        "title": vote["title"],
        "content": summary_content,
        "retrieval_text": f"{vote['vote_date']} {vote['title']} {summary_content}",
    }
    result = [{"id": f"{vote['vote_id']}:summary", "jsonData": json.dumps(summary, ensure_ascii=False, separators=(",", ":"))}]
    choice_codes = {"찬성": "YES", "반대": "NO", "기권": "ABSTAIN"}
    for label, choice in vote["choices"].items():
        for index, member_name in enumerate(choice["names"], start=1):
            ids = names_to_ids.get(member_name, [])
            legislator_id = ids[0] if len(ids) == 1 else None
            content = f"{member_name} 의원은 {vote['title']} 전자투표에서 {label}하였다."
            payload = {
                **common,
                "document_type": "assembly_vote_member",
                "title": f"{member_name} - {vote['title']}",
                "content": content,
                "retrieval_text": f"{vote['vote_date']} {member_name} {vote['title']} {label} {content}",
                "member_name": member_name,
                "legislator_id": legislator_id,
                "choice": choice_codes[label],
                "choice_ko": label,
                "identity_status": "MATCHED" if legislator_id else "AMBIGUOUS",
            }
            result.append({
                "id": f"{vote['vote_id']}:{choice_codes[label].lower()}:{index:03d}:{sha256(member_name)[:8]}",
                "jsonData": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            })
    return result


def parse_args() -> argparse.Namespace:
    """표결 문서 생성 대상과 실제 반영 여부(--apply)를 읽는다."""
    parser = argparse.ArgumentParser(description="Build validated vote search documents")
    parser.add_argument("--project", default=PROJECT)
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    """검증된 전자투표만 vote_search_documents에 전체 게시한다."""
    args = parse_args()
    client = bigquery.Client(project=args.project)
    prefix = f"{args.project}.{args.dataset}"
    legislators = list(client.query(
        f"SELECT legislator_id, name FROM `{prefix}.legislators` ORDER BY legislator_id"
    ).result())
    names_to_ids: dict[str, list[str]] = defaultdict(list)
    for row in legislators:
        names_to_ids[compact(row.name)].append(row.legislator_id)
    pattern = member_pattern(list(names_to_ids))

    meeting_rows = list(client.query(f"""
        SELECT meeting_id, meeting_date, meeting_type, committee_name, raw_pdf_gcs_uri
        FROM `{prefix}.meetings`
        WHERE meeting_type = 'plenary' AND raw_pdf_gcs_uri IS NOT NULL
        ORDER BY meeting_date, meeting_id
    """).result())
    meetings = {row.meeting_id: dict(row.items()) for row in meeting_rows}
    pages_by_meeting: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in client.query(f"""
        SELECT meeting_id, page_number, extracted_text
        FROM `{prefix}.pdf_pages`
        WHERE meeting_type = 'plenary'
        ORDER BY meeting_id, page_number
    """).result(page_size=5000):
        pages_by_meeting[row.meeting_id].append(dict(row.items()))

    documents: list[dict[str, str]] = []
    votes: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for meeting_id, pages in pages_by_meeting.items():
        valid, invalid = parse_meeting(meetings[meeting_id], pages, names_to_ids, pattern)
        votes.extend(valid)
        rejected.extend(invalid)
        for vote in valid:
            documents.extend(documents_for_vote(vote, meetings[meeting_id], names_to_ids))

    ids = [row["id"] for row in documents]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate document IDs")
    member_docs = [json.loads(row["jsonData"]) for row in documents if ':summary' not in row["id"]]
    metrics = {
        "meetings_with_candidate_pages": len(pages_by_meeting),
        "validated_votes": len(votes),
        "rejected_votes": len(rejected),
        "documents": len(documents),
        "summary_documents": len(votes),
        "member_documents": len(member_docs),
        "matched_member_documents": sum(doc["legislator_id"] is not None for doc in member_docs),
        "yes_member_documents": sum(doc["choice"] == "YES" for doc in member_docs),
        "no_member_documents": sum(doc["choice"] == "NO" for doc in member_docs),
        "abstain_member_documents": sum(doc["choice"] == "ABSTAIN" for doc in member_docs),
    }
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if rejected:
        reason_counts = Counter(reason for vote in rejected for reason in vote["reasons"])
        print(json.dumps({"rejection_reasons": reason_counts}, ensure_ascii=False, indent=2))
    if not documents:
        raise RuntimeError("no validated vote documents generated")
    if not args.apply:
        print("dry run; no BigQuery changes")
        return 0

    schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("jsonData", "STRING"),
    ]
    staging = f"{prefix}.vote_search_documents_staging"
    target = f"{prefix}.vote_search_documents"
    client.delete_table(staging, not_found_ok=True)
    client.create_table(bigquery.Table(staging, schema=schema))
    with tempfile.NamedTemporaryFile(mode="w+b", suffix=".ndjson") as stream:
        for row in documents:
            stream.write((json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
        stream.seek(0)
        client.load_table_from_file(stream, staging, job_config=bigquery.LoadJobConfig(
            schema=schema,
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )).result()
    loaded = client.get_table(staging).num_rows
    if loaded != len(documents):
        raise RuntimeError(f"staging count mismatch: {loaded} != {len(documents)}")
    client.copy_table(staging, target, job_config=bigquery.CopyJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
    )).result()
    client.delete_table(staging)
    table = client.get_table(target)
    table.description = "Validated PDF roll-call documents for Vertex AI Search"
    client.update_table(table, ["description"])
    print(f"published {loaded:,} documents -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
