import io
import re
import os
import glob
import csv
import json
import tempfile
from pathlib import Path
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

SCHEMA_CATALOG = json.loads(Path(__file__).with_name("pirl_schemas.json").read_text())
ELEMENT_PATTERN = r"^(\d+(?:-[A-Za-z]|[A-Za-z])?)\b"
SCHOOL_STATUS_CODES = {
    "1": "In school: secondary school or less",
    "2": "In school: alternative school",
    "3": "In school: postsecondary school",
    "4": "Not attending school or secondary school dropout (beyond compulsory school age)",
    "5": "Not attending school: secondary school graduate or recognized equivalent",
    "6": "Not attending school: within compulsory school age, without a diploma or equivalent",
}

class PIRLParser:
    def __init__(self, schema_header_path: Optional[str] = "PIRL Schema Header.csv"):
        self.schema_columns: List[str] = []
        self.element_number_to_col_index: Dict[str, int] = {}
        self.col_index_to_element_number: Dict[int, str] = {}
        self.pirl_df: Optional[pd.DataFrame] = None
        self.errors_list: List[Dict[str, Any]] = []
        self.pre_discrepancies_list: List[Dict[str, Any]] = []
        self.loaded_filepath: Optional[str] = None
        self.loaded_filename: str = "wp_pirl.csv"
        self.has_header: bool = False
        self.edits_count: int = 0
        self.saved_edits_count: int = 0
        self.last_saved_path: Optional[str] = None
        self.schema_name: str = "Custom schema"
        self.schema_notice: str = ""

        if schema_header_path:
            self.load_schema_header(schema_header_path)
        if not self.schema_columns:
            self._set_schema(SCHEMA_CATALOG["schemas"]["2025"], "Full PIRL 2025")

        # Auto-detect existing 22_FULL_PIRL data file in root directory if present
        self.auto_load_default_pirl_file()

    def auto_load_default_pirl_file(self) -> bool:
        """Locates and auto-loads 22_FULL_PIRL*.csv file if present in workspace root."""
        if self.pirl_df is not None:
            return True

        pirl_candidates = [
            f for f in glob.glob("22_FULL_PIRL*.csv")
            if not os.path.basename(f).startswith("._")
        ]
        if pirl_candidates and os.path.exists(pirl_candidates[0]):
            try:
                abs_path = os.path.abspath(pirl_candidates[0])
                print(f"Auto-loading local dataset: {abs_path}")
                with open(abs_path, "rb") as f:
                    content = f.read()
                self.parse_pirl_data(content, pirl_candidates[0], filepath=abs_path)
                return True
            except Exception as e:
                print(f"Auto-load of {pirl_candidates[0]} failed: {e}")
        return False

    def load_schema_header(self, filepath_or_buffer, allow_layout_change: bool = False) -> List[str]:
        """Loads PIRL schema header columns and builds element mapping index (filters out empty/unnamed trailing cols)."""
        try:
            df = pd.read_csv(filepath_or_buffer, nrows=0)
            columns = [col.strip() for col in df.columns if col.strip() and not col.startswith("Unnamed:")]
            matches = [re.match(ELEMENT_PATTERN, c) for c in columns]
            if not columns or not all(matches) or len({m.group(1) for m in matches}) != len(columns):
                raise ValueError("Schema headers must start with distinct PIRL element numbers (for example, 409 - School Status).")
            if self.pirl_df is not None and not allow_layout_change and (
                len(columns) != len(self.pirl_df.columns)
                or [re.match(ELEMENT_PATTERN, c).group(1) for c in columns]
                != list(self.col_index_to_element_number.values())
            ):
                raise ValueError("Schema width does not match the loaded dataset. Load the matching data and schema together.")
            self._set_schema(columns)
            if self.pirl_df is not None and not allow_layout_change:
                self.pirl_df.columns = columns
                self.run_pirl_pre_validation_checks()
                self._link_errors_to_pirl()
            return self.schema_columns
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as exc:
            raise ValueError("Could not read the PIRL schema header.") from exc

    def _set_schema(self, columns: List[str], name: str = "Custom schema"):
        self.schema_columns = list(columns)
        self.schema_name = name
        self.element_number_to_col_index = {}
        self.col_index_to_element_number = {}
        for idx, col in enumerate(columns):
            match = re.match(ELEMENT_PATTERN, col)
            elem_num = match.group(1) if match else str(idx)
            self.element_number_to_col_index[elem_num] = idx
            self.element_number_to_col_index[col.lower()] = idx
            self.col_index_to_element_number[idx] = elem_num

    def _write_csv_atomic(self, target_path: str):
        """Replace a complete file only after serialization succeeds."""
        directory = os.path.dirname(os.path.abspath(target_path))
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=directory, delete=False) as output:
                temp_path = output.name
                self.pirl_df.to_csv(output, header=self.has_header, index=False, lineterminator="\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, target_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def save_to_disk(self) -> Tuple[bool, str]:
        """Saves corrected dataset directly back to disk in its original CSV format (headerless if originally headerless)."""
        if self.pirl_df is None:
            return False, "No PIRL dataset loaded."

        # A browser upload supplies a name, never the original computer path.
        # Save a clearly named server-side copy rather than implying that it was overwritten.
        target_path = self.loaded_filepath or str(Path(__file__).parent / (Path(self.loaded_filename).stem + "_corrected.csv"))

        try:
            self._write_csv_atomic(target_path)
            self.last_saved_path = os.path.abspath(target_path)
            self.saved_edits_count = self.edits_count
            return True, f"Saved {self.edits_count} correction(s), {len(self.pirl_df):,} rows × {len(self.pirl_df.columns)} columns to {self.last_saved_path}."
        except Exception as e:
            print(f"Failed saving to disk: {e}")
            return False, f"Failed to save file to disk: {e}"

    def parse_pirl_data(self, file_content: bytes, filename: str, filepath: Optional[str] = None) -> int:
        """Parses PIRL CSV file content (headerless or with header matching schema)."""
        text = file_content.decode("utf-8-sig")
        rows = csv.reader(io.StringIO(text), strict=True)
        first_row = next((row for row in rows if row), None)
        if first_row is None:
            raise pd.errors.EmptyDataError("The PIRL CSV contains no data rows.")
        width = len(first_row)
        for row_number, row in enumerate(rows, 2):
            if row and len(row) != width:
                raise ValueError(f"CSV row {row_number} has {len(row)} columns; expected {width}. No data was loaded.")

        has_header = bool(re.match(ELEMENT_PATTERN, first_row[0]) and " - " in first_row[0])
        columns = self.schema_columns
        schema_name = self.schema_name
        notice = ""
        official = next(((year, cols) for year, cols in SCHEMA_CATALOG["schemas"].items() if len(cols) == width), None)
        if official:
            year, official_columns = official
            schema_name = f"Full PIRL {year}"
            columns = self.schema_columns if self.schema_columns == official_columns or (
                len(self.schema_columns) == width
                and list(self.col_index_to_element_number.values()) == [re.match(ELEMENT_PATTERN, c).group(1) for c in official_columns]
            ) else official_columns
            if width != len(self.schema_columns):
                notice = f"Selected {schema_name} ({width} columns); the previous schema had {len(self.schema_columns)} columns."
            if has_header and [re.match(ELEMENT_PATTERN, c).group(1) if re.match(ELEMENT_PATTERN, c) else None for c in first_row] != [re.match(ELEMENT_PATTERN, c).group(1) for c in columns]:
                raise ValueError("CSV headers do not match the official PIRL element order. Use the matching schema and column order.")
        elif width != len(columns):
            raise ValueError(f"CSV has {width} columns, but the selected schema has {len(columns)}. Upload a matching schema; columns will not be padded or discarded.")

        df = pd.read_csv(io.StringIO(text), header=0 if has_header else None, dtype=str, keep_default_na=False)
        df.columns = columns

        if df.empty:
            raise pd.errors.EmptyDataError("The PIRL CSV contains no data rows.")

        df = df.fillna("").astype(str)
        self._set_schema(columns, schema_name)
        self.schema_notice = notice
        self.loaded_filename = Path(filename.replace("\\", "/")).name
        self.loaded_filepath = os.path.abspath(filepath) if filepath else None
        self.has_header = has_header
        self.edits_count = self.saved_edits_count = 0
        self.last_saved_path = None
        self.pirl_df = df
        
        # Run pre-validation checks and re-link DOL errors
        self.run_pirl_pre_validation_checks()
        if self.errors_list:
            self._link_errors_to_pirl()
            
        return len(df)

    def run_pirl_pre_validation_checks(self) -> List[Dict[str, Any]]:
        """Scans the PIRL dataframe for cross-field logical discrepancies before WIPS upload (vectorized for speed)."""
        if self.pirl_df is None or self.pirl_df.empty:
            self.pre_discrepancies_list = []
            return []

        df = self.pirl_df
        discrepancies = []

        def get_series(elem_num: str) -> pd.Series:
            idx = self.element_number_to_col_index.get(elem_num)
            if idx is not None and idx < len(df.columns):
                return df.iloc[:, idx].astype(str).str.strip()
            return pd.Series([""] * len(df), index=df.index)

        def get_col_name(elem_num: str) -> str:
            idx = self.element_number_to_col_index.get(elem_num)
            if idx is not None and idx < len(self.schema_columns):
                return self.schema_columns[idx]
            return f"Element {elem_num}"

        uiid_s = get_series("100")
        state_code_s = get_series("101")
        dob_s = get_series("200")
        veteran_s = get_series("300")
        v_elig_s = get_series("301")
        v_sep_s = get_series("304")
        entry_date_s = get_series("900")
        exit_date_s = get_series("901")
        training_flag_s = get_series("1300")
        training_date_s = get_series("1302")
        training_type_s = get_series("1303")
        emp_2nd_qtr_s = get_series("1602")
        earn_2nd_qtr_s = get_series("1704")

        # Check 1: User ID / Unique ID missing or invalid
        chk1_mask = uiid_s.isna() | uiid_s.isin(["", "0", "000000000", "nan", "None"])
        if chk1_mask.any():
            elem_name = get_col_name("100")
            for r_idx in df.index[chk1_mask]:
                val = uiid_s.at[r_idx]
                discrepancies.append({
                    "id": len(discrepancies) + 1,
                    "pirl_row_index": int(r_idx),
                    "row_number": str(int(r_idx) + 1),
                    "uiid": val or "MISSING",
                    "element": "100",
                    "element_number": "100",
                    "element_col_name": elem_name,
                    "error_code": "PRE_CHECK_UIID",
                    "error_message": "Pre-Check Warning: Element 100 (Unique Individual Identifier) is missing or invalid.",
                    "current_value": val,
                    "severity": "Warning",
                    "is_pre_check": True
                })

        # Check 2: State missing
        chk2_mask = state_code_s.isna() | (state_code_s == "")
        if chk2_mask.any():
            elem_name = get_col_name("101")
            for r_idx in df.index[chk2_mask]:
                discrepancies.append({
                    "id": len(discrepancies) + 1,
                    "pirl_row_index": int(r_idx),
                    "row_number": str(int(r_idx) + 1),
                    "uiid": uiid_s.at[r_idx],
                    "element": "101",
                    "element_number": "101",
                    "element_col_name": elem_name,
                    "error_code": "PRE_CHECK_STATE",
                    "error_message": "Pre-Check Warning: Element 101 (State Code of Residence) is blank.",
                    "current_value": "",
                    "severity": "Warning",
                    "is_pre_check": True
                })

        # Check 3: Invalid Date of Birth
        chk3_mask = dob_s.isna() | (dob_s == "") | (dob_s == "00000000") | (dob_s.str.len() != 8) | (~dob_s.str.isdigit())
        if chk3_mask.any():
            elem_name = get_col_name("200")
            for r_idx in df.index[chk3_mask]:
                val = dob_s.at[r_idx]
                discrepancies.append({
                    "id": len(discrepancies) + 1,
                    "pirl_row_index": int(r_idx),
                    "row_number": str(int(r_idx) + 1),
                    "uiid": uiid_s.at[r_idx],
                    "element": "200",
                    "element_number": "200",
                    "element_col_name": elem_name,
                    "error_code": "PRE_CHECK_DOB",
                    "error_message": "Pre-Check Warning: Element 200 (Date of Birth) is invalid or formatted as 00000000.",
                    "current_value": val,
                    "severity": "Warning",
                    "is_pre_check": True
                })

        # Check 4: Training Flag vs Training Date/Type Alignment
        chk4_mask = (training_flag_s == "1") & (training_date_s == "") & (training_type_s == "")
        if chk4_mask.any():
            elem_name = get_col_name("1300")
            for r_idx in df.index[chk4_mask]:
                val = training_flag_s.at[r_idx]
                discrepancies.append({
                    "id": len(discrepancies) + 1,
                    "pirl_row_index": int(r_idx),
                    "row_number": str(int(r_idx) + 1),
                    "uiid": uiid_s.at[r_idx],
                    "element": "1300",
                    "element_number": "1300",
                    "element_col_name": elem_name,
                    "error_code": "PRE_CHECK_TRAINING_ALIGN",
                    "error_message": "Pre-Check Warning: Element 1300 indicates Received Training ('1') but Date Entered Training (1302) and Type of Training (1303) are blank.",
                    "current_value": val,
                    "severity": "Warning",
                    "is_pre_check": True
                })

        # Check 5: Timeline Logical Inversion (Exit Date < Entry Date)
        # Blank and all-zero dates are unset. Active participants may have no exit.
        valid_dates_mask = (
            entry_date_s.str.isdigit()
            & exit_date_s.str.isdigit()
            & ~entry_date_s.str.fullmatch(r"0+", na=False)
            & ~exit_date_s.str.fullmatch(r"0+", na=False)
        )
        if valid_dates_mask.any():
            en_int = pd.to_numeric(entry_date_s[valid_dates_mask], errors='coerce')
            ex_int = pd.to_numeric(exit_date_s[valid_dates_mask], errors='coerce')
            inverted_mask = (ex_int < en_int).fillna(False)
            inverted_indices = inverted_mask.index[inverted_mask]
            if len(inverted_indices) > 0:
                elem_name = get_col_name("901")
                for r_idx in inverted_indices:
                    en_val = entry_date_s.at[r_idx]
                    ex_val = exit_date_s.at[r_idx]
                    discrepancies.append({
                        "id": len(discrepancies) + 1,
                        "pirl_row_index": int(r_idx),
                        "row_number": str(int(r_idx) + 1),
                        "uiid": uiid_s.at[r_idx],
                        "element": "901",
                        "element_number": "901",
                        "element_col_name": elem_name,
                        "error_code": "PRE_CHECK_TIMELINE_INVERT",
                        "error_message": f"Pre-Check Warning: Program Exit Date (901: '{ex_val}') is earlier than Program Entry Date (900: '{en_val}').",
                        "current_value": ex_val,
                        "severity": "Warning",
                        "is_pre_check": True
                    })

        # Check 6: Veteran Flag vs Veteran Details
        chk6_mask = (veteran_s == "1") & (v_elig_s == "") & (v_sep_s == "")
        if chk6_mask.any():
            elem_name = get_col_name("300")
            for r_idx in df.index[chk6_mask]:
                val = veteran_s.at[r_idx]
                discrepancies.append({
                    "id": len(discrepancies) + 1,
                    "pirl_row_index": int(r_idx),
                    "row_number": str(int(r_idx) + 1),
                    "uiid": uiid_s.at[r_idx],
                    "element": "300",
                    "element_number": "300",
                    "element_col_name": elem_name,
                    "error_code": "PRE_CHECK_VETERAN_ALIGN",
                    "error_message": "Pre-Check Warning: Element 300 indicates Veteran Status ('1') but Eligible Veteran Status (301) and Military Separation Date (304) are blank.",
                    "current_value": val,
                    "severity": "Warning",
                    "is_pre_check": True
                })

        # Check 7: Employment Flag vs $0 Quarter Earnings
        chk7_mask = (emp_2nd_qtr_s == "1") & (earn_2nd_qtr_s.isin(["", "0", "0.00", "$0", "0.0"]))
        if chk7_mask.any():
            elem_name = get_col_name("1704")
            for r_idx in df.index[chk7_mask]:
                val = earn_2nd_qtr_s.at[r_idx]
                discrepancies.append({
                    "id": len(discrepancies) + 1,
                    "pirl_row_index": int(r_idx),
                    "row_number": str(int(r_idx) + 1),
                    "uiid": uiid_s.at[r_idx],
                    "element": "1704",
                    "element_number": "1704",
                    "element_col_name": elem_name,
                    "error_code": "PRE_CHECK_EARNINGS_ALIGN",
                    "error_message": "Pre-Check Warning: Participant is flagged as Employed in 2nd Qtr After Exit (1602 = '1') but 2nd Qtr Earnings (1704) is $0 or blank.",
                    "current_value": val,
                    "severity": "Warning",
                    "is_pre_check": True
                })

        # Check 8: Duplicate UIID records with conflicting Dates of Birth (WIPS Element 200 Rule)
        valid_uiids = ~uiid_s.str.lower().isin(["", "0", "nan", "none"]) & ~uiid_s.str.fullmatch(r"0+", na=False)
        if valid_uiids.any() and "200" in self.element_number_to_col_index:
            dob_series = dob_s
            grouped_dobs = dob_s[valid_uiids].groupby(uiid_s[valid_uiids]).nunique()
            conflict_uiids = set(grouped_dobs[grouped_dobs > 1].index)
            
            if conflict_uiids:
                elem_name = get_col_name("200")
                for r_idx in df.index[uiid_s.isin(conflict_uiids)]:
                    val = dob_series.at[r_idx]
                    discrepancies.append({
                        "id": len(discrepancies) + 1,
                        "pirl_row_index": int(r_idx),
                        "row_number": str(int(r_idx) + 1),
                        "uiid": uiid_s.at[r_idx],
                        "element": "200",
                        "element_number": "200",
                        "element_col_name": elem_name,
                        "error_code": "WIPS_ELEM_200",
                        "error_message": "C) IF multiple records have the same Unique Individual Identifier (WIOA) (PIRL 100), THEN each record must have the same Date of Birth (WIOA) (PIRL 200)",
                        "current_value": val,
                        "severity": "Error",
                        "is_pre_check": True
                    })

        # A program entry date establishes participation for this local check.
        # All-zero dates are unset; pre-apprenticeship has its own mandatory rule.
        if "409" in self.element_number_to_col_index:
            school_s = get_series("409")
            participant = entry_date_s.str.fullmatch(r"\d{8}", na=False) & (entry_date_s != "00000000")
            pre_apprentice = ~get_series("2901").isin(["", "0"]) & ~get_series("2902").isin(["", "0", "00000000"])
            missing_school = (participant | pre_apprentice) & school_s.eq("")
            invalid_school = school_s.ne("") & ~school_s.isin(SCHOOL_STATUS_CODES)
            for r_idx in df.index[missing_school | invalid_school]:
                discrepancies.append({
                    "id": len(discrepancies) + 1,
                    "pirl_row_index": int(r_idx), "row_number": str(int(r_idx) + 1),
                    "uiid": uiid_s.at[r_idx], "element": "409", "element_number": "409",
                    "element_col_name": get_col_name("409"), "error_code": "WIPS_ELEM_409",
                    "error_message": (
                        "B) IF [Participant] THEN School Status at Program Entry (WIOA) (PIRL 409) must NOT be blank"
                        if missing_school.at[r_idx] and participant.at[r_idx] else
                        "PIRL 409 is required when pre-apprenticeship status and enrollment date are provided."
                        if missing_school.at[r_idx] else
                        "School Status at Program Entry (PIRL 409) must be a code from 1 through 6."
                    ),
                    "current_value": school_s.at[r_idx], "severity": "Error", "is_pre_check": True,
                })

        for err in discrepancies:
            row = df.iloc[err["pirl_row_index"]]
            err.update(self._row_context(row))
        self.pre_discrepancies_list = discrepancies
        return discrepancies


    def parse_error_report(self, file_content: bytes, filename: str) -> List[Dict[str, Any]]:
        errors = []
        ext = filename.lower().split('.')[-1]

        if ext in ['xlsx', 'xls']:
            buffer = io.BytesIO(file_content)
            df = pd.read_excel(buffer, dtype=str).fillna("")
            errors = self._parse_error_dataframe(df)
        elif ext in ['csv', 'tsv', 'txt']:
            content_str = file_content.decode('utf-8', errors='ignore')
            lines = content_str.strip().split('\n')
            if len(lines) > 0 and (',' in lines[0] or '\t' in lines[0]):
                sep = '\t' if '\t' in lines[0] and ',' not in lines[0] else ','
                buffer = io.StringIO(content_str)
                try:
                    df = pd.read_csv(buffer, sep=sep, dtype=str).fillna("")
                    if len(df.columns) > 1:
                        errors = self._parse_error_dataframe(df)
                    else:
                        errors = self._parse_error_log_lines(lines)
                except Exception:
                    errors = self._parse_error_log_lines(lines)
            else:
                errors = self._parse_error_log_lines(lines)
        elif ext == 'json':
            content_str = file_content.decode('utf-8', errors='ignore')
            import json
            data = json.loads(content_str)
            if isinstance(data, list):
                errors = self._parse_error_list_dicts(data)

        self.errors_list = errors
        self._link_errors_to_pirl()
        return self.errors_list

    def parse_pasted_wips_text(self, raw_text: str) -> List[Dict[str, Any]]:
        lines = raw_text.strip().split('\n')
        errors = self._parse_error_log_lines(lines)
        self.errors_list = errors
        self._link_errors_to_pirl()
        return self.errors_list

    def _parse_error_dataframe(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        errors = []
        cols = {c.strip().lower(): c for c in df.columns}

        def find_col(candidates):
            for cand in candidates:
                for col_lower, original in cols.items():
                    if cand in col_lower:
                        return original
            return None

        col_row = find_col(['row', 'record', 'line', 'row_number', 'record_number', 'index'])
        col_uiid = find_col(['unique', 'uiid', 'ssn', 'participant', 'individual', 'element 100', 'elem 100', 'id'])
        col_elem = find_col(['element', 'field', 'column', 'pirl element', 'elem'])
        col_code = find_col(['rule', 'edit', 'code', 'error code', 'edit id', 'rule id'])
        col_msg = find_col(['message', 'description', 'error message', 'error description', 'reason', 'validation error', 'detail'])
        col_val = find_col(['value', 'submitted', 'current', 'bad value', 'data'])
        col_sev = find_col(['severity', 'type', 'level', 'status'])

        for idx, row in df.iterrows():
            row_num = row[col_row].strip() if col_row and row[col_row] else ""
            uiid = row[col_uiid].strip() if col_uiid and row[col_uiid] else ""
            elem = row[col_elem].strip() if col_elem and row[col_elem] else ""
            code = row[col_code].strip() if col_code and row[col_code] else "WIPS_ERR"
            msg = row[col_msg].strip() if col_msg and row[col_msg] else "Validation failure reported by WIPS"
            val = row[col_val].strip() if col_val and row[col_val] else ""
            sev = row[col_sev].strip().capitalize() if col_sev and row[col_sev] else "Error"

            if not msg and not elem:
                continue

            errors.append({
                "id": idx + 1,
                "row_number": row_num,
                "uiid": uiid,
                "element": elem,
                "error_code": code,
                "error_message": msg,
                "current_value": val,
                "severity": sev if sev in ["Error", "Warning", "Info"] else "Error",
                "is_pre_check": False
            })

        return errors

    def _parse_error_log_lines(self, lines: List[str]) -> List[Dict[str, Any]]:
        """Parses WIPS error log text, including block web page copy-pastes and single-line log entries."""
        errors = []
        raw_full_text = "\n".join(lines)
        
        # Keep each section's element and rule together while stripping the
        # empty Markdown tables produced when copying the WIPS results page.
        if any(k in raw_full_text for k in ["Error Message for Element No.", "Row Number", "Value Provided"]):
            tokens = []
            for line in lines:
                line = line.strip().replace("**", "")
                if not line or re.fullmatch(r"[|:\-\s]+", line):
                    continue
                tokens.extend(t.strip().strip('"') for t in re.split(r"[\t|]+", line) if t.strip())
            current_element, current_msg = "", ""
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                heading = re.search(r"Element No\.\s*(\d+[A-Za-z]?)", tok, re.I)
                if heading:
                    current_element, current_msg = heading.group(1), ""
                    i += 1
                    continue
                if current_element and re.search(r"\b(?:IF|THEN|must)\b", tok, re.I):
                    current_msg = tok
                    i += 1
                    continue
                if tok.isdigit() and int(tok) > 0 and i + 1 < len(tokens):
                    uiid = tokens[i + 1]
                    if re.fullmatch(r"(?:0\d+|\d{6,}|[A-Za-z0-9_-]{6,})", uiid):
                        val = ""
                        advance = 2
                        if i + 2 < len(tokens):
                            candidate = tokens[i + 2]
                            next_row = candidate.isdigit() and i + 3 < len(tokens) and re.fullmatch(r"(?:0\d+|\d{6,}|[A-Za-z0-9_-]{6,})", tokens[i + 3])
                            if not next_row and not re.search(r"Element No\.", candidate, re.I):
                                val = "" if candidate.lower() in ("no value provided", "missing", "none") else candidate
                                advance = 3
                        elem = current_element or ("200" if len(val) == 8 and val.isdigit() else "409")
                        errors.append({
                            "id": len(errors) + 1, "row_number": tok, "uiid": uiid,
                            "element": elem, "error_code": f"WIPS_ELEM_{elem}",
                            "error_message": current_msg or f"WIPS validation failure for PIRL {elem}",
                            "current_value": val, "severity": "Error", "is_pre_check": False,
                        })
                        i += advance
                        continue
                i += 1
            if errors:
                return errors

        # Fallback to single-line regex log parser
        row_pat = re.compile(r'(?:Row|Record|Line)\s*#?\s*:?\s*(\d+)', re.IGNORECASE)
        uiid_pat = re.compile(r'(?:UIID|Unique ID|Participant ID|SSN|Identifier)\s*#?\s*:?\s*([\w-]+)', re.IGNORECASE)
        elem_pat = re.compile(r'(?:Element|Field)\s*#?\s*:?\s*(\d+[A-Za-z]?)', re.IGNORECASE)
        code_pat = re.compile(r'\b(ERR[_\-\w]+|E\d+|R\d+|RULE[_\-\w]+|EDIT[_\-\w]+)\b', re.IGNORECASE)

        for idx, line in enumerate(lines):
            line_clean = line.strip()
            if not line_clean or line_clean.startswith("#") or line_clean.startswith("Element No."):
                continue

            row_m = row_pat.search(line_clean)
            uiid_m = uiid_pat.search(line_clean)
            elem_m = elem_pat.search(line_clean)
            code_m = code_pat.search(line_clean)

            if not row_m and not uiid_m and not elem_m:
                continue

            errors.append({
                "id": idx + 1,
                "row_number": row_m.group(1) if row_m else "",
                "uiid": uiid_m.group(1) if uiid_m else "",
                "element": elem_m.group(1) if elem_m else "",
                "error_code": code_m.group(1) if code_m else "WIPS_LOG_ERR",
                "error_message": line_clean,
                "current_value": "",
                "severity": "Error" if "warning" not in line_clean.lower() else "Warning",
                "is_pre_check": False
            })
        return errors

    def _parse_error_list_dicts(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        errors = []
        for idx, item in enumerate(data):
            errors.append({
                "id": idx + 1,
                "row_number": str(item.get("row_number", item.get("row", ""))),
                "uiid": str(item.get("uiid", item.get("unique_id", ""))),
                "element": str(item.get("element", item.get("field", ""))),
                "error_code": str(item.get("error_code", item.get("code", "WIPS_ERR"))),
                "error_message": str(item.get("error_message", item.get("message", "Validation error"))),
                "current_value": str(item.get("current_value", item.get("value", ""))),
                "severity": str(item.get("severity", "Error")).capitalize(),
                "is_pre_check": False
            })
        return errors

    def _row_context(self, row):
        context = {}
        for key, elem in (("entry_date", "900"), ("exit_date", "901"), ("state_code", "101"), ("school_status", "409"), ("employment_status", "400")):
            idx = self.element_number_to_col_index.get(elem)
            context[key] = str(row.iloc[idx]).strip() if idx is not None and idx < len(row) else ""
        code = context["school_status"]
        context["school_status_label"] = f"{code} - {SCHOOL_STATUS_CODES.get(code, 'Invalid code')}" if code else "(blank)"
        return context

    def _link_errors_to_pirl(self):
        if self.pirl_df is None or self.pirl_df.empty:
            return

        total_pirl_rows = len(self.pirl_df)

        uiid_col_idx = self.element_number_to_col_index.get("100")
        uiid_to_row_indices = {}
        if uiid_col_idx is not None and uiid_col_idx < len(self.pirl_df.columns):
            uiid_col_name = self.pirl_df.columns[uiid_col_idx]
            for row_idx, val in self.pirl_df[uiid_col_name].items():
                val_clean = str(val).strip()
                if val_clean:
                    if val_clean not in uiid_to_row_indices:
                        uiid_to_row_indices[val_clean] = []
                    uiid_to_row_indices[val_clean].append(row_idx)

        resolved_errors = []

        for err in self.errors_list:
            matched_row_idx = None

            if err["row_number"].isdigit():
                r_num = int(err["row_number"])
                if 1 <= r_num <= total_pirl_rows:
                    matched_row_idx = r_num - 1
                if matched_row_idx is not None and err["uiid"] and uiid_col_idx is not None:
                    if str(self.pirl_df.iat[matched_row_idx, uiid_col_idx]).strip() != err["uiid"].strip():
                        matched_row_idx = None

            if matched_row_idx is None and err["uiid"]:
                clean_uiid = err["uiid"].strip()
                if clean_uiid in uiid_to_row_indices:
                    candidates = uiid_to_row_indices[clean_uiid]
                    # Row numbers from another file may be stale. Only fall back
                    # when the participant/value identifies a single record.
                    elem_match = re.search(r'\d+(?:-[A-Za-z]|[A-Za-z])?', err["element"])
                    elem_idx = self.element_number_to_col_index.get(elem_match.group(0)) if elem_match else None
                    if len(candidates) > 1 and elem_idx is not None:
                        candidates = [r for r in candidates if str(self.pirl_df.iat[r, elem_idx]).strip() == err["current_value"].strip()]
                    if len(candidates) == 1:
                        matched_row_idx = candidates[0]

            err["pirl_row_index"] = matched_row_idx

            element_col_name = None
            element_num_clean = None
            current_val = err["current_value"]

            raw_elem = err["element"].strip()
            elem_num_match = re.search(r'(\d+[A-Za-z]?)', raw_elem)
            if elem_num_match:
                element_num_clean = elem_num_match.group(1)

            if element_num_clean and element_num_clean in self.element_number_to_col_index:
                col_idx = self.element_number_to_col_index[element_num_clean]
                if col_idx < len(self.schema_columns):
                    element_col_name = self.schema_columns[col_idx]

            err["element_col_name"] = element_col_name or raw_elem or "N/A"
            err["element_number"] = element_num_clean or raw_elem or "N/A"

            if matched_row_idx is not None and self.pirl_df is not None:
                row_data = self.pirl_df.iloc[matched_row_idx]
                if not err["uiid"] and uiid_col_idx is not None and uiid_col_idx < len(row_data):
                    err["uiid"] = str(row_data.iloc[uiid_col_idx])
                
                if element_col_name and element_col_name in row_data:
                    current_val = str(row_data[element_col_name])
                elif element_num_clean and element_num_clean in self.element_number_to_col_index:
                    col_idx = self.element_number_to_col_index[element_num_clean]
                    if col_idx < len(row_data):
                        current_val = str(row_data.iloc[col_idx])

                err.update(self._row_context(row_data))
            else:
                err["entry_date"] = ""
                err["exit_date"] = ""
                err["state_code"] = ""
                err["school_status"] = ""
                err["employment_status"] = ""
                err["school_status_label"] = "(blank)"

            err["current_value"] = current_val
            resolved_errors.append(err)

        self.errors_list = resolved_errors

    def get_pirl_table_data(
        self,
        query: str = "",
        uiid: str = "",
        state_id: str = "",
        dob: str = "",
        veteran: str = "",
        disability: str = "",
        page: int = 1,
        page_size: int = 50
    ) -> Dict[str, Any]:
        """Returns paginated reconstructed PIRL table rows with column-specific filters."""
        if self.pirl_df is None or self.pirl_df.empty:
            return {"total_records": 0, "page": page, "page_size": page_size, "total_pages": 1, "records": []}

        df = self.pirl_df

        idx_uiid = self.element_number_to_col_index.get("100")
        idx_state = self.element_number_to_col_index.get("101")
        idx_dob = self.element_number_to_col_index.get("200")
        idx_disability = self.element_number_to_col_index.get("202")
        idx_veteran = self.element_number_to_col_index.get("300")

        # Combine DOL reported errors + pre-validation discrepancies
        errors_by_row = {}
        all_errs = self.errors_list + self.pre_discrepancies_list
        for err in all_errs:
            r_idx = err.get("pirl_row_index")
            if r_idx is not None:
                if r_idx not in errors_by_row:
                    errors_by_row[r_idx] = []
                errors_by_row[r_idx].append(err)

        q_clean = query.lower().strip()
        uiid_clean = uiid.lower().strip()
        state_clean = state_id.lower().strip()
        dob_clean = dob.lower().strip()
        vet_clean = veteran.lower().strip()
        dis_clean = disability.lower().strip()

        # Filter columns directly; constructing every 495-field row just to show
        # one page is particularly expensive with pandas 3 string columns.
        matching_rows = pd.Series(True, index=df.index)
        for term, col_idx in (
            (uiid_clean, idx_uiid), (state_clean, idx_state), (dob_clean, idx_dob),
            (vet_clean, idx_veteran), (dis_clean, idx_disability),
        ):
            if term and col_idx is not None and col_idx < len(df.columns):
                matching_rows &= df.iloc[:, col_idx].astype(str).str.contains(
                    term, case=False, regex=False, na=False
                )

        filtered_indices = df.index[matching_rows].tolist()
        if q_clean:
            # Keep global search semantics, including phrases spanning fields.
            candidates = df.loc[filtered_indices]
            filtered_indices = [
                r_idx for r_idx, values in zip(
                    filtered_indices, candidates.itertuples(index=False, name=None)
                )
                if q_clean in " ".join(map(str, values)).lower()
            ]

        total_count = len(filtered_indices)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        page_indices = filtered_indices[start_idx:end_idx]

        records = []
        for r_idx in page_indices:
            row = df.iloc[r_idx]
            val_uiid = str(row.iloc[idx_uiid]) if idx_uiid is not None and idx_uiid < len(row) else ""
            val_state = str(row.iloc[idx_state]) if idx_state is not None and idx_state < len(row) else ""
            val_dob = str(row.iloc[idx_dob]) if idx_dob is not None and idx_dob < len(row) else ""
            val_vet = str(row.iloc[idx_veteran]) if idx_veteran is not None and idx_veteran < len(row) else ""
            val_dis = str(row.iloc[idx_disability]) if idx_disability is not None and idx_disability < len(row) else ""

            row_errors = errors_by_row.get(r_idx, [])

            records.append({
                "pirl_row_index": r_idx,
                "row_number_display": r_idx + 1,
                "uiid": val_uiid,
                "state_code": val_state,
                "dob": val_dob,
                "veteran_status": val_vet,
                "disability_status": val_dis,
                "has_errors": len(row_errors) > 0,
                "error_count": len(row_errors),
                "errors": row_errors
            })

        return {
            "total_records": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 1,
            "records": records
        }

    def update_cell_value(self, pirl_row_index: int, col_index_or_name: Any, new_value: str) -> bool:
        if self.pirl_df is None or pirl_row_index < 0 or pirl_row_index >= len(self.pirl_df):
            return False

        col_idx = None
        col_key = str(col_index_or_name).strip().lower()

        if isinstance(col_index_or_name, int):
            col_idx = col_index_or_name
        elif col_key.isdigit() and int(col_key) < len(self.schema_columns):
            col_idx = int(col_key)
        elif col_key.startswith("element:"):
            col_idx = self.element_number_to_col_index.get(col_key.split(":", 1)[1])
        elif col_key in self.element_number_to_col_index and not col_key.isdigit():
            col_idx = self.element_number_to_col_index[col_key]
        else:
            for idx, c in enumerate(self.schema_columns):
                if c.lower() == col_key:
                    col_idx = idx
                    break

        if col_idx is None or col_idx < 0 or col_idx >= len(self.pirl_df.columns):
            return False

        value = str(new_value).strip()
        if self.col_index_to_element_number.get(col_idx) == "409" and value and value not in SCHOOL_STATUS_CODES:
            return False
        if str(self.pirl_df.iat[pirl_row_index, col_idx]) == value:
            return True
        self.pirl_df.iat[pirl_row_index, col_idx] = value
        self.edits_count += 1
        
        # Re-run pre-validation & re-link errors
        self.run_pirl_pre_validation_checks()
        self._link_errors_to_pirl()
        return True

    def generate_headerless_pirl_csv(self) -> Tuple[bytes, str]:
        if self.pirl_df is None or self.pirl_df.empty:
            raise ValueError("No PIRL dataset loaded to export.")
        df = self.pirl_df

        buffer = io.StringIO()
        df.to_csv(buffer, header=False, index=False, lineterminator='\n')
        
        filename = f"wp_pirl_corrected_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return buffer.getvalue().encode('utf-8'), filename

    def get_filtered_errors(self, query: str = "", element_filter: str = "", severity_filter: str = "", page: int = 1, page_size: int = 50, include_pre_checks: bool = True) -> Dict[str, Any]:
        all_errs = self.errors_list + (self.pre_discrepancies_list if include_pre_checks else [])
        filtered = []
        q = query.lower().strip()
        elem_f = element_filter.lower().strip()
        sev_f = severity_filter.lower().strip()

        for err in all_errs:
            if sev_f and err["severity"].lower() != sev_f:
                continue
            if elem_f and elem_f not in err["element_number"].lower() and elem_f not in err["element_col_name"].lower():
                continue
            if q:
                match_q = (
                    q in str(err["row_number"]).lower() or
                    q in str(err["uiid"]).lower() or
                    q in err["element_number"].lower() or
                    q in err["element_col_name"].lower() or
                    q in err["error_code"].lower() or
                    q in err["error_message"].lower() or
                    q in err["current_value"].lower()
                )
                if not match_q:
                    continue

            filtered.append(err)

        # Sort filtered errors by UIID so matching participant records are always paired together
        filtered.sort(key=lambda x: (str(x.get("uiid") or "").strip(), int(x.get("row_number") or 0) if str(x.get("row_number")).isdigit() else 0))

        total_count = len(filtered)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_errors = filtered[start_idx:end_idx]

        return {
            "total_errors": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": (total_count + page_size - 1) // page_size if page_size > 0 else 1,
            "errors": paginated_errors
        }

    def get_stats(self) -> Dict[str, Any]:
        total_pirl_records = len(self.pirl_df) if self.pirl_df is not None else 0
        all_errors = self.errors_list + self.pre_discrepancies_list
        total_errors = len(all_errors)
        
        affected_rows = set()
        element_counts: Dict[str, int] = {}
        error_code_counts: Dict[str, int] = {}
        severity_counts: Dict[str, int] = {"Error": 0, "Warning": 0, "Info": 0}

        for err in all_errors:
            if err.get("pirl_row_index") is not None:
                affected_rows.add(err["pirl_row_index"])
            elif err.get("row_number"):
                affected_rows.add(err["row_number"])
            elif err.get("uiid"):
                affected_rows.add(err["uiid"])

            elem_name = err.get("element_col_name") or err.get("element_number") or "Unknown"
            element_counts[elem_name] = element_counts.get(elem_name, 0) + 1

            code = err.get("error_code") or "DOL_ERR"
            error_code_counts[code] = error_code_counts.get(code, 0) + 1

            sev = err.get("severity", "Error")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        top_elements = sorted(
            [{"element": k, "count": v} for k, v in element_counts.items()],
            key=lambda x: x["count"],
            reverse=True
        )[:5]

        top_codes = sorted(
            [{"code": k, "count": v} for k, v in error_code_counts.items()],
            key=lambda x: x["count"],
            reverse=True
        )[:5]

        return {
            "total_pirl_records": total_pirl_records,
            "total_errors": total_errors,
            "dol_errors_count": len(self.errors_list),
            "pre_discrepancies_count": len(self.pre_discrepancies_list),
            "affected_participants": len(affected_rows),
            "severity_counts": severity_counts,
            "top_failing_elements": top_elements,
            "top_error_codes": top_codes,
            "schema_loaded": len(self.schema_columns) > 0,
            "schema_column_count": len(self.schema_columns),
            "schema_name": self.schema_name,
            "schema_notice": self.schema_notice,
            "loaded_filename": self.loaded_filename,
            "loaded_filepath": self.loaded_filepath,
            "edits_count": self.edits_count,
            "unsaved_edits_count": self.edits_count - self.saved_edits_count,
            "last_saved_path": self.last_saved_path,
            "school_status_codes": SCHOOL_STATUS_CODES,
        }

    def get_full_record_details(self, pirl_row_index: int) -> Optional[Dict[str, Any]]:
        if self.pirl_df is None or pirl_row_index < 0 or pirl_row_index >= len(self.pirl_df):
            return None

        row_series = self.pirl_df.iloc[pirl_row_index]
        fields = []

        all_errs = self.errors_list + self.pre_discrepancies_list
        row_errors = [e for e in all_errs if e.get("pirl_row_index") == pirl_row_index]
        failing_cols_map = {}
        for err in row_errors:
            col_name = err.get("element_col_name")
            if col_name:
                if col_name not in failing_cols_map:
                    failing_cols_map[col_name] = []
                failing_cols_map[col_name].append(err)

        uiid_val = ""
        uiid_col_idx = self.element_number_to_col_index.get("100")
        if uiid_col_idx is not None and uiid_col_idx < len(row_series):
            uiid_val = str(row_series.iloc[uiid_col_idx])

        for col_idx, col_name in enumerate(self.schema_columns):
            val = str(row_series.iloc[col_idx]) if col_idx < len(row_series) else ""
            elem_num = self.col_index_to_element_number.get(col_idx, str(col_idx))
            
            errs_for_field = failing_cols_map.get(col_name, [])
            
            fields.append({
                "col_index": col_idx,
                "element_number": elem_num,
                "column_name": col_name,
                "value": val,
                "has_error": len(errs_for_field) > 0,
                "errors": errs_for_field,
                "code_values": SCHOOL_STATUS_CODES if elem_num == "409" else {},
                "help_text": "Choose the status at program entry from verified participant records." if elem_num == "409" else "Use YYYYMMDD. Every record for this participant must have the same verified date of birth." if elem_num == "200" else "",
            })

        return {
            "pirl_row_index": pirl_row_index,
            "row_number_display": pirl_row_index + 1,
            "uiid": uiid_val,
            "total_fields": len(fields),
            "error_fields_count": len(failing_cols_map),
            "fields": fields
        }

    def generate_demo_data(self) -> Tuple[int, int]:
        import random

        if not self.schema_columns:
            self.schema_columns = [
                "0 - OBS Number", "100 - Unique Individual Identifier(WIOA)", "101 - State Code of Residence (WIOA)",
                "200 - Date of Birth", "201 - Sex", "202 - Individual with a Disability", "300 - Veteran Status",
                "400 - Employment Status at Program Entry (WIOA)", "900 - Date of Program Entry (WIOA)", "1300 - Received Training (WIOA)", "2700 - Social Security Number"
            ]

        demo_rows = []
        demo_errors = []
        
        for i in range(1, 51):
            uiid = f"WIOA-{2026000 + i}"
            state_code = "48" if i % 4 != 0 else "99"
            dob = "19920514" if i % 5 != 0 else "00000000"
            sex = "1" if i % 2 == 0 else "2"
            disability = "1" if i % 3 == 0 else "0"
            veteran = "1" if i % 6 == 0 else "0"
            entry_date = "20260115" if i % 7 != 0 else ""
            training = "1" if i % 5 == 0 else "0"
            ssn = f"{random.randint(100,999)}-{random.randint(10,99)}-{random.randint(1000,9999)}"

            row_data = [""] * len(self.schema_columns)
            for elem, value in {"0": str(i), "100": uiid, "101": state_code, "200": dob, "201": sex, "202": disability, "300": veteran, "400": "1", "900": entry_date, "1300": training, "2700": ssn}.items():
                col = self.element_number_to_col_index.get(elem)
                if col is not None:
                    row_data[col] = value

            demo_rows.append(row_data)

            if dob == "00000000":
                demo_errors.append({
                    "id": len(demo_errors) + 1,
                    "row_number": str(i),
                    "uiid": uiid,
                    "element": "200",
                    "error_code": "ERR_200_DATE_FMT",
                    "error_message": "Element 200 (Date of Birth) must be a valid date in YYYYMMDD format",
                    "current_value": "00000000",
                    "severity": "Error",
                    "is_pre_check": False
                })

        self.pirl_df = pd.DataFrame(demo_rows, columns=self.schema_columns)
        self.loaded_filepath = self.last_saved_path = None
        self.loaded_filename = "demo_pirl.csv"
        self.has_header = False
        self.edits_count = self.saved_edits_count = 0
        self.errors_list = demo_errors
        self.run_pirl_pre_validation_checks()
        self._link_errors_to_pirl()

        return len(demo_rows), len(demo_errors) + len(self.pre_discrepancies_list)
