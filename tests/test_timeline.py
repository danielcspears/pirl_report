import io
import unittest
from unittest.mock import patch

from pirl_parser import PIRLParser


class TimelineTests(unittest.TestCase):
    def setUp(self):
        with patch.object(PIRLParser, "auto_load_default_pirl_file", return_value=False):
            self.parser = PIRLParser(schema_header_path=None)
        self.parser.load_schema_header(io.BytesIO(
            b"100 - Unique Individual Identifier,101 - State Code,200 - Date of Birth,"
            b"900 - Date of Program Entry,901 - Date of Program Exit\n"
        ))

    def timeline_warnings(self, entry_date, exit_date):
        self.parser.parse_pirl_data(
            f"TEST-001,22,19900101,{entry_date},{exit_date}\n".encode(),
            "synthetic.csv"
        )
        return [
            warning for warning in self.parser.pre_discrepancies_list
            if warning["error_code"] == "PRE_CHECK_TIMELINE_INVERT"
        ]

    def test_active_participants_without_an_exit_date_have_no_timeline_warning(self):
        for exit_date in ("", "0", "00000000", " 0 "):
            with self.subTest(exit_date=exit_date):
                self.assertEqual(self.timeline_warnings("20220524", exit_date), [])

    def test_real_exit_before_entry_still_warns(self):
        warnings = self.timeline_warnings("20220524", "20220523")
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["current_value"], "20220523")

    def test_equal_or_later_exit_and_missing_entry_have_no_timeline_warning(self):
        for entry_date, exit_date in (
            ("20220524", "20220524"),
            ("20220524", "20220525"),
            ("", "20220523"),
            ("0", "20220523"),
            ("00000000", "20220523"),
        ):
            with self.subTest(entry_date=entry_date, exit_date=exit_date):
                self.assertEqual(self.timeline_warnings(entry_date, exit_date), [])


if __name__ == "__main__":
    unittest.main()
