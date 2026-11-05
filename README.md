# 3M Cash Flow Forecast – VITRONIC / ITIS Holding

Keboola Data App (Python + Streamlit) for the three-month rolling cash flow forecast: entities enter weekly
forecasts and submit them, HQ reviews, adjusts (fully audited) and consolidates.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the backend analysis, writeback design and open questions.

## Structure

```
app.py                 entry point, auth, top navigation
pages/                 forecast_editor, hq_workspace, methodology (more screens in progress)
components/            ITIS theme, AgGrid forecast grid
domain/                pure financial logic: metrics, calc, validation, rolling 2+1, calendar
services/              Keboola gateway, revision repository, use cases, auth, reference data
scripts/               create_app_tables.py (dry run by default)
tests/                 pytest suite (in-memory backend with Keboola upsert semantics)
```

## Local development

Requirements: Python 3.11+, git.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pytest -q
```

Run against Keboola (read-only staging + app bucket) with a Storage token that has access to project 810:

```powershell
$env:KBC_TOKEN = "<token>"
$env:APP_DEV_USER = "you@embedit.com"; $env:APP_DEV_ROLE = "HQ"   # local only
streamlit run app.py
```

## Deployment (Keboola Data App) – not done yet, requires approval

1. Create writeback tables: `python scripts/create_app_tables.py` (review) then `--execute`.
2. Create a Streamlit Data App from this Git repository, entry point `app.py`.
3. Secrets: `KBC_TOKEN` (or platform-provided), `APP_BUCKET`, `APP_BOOTSTRAP_ADMINS`, `APP_PUBLIC_URL`.
4. Enable app authentication so `X-Kbc-User-Email` is provided; fill `DIM_3MFC_APP_USER_ACCESS`.
5. In **HQ Workspace › Snapshots** run *Initial import from Excel seed* for snapshot 202610.

## Rules the app enforces

Weekly intervals are the only editable flow granularity; months are derived. Empty ≠ zero.
Calculated rows are never editable. Every change creates a revision and audit records; the SUBMITTED
revision stays immutable. No approval workflow, no real e-mails, no IC elimination in official numbers.
