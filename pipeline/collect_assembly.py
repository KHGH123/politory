#!/usr/bin/env python3
"""Collect National Assembly minutes into GCS and BigQuery.

The production path is ``--pdf-only``: preserve official PDFs and API metadata
without requesting or parsing the unreliable HTML viewer response. PDF text is
parsed later by ``rebuild_pdf_tables.py``. Authentication uses Google ADC.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from google.cloud import bigquery, storage
from google.api_core.exceptions import Forbidden, Unauthorized
from google.auth.exceptions import RefreshError


API_BASE = "https://open.assembly.go.kr/portal/openapi"
RECORD_BASE = "https://record.assembly.go.kr/assembly/viewer/minutes"
USER_AGENT = "Mozilla/5.0 (compatible; AssemblyMinutesCollector/1.0)"
PARSER_VERSION = "minutes-html-v2"
ENDPOINTS = {
    "plenary": "nzbyfwhwaoanttzje",
    "committee": "ncwgseseafwbuheph",
}


def clean_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    children: list[Any]


VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "dl",
    "dt",
    "dd",
    "figcaption",
    "figure",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "section",
    "table",
    "tr",
    "ul",
}


def node_classes(node: HtmlNode) -> set[str]:
    return set(node.attrs.get("class", "").split())


def iter_nodes(node: HtmlNode):
    yield node
    for child in node.children:
        if isinstance(child, HtmlNode):
            yield from iter_nodes(child)


def node_text(node: HtmlNode) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
            continue
        if child.tag == "br":
            parts.append("\n")
            continue
        parts.append(node_text(child))
        if child.tag in BLOCK_TAGS:
            parts.append("\n")
        elif child.tag in {"a", "span", "strong"}:
            parts.append(" ")
    return clean_text("".join(parts))


def table_text(node: HtmlNode) -> str:
    rows: list[str] = []
    for row in (item for item in iter_nodes(node) if item.tag == "tr"):
        cells = [
            node_text(cell)
            for cell in row.children
            if isinstance(cell, HtmlNode) and cell.tag in {"th", "td"}
        ]
        if cells:
            rows.append(" | ".join(cells))
    return clean_text("\n".join(rows)) if rows else node_text(node)


class MinutesParser(HTMLParser):
    """Parse the official minutes header, body, and footer in source order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: dict[str, HtmlNode] = {}
        self.active_section: str | None = None
        self.stack: list[HtmlNode] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key: value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = self._attrs(attrs)
        classes = set(attr.get("class", "").split())
        if self.active_section is None and tag == "div":
            section = next(
                (name for name in ("minutes_header", "minutes_body", "minutes_footer") if name in classes),
                None,
            )
            if section:
                root = HtmlNode(tag=tag, attrs=attr, children=[])
                self.sections[section] = root
                self.active_section = section
                self.stack = [root]
                return
        if self.active_section is None:
            return
        node = HtmlNode(tag=tag, attrs=attr, children=[])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self.active_section is not None and self.stack:
            self.stack[-1].children.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.active_section is None or not self.stack:
            return
        matching_index = next(
            (index for index in range(len(self.stack) - 1, -1, -1) if self.stack[index].tag == tag),
            None,
        )
        if matching_index is None:
            return
        closing_root = matching_index == 0
        del self.stack[matching_index:]
        if closing_root:
            self.active_section = None

    def agendas(self) -> list[dict[str, Any]]:
        body = self.sections.get("minutes_body")
        if body is None:
            return []
        agendas: list[dict[str, Any]] = []
        seen: set[int] = set()
        for anchor in iter_nodes(body):
            anchor_id = anchor.attrs.get("id", "")
            if anchor.tag != "a" or not re.fullmatch(r"item\d+", anchor_id):
                continue
            index = int(anchor_id[4:])
            if index in seen:
                continue
            seen.add(index)
            title = node_text(anchor)
            href = anchor.attrs.get("href", "")
            query = parse_qs(urlparse(href).query)
            bill_id = (query.get("billId") or [None])[0]
            bill_match = re.search(r"의안번호\s*(\d+)", title)
            number_match = re.match(r"\s*(\d+)\s*[.]", title)
            agendas.append(
                {
                    "index": index,
                    "title": title,
                    "agenda_no": int(number_match.group(1)) if number_match else None,
                    "bill_number": bill_match.group(1) if bill_match else None,
                    "bill_id": bill_id,
                    "source_anchor": anchor_id,
                }
            )
        return agendas

    @staticmethod
    def _agenda_anchor(node: HtmlNode) -> HtmlNode | None:
        return next(
            (
                item
                for item in iter_nodes(node)
                if item.tag == "a" and re.fullmatch(r"item\d+", item.attrs.get("id", ""))
            ),
            None,
        )

    @staticmethod
    def _contains_semantic(node: HtmlNode) -> bool:
        for item in iter_nodes(node):
            if item is node:
                continue
            classes = node_classes(item)
            if "speaker" in classes:
                return True
            if item.tag == "table":
                return True
            if item.tag == "p" and ("taR" in classes or MinutesParser._agenda_anchor(item)):
                return True
        return False

    @staticmethod
    def _speaker_block(node: HtmlNode) -> dict[str, Any]:
        classes = node_classes(node)
        item_class = next((value for value in classes if re.fullmatch(r"item\d+", value)), "item0")
        spans = [item for item in iter_nodes(node) if "spk_sub" in node_classes(item)]
        text = clean_text("\n".join(node_text(span) for span in spans))
        anchor = next((span.attrs.get("id") for span in spans if span.attrs.get("id")), None)
        return {
            "section": "body",
            "block_type": "speech",
            "agenda_index": int(item_class[4:]),
            "member_id": node.attrs.get("data-mem_id") or None,
            "name": clean_text(node.attrs.get("data-name", "")) or None,
            "position": clean_text(node.attrs.get("data-pos", "")) or None,
            "text": text,
            "source_anchor": anchor or node.attrs.get("id") or None,
        }

    @staticmethod
    def _generic_type(text: str) -> str:
        if "출석 위원" in text or "정부측 및 기타 참석자" in text:
            return "attendance"
        if "투표 결과" in text or "표결 결과" in text:
            return "vote_result"
        if "보고사항" in text:
            return "report"
        if "부록" in text:
            return "appendix"
        return "proceeding"

    def _emit_body_node(self, node: HtmlNode, output: list[dict[str, Any]]) -> None:
        classes = node_classes(node)
        if "speaker" in classes:
            block = self._speaker_block(node)
            if block["text"]:
                output.append(block)
            return

        agenda_anchor = self._agenda_anchor(node) if node.tag == "p" else None
        if agenda_anchor is not None:
            title = node_text(agenda_anchor)
            if title:
                output.append(
                    {
                        "section": "body",
                        "block_type": "agenda",
                        "agenda_index": int(agenda_anchor.attrs["id"][4:]),
                        "member_id": None,
                        "name": None,
                        "position": None,
                        "text": title,
                        "source_anchor": agenda_anchor.attrs["id"],
                    }
                )
            return

        if node.tag == "p" and "taR" in classes:
            text = node_text(node)
            if text:
                output.append(
                    {
                        "section": "body",
                        "block_type": "proceeding",
                        "agenda_index": None,
                        "member_id": None,
                        "name": None,
                        "position": None,
                        "text": text,
                        "source_anchor": node.attrs.get("id") or None,
                    }
                )
            return

        if node.tag == "table":
            text = table_text(node)
            if text:
                output.append(
                    {
                        "section": "body",
                        "block_type": "table",
                        "agenda_index": None,
                        "member_id": None,
                        "name": None,
                        "position": None,
                        "text": text,
                        "source_anchor": node.attrs.get("id") or None,
                    }
                )
            return

        if self._contains_semantic(node):
            for child in node.children:
                if isinstance(child, HtmlNode):
                    self._emit_body_node(child, output)
            return

        text = node_text(node)
        if text:
            output.append(
                {
                    "section": "body",
                    "block_type": self._generic_type(text),
                    "agenda_index": None,
                    "member_id": None,
                    "name": None,
                    "position": None,
                    "text": text,
                    "source_anchor": node.attrs.get("id") or None,
                }
            )

    def blocks(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        header = self.sections.get("minutes_header")
        if header is not None:
            text = node_text(header)
            if text:
                output.append(
                    {
                        "section": "header",
                        "block_type": "meeting_header",
                        "agenda_index": None,
                        "member_id": None,
                        "name": None,
                        "position": None,
                        "text": text,
                        "source_anchor": None,
                    }
                )
        body = self.sections.get("minutes_body")
        if body is not None:
            for child in body.children:
                if isinstance(child, HtmlNode):
                    self._emit_body_node(child, output)
        footer = self.sections.get("minutes_footer")
        if footer is not None:
            text = node_text(footer)
            if text:
                output.append(
                    {
                        "section": "footer",
                        "block_type": self._generic_type(text),
                        "agenda_index": None,
                        "member_id": None,
                        "name": None,
                        "position": None,
                        "text": text,
                        "source_anchor": None,
                    }
                )
        return output

    def source_text(self) -> str:
        return clean_text(
            "\n".join(
                node_text(self.sections[name])
                for name in ("minutes_header", "minutes_body", "minutes_footer")
                if name in self.sections
            )
        )


@dataclass(frozen=True)
class Config:
    api_key: str
    project: str
    bucket: str
    dataset: str
    assembly_no: int
    start_date: date
    end_date: date
    meeting_types: tuple[str, ...]
    max_meetings: int | None
    download_pdf: bool
    request_delay: float
    batch_size: int
    use_cached_discovery: bool
    reprocess_existing: bool
    meeting_ids: tuple[str, ...]
    pdf_only: bool


class Collector:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.storage = storage.Client(project=config.project)
        self.bucket = self.storage.bucket(config.bucket)
        self.bigquery = bigquery.Client(project=config.project)

    def request(self, url: str, params: dict[str, Any] | None = None) -> bytes:
        if params:
            url = f"{url}?{urlencode(params)}"
        request = Request(url, headers={"User-Agent": USER_AGENT})
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                with urlopen(request, timeout=90) as response:
                    data = response.read()
                if self.cfg.request_delay:
                    time.sleep(self.cfg.request_delay)
                return data
            except (HTTPError, URLError, TimeoutError) as exc:
                last_error = exc
                if attempt == 4:
                    break
                time.sleep(2**attempt)
        raise RuntimeError(f"request failed after retries: {url}") from last_error

    def api_rows(self, meeting_type: str, meeting_date: date) -> tuple[list[dict[str, Any]], bytes | None]:
        endpoint = ENDPOINTS[meeting_type]
        page_size = 1000
        rows: list[dict[str, Any]] = []
        raw_pages: list[bytes] = []
        first_head: list[dict[str, Any]] = []
        total = 0
        page = 1
        while True:
            params = {
                "KEY": self.cfg.api_key,
                "Type": "json",
                "pIndex": page,
                "pSize": page_size,
                "DAE_NUM": self.cfg.assembly_no,
                "CONF_DATE": meeting_date.isoformat(),
            }
            raw = self.request(f"{API_BASE}/{endpoint}", params)
            payload = json.loads(raw)
            if "RESULT" in payload:
                if payload["RESULT"].get("CODE") == "INFO-200":
                    return [], None
                raise RuntimeError(f"API error: {payload['RESULT']}")
            sections = payload.get(endpoint)
            if not sections:
                raise RuntimeError(f"unexpected API response for {endpoint}")
            head = sections[0].get("head", [])
            result = next((item["RESULT"] for item in head if "RESULT" in item), {})
            if result.get("CODE") != "INFO-000":
                raise RuntimeError(f"API error: {result}")
            if page == 1:
                first_head = head
                total = int(next((item["list_total_count"] for item in head if "list_total_count" in item), 0))
            page_rows = sections[1].get("row", []) if len(sections) > 1 else []
            raw_pages.append(raw)
            rows.extend(page_rows)
            if not page_rows or len(rows) >= total:
                break
            page += 1

        if len(rows) != total:
            raise RuntimeError(
                f"pagination mismatch for {meeting_type} {meeting_date}: expected={total}, received={len(rows)}"
            )
        api_prefix = f"raw/api/{meeting_type}/year={meeting_date.year}/date={meeting_date.isoformat()}"
        for page_number, page_raw in enumerate(raw_pages, start=1):
            self.upload(
                f"{api_prefix}/response-page={page_number:04d}.json",
                page_raw,
                "application/json; charset=utf-8",
            )
        combined = json.dumps(
            {endpoint: [{"head": first_head}, {"row": rows}]},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return rows, combined

    def upload(self, path: str, data: bytes, content_type: str) -> str:
        self.bucket.blob(path).upload_from_string(data, content_type=content_type)
        return f"gs://{self.cfg.bucket}/{path}"

    def existing_meeting_ids(self) -> set[str]:
        sql = f"""
            SELECT meeting_id
            FROM `{self.cfg.project}.{self.cfg.dataset}.meetings`
            WHERE meeting_date BETWEEN @start_date AND @end_date
              AND meeting_type IN UNNEST(@meeting_types)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_date", "DATE", self.cfg.start_date),
                bigquery.ScalarQueryParameter("end_date", "DATE", self.cfg.end_date),
                bigquery.ArrayQueryParameter("meeting_types", "STRING", list(self.cfg.meeting_types)),
            ]
        )
        return {row.meeting_id for row in self.bigquery.query(sql, job_config=job_config).result()}

    def discover(self) -> dict[str, list[dict[str, Any]]]:
        if self.cfg.use_cached_discovery:
            return self.discover_cached()
        meetings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        current = self.cfg.start_date
        days = (self.cfg.end_date - self.cfg.start_date).days + 1
        for day_index in range(days):
            for meeting_type in self.cfg.meeting_types:
                rows, raw = self.api_rows(meeting_type, current)
                if raw is not None:
                    api_path = (
                        f"raw/api/{meeting_type}/year={current.year}/date={current.isoformat()}/response.json"
                    )
                    self.upload(api_path, raw, "application/json; charset=utf-8")
                for row in rows:
                    meeting_id = f"{meeting_type}:{row['CONFER_NUM']}"
                    row = dict(row)
                    row["_meeting_type"] = meeting_type
                    meetings[meeting_id].append(row)
            if day_index % 20 == 0 or day_index == days - 1:
                print(f"discovery {current}: {len(meetings)} unique meetings", flush=True)
            current += timedelta(days=1)
        return meetings

    def discover_cached(self) -> dict[str, list[dict[str, Any]]]:
        meetings: dict[str, list[dict[str, Any]]] = defaultdict(list)
        pattern = re.compile(r"raw/api/([^/]+)/year=(\d{4})/date=(\d{4}-\d{2}-\d{2})/response[.]json$")
        selected: list[tuple[Any, str]] = []
        for blob in self.storage.list_blobs(self.cfg.bucket, prefix="raw/api/"):
            match = pattern.fullmatch(blob.name)
            if not match:
                continue
            meeting_type, _, date_text = match.groups()
            meeting_date = date.fromisoformat(date_text)
            if meeting_type not in self.cfg.meeting_types:
                continue
            if not self.cfg.start_date <= meeting_date <= self.cfg.end_date:
                continue
            selected.append((blob, meeting_type))

        def read_blob(item: tuple[Any, str]) -> tuple[str, list[dict[str, Any]]]:
            blob, meeting_type = item
            endpoint = ENDPOINTS[meeting_type]
            payload = json.loads(blob.download_as_bytes())
            sections = payload.get(endpoint, [])
            rows = sections[1].get("row", []) if len(sections) > 1 else []
            return meeting_type, rows

        with ThreadPoolExecutor(max_workers=16) as executor:
            cached_rows = executor.map(read_blob, selected)
            for meeting_type, rows in cached_rows:
                for row in rows:
                    meeting_id = f"{meeting_type}:{row['CONFER_NUM']}"
                    row = dict(row)
                    row["_meeting_type"] = meeting_type
                    meetings[meeting_id].append(row)
        print(f"cached discovery: {len(meetings)} unique meetings", flush=True)
        return meetings

    @staticmethod
    def meeting_numbers(title: str) -> tuple[int | None, int | None]:
        match = re.search(r"제\d+대\s+제(\d+)회\s+제(\d+)차", title)
        return (int(match.group(1)), int(match.group(2))) if match else (None, None)

    def process_meeting(
        self, meeting_id: str, api_rows: list[dict[str, Any]], collected_at: str
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        meta = api_rows[0]
        meeting_type = meta["_meeting_type"]
        confer_num = int(meta["CONFER_NUM"])
        meeting_date = date.fromisoformat(meta["CONF_DATE"])
        title = clean_text(meta["TITLE"])
        session_no, meeting_no = self.meeting_numbers(title)
        view_url = f"{RECORD_BASE}/xml.do?id={confer_num}&type=view"
        pdf_url = meta.get("PDF_LINK_URL") or f"{RECORD_BASE}/download/pdf.do?id={confer_num}"
        prefix = f"raw/minutes/{meeting_type}/year={meeting_date.year}/confer_num={confer_num}"

        html_path = f"{prefix}/transcript.html"
        html_blob = self.bucket.blob(html_path)
        if html_blob.exists():
            html = html_blob.download_as_bytes()
            html_uri = f"gs://{self.cfg.bucket}/{html_path}"
        else:
            html = self.request(view_url)
            html_uri = self.upload(html_path, html, "text/html; charset=utf-8")
        pdf_path = f"{prefix}/minutes.pdf"
        pdf_uri: str | None = None
        pdf_blob = self.bucket.blob(pdf_path)
        if self.cfg.download_pdf:
            if not pdf_blob.exists():
                pdf = self.request(pdf_url)
                if not pdf.startswith(b"%PDF"):
                    raise RuntimeError(f"invalid PDF response for {meeting_id}")
                self.upload(pdf_path, pdf, "application/pdf")
            pdf_uri = f"gs://{self.cfg.bucket}/{pdf_path}"
        elif pdf_blob.exists():
            pdf_uri = f"gs://{self.cfg.bucket}/{pdf_path}"

        parser = MinutesParser()
        parser.feed(html.decode("utf-8", errors="replace"))
        parsed_agendas = parser.agendas()
        parsed_blocks = parser.blocks()
        if not parsed_blocks:
            raise RuntimeError(f"no minutes blocks parsed for {meeting_id}")

        agenda_rows: list[dict[str, Any]] = []
        agenda_ids: dict[int, str] = {}
        agenda_ids_by_no: dict[int, list[str]] = defaultdict(list)
        seen_agendas: set[int] = set()
        for agenda in sorted(parsed_agendas, key=lambda row: row["index"]):
            index = agenda["index"]
            if index in seen_agendas:
                continue
            seen_agendas.add(index)
            agenda_id = f"{meeting_id}:agenda:{index}"
            agenda_ids[index] = agenda_id
            if agenda["agenda_no"] is not None:
                agenda_ids_by_no[agenda["agenda_no"]].append(agenda_id)
            agenda_rows.append(
                {
                    "agenda_id": agenda_id,
                    "meeting_id": meeting_id,
                    "agenda_no": agenda["agenda_no"],
                    "title": agenda["title"],
                    "bill_number": agenda["bill_number"],
                    "bill_id": agenda["bill_id"],
                    "source_anchor": agenda["source_anchor"],
                    "collected_at": collected_at,
                }
            )

        block_rows: list[dict[str, Any]] = []
        utterance_rows: list[dict[str, Any]] = []
        utterance_sequence = 0
        for block_order, block in enumerate(parsed_blocks, start=1):
            text = block["text"]
            member_id = block["member_id"]
            if member_id == "0":
                member_id = None
            linked_agenda_ids: list[str] = []
            link_method = "unresolved"
            range_match = re.search(r"제\s*(\d+)\s*항부터\s*제\s*(\d+)\s*항까지", text)
            if block["block_type"] == "speech" and range_match:
                start_no, end_no = map(int, range_match.groups())
                if start_no <= end_no:
                    linked_agenda_ids = [
                        agenda_id
                        for agenda_no in range(start_no, end_no + 1)
                        for agenda_id in agenda_ids_by_no.get(agenda_no, [])
                    ]
                    if linked_agenda_ids:
                        link_method = "explicit_range"
            if not linked_agenda_ids and block["agenda_index"] is not None:
                agenda_id = agenda_ids.get(block["agenda_index"])
                if agenda_id:
                    linked_agenda_ids = [agenda_id]
                    link_method = "html_context"

            block_id = f"{meeting_id}:block:{block_order}"
            content_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
            block_rows.append(
                {
                    "block_id": block_id,
                    "meeting_id": meeting_id,
                    "block_order": block_order,
                    "section": block["section"],
                    "block_type": block["block_type"],
                    "agenda_ids": linked_agenda_ids,
                    "agenda_link_method": link_method,
                    "speaker_member_id": member_id,
                    "source_speaker_id": member_id,
                    "legislator_id": None,
                    "speaker_name": block["name"],
                    "speaker_position": block["position"],
                    "text": text,
                    "source_anchor": block["source_anchor"],
                    "source_gcs_uri": html_uri,
                    "content_sha256": content_sha256,
                    "parser_version": PARSER_VERSION,
                    "meeting_date": meeting_date.isoformat(),
                    "meeting_type": meeting_type,
                    "committee_name": meta.get("COMM_NAME"),
                    "collected_at": collected_at,
                }
            )
            if block["block_type"] == "speech":
                utterance_sequence += 1
                utterance_rows.append(
                    {
                        "utterance_id": f"{meeting_id}:utterance:{utterance_sequence}",
                        "meeting_id": meeting_id,
                        "sequence_no": utterance_sequence,
                        "speaker_member_id": member_id,
                        "source_speaker_id": member_id,
                        "legislator_id": None,
                        "speaker_name": block["name"],
                        "speaker_position": block["position"],
                        "utterance_text": text,
                        "content_sha256": content_sha256,
                        "agenda_ids": linked_agenda_ids,
                        "source_anchor": block["source_anchor"],
                        "meeting_date": meeting_date.isoformat(),
                        "meeting_type": meeting_type,
                        "committee_name": meta.get("COMM_NAME"),
                        "collected_at": collected_at,
                        "block_id": block_id,
                        "agenda_link_method": link_method,
                    }
                )

        if not utterance_rows:
            raise RuntimeError(f"no utterances parsed for {meeting_id}")

        meeting_row = {
            "meeting_id": meeting_id,
            "confer_num": confer_num,
            "assembly_no": int(meta["DAE_NUM"]),
            "meeting_type": meeting_type,
            "committee_name": meta.get("COMM_NAME"),
            "meeting_date": meeting_date.isoformat(),
            "title": title,
            "session_no": session_no,
            "meeting_no": meeting_no,
            "official_url": view_url,
            "pdf_url": pdf_url,
            "raw_gcs_uri": html_uri,
            "raw_html_gcs_uri": html_uri,
            "raw_pdf_gcs_uri": pdf_uri,
            "collected_at": collected_at,
        }
        document_row = {
            "document_id": f"minutes_html:{meeting_id}",
            "source_type": "minutes_html",
            "assembly_no": int(meta["DAE_NUM"]),
            "meeting_type": meeting_type,
            "confer_num": confer_num,
            "api_endpoint": ENDPOINTS[meeting_type],
            "source_url": view_url,
            "pdf_url": pdf_url,
            "raw_gcs_uri": html_uri,
            "raw_sha256": hashlib.sha256(html).hexdigest(),
            "source_format": "html",
            "parser_version": PARSER_VERSION,
            "fetch_status": "SUCCESS",
            "parse_status": "SUCCESS",
            "error_message": None,
            "discovered_at": collected_at,
            "fetched_at": collected_at,
            "parsed_at": collected_at,
            "block_count": len(block_rows),
            "source_text_char_count": len(parser.source_text()),
            "block_text_char_count": sum(len(row["text"]) for row in block_rows),
            "validation_status": "STRUCTURE_PARSED",
        }
        return meeting_row, agenda_rows, block_rows, utterance_rows, document_row

    def process_meeting_pdf(
        self, meeting_id: str, api_rows: list[dict[str, Any]], collected_at: str
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        """Download one official PDF and retain only API-derived metadata."""
        meta = api_rows[0]
        meeting_type = meta["_meeting_type"]
        confer_num = int(meta["CONFER_NUM"])
        meeting_date = date.fromisoformat(meta["CONF_DATE"])
        title = clean_text(meta["TITLE"])
        session_no, meeting_no = self.meeting_numbers(title)
        official_url = meta.get("CONF_LINK_URL")
        pdf_url = meta.get("PDF_LINK_URL") or f"{RECORD_BASE}/download/pdf.do?id={confer_num}"
        prefix = f"raw/minutes/{meeting_type}/year={meeting_date.year}/confer_num={confer_num}"
        pdf_path = f"{prefix}/minutes.pdf"
        pdf_blob = self.bucket.blob(pdf_path)
        if pdf_blob.exists():
            pdf = pdf_blob.download_as_bytes()
        else:
            pdf = self.request(pdf_url)
            if not pdf.startswith(b"%PDF"):
                raise RuntimeError(f"invalid PDF response for {meeting_id}")
            self.upload(pdf_path, pdf, "application/pdf")
        if not pdf.startswith(b"%PDF"):
            raise RuntimeError(f"invalid cached PDF for {meeting_id}")
        pdf_uri = f"gs://{self.cfg.bucket}/{pdf_path}"

        agenda_rows: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for source in api_rows:
            agenda_title = clean_text(source.get("SUB_NAME") or "")
            if not agenda_title or agenda_title in seen_titles:
                continue
            seen_titles.add(agenda_title)
            number_match = re.match(r"(\d+)\s*[.]\s*", agenda_title)
            bill_match = re.search(r"의안번호\s*(\d+)", agenda_title)
            agenda_rows.append(
                {
                    "agenda_id": f"{meeting_id}:agenda:{len(agenda_rows) + 1}",
                    "meeting_id": meeting_id,
                    "agenda_no": int(number_match.group(1)) if number_match else None,
                    "title": agenda_title,
                    "bill_number": bill_match.group(1) if bill_match else None,
                    "bill_id": None,
                    "source_anchor": None,
                    "collected_at": collected_at,
                }
            )

        meeting_row = {
            "meeting_id": meeting_id,
            "confer_num": confer_num,
            "assembly_no": int(meta["DAE_NUM"]),
            "meeting_type": meeting_type,
            "committee_name": meta.get("COMM_NAME"),
            "meeting_date": meeting_date.isoformat(),
            "title": title,
            "session_no": session_no,
            "meeting_no": meeting_no,
            "official_url": official_url,
            "pdf_url": pdf_url,
            "raw_gcs_uri": pdf_uri,
            "raw_html_gcs_uri": None,
            "raw_pdf_gcs_uri": pdf_uri,
            "collected_at": collected_at,
        }
        document_row = {
            "document_id": f"{meeting_id}:pdf",
            "source_type": "official_pdf",
            "assembly_no": int(meta["DAE_NUM"]),
            "meeting_type": meeting_type,
            "confer_num": confer_num,
            "api_endpoint": ENDPOINTS[meeting_type],
            "source_url": pdf_url,
            "pdf_url": pdf_url,
            "raw_gcs_uri": pdf_uri,
            "raw_sha256": hashlib.sha256(pdf).hexdigest(),
            "source_format": "PDF",
            "parser_version": "pdf-download-v1",
            "fetch_status": "SUCCESS",
            "parse_status": "PENDING",
            "error_message": None,
            "discovered_at": collected_at,
            "fetched_at": collected_at,
            "parsed_at": None,
            "block_count": None,
            "source_text_char_count": None,
            "block_text_char_count": None,
            "validation_status": "PDF_SIGNATURE_VALID",
        }
        return meeting_row, agenda_rows, [], [], document_row

    def cleanup_partial(self, meeting_ids: list[str], document_ids: list[str]) -> None:
        if not meeting_ids and not document_ids:
            return
        sql = f"""
            DELETE FROM `{self.cfg.project}.{self.cfg.dataset}.agendas`
            WHERE meeting_id IN UNNEST(@meeting_ids);
            DELETE FROM `{self.cfg.project}.{self.cfg.dataset}.utterances`
            WHERE meeting_id IN UNNEST(@meeting_ids);
            DELETE FROM `{self.cfg.project}.{self.cfg.dataset}.ingestion_documents`
            WHERE document_id IN UNNEST(@document_ids);
            DELETE FROM `{self.cfg.project}.{self.cfg.dataset}.meetings`
            WHERE meeting_id IN UNNEST(@meeting_ids);
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("meeting_ids", "STRING", meeting_ids),
                bigquery.ArrayQueryParameter("document_ids", "STRING", document_ids),
            ]
        )
        self.bigquery.query(sql, job_config=job_config).result()

    def load_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        table_id = f"{self.cfg.project}.{self.cfg.dataset}.{table}"
        config = bigquery.LoadJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_APPEND)
        self.bigquery.load_table_from_json(rows, table_id, job_config=config).result()
        print(f"loaded {len(rows):,} rows -> {table_id}", flush=True)

    def flush_batch(
        self,
        meetings: list[dict[str, Any]],
        agendas: list[dict[str, Any]],
        blocks: list[dict[str, Any]],
        utterances: list[dict[str, Any]],
        documents: list[dict[str, Any]],
    ) -> None:
        if not meetings and not documents:
            return
        self.cleanup_partial(
            [row["meeting_id"] for row in meetings],
            [row["document_id"] for row in documents],
        )
        self.load_rows("ingestion_documents", documents)
        self.load_rows("agendas", agendas)
        self.load_rows("minutes_blocks", blocks)
        self.load_rows("utterances", utterances)
        self.load_rows("meetings", meetings)  # completion marker; load last

    def failed_document(
        self, meeting_id: str, api_rows: list[dict[str, Any]], collected_at: str, error: Exception
    ) -> dict[str, Any]:
        meta = api_rows[0]
        meeting_type = meta["_meeting_type"]
        confer_num = int(meta["CONFER_NUM"])
        view_url = meta.get("CONF_LINK_URL")
        pdf_url = meta.get("PDF_LINK_URL") or f"{RECORD_BASE}/download/pdf.do?id={confer_num}"
        source_type = "official_pdf"
        return {
            "document_id": f"{meeting_id}:pdf",
            "source_type": source_type,
            "assembly_no": int(meta["DAE_NUM"]),
            "meeting_type": meeting_type,
            "confer_num": confer_num,
            "api_endpoint": ENDPOINTS[meeting_type],
            "source_url": pdf_url,
            "pdf_url": pdf_url,
            "raw_gcs_uri": None,
            "raw_sha256": None,
            "source_format": "PDF",
            "parser_version": "pdf-download-v1",
            "fetch_status": "FAILED",
            "parse_status": "FAILED",
            "error_message": str(error)[:1000],
            "discovered_at": collected_at,
            "fetched_at": None,
            "parsed_at": None,
            "block_count": None,
            "source_text_char_count": None,
            "block_text_char_count": None,
            "validation_status": "FAILED",
        }

    def run(self) -> None:
        discovered = self.discover()
        existing = self.existing_meeting_ids()
        candidates = list(sorted(discovered.items()))
        if self.cfg.meeting_ids:
            selected_ids = set(self.cfg.meeting_ids)
            candidates = [(key, value) for key, value in candidates if key in selected_ids]
            missing_ids = selected_ids - {key for key, _ in candidates}
            if missing_ids:
                raise RuntimeError(f"meeting IDs not found in discovery data: {sorted(missing_ids)}")
        if not self.cfg.reprocess_existing:
            candidates = [(key, value) for key, value in candidates if key not in existing]
        already_loaded = len(set(discovered) & existing)
        if self.cfg.max_meetings is not None:
            candidates = candidates[: self.cfg.max_meetings]
        print(
            f"discovered={len(discovered)}, already_loaded={already_loaded}, "
            f"to_process={len(candidates)}",
            flush=True,
        )
        if not candidates:
            return

        meeting_rows: list[dict[str, Any]] = []
        agenda_rows: list[dict[str, Any]] = []
        block_rows: list[dict[str, Any]] = []
        utterance_rows: list[dict[str, Any]] = []
        document_rows: list[dict[str, Any]] = []
        collected_at = datetime.now(timezone.utc).isoformat()
        # Production collection is PDF-only. The Assembly HTML viewer has
        # returned content for a different meeting under otherwise valid URLs,
        # so it must never be used as a source of record.
        processor = self.process_meeting_pdf

        def process_candidate(
            candidate: tuple[str, list[dict[str, Any]]]
        ) -> tuple[str, list[dict[str, Any]], Any | None, Exception | None]:
            meeting_id, rows = candidate
            try:
                return meeting_id, rows, processor(meeting_id, rows, collected_at), None
            except Exception as exc:  # reported and persisted by the main thread
                return meeting_id, rows, None, exc

        executor = ThreadPoolExecutor(max_workers=8)
        results = executor.map(process_candidate, candidates)
        try:
            for index, (meeting_id, rows, result, error) in enumerate(results, start=1):
                if error is None:
                    meeting, agendas, blocks, utterances, document = result
                    meeting_rows.append(meeting)
                    agenda_rows.extend(agendas)
                    block_rows.extend(blocks)
                    utterance_rows.extend(utterances)
                    document_rows.append(document)
                    print(
                        f"processed {index}/{len(candidates)} {meeting_id}: "
                        f"agendas={len(agendas)}, blocks={len(blocks)}, utterances={len(utterances)}",
                        flush=True,
                    )
                else:
                    if isinstance(error, (RefreshError, Unauthorized, Forbidden)):
                        raise error
                    document_rows.append(self.failed_document(meeting_id, rows, collected_at, error))
                    print(
                        f"failed {index}/{len(candidates)} {meeting_id}: {error}",
                        file=sys.stderr,
                        flush=True,
                    )
                if index % self.cfg.batch_size == 0 or index == len(candidates):
                    self.flush_batch(
                        meeting_rows, agenda_rows, block_rows, utterance_rows, document_rows
                    )
                    meeting_rows.clear()
                    agenda_rows.clear()
                    block_rows.clear()
                    utterance_rows.clear()
                    document_rows.clear()
        finally:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Collect Korean National Assembly minutes")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--meeting-types", nargs="+", choices=ENDPOINTS, default=list(ENDPOINTS))
    parser.add_argument("--max-meetings", type=int, help="validation limit after discovery")
    parser.add_argument("--skip-pdf", action="store_true")
    parser.add_argument(
        "--pdf-only",
        action="store_true",
        help="deprecated compatibility flag; collection is always PDF-only",
    )
    parser.add_argument("--request-delay", type=float, default=0.05)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument(
        "--use-cached-discovery",
        action="store_true",
        help="read previously stored official API responses from GCS",
    )
    parser.add_argument(
        "--reprocess-existing",
        action="store_true",
        help="replace already loaded meetings with the current parser version",
    )
    parser.add_argument(
        "--meeting-ids",
        nargs="*",
        default=[],
        help="process only these stable IDs, for example committee:56234",
    )
    parser.add_argument("--project", default="proj-aj04-211200020328")
    parser.add_argument("--bucket", default="proj-aj04-211200020328-assembly-us")
    parser.add_argument("--dataset", default="assembly")
    parser.add_argument("--assembly-no", type=int, default=22)
    args = parser.parse_args()

    api_key = os.environ.get("ASSEMBLY_API_KEY")
    if not api_key and not args.use_cached_discovery:
        parser.error("set ASSEMBLY_API_KEY in the environment")
    year_start = date(args.year, 1, 1)
    year_end = date(args.year, 12, 31)
    today = date.today()
    default_end = min(today, year_end) if args.year == today.year else year_end
    start_date = args.start_date or year_start
    end_date = args.end_date or default_end
    if start_date > end_date:
        parser.error("start date must not be after end date")
    if args.batch_size < 1:
        parser.error("batch size must be at least 1")
    if start_date.year != args.year or end_date.year != args.year:
        parser.error("date range must stay inside --year")

    return Config(
        api_key=api_key or "",
        project=args.project,
        bucket=args.bucket,
        dataset=args.dataset,
        assembly_no=args.assembly_no,
        start_date=start_date,
        end_date=end_date,
        meeting_types=tuple(args.meeting_types),
        max_meetings=args.max_meetings,
        download_pdf=not args.skip_pdf,
        request_delay=args.request_delay,
        batch_size=args.batch_size,
        use_cached_discovery=args.use_cached_discovery,
        reprocess_existing=args.reprocess_existing,
        meeting_ids=tuple(args.meeting_ids),
        pdf_only=True,
    )


def main() -> int:
    try:
        Collector(parse_args()).run()
        return 0
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
