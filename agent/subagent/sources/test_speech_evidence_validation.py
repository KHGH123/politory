import json
import importlib.util
import unittest
from pathlib import Path


_MODULE_PATH = Path(__file__).with_name("speech_evidence_validation.py")
_SPEC = importlib.util.spec_from_file_location("speech_evidence_validation", _MODULE_PATH)
validation = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(validation)


class SpeechEvidenceValidationTest(unittest.TestCase):
    def setUp(self):
        self.source = {
            "u1": {
                "utterance_id": "u1",
                "speaker_name": "홍길동",
                "legislator_id": "l1",
                "meeting_date": "2026-01-01",
                "meeting_title": "회의",
                "utterance_text": "정부는 충분한 검토를 거쳐야 합니다.",
                "page_start": 1,
                "page_end": 1,
                "source_pdf_url": "https://example.test/a.pdf",
            }
        }
        item = {
            key: self.source["u1"][key]
            for key in validation.EVIDENCE_FIELDS
            if key != "quote"
        }
        item["quote"] = "충분한 검토를 거쳐야 합니다."
        self.speech_info = {"evidence": [item]}

    def test_accepts_exact_source_quote_and_metadata(self):
        result = validation.validate_speech_info(
            json.dumps(self.speech_info, ensure_ascii=False), self.source, "l1"
        )
        self.assertEqual(result, (True, ""))

    def test_rejects_quote_not_present_in_source(self):
        self.speech_info["evidence"][0]["quote"] = "원문에 없는 주장입니다."
        valid, reason = validation.validate_speech_info(
            json.dumps(self.speech_info, ensure_ascii=False), self.source, "l1"
        )
        self.assertFalse(valid)
        self.assertIn("원문에 없다", reason)

    def test_rejects_changed_metadata(self):
        self.speech_info["evidence"][0]["meeting_date"] = "2026-01-02"
        valid, reason = validation.validate_speech_info(self.speech_info, self.source, "l1")
        self.assertFalse(valid)
        self.assertIn("meeting_date", reason)

    def test_rejects_interpretation_outside_evidence_array(self):
        self.speech_info["interpretation"] = "이 의원은 입장을 바꿨다."
        valid, reason = validation.validate_speech_info(self.speech_info, self.source, "l1")
        self.assertFalse(valid)
        self.assertIn("해석", reason)

    def test_rejects_empty_evidence_to_trigger_retry(self):
        valid, reason = validation.validate_speech_info({"evidence": []}, self.source, "l1")
        self.assertFalse(valid)
        self.assertIn("다시 검색", reason)

    def test_rejects_evidence_without_legislator_id(self):
        """국회의원이 아닌 진술인·정부 관계자 발언(legislator_id=None)은
        이름이 질문 속 의원과 같아 보여도 동명이인일 수 있으므로 거부해야
        한다 (실측: "김민수 의원" 질문에 대한의사협회 진술인 "김민수"의
        발언이 근거로 쓰인 사례)."""
        self.source["u1"]["legislator_id"] = None
        self.speech_info["evidence"][0]["legislator_id"] = None
        valid, reason = validation.validate_speech_info(
            json.dumps(self.speech_info, ensure_ascii=False), self.source, "l1"
        )
        self.assertFalse(valid)
        self.assertIn("legislator_id", reason)

    def test_rejects_evidence_with_blank_legislator_id(self):
        self.source["u1"]["legislator_id"] = "   "
        self.speech_info["evidence"][0]["legislator_id"] = "   "
        valid, reason = validation.validate_speech_info(
            json.dumps(self.speech_info, ensure_ascii=False), self.source, "l1"
        )
        self.assertFalse(valid)
        self.assertIn("legislator_id", reason)

    def test_rejects_search_without_resolved_legislator_id(self):
        valid, reason = validation.validate_speech_info(
            self.speech_info, self.source, None
        )
        self.assertFalse(valid)
        self.assertIn("의원 ID 없이", reason)

    def test_rejects_different_legislator_id(self):
        valid, reason = validation.validate_speech_info(
            self.speech_info, self.source, "different-id"
        )
        self.assertFalse(valid)
        self.assertIn("조회 대상 의원", reason)


if __name__ == "__main__":
    unittest.main()
