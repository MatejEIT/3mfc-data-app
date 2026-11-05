# Architecture – 3M Cash Flow Forecast Data App

Status: v0.1 (2026-11-05). Backend analysis is based on live queries against Keboola project 810.

## 1. Verified backend state (project 810)

| Area | Finding |
|---|---|
| Entities | `DIM_3MFC_ENTITY`: 18 entities. Excel seed exists for 16; **V013DE (ERA Beteiligungs) and V014CO (Colombia) have no file**. |
| Template | 81 lines per entity in `FT_3MFC_APP_WEEKLY_INPUT_SEED` (68 DETAIL), stable SHA-256 `LINE_ID`, identical structure for all 16 entities. The duplicate Net Cash Movement row (`IS_DUPLICATE_KPI`) is excluded from the seed. |
| Metric dictionary | `DIM_3MFC_APP_METRIC` (32 metrics) is mirrored in `domain/metrics.py`; `check_dictionary_drift()` detects template changes. |
| Calendar | `DIM_3MFC_WEEK_PERIOD`: Oct–Dec 2026 `SOURCE_EXCEL`, Jan 2027 `PROPOSED_CONFIRM_REQUIRED`. The generator in `domain/calendar.py` reproduces all four months exactly. |
| Blank vs zero | `BALANCE_AMOUNT_LCY` is physically VARCHAR; Excel blanks are `''`. DETAIL cells: 12,144 blank, 174 explicit 0, 738 non-zero. The distinction is preserved end-to-end. |
| Formulas | NCM and Closing = Opening + NCM hold for all 192 entity×week rows. Opening(w) = Closing(w-1) holds everywhere except **V015AT 202612_W1** (Excel opening empty, previous closing 46,000). The app recalculates the chain, so this gap disappears and is visible in the audit diff. |
| FX | `FT_3MFC_FX_3MONTH` has rates for forecast start 202609 and 202610 only. **No FX for snapshot 202611 (reference 202610) yet**; EUR values for 202611 are reported as missing. |
| AR/AP balances | Not present in monthly facts at all (bootstrap: NULL). The app treats them as MISSING required inputs. |
| IC | `FT_3MFC_APP_IC_SEED` is monthly and has no counterparty (`NEEDS_COUNTERPARTY`). The app's split is weekly; the seed is not used to fabricate allocations. |
| Opening editability | The seed flags W1 of every month as editable for Opening; the app allows input **only for the first interval of the horizon** (later openings are calculated). |

Nothing in the existing transformations, flow or extractor was changed.

## 2. Layers

```
pages/ (Streamlit UI)  ──►  services/ (use cases, auth, persistence)  ──►  Keboola Storage
        │                           │
        └──── components/           └──── domain/ (pure Python: calc, validation, rolling, calendar)
```

