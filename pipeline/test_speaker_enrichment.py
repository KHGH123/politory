import unittest
from datetime import date
from unittest.mock import patch

from step01_collect_assembly import MinutesParser
from step08_enrich_speaker_ids import (
    Meeting,
    best_text_similarity,
    backfill_verified_links,
    fetch_validated_attendees,
    leading_anchor,
    name_keys,
    parse_args,
    parse_attendees,
    resolve_member,
)
from step03_normalize_legislators import apply_updates, is_legislative_role


class SpeakerEnrichmentTest(unittest.TestCase):
    def test_step03_preserves_existing_legislator_ids(self):
        class Result:
            def result(self):
                return None

        class Client:
            sql = ""

            def query(self, sql):
                self.sql = sql
                return Result()

        client = Client()
        apply_updates(client, "project", "dataset")
        self.assertNotIn("SET legislator_id = NULL", client.sql)
        self.assertIn("ASSERT NOT EXISTS", client.sql)
        self.assertIn("u.legislator_id IS NULL", client.sql)

    def test_official_viewer_safe_cli_defaults(self):
        with patch("sys.argv", ["step08_enrich_speaker_ids.py"]):
            args = parse_args()
        self.assertEqual(args.workers, 1)
        self.assertEqual(args.request_delay, 1.5)
        self.assertEqual(args.fetch_attempts, 5)
        self.assertEqual(args.max_consecutive_source_failures, 3)

    def test_verified_link_backfill_can_be_meeting_scoped(self):
        class Result:
            def result(self):
                return None

        class Client:
            sql = ""
            job_config = None

            def query(self, sql, job_config=None):
                self.sql = sql
                self.job_config = job_config
                return Result()

        client = Client()
        backfill_verified_links(
            client,
            "project",
            "dataset",
            22,
            update_search_documents=False,
            meeting_ids=["committee:1"],
        )
        self.assertIn("M.meeting_id IN UNNEST(@meeting_ids)", client.sql)
        self.assertNotIn("`project.dataset.search_documents`", client.sql)
        parameters = {parameter.name for parameter in client.job_config.query_parameters}
        self.assertEqual(parameters, {"assembly_no", "meeting_ids"})

    @patch("step08_enrich_speaker_ids.random.uniform", return_value=0.0)
    @patch("step08_enrich_speaker_ids.time.sleep")
    @patch("step08_enrich_speaker_ids.fetch_html", return_value=b"")
    def test_official_viewer_uses_exponential_backoff(
        self, _fetch_html, sleep, _uniform
    ):
        meeting = Meeting(
            meeting_id="committee:1",
            confer_num=1,
            assembly_no=22,
            meeting_date=date(2026, 1, 1),
            meeting_type="committee",
            committee_name="법제사법위원회",
            title="test",
            official_url="https://example.invalid/xml.do?id=1&type=summary",
        )
        with self.assertRaises(RuntimeError):
            fetch_validated_attendees(meeting, attempts=3, base_backoff=2.0)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 4.0])

    def test_named_committee_chair_is_legislative_role(self):
        self.assertTrue(is_legislative_role("법제사법위원장"))
        self.assertTrue(is_legislative_role("국토교통위원장"))

    def test_parser_keeps_official_profile_evidence(self):
        parser = MinutesParser()
        parser.feed(
            '<div class="minutes_body">'
            '<div class="item1 speaker spk_mem" data-mem_id="7223" '
            'data-name="朴芝源" data-pos="의원">'
            '<div class="man"><a href="https://www.assembly.go.kr/members/22nd/PARKJIWON">'
            '<img src="https://www.assembly.go.kr/static/portal/img/openassm/H7X3372O.jpg">'
            '<span class="area">(전북 군산시김제시부안군을)</span></a></div>'
            '<span class="spk_sub" id="spk_sub1-1">반갑습니다.</span>'
            '</div></div>'
        )
        speech = parser.blocks()[0]
        self.assertEqual(speech["member_id"], "7223")
        self.assertEqual(speech["profile_image_url"].rsplit("/", 1)[-1], "H7X3372O.jpg")
        self.assertEqual(speech["district_label"], "전북 군산시김제시부안군을")

    def test_parenthesized_hangul_and_hanja_are_aliases(self):
        self.assertEqual(name_keys("朴芝源(박지원)"), {"朴芝源", "박지원", "朴芝源(박지원)"})

    def test_official_code_in_profile_image_wins(self):
        blocks = [{"name": "朴芝源", "profile_image_url": "https://example/H7X3372O.jpg"}]
        index = {
            "by_code": {"H7X3372O": {"legislator_id": "krna:H7X3372O"}},
            "by_image": {},
            "by_name": {},
        }
        self.assertEqual(
            resolve_member(blocks, index),
            ("krna:H7X3372O", "OFFICIAL_CODE_IN_PROFILE_IMAGE", 1.0),
        )

    def test_attendance_name_and_district_resolve_duplicate_name(self):
        html = b'''<input class="speakerMem" data-mem_id="6735"
          data-name="\xeb\xb0\x95\xec\xa7\x80\xec\x9b\x90" data-pos="\xec\x9c\x84\xec\x9b\x90">
          <label><span class="area">(\xec\xa0\x84\xeb\x82\xa8 \xed\x95\xb4\xeb\x82\xa8\xea\xb5\xb0\xec\x99\x84\xeb\x8f\x84\xea\xb5\xb0\xec\xa7\x84\xeb\x8f\x84\xea\xb5\xb0)</span></label>'''
        _, attendees = parse_attendees(html)
        index = {
            "by_code": {},
            "by_image": {},
            "by_name": {
                "\ubc15\uc9c0\uc6d0": [
                    {"legislator_id": "krna:old"},
                    {"legislator_id": "krna:new"},
                ]
            },
            "by_district": {
                "\uc804\ub0a8\ud574\ub0a8\uad70\uc644\ub3c4\uad70\uc9c4\ub3c4\uad70": [
                    {"legislator_id": "krna:old"}
                ]
            },
        }
        self.assertEqual(
            resolve_member(attendees, index),
            ("krna:old", "OFFICIAL_ATTENDEE_NAME_AND_DISTRICT", 0.995),
        )

    def test_short_first_sentence_extends_to_safe_anchor(self):
        text = "예. 그렇게 하겠습니다. 추가 자료도 오늘 회의가 끝나기 전에 제출하겠습니다."
        anchor = leading_anchor(text)
        self.assertGreaterEqual(len(anchor), 30)
        self.assertEqual(best_text_similarity(text, [text]), 1.0)
        self.assertEqual(best_text_similarity(text, ["예. 다른 답변입니다."]), 0.0)


if __name__ == "__main__":
    unittest.main()
