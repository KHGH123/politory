import copy
import importlib.util
import json
import unittest
from pathlib import Path


_MODULE_PATH = Path(__file__).with_name("action_evidence_validation.py")
_SPEC = importlib.util.spec_from_file_location("action_evidence_validation", _MODULE_PATH)
validation = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(validation)


class ActionEvidenceValidationTest(unittest.TestCase):
    def setUp(self):
        self.member = {
            "document_id": "vote_1_yes_1",
            "vote_id": "vote:1",
            "document_type": "assembly_vote_member",
            "member_name": "홍길동",
            "legislator_id": "krna:1",
            "identity_status": "MATCHED",
            "vote_title": "테스트 법률안",
            "vote_date": "2026-01-01",
            "meeting_id": "plenary:1",
            "meeting_title": "제1차 본회의",
            "choice": "YES",
            "choice_ko": "찬성",
            "total_count": 3,
            "yes_count": 2,
            "no_count": 1,
            "abstain_count": 0,
            "content": "홍길동 의원은 전자투표에서 찬성하였다.",
            "page_start": 10,
            "page_end": 11,
            "source_pdf_url": "https://example.test/minutes.pdf",
        }
        self.sources = {self.member["document_id"]: self.member}

    def action_info(self, item=None):
        return {"evidence": [copy.deepcopy(item or self.member)]}

    def test_accepts_exact_member_vote(self):
        result = validation.validate_action_info(
            json.dumps(self.action_info(), ensure_ascii=False), self.sources
        )
        self.assertEqual(result, (True, ""))

    def test_rejects_changed_choice(self):
        info = self.action_info()
        info["evidence"][0]["choice"] = "NO"
        valid, reason = validation.validate_action_info(info, self.sources)
        self.assertFalse(valid)
        self.assertIn("choice", reason)

    def test_rejects_changed_counts(self):
        info = self.action_info()
        info["evidence"][0]["yes_count"] = 3
        valid, reason = validation.validate_action_info(info, self.sources)
        self.assertFalse(valid)
        self.assertIn("yes_count", reason)

    def test_rejects_unknown_document(self):
        info = self.action_info()
        info["evidence"][0]["document_id"] = "invented"
        valid, reason = validation.validate_action_info(info, self.sources)
        self.assertFalse(valid)
        self.assertIn("실제 MCP", reason)

    def test_rejects_ambiguous_member(self):
        source = copy.deepcopy(self.member)
        source["identity_status"] = "AMBIGUOUS"
        info = self.action_info(source)
        valid, reason = validation.validate_action_info(
            info, {source["document_id"]: source}
        )
        self.assertFalse(valid)
        self.assertIn("MATCHED", reason)

    def test_accepts_summary_with_null_member_fields(self):
        summary = copy.deepcopy(self.member)
        summary["document_id"] = "vote_1_summary"
        summary["document_type"] = "assembly_vote_summary"
        for field in (
            "member_name",
            "legislator_id",
            "identity_status",
            "choice",
            "choice_ko",
        ):
            summary[field] = None
        result = validation.validate_action_info(
            self.action_info(summary), {summary["document_id"]: summary}
        )
        self.assertEqual(result, (True, ""))

    def test_rejects_summary_with_invented_choice(self):
        summary = copy.deepcopy(self.member)
        summary["document_id"] = "vote_1_summary"
        summary["document_type"] = "assembly_vote_summary"
        for field in ("member_name", "legislator_id", "identity_status"):
            summary[field] = None
        valid, reason = validation.validate_action_info(
            self.action_info(summary), {summary["document_id"]: summary}
        )
        self.assertFalse(valid)
        self.assertIn("choice", reason)

    def test_rejects_empty_evidence_to_trigger_retry(self):
        valid, reason = validation.validate_action_info({"evidence": []}, self.sources)
        self.assertFalse(valid)
        self.assertIn("다시 검색", reason)

    def test_collects_wrapped_and_direct_tool_results(self):
        rows = [self.member]
        self.assertEqual(validation.collect_tool_votes(rows), rows)
        self.assertEqual(validation.collect_tool_votes({"result": rows}), rows)
        self.assertEqual(
            validation.collect_tool_votes(
                {"structuredContent": {"result": rows}, "isError": False}
            ),
            rows,
        )


if __name__ == "__main__":
    unittest.main()
