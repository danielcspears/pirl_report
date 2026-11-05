import importlib
import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pirl_parser import PIRLParser


# Keep regression tests independent of participant files in the project directory.
with patch.object(PIRLParser, "auto_load_default_pirl_file", return_value=False):
    server = importlib.import_module("app")


class UploadTests(unittest.TestCase):
    def setUp(self):
        with patch.object(PIRLParser, "auto_load_default_pirl_file", return_value=False):
            self.parser = PIRLParser(schema_header_path=None)
        self.parser.load_schema_header(io.BytesIO(
            b"0 - OBS Number,100 - Unique Individual Identifier,101 - State Code,200 - Date of Birth\n"
        ))
        self.parser_patch = patch.object(server, "parser", self.parser)
        self.parser_patch.start()
        self.addCleanup(self.parser_patch.stop)
        self.client = TestClient(server.app)

    def test_upload_loads_headerless_csv_and_allows_preflight(self):
        response = self.client.post("/api/upload", files={
            "pirl_file": ("synthetic.csv", b"1,TEST-001,22,19900101\n", "text/csv")
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pirl_records_processed"], 1)
        self.assertEqual(self.client.get("/api/stats").json()["total_pirl_records"], 1)
        self.assertEqual(self.client.post("/api/preflight-check").status_code, 200)

    def test_refresh_finds_a_csv_added_after_startup(self):
        def load_local_file():
            self.parser.parse_pirl_data(b"1,TEST-001,22,19900101\n", "synthetic.csv")
            return True

        with patch.object(self.parser, "auto_load_default_pirl_file", side_effect=load_local_file) as load:
            response = self.client.post("/api/upload")
        load.assert_called_once()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pirl_records_processed"], 1)

    def test_refresh_without_a_dataset_explains_how_to_load_one(self):
        with patch.object(self.parser, "auto_load_default_pirl_file", return_value=False):
            response = self.client.post("/api/upload")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose a PIRL Data File", response.json()["detail"])

    def test_empty_and_invalid_uploads_return_readable_errors(self):
        self.parser.parse_pirl_data(b"1,TEST-001,22,19900101\n", "previous.csv")
        for contents in (b"", b'"unterminated', b"\xff\xfeinvalid"):
            with self.subTest(contents=contents):
                response = self.client.post("/api/upload", files={
                    "pirl_file": ("invalid.csv", contents, "text/csv")
                })
                self.assertEqual(response.status_code, 422)
                self.assertIn("Could not read the PIRL CSV", response.json()["detail"])
                self.assertEqual(self.parser.loaded_filename, "previous.csv")
                self.assertEqual(len(self.parser.pirl_df), 1)

    def test_upload_does_not_keep_a_previous_datasets_save_path(self):
        self.parser.loaded_filepath = "/previous/dataset.csv"
        response = self.client.post("/api/upload", files={
            "pirl_file": ("synthetic.csv", b"1,TEST-001,22,19900101\n", "text/csv")
        })
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.parser.loaded_filepath)

    def test_table_pagination_and_literal_filters(self):
        self.parser.parse_pirl_data(
            b"1,TEST.A,22,19900101\n2,TEST-B,23,19920202\n3,TEST-C,22,19930303\n",
            "synthetic.csv"
        )
        page = self.parser.get_pirl_table_data(page=2, page_size=2)
        self.assertEqual(page["total_records"], 3)
        self.assertEqual([row["uiid"] for row in page["records"]], ["TEST-C"])
        matches = self.parser.get_pirl_table_data(uiid="test.", state_id="22")
        self.assertEqual(matches["total_records"], 1)
        self.assertEqual(matches["records"][0]["uiid"], "TEST.A")
        phrase = self.parser.get_pirl_table_data(query="test-b 23")
        self.assertEqual(phrase["total_records"], 1)
        self.assertEqual(phrase["records"][0]["uiid"], "TEST-B")


if __name__ == "__main__":
    unittest.main()
