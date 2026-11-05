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

Requirements: [uv](https://docs.astral.sh/uv/), git. Dependencies live in `pyproject.toml` / `uv.lock`
(the Keboola python-js runtime installs them with `uv sync`).

```powershell
uv sync
uv run pytest -q
```

Running locally against Keboola needs a project token and a workspace (`KBC_TOKEN`, `WORKSPACE_ID`,
`QUERY_SERVICE_URL`, `APP_DEV_USER` in `.env`). The usual way to see the app with real data is the
Keboola dev draft (see below).

## Keboola runtime

`keboola-config/` follows the python-js Data App contract: nginx on 8888 → Streamlit on 8501 (supervisord),
`setup.sh` runs `uv sync`. `supervisord-dev/` is used only by the password-protected dev draft and sets a fixed
HQ identity there. Reads and writes go through Query Service on the app's Storage-access workspace; the
`in.c-3mfc-app` tables are granted to it with `direct-grant` output mappings.

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
