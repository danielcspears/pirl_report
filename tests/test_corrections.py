import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pirl_parser import PIRLParser, SCHEMA_CATALOG
from test_upload import server


def csv_bytes(rows):
    output = io.StringIO()
    csv.writer(output, lineterminator='\n').writerows(rows)
    return output.getvalue().encode()


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        with patch.object(PIRLParser, 'auto_load_default_pirl_file', return_value=False):
            self.parser = PIRLParser(schema_header_path=None)
        self.parser_patch = patch.object(server, 'parser', self.parser)
        self.parser_patch.start()
        self.addCleanup(self.parser_patch.stop)
        self.client = TestClient(server.app)

    def row(self, year='2025', **values):
        columns = SCHEMA_CATALOG['schemas'][year]
        defaults = {'0': '000000001', '100': 'TEST-001', '101': 'LA', '200': '19900101'}
        defaults.update(values)
        return [defaults.get(c.split(' - ')[0], '') for c in columns]

    def load(self, rows, year='2025', header=False, filepath=None):
        if header:
            rows = [SCHEMA_CATALOG['schemas'][year]] + rows
        self.parser.parse_pirl_data(csv_bytes(rows), 'synthetic.csv', filepath=filepath)

    def test_full_schema_switch_preserves_all_values_and_108_suffixes(self):
        for year, width, school_col in [('2021', 495, 53), ('2025', 475, 47)]:
            with self.subTest(year=year):
                row = self.row(year, **{'409': '5', '900': '20250101', '108-A': '111', '108-B': '222', '108-C': '333'})
                self.load([row], year)
                self.assertEqual(self.parser.element_number_to_col_index['409'], school_col)
                self.assertEqual(len(self.parser.schema_columns), width)
                self.assertEqual(len({self.parser.element_number_to_col_index[e] for e in ('108-A','108-B','108-C')}), 3)
                exported, name = self.parser.generate_headerless_pirl_csv()
                self.assertEqual(list(csv.reader(io.StringIO(exported.decode()))), [row])
                self.assertIn('corrected', name)

    def test_wips_export_strips_headers_but_disk_save_preserves_source_header(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.csv'
            self.load([self.row()], header=True, filepath=str(path))
            self.assertTrue(self.parser.update_cell_value(0, 12, '19920202'))
            self.assertTrue(self.parser.save_to_disk()[0])
            with path.open(newline='') as f:
                saved = list(csv.reader(f))
            self.assertEqual(saved[0], SCHEMA_CATALOG['schemas']['2025'])
            self.assertEqual(saved[1][12], '19920202')
            export = self.client.get('/api/export-headerless-pirl')
            self.assertEqual(export.headers['cache-control'], 'no-store')
            self.assertEqual(len(list(csv.reader(io.StringIO(export.text)))), 1)
            self.assertEqual(next(csv.reader(io.StringIO(export.text)))[12], '19920202')

    def test_column_position_100_never_edits_element_100(self):
        self.load([self.row()])
        with patch.object(self.parser, 'save_to_disk', return_value=(True, 'Saved')) as save:
            result = self.client.put('/api/cell-update', json={'pirl_row_index': 0, 'col_index': 100, 'new_value': '20250202'})
        self.assertEqual(result.status_code, 200)
        save.assert_called_once()
        self.assertEqual(self.parser.pirl_df.iat[0, 1], 'TEST-001')
        self.assertEqual(self.parser.pirl_df.iat[0, 100], '20250202')
        self.assertTrue(self.parser.update_cell_value(0, '100', '20250303'))
        self.assertEqual(self.parser.pirl_df.iat[0, 1], 'TEST-001')
        self.assertTrue(self.parser.update_cell_value(0, 'element:200', '19930303'))
        self.assertEqual(self.parser.pirl_df.iat[0, 12], '19930303')

    def test_preflight_catches_school_missing_and_invalid_codes(self):
        self.load([
            self.row(**{'900':'20250101'}),
            self.row(**{'100':'TEST-002','900':'00000000'}),
            self.row(**{'100':'TEST-003','900':'20250101','409':'0'}),
            self.row(**{'100':'TEST-004','900':'20250101','409':'5'}),
            self.row(**{'100':'TEST-005','2901':'1','2902':'20250101'}),
        ])
        errors = [e for e in self.parser.pre_discrepancies_list if e['element_number']=='409']
        self.assertEqual({e['pirl_row_index'] for e in errors}, {0,2,4})
        self.assertEqual(errors[0]['entry_date'], '20250101')
        details = self.client.get('/api/record/0').json()
        school = next(f for f in details['fields'] if f['element_number']=='409')
        self.assertEqual(school['col_index'], 47)
        self.assertTrue(school['has_error'])
        self.assertEqual(set(school['code_values']), set('123456'))
        self.assertFalse(self.parser.update_cell_value(0, 47, '9'))
        self.assertTrue(self.parser.update_cell_value(0, 47, '5'))
        self.assertNotIn(0, {e['pirl_row_index'] for e in self.parser.pre_discrepancies_list if e['element_number']=='409'})

    def test_dob_conflicts_clear_after_correction_and_survive_disk_roundtrip(self):
        rows = [self.row(**{'200':'19900101'}), self.row(**{'200':'19900202'}), self.row(**{'100':'', '200':'20000101'}), self.row(**{'100':'','200':'20000202'})]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.csv'
            self.load(rows, filepath=str(path))
            conflicts = [e for e in self.parser.pre_discrepancies_list if e['error_code']=='WIPS_ELEM_200']
            self.assertEqual([e['pirl_row_index'] for e in conflicts], [0,1])
            result = self.client.put('/api/cell-update', json={'pirl_row_index':1,'col_index':12,'new_value':'19900101'})
            self.assertTrue(result.json()['disk_saved'])
            self.assertEqual(result.json()['stats']['edits_count'], 1)
            self.assertEqual(result.json()['stats']['unsaved_edits_count'], 0)
            self.parser.parse_pirl_data(path.read_bytes(), path.name)
            self.assertEqual([e for e in self.parser.pre_discrepancies_list if e['error_code']=='WIPS_ELEM_200'], [])
            self.assertEqual(self.parser.pirl_df.iat[1,12], '19900101')

    def test_upload_cannot_reuse_an_existing_basename_as_original_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'existing.csv'
            path.write_text('original content')
            self.parser.parse_pirl_data(csv_bytes([self.row()]), str(path))
            self.assertIsNone(self.parser.loaded_filepath)
            with patch.object(self.parser, '_write_csv_atomic') as write:
                saved, message = self.parser.save_to_disk()
            self.assertTrue(saved)
            target = write.call_args.args[0]
            self.assertEqual(Path(target).name, 'existing_corrected.csv')
            self.assertNotEqual(target, str(path))
            self.assertIn(str(Path(target).absolute()), message)
            self.assertEqual(path.read_text(), 'original content')

    def test_failed_atomic_save_preserves_source_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source.csv'
            path.write_text('original content')
            self.load([self.row()], filepath=str(path))
            with patch('pirl_parser.os.replace', side_effect=OSError('disk failure')):
                result = self.client.put('/api/cell-update', json={'pirl_row_index':0,'col_index':12,'new_value':'19920202'})
            self.assertFalse(result.json()['disk_saved'])
            self.assertEqual(result.json()['stats']['unsaved_edits_count'], 1)
            self.assertEqual(path.read_text(), 'original content')
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertEqual(next(csv.reader(io.StringIO(self.client.get('/api/export-headerless-pirl').text)))[12], '19920202')

    def test_malformed_column_counts_leave_loaded_dataset_intact(self):
        self.load([self.row()])
        for rows in ([['1','2']], [self.row(), self.row()[:-1]], [self.row(), self.row()+['extra']]):
            result = self.client.post('/api/upload', files={'pirl_file':('bad.csv',csv_bytes(rows),'text/csv')})
            self.assertEqual(result.status_code, 422)
            self.assertEqual(len(self.parser.pirl_df),1)
            self.assertEqual(len(self.parser.pirl_df.columns),475)

    def test_combined_markdown_wips_paste_keeps_each_sections_element(self):
        text = '''##### Error Message for Element No. 409
B) IF [Participant] THEN School Status (PIRL 409) must NOT be blank
| **Row Number** | **Unique Individual Identifier** | **Value Provided** |
| :--- | :--- | :--- |
| | | |
1
|    |
| :- |
TEST-001
|    |
| :- |
No Value Provided
##### Error Message for Element No. 200
C) IF multiple records have the same Identifier THEN DOB must match
2
|    |
| :- |
TEST-002
|    |
| :- |
"19900101"
3
TEST-002
"19900202"
'''
        self.load([self.row(),self.row(**{'100':'TEST-002'}),self.row(**{'100':'TEST-002','200':'19900202'})])
        errors = self.parser.parse_pasted_wips_text(text)
        self.assertEqual([e['element_number'] for e in errors], ['409','200','200'])
        self.assertEqual([e['pirl_row_index'] for e in errors], [0,1,2])
        self.assertEqual(errors[0]['current_value'], '')
        self.assertIn('School Status',errors[0]['error_message'])

    def test_stale_wips_row_does_not_link_to_another_participant(self):
        self.load([self.row(),self.row(**{'100':'TEST-002'})])
        errors = self.parser.parse_pasted_wips_text('Error Message for Element No. 409\n2\nTEST-001\nNo Value Provided')
        self.assertEqual(errors[0]['pirl_row_index'],0)

    def test_export_without_loaded_dataset_is_a_readable_error(self):
        self.assertEqual(self.client.get('/api/export-headerless-pirl').status_code,400)


if __name__ == '__main__':
    unittest.main()
