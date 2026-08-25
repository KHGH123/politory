from __future__ import annotations

import unittest
from datetime import date

from incremental_pipeline.main import Settings, date_ranges_by_year, speaker_enrichment_command


class DateRangesByYearTest(unittest.TestCase):
    def test_single_year(self) -> None:
        self.assertEqual(
            date_ranges_by_year(date(2026, 8, 18), date(2026, 8, 24)),
            [(date(2026, 8, 18), date(2026, 8, 24))],
        )

    def test_year_boundary(self) -> None:
        self.assertEqual(
            date_ranges_by_year(date(2025, 12, 29), date(2026, 1, 3)),
            [
                (date(2025, 12, 29), date(2025, 12, 31)),
                (date(2026, 1, 1), date(2026, 1, 3)),
            ],
        )

    def test_speaker_enrichment_is_scoped_and_strict(self) -> None:
        settings = Settings(
            project="project",
            dataset="dataset",
            bucket="bucket",
            assembly_no=22,
            start_date=date(2026, 8, 24),
            end_date=date(2026, 8, 25),
            meeting_types=("plenary", "committee"),
            search_data_store_id=None,
            vote_data_store_id=None,
            vertex_project="vertex-project",
            vertex_location="global",
            vertex_timeout_seconds=3600,
            speaker_request_delay=1.5,
            speaker_fetch_attempts=5,
            speaker_max_consecutive_failures=3,
            apply=True,
            keep_delta_tables=False,
        )
        command = speaker_enrichment_command(
            settings, ["committee:1", "plenary:2"]
        )
        self.assertIn("--skip-search-documents", command)
        self.assertIn("--fail-on-rejected", command)
        self.assertEqual(command[command.index("--workers") + 1], "1")
        self.assertEqual(
            [command[index + 1] for index, value in enumerate(command) if value == "--meeting-id"],
            ["committee:1", "plenary:2"],
        )


if __name__ == "__main__":
    unittest.main()
