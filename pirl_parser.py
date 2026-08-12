import io
import re
import os
import glob
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

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

        if schema_header_path:
            self.load_schema_header(schema_header_path)

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

    def load_schema_header(self, filepath_or_buffer) -> List[str]:
        """Loads PIRL schema header columns and builds element mapping index (filters out empty/unnamed trailing cols)."""
        try:
            if isinstance(filepath_or_buffer, str):
                df = pd.read_csv(filepath_or_buffer, nrows=0)
            else:
                df = pd.read_csv(filepath_or_buffer, nrows=0)
            
            raw_columns = [col.strip() for col in df.columns.tolist()]
            self.schema_columns = [col for col in raw_columns if col and not col.startswith("Unnamed:")]
            self.element_number_to_col_index = {}
            self.col_index_to_element_number = {}

            for idx, col in enumerate(self.schema_columns):
                match = re.match(r"^(\d+[A-Za-z]?)\b", col)
                elem_num = match.group(1) if match else str(idx)
                self.element_number_to_col_index[elem_num] = idx
                self.element_number_to_col_index[col.lower()] = idx
                self.col_index_to_element_number[idx] = elem_num

            return self.schema_columns
        except Exception as e:
            print(f"Error loading schema header: {e}")
            return []

    def save_to_disk(self) -> Tuple[bool, str]:
        """Saves corrected dataset directly back to disk in its original CSV format (headerless if originally headerless)."""
        if self.pirl_df is None:
            return False, "No PIRL dataset loaded."

        target_path = self.loaded_filepath
        if not target_path or not os.path.exists(os.path.dirname(os.path.abspath(target_path))):
            target_path = self.loaded_filename if self.loaded_filename else "wp_pirl_corrected.csv"

        try:
            # Preserve exact CSV format as-is (headerless if original was headerless, no extra index column)
            self.pirl_df.to_csv(target_path, header=self.has_header, index=False, lineterminator='\n')
            print(f"Saved corrected PIRL data directly to disk: {target_path}")
            return True, f"Saved corrected dataset to {os.path.basename(target_path)}"
        except Exception as e:
            print(f"Failed saving to disk: {e}")
            return False, f"Failed to save file to disk: {e}"

    def parse_pirl_data(self, file_content: bytes, filename: str, filepath: Optional[str] = None) -> int:
        """Parses PIRL CSV file content (headerless or with header matching schema)."""
        self.loaded_filename = filename
        self.loaded_filepath = filepath or (filename if os.path.exists(filename) else self.loaded_filepath)

        buffer = io.BytesIO(file_content)
        
        sample_line = buffer.readline().decode('utf-8', errors='ignore')
        buffer.seek(0)

        is_headerless = True
        if self.schema_columns:
            first_col = self.schema_columns[0].lower()
            if first_col in sample_line.lower() or "obs number" in sample_line.lower():
                is_headerless = False

        self.has_header = not is_headerless

        if is_headerless:
            names = self.schema_columns if self.schema_columns else None
            df = pd.read_csv(buffer, header=None, names=names, dtype=str, keep_default_na=False)
        else:
            df = pd.read_csv(buffer, dtype=str, keep_default_na=False)
            if self.schema_columns and len(df.columns) == len(self.schema_columns):
                df.columns = self.schema_columns

        df = df.astype(str).fillna("")
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
        valid_dates_mask = entry_date_s.str.isdigit() & exit_date_s.str.isdigit() & (entry_date_s != "") & (exit_date_s != "")
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
        valid_uiids = uiid_s[(uiid_s != "") & (uiid_s != "0") & (uiid_s.str.lower() != "nan")]
        if not valid_uiids.empty:
            dob_col = self.element_number_to_col_index.get("200", 12)
            dob_series = df.iloc[:, dob_col].astype(str).str.strip()
            # Group by UIID and count unique non-empty DOBs
            grouped_dobs = df.groupby(uiid_s)[df.columns[dob_col]].nunique()
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
        
        # Check if text contains WIPS block format or WIPS table headers/columns
        if any(k in raw_full_text for k in ["Error Message for Element No.", "Row Number", "Unique Individual Identifier", "Value Provided"]):
            elem_hdr_pat = re.compile(r'Element No\.\s*(\d+[A-Za-z]?)', re.IGNORECASE)
            
            # Extract header elements & error descriptions from text lines
            current_element = ""
            current_msg = ""
            for line in lines:
                m_elem = elem_hdr_pat.search(line)
                if m_elem:
                    current_element = m_elem.group(1)
                elif current_element and ("IF " in line or "THEN " in line or "must " in line):
                    current_msg = line.strip()

            # Tokenize entire text by tabs and newlines to support multi-line web table pastes
            tokens = [t.strip().strip('"') for t in re.split(r'[\r\n\t]+', raw_full_text) if t.strip()]
            
            i = 0
            while i < len(tokens):
                tok = tokens[i]
                # Skip header label tokens
                if tok.lower() in ["row", "number", "unique", "individual", "identifier", "value", "provided", "row number", "element", "no.", "count", "of", "errors"]:
                    i += 1
                    continue
                    
                if tok.isdigit() and int(tok) > 0:
                    row_num = tok
                    if i + 1 < len(tokens):
                        possible_uiid = tokens[i+1]
                        if re.match(r'^(?:0\d+|\d{6,}|[A-Za-z0-9_-]{6,})$', possible_uiid):
                            uiid = possible_uiid
                            val = ""
                            advance = 2
                            if i + 2 < len(tokens):
                                possible_val = tokens[i+2]
                                if not possible_val.isdigit() or len(possible_val) == 8 or possible_val in ["No Value Provided", "MISSING", "None"]:
                                    if not (possible_val.isdigit() and len(possible_val) <= 6 and i+3 < len(tokens) and re.match(r'^(?:0\d+|\d{6,}|[A-Za-z0-9_-]{6,})$', tokens[i+3])):
                                        val = "" if possible_val in ["No Value Provided", "MISSING", "None"] else possible_val
                                        advance = 3
                            
                            elem = current_element
                            msg = current_msg
                            if not elem:
                                if len(val) == 8 and val.isdigit():
                                    elem = "200"
                                    msg = "C) IF multiple records have the same Unique Individual Identifier (WIOA) (PIRL 100), THEN each record must have the same Date of Birth (WIOA) (PIRL 200)"
                                else:
                                    elem = "409"
                                    msg = "B) IF [Participant] THEN School Status at Program Entry (WIOA) (PIRL 409) must NOT be blank"
                                    
                            errors.append({
                                "id": len(errors) + 1,
                                "row_number": row_num,
                                "uiid": uiid,
                                "element": elem,
                                "error_code": f"WIPS_ELEM_{elem}",
                                "error_message": msg,
                                "current_value": val,
                                "severity": "Error",
                                "is_pre_check": False
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
                elif 0 <= r_num < total_pirl_rows:
                    matched_row_idx = r_num

            if matched_row_idx is None and err["uiid"]:
                clean_uiid = err["uiid"].strip()
                if clean_uiid in uiid_to_row_indices:
                    matched_row_idx = uiid_to_row_indices[clean_uiid][0]

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

            idx_900 = self.element_number_to_col_index.get("900")
            idx_901 = self.element_number_to_col_index.get("901")
            idx_101 = self.element_number_to_col_index.get("101")
            idx_409 = self.element_number_to_col_index.get("409")
            idx_400 = self.element_number_to_col_index.get("400")

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

                err["entry_date"] = str(row_data.iloc[idx_900]).strip() if idx_900 is not None and idx_900 < len(row_data) else ""
                err["exit_date"] = str(row_data.iloc[idx_901]).strip() if idx_901 is not None and idx_901 < len(row_data) else ""
                err["state_code"] = str(row_data.iloc[idx_101]).strip() if idx_101 is not None and idx_101 < len(row_data) else ""
                err["school_status"] = str(row_data.iloc[idx_409]).strip() if idx_409 is not None and idx_409 < len(row_data) else ""
                err["employment_status"] = str(row_data.iloc[idx_400]).strip() if idx_400 is not None and idx_400 < len(row_data) else ""
            else:
                err["entry_date"] = ""
                err["exit_date"] = ""
                err["state_code"] = ""
                err["school_status"] = ""
                err["employment_status"] = ""

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

        filtered_indices = []

        q_clean = query.lower().strip()
        uiid_clean = uiid.lower().strip()
        state_clean = state_id.lower().strip()
        dob_clean = dob.lower().strip()
        vet_clean = veteran.lower().strip()
        dis_clean = disability.lower().strip()

        for r_idx in range(len(df)):
            row = df.iloc[r_idx]

            if uiid_clean and idx_uiid is not None and idx_uiid < len(row):
                if uiid_clean not in str(row.iloc[idx_uiid]).lower():
                    continue
            if state_clean and idx_state is not None and idx_state < len(row):
                if state_clean not in str(row.iloc[idx_state]).lower():
                    continue
            if dob_clean and idx_dob is not None and idx_dob < len(row):
                if dob_clean not in str(row.iloc[idx_dob]).lower():
                    continue
            if vet_clean and idx_veteran is not None and idx_veteran < len(row):
                if vet_clean not in str(row.iloc[idx_veteran]).lower():
                    continue
            if dis_clean and idx_disability is not None and idx_disability < len(row):
                if dis_clean not in str(row.iloc[idx_disability]).lower():
                    continue

            if q_clean:
                row_str = " ".join([str(v) for v in row.values]).lower()
                if q_clean not in row_str:
                    continue

            filtered_indices.append(r_idx)

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
        elif col_key in self.element_number_to_col_index:
            col_idx = self.element_number_to_col_index[col_key]
        elif col_key.isdigit() and int(col_key) < len(self.schema_columns):
            col_idx = int(col_key)
        else:
            for idx, c in enumerate(self.schema_columns):
                if c.lower() == col_key:
                    col_idx = idx
                    break

        if col_idx is None or col_idx < 0 or col_idx >= len(self.pirl_df.columns):
            return False

        self.pirl_df.iat[pirl_row_index, col_idx] = str(new_value).strip()
        self.edits_count += 1
        
        # Save updated data directly back to disk preserving exact CSV format
        if self.loaded_filepath:
            self.save_to_disk()

        # Re-run pre-validation & re-link errors
        self.run_pirl_pre_validation_checks()
        self._link_errors_to_pirl()
        return True

    def generate_headerless_pirl_csv(self) -> Tuple[bytes, str]:
        if self.pirl_df is None:
            df = pd.DataFrame()
        else:
            df = self.pirl_df

        buffer = io.StringIO()
        df.to_csv(buffer, header=self.has_header, index=False, lineterminator='\n')
        
        filename = self.loaded_filename if self.loaded_filename else "wp_pirl.csv"
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
            "schema_column_count": len(self.schema_columns)
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
                "errors": errs_for_field
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

            row_data = [str(i), uiid, state_code, dob, sex, disability, veteran, "1", entry_date, training, ssn]
            while len(row_data) < len(self.schema_columns):
                row_data.append("")

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
        self.errors_list = demo_errors
        self.run_pirl_pre_validation_checks()
        self._link_errors_to_pirl()

        return len(demo_rows), len(demo_errors) + len(self.pre_discrepancies_list)
