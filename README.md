# PIRL DOL Error Report Inspector 📊

An interactive, privacy-focused FastAPI web application designed to validate, search, and correct **Participant Individual Record Layout (PIRL)** data files (ETA 9172) against Department of Labor (DOL / WIPS) edit check error reports.

---

## 🌟 Key Features

- **Reconstructed PIRL Table View**: View the complete dataset on screen mapped to all 495 PIRL schema headers (`PIRL Schema Header.csv`). Automatically detects and loads raw uploaded data files starting with `22_FULL_PIRL` (e.g., `22_FULL_PIRL_COMBINED_SCHEMA2021__20254_20260728194725.csv`).
- **Common Column Quick Filters**: Search & filter specifically by:
  - **User / Unique ID** (`100 - Unique Individual Identifier`)
  - **State ID** (`101 - State Code of Residence`)
  - **Date of Birth** (`200 - Date of Birth`)
  - **Veteran Status** (`300 - Veteran Status`)
  - **Disability** (`202 - Individual with a Disability`)
- **Copy-Paste WIPS Error Log Parser**: Added a **"📋 Paste WIPS Text Errors"** input so staff can copy-paste text directly from the WIPS website edit check page (Step 3 in WIPS process guide) without needing a CSV file.
- **Inline Field Editing & Headerless CSV Export**:
  - Edit bad values directly in the Record Inspector modal and save changes to memory.
  - Download the updated headerless `wp_pirl_{date}.csv` file ready for WIPS re-upload (Step 4 in WIPS process guide).
- **Privacy & PII Protection**: All uploaded data files are processed locally in memory. File patterns (`*.csv`, `*.xlsx`, `*.log`, `._*`) are explicitly git-ignored so sensitive participant information (PII) is never committed to version control.

---

## 📂 Project Structure

```text
pirl_report/
├── app.py                     # FastAPI server & REST API endpoints
├── pirl_parser.py             # PIRL schema & WIPS/DOL error report parsing engine
├── pirl.py                    # PIRL CSV reconstruction CLI script (auto-detects 22_FULL_PIRL files)
├── PIRL Schema Header.csv     # Official PIRL ETA 9172 Schema Headers (495 elements)
├── templates/
│   └── index.html             # Drag-and-drop Web GUI dashboard & table inspector
├── Pipfile                    # Dependency configuration
├── Pipfile.lock               # Locked dependency versions
├── .gitignore                 # Excludes data files, PII, and virtual environments
└── README.md                  # Project documentation
```

---

## 🚀 Quick Start Guide

### 1. Install Dependencies
```bash
pipenv install
```

### 2. Launch the Application
```bash
pipenv run uvicorn app:app --reload --port 8000
```

### 3. Open in Browser
Open your browser and navigate to:
👉 **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 📖 How to Use (Matching WIPS How-To Process)

1. **Upload or Paste Errors**:
   - Drag & drop your `wp_pirl_{date}.csv` file and DOL Error Report file onto the page, OR click **📋 Paste WIPS Text Errors** to paste error text straight from the WIPS website results page.
2. **Switch Views & Filter**:
   - Toggle between **"⚠️ DOL Errors View"** and **"📊 Reconstructed PIRL Table View"**.
   - Search by User ID, State ID, Date of Birth, Veteran Status, or Disability.
3. **Correct Errors & Edit Fields**:
   - Click **Inspect / Edit 🔍** on any record to fix invalid fields directly on screen.
4. **Export for WIPS Re-upload**:
   - Click **"📥 Export Corrected wp_pirl.csv"** to download the updated headerless CSV ready to re-upload to WIPS.