* **domain/** – no I/O. The same code runs in the app, in tests and can be packaged into a Keboola
  Python transformation for independent, reproducible recalculation (`FT_3MFC_CALCULATED_*`, `FT_3MFC_CONSO_*`).
* **services/** – authorisation and validation are enforced here on every call (Streamlit runs server-side;
  the browser never talks to Keboola and never sees the token).
* **components/** – ITIS-styled grid, KPI cards, legend.

## 3. Writeback design

Keboola Storage offers table loads (full or incremental upsert on PK) but **no multi-table transactions and
no compare-and-swap**. Consistency therefore comes from a revision protocol (`services/repository.py`):

1. per-(snapshot, entity) in-process lock (a Data App is a single container);
2. idempotency key – a repeated click returns the already committed revision;
3. optimistic lock – `BASE_REVISION_NO` must equal the committed head, else `RevisionConflict`;
4. revision row written `PENDING` with a fresh `WRITE_ID`, verified;
5. full input state + IC split + audit rows written, all tagged with `WRITE_ID`;
6. revision flipped to `COMMITTED`, verified; submission status updated.

Readers only use `COMMITTED` revisions and only rows carrying that revision's `WRITE_ID`.
A failure at any step leaves the previous revision current (covered by `test_failed_write_leaves_previous_revision_current`).

**Full state per revision** (~1,100 input cells per entity) makes every revision – in particular the
SUBMITTED one – immutable and directly comparable; storage volume is negligible (~1 MB / snapshot / 100 revisions).

### Tables (bucket `in.c-3mfc-app`)

Created 2026-11-05 by the one-off Keboola config *3MFC – App Tables Setup (one-off)*
(`keboola.python-transformation-v2`, id `01m6nvs5ydwd9etbvn6ymxm246`, job 119041975). It writes header-only
CSVs with incremental output, so re-running it never deletes data. `scripts/create_app_tables.py` is the
token-based alternative with the same schema.

| Table | PK | Purpose |
|---|---|---|
| `FT_3MFC_APP_REVISION` | REVISION_ID | revision header, kind, status, author, idempotency key |
| `FT_3MFC_APP_INPUT` | WRITE_ID, LINE_ID, CODE_WEEK_PERIOD | full input state per revision incl. provenance and VALUE_STATE |
| `FT_3MFC_APP_IC_DETAIL` | WRITE_ID, DETAIL_ID | IC counterparty split per revision |
| `FT_3MFC_APP_AUDIT` | EVENT_ID | append-only change log (old/new value and state, actor, role, comment) |
| `FT_3MFC_APP_SUBMISSION` | CODE_SNAPSHOT, CODE_BUSINESS_UNIT | DRAFT/SUBMITTED, submitted & current revision, HQ-adjusted, notification pending |
| `FT_3MFC_APP_CALENDAR` | CODE_PERIOD_VALUE, WEEK_INDEX, CALENDAR_VERSION | HQ-confirmed calendar, versioned |
| `FT_3MFC_APP_SNAPSHOT` | CODE_SNAPSHOT | snapshot header |
| `FT_3MFC_APP_NOTIFICATION_QUEUE` / `_LOG` | NOTIFICATION_ID / LOG_ID | prepared notifications, send log (no real e-mails in phase 1) |
| `DIM_3MFC_APP_LINE` | CODE_BUSINESS_UNIT, LINE_ID | entity line labels (later: renaming / adding customer lines) |
| `DIM_3MFC_APP_USER_ACCESS` | USER_EMAIL | role (ENTITY/HQ/ADMIN) and entity scope |

Amounts are stored as text: `''` = blank/missing, `'0'` = explicit zero; `VALUE_STATE` repeats it explicitly.
These tables are written only by the app. No transformation may output into this bucket.

### Value provenance
`VALUE_ORIGIN` ∈ EXCEL_IMPORT, ROLLOVER, CARRY_FORWARD, MANUAL, HQ_ADJUSTMENT (CALCULATED values are never stored as input)
plus SOURCE_SNAPSHOT / PERIOD / WEEK / LINE_ID / REVISION_ID, MODIFIED_BY / AT.

## 4. Calculation placement

The brief asks for calculations centralised in Keboola and immediate recalculation in the UI. Both are met
with **one implementation**: `domain/calc.py`. The app uses it server-side for preview and save-time
validation; the planned Keboola Python transformation imports the same module to materialise
`FT_3MFC_CALCULATED_WEEKLY/MONTHLY`, `FT_3MFC_CONSO_WEEKLY/MONTHLY` and `CHECK_3MFC_APP_VALIDATION`
from committed revisions. `REVISION.CALC_STATUS` tracks whether backend materialisation is current.

## 5. Security

* Identity: `X-Kbc-User-Email` from the Data App auth proxy (deploy with authentication enabled).
* Authorisation: `DIM_3MFC_APP_USER_ACCESS`; `APP_BOOTSTRAP_ADMINS` only until the table is filled.
* Every service method checks `require_view / require_edit / require_hq`. Calculated cells are rejected server-side.
* Token only in Data App secrets / platform injection.
* **TEMPORARY (pre-release):** the Keboola app is password-protected (basic auth, no identity) and
  `keboola-config/supervisord/services/app.conf` sets `APP_DEV_USER=matej.loub@embedit.com` (HQ). This MUST be
  removed when OIDC is configured and before any entity/HQ user gets access.

## 6. Known limitations

* Each save performs ~6 Storage API loads (≈10–20 s). Acceptable for a monthly process; can be reduced later.
* The in-process lock assumes one app replica (Keboola default). Cross-replica races are still detected by
  the optimistic check, but the protocol is not a distributed lock.
* Audit append-only is guaranteed by the app (unique EVENT_ID, insert only), not by Storage itself.

## 7. Open business questions (safe default in brackets)

1. **IC split vs. main line**: must the split equal row 89 before SUBMIT? *(yes – BLOCKING)*
2. **AR/AP balances**: are all four mandatory for every month at SUBMIT? *(yes – BLOCKING)*
3. **Missing FX** blocks entity submit? *(no – WARNING; HQ responsibility; EUR shown as missing)*
4. **Re-submit**: can an entity re-open after SUBMIT? *(no; only HQ edits after submit)*
5. **Carry-forward review**: explicit confirmation per month before SUBMIT? *(yes – BLOCKING checkbox)*
6. **Calendar rule** for future months: observed rule documented in `domain/calendar.py` – HQ must confirm.
7. **Detail line labels**: may entities rename placeholder lines ("Project name and Customer name") or add lines?
8. **V013DE / V014CO**: are they expected to submit forecasts?
9. **IC elimination rules** – not implemented in official consolidation until agreed.
