# PIRL DOL Error Report Inspector 📊

An interactive, privacy-focused FastAPI web application designed to validate, search, and correct **Participant Individual Record Layout (PIRL)** data files (ETA 9172) against Department of Labor (DOL / WIPS) edit check error reports.

---

## 🌟 Key Features

- **Reconstructed PIRL Table View**: Maps Full PIRL 2025 (475 columns) and Full PIRL 2021 (495 columns) using the bundled DOL schema catalog. Prefers `PIRLSchemaHeader2025.csv` at startup when available. The file width selects the matching official layout; malformed or unsupported widths are rejected rather than padded. Automatically detects local data files starting with `22_FULL_PIRL`.
- **Common Column Quick Filters**: Search & filter specifically by:
  - **User / Unique ID** (`100 - Unique Individual Identifier`)
  - **State ID** (`101 - State Code of Residence`)
  - **Date of Birth** (`200 - Date of Birth`)
  - **Veteran Status** (`300 - Veteran Status`)
  - **Disability** (`202 - Individual with a Disability`)
- **Copy-Paste WIPS Error Log Parser**: Added a **"📋 Paste WIPS Text Errors"** input so staff can copy-paste text directly from the WIPS website edit check page (Step 3 in WIPS process guide) without needing a CSV file.
- **Inline Field Editing & Headerless CSV Export**:
  - Edit fields by their actual column positions in the Record Inspector. PIRL 409 provides all six labeled code choices; the field search finds elements by number or name.
  - Preflight detects conflicting DOBs for the same participant, missing school status when a program entry date is set, and invalid school-status codes. These local checks do not replace WIPS validation.
  - Cell saves persist corrections once and report any disk failure. Save to File and exports also save pending edits in the editor before continuing.
  - Export WIPS File downloads the current data as a timestamped, headerless corrected CSV with the original field count and order. Full Dataset exports include headers for review, and Excel includes every row.
  - An automatically loaded local file saves back to its known path. Browser uploads do not expose their original path: the app saves an `<original-name>_corrected.csv` copy in the application folder. The page and save confirmation show the full destination and correction count.
- **Privacy & PII Protection**: Data processing and corrected file saves stay on this computer. File patterns (`*.csv`, `*.xlsx`, `*.log`, `._*`) are explicitly git-ignored so participant data is excluded from version control.

---

## 📂 Project Structure

```text
pirl_report/
├── app.py                     # FastAPI server & REST API endpoints
├── pirl_parser.py             # PIRL schema & WIPS/DOL error report parsing engine
├── pirl_schemas.json           # Official Full PIRL 2021/2025 field order and source URL
├── pirl.py                    # PIRL CSV reconstruction CLI script (auto-detects 22_FULL_PIRL files)
├── PIRL Schema Header.csv     # Official PIRL ETA 9172 Schema Headers (495 elements)
├── templates/
│   └── index.html             # Drag-and-drop Web GUI dashboard & table inspector
├── pyproject.toml             # Project metadata & dependencies managed by uv
├── uv.lock                    # Locked dependency versions
├── .python-version            # Python 3.13 used by uv
├── .gitignore                 # Excludes data files, PII, and virtual environments
└── README.md                  # Project documentation
```

---

## 🚀 Quick Start Guide

### 1. Install Dependencies
Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed, then run from the project directory:

```bash
uv sync --locked
```

The project uses Python 3.13. uv creates `.venv` and installs the versions in `uv.lock`.

### 2. Launch the Application
```bash
uv run uvicorn app:app --reload --port 8000
```

No virtual environment activation is required. Use `uv add <package>` to add a dependency and update both `pyproject.toml` and `uv.lock`.

### 3. Open in Browser
Open your browser and navigate to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

### Repairing an Incomplete Virtual Environment

If uv reports `failed to remove directory .../.venv/lib: Directory not empty`, stop the server and any processes using `.venv`, then move the old environment aside and rebuild it:

```bash
# Run once; choose a different backup name if .venv.bak already exists.
mv .venv .venv.bak
uv sync --locked
uv run uvicorn app:app --reload --port 8000
```

The backup contains the old environment. Once the app works, it can be deleted.

---

## 📖 How to Use (Matching WIPS How-To Process)

1. **Upload or Paste Errors**:
   - Drag & drop your `wp_pirl_{date}.csv` file and DOL Error Report file onto the page, OR click **📋 Paste WIPS Text Errors** to paste error text straight from the WIPS website results page.
   - Choosing a PIRL CSV starts loading automatically. Wait for **Loaded ... PIRL records** before running a preflight check. **Load / Refresh Dataset** also checks for a `22_FULL_PIRL*.csv` copied into the project after the server started.
2. **Switch Views & Filter**:
   - Toggle between **"⚠️ DOL Errors View"** and **"📊 Reconstructed PIRL Table View"**.
   - Search by User ID, State ID, Date of Birth, Veteran Status, or Disability.
3. **Correct Errors & Edit Fields**:
   - Click **Inspect / Edit 🔍** on any record to fix invalid fields directly on screen.
4. **Export for WIPS Re-upload**:
   - Resolve preflight issues, then click **"📥 Export WIPS File"** to download the updated headerless CSV. Use this CSV for WIPS; the Full Dataset Excel export is for review.
   - **Save to File** shows the exact saved location. For a browser upload, this is a corrected copy in the app folder, rather than the file originally selected in Downloads or another folder.

The bundled column layouts come from [DOL's WIPS File Schema workbook (PIRL 2021 and 2025)](https://www.dol.gov/sites/dolgov/files/ETA/Performance/WIPS%20File%20Schema%20PIRL%20YRS%202021%20%26%202025.xlsx). School-status labels follow the ETA-9172 key: 1 secondary school or less, 2 alternative school, 3 postsecondary, 4 not attending/dropout beyond compulsory school age, 5 not attending with diploma/equivalent, 6 not attending within compulsory school age without diploma/equivalent. Choose the status at program entry from verified records; do not infer attendance solely from education level.

Run regression checks with `.venv/bin/python -m unittest discover -s tests -v`.
