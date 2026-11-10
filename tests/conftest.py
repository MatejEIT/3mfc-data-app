"""Shared fixtures: a compact but structurally complete entity template and an
in-memory backend that mimics the verified Keboola staging tables."""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.calendar import propose_month  # noqa: E402
from domain.metrics import METRICS  # noqa: E402
from domain.models import (  # noqa: E402
    CalendarStatus,
    InputCell,
    Line,
    Provenance,
    Role,
    RowType,
    ValueOrigin,
    ValueState,
    WeekPeriod,
)
from services.auth_service import User  # noqa: E402
from services.config import Settings  # noqa: E402
from services.forecast_service import ForecastService  # noqa: E402
from services.keboola_client import InMemoryGateway  # noqa: E402
from services.reference_data import ReferenceData, ar_ap_line_id  # noqa: E402
from services.repository import ForecastRepository  # noqa: E402
from services.snapshot_service import SnapshotService  # noqa: E402

BU = "V002US"

# One line per metric (detail metrics get one detail line); IDs are readable on purpose.
TEMPLATE: list[Line] = [
    Line(f"L_{m.code}", m.code, m.row_type, m.label, m.template_row)
    for m in sorted(METRICS.values(), key=lambda m: m.template_row)
    if m.code not in ("AR_CLOSING_BALANCE", "AP_CLOSING_BALANCE", "AR_OVERDUE_CLOSING_BALANCE",
                      "AP_OVERDUE_CLOSING_BALANCE")
] + [Line("L_AR_NOT_OVERDUE_DETAIL_2", "AR_NOT_OVERDUE_DETAIL", RowType.DETAIL, "Second customer", 16),
     # free customer slots exactly as in the Excel template
     Line("L_AR_PH1", "AR_NOT_OVERDUE_DETAIL", RowType.DETAIL, "Project name and Customer name", 17),
     Line("L_AR_PH2", "AR_NOT_OVERDUE_DETAIL", RowType.DETAIL, "Project name and Customer name", 18),
     # "IC (other VI-group)" inflow, template row 65 (row 89 outflow is L_INTERCOMPANY_OTHER_OUTFLOW)
     Line("L_IC_OTHER_IN", "OTHER_RECEIPTS_DETAIL", RowType.DETAIL, "IC (other VI-group)", 65)]


def _wk(p, i, s, e, status=CalendarStatus.SOURCE_EXCEL):
    return WeekPeriod(p, i, f"{p}_W{i}", s, e, status)


CAL_202610 = [
    _wk("202610", 1, date(2026, 10, 1), date(2026, 10, 11)), _wk("202610", 2, date(2026, 10, 12), date(2026, 10, 18)),
    _wk("202610", 3, date(2026, 10, 19), date(2026, 10, 25)), _wk("202610", 4, date(2026, 10, 26), date(2026, 10, 31)),
    _wk("202611", 1, date(2026, 11, 1), date(2026, 11, 8)), _wk("202611", 2, date(2026, 11, 9), date(2026, 11, 15)),
    _wk("202611", 3, date(2026, 11, 16), date(2026, 11, 22)), _wk("202611", 4, date(2026, 11, 23), date(2026, 11, 30)),
    _wk("202612", 1, date(2026, 12, 1), date(2026, 12, 13)), _wk("202612", 2, date(2026, 12, 14), date(2026, 12, 20)),
    _wk("202612", 3, date(2026, 12, 21), date(2026, 12, 27)), _wk("202612", 4, date(2026, 12, 28), date(2026, 12, 31)),
]
CAL_202611 = [w for w in CAL_202610 if w.code_period_value != "202610"] + propose_month("202701")


def cell(line_id: str, week: str, amount, state=ValueState.ENTERED, origin=ValueOrigin.EXCEL_IMPORT) -> InputCell:
    metric = next(ln.metric_code for ln in TEMPLATE if ln.line_id == line_id) if line_id.startswith("L_") else ""
    return InputCell(line_id, metric, week[:6], week, None if amount is None else Decimal(str(amount)), state, origin,
                     Provenance())


def v002_october_w1_cells() -> dict:
    """Real V002US numbers for 202610_W1 (FT_3MFC_APP_WEEKLY_INPUT_SEED)."""
    w = "202610_W1"
    cells = [
        cell("L_AR_NOT_OVERDUE_DETAIL", w, 200000),
        cell("L_NEW_INVOICE_DETAIL", w, 250000),
        cell("L_AR_OVERDUE_DETAIL", w, None, ValueState.BLANK),
        cell("L_PROJECT_EXPENSE_DETAIL", w, 200000),
        cell("L_EMPLOYMENT", w, 0),
        cell("L_OTHER_EXPENSES", w, 200000),
        cell("L_INTERCOMPANY_GMBH_OUTFLOW", w, 1043000),
        cell("L_OPENING_CASH_BALANCE", w, 3131000),
    ]
    return {c.key: c for c in cells}


def seed_frame(bu: str = BU) -> pd.DataFrame:
    """Shape of FT_3MFC_APP_WEEKLY_INPUT_SEED for the template above."""
    rows = []
    vals = {("L_AR_NOT_OVERDUE_DETAIL", "202610_W1"): "200000.000000000000",
            ("L_INTERCOMPANY_OTHER_OUTFLOW", "202610_W2"): "100.000000000000",
            ("L_OPENING_CASH_BALANCE", "202610_W1"): "3131000.000000000000",
            ("L_EMPLOYMENT", "202612_W4"): "0.000000000000"}
    for ln in TEMPLATE:
        for w in CAL_202610:
            rows.append({"CODE_SNAPSHOT": "202610", "CODE_BUSINESS_UNIT": bu, "CODE_PERIOD_VALUE": w.code_period_value,
                         "CODE_WEEK_PERIOD": w.code_week_period, "LINE_ID": ln.line_id, "METRIC_CODE": ln.metric_code,
                         "ROW_TYPE": ln.row_type.value, "LINE_DESCRIPTION": ln.label, "SOURCE_ROW": str(ln.source_row),
                         "BALANCE_AMOUNT_LCY": vals.get((ln.line_id, w.code_week_period), "")})
    return pd.DataFrame(rows)


def make_settings() -> Settings:
    return Settings(kbc_url="", query_service_url="", branch_id="1", workspace_id="", app_bucket="in.c-3mfc-app",
                    staging_db="DB", bucket_weekly="weekly", bucket_bootstrap="bootstrap", bucket_fx="fx",
                    access_bucket="acl", access_table="ACCESS",
                    backend="memory", dev_user="", dev_role="HQ", app_url="")


def make_gateway() -> InMemoryGateway:
    entities = pd.DataFrame([
        {"CODE_BUSINESS_UNIT": "V001DE", "CODE_ENTITY": "V001", "NAME_ENTITY": "VITRONIC Machine Vision GmbH",
         "COUNTRY": "DE", "CODE_REGION": "WEE", "CODE_LOCAL_CURRENCY": "EUR"},
        {"CODE_BUSINESS_UNIT": BU, "CODE_ENTITY": "V002", "NAME_ENTITY": "VITRONIC Machine Vision Ltd.",
         "COUNTRY": "USA", "CODE_REGION": "AME", "CODE_LOCAL_CURRENCY": "USD"},
        {"CODE_BUSINESS_UNIT": "V016MX", "CODE_ENTITY": "V016", "NAME_ENTITY": "VITRONIC Mexico",
         "COUNTRY": "Mexico", "CODE_REGION": "AME", "CODE_LOCAL_CURRENCY": "MXN"},
    ])
    cps = pd.DataFrame([{"CODE_COUNTERPARTY_ENTITY": b, "IS_ACTIVE": "1"} for b in ("V001DE", BU)])
    week_dim = pd.DataFrame([{"CODE_SNAPSHOT": "202610", "CODE_PERIOD_VALUE": w.code_period_value,
                              "WEEK_INDEX": str(w.week_index), "CODE_WEEK_PERIOD": w.code_week_period,
                              "PERIOD_START_DATE": w.start.isoformat(), "PERIOD_END_DATE": w.end.isoformat(),
                              "CALENDAR_STATUS": w.status.value} for w in CAL_202610 + propose_month("202701")])
    fx = pd.DataFrame([{"CODE_BUSINESS_UNIT": BU, "CODE_LOCAL_CURRENCY": "USD", "FX_REFERENCE_MONTH": "202609",
                        "CODE_FORECAST_START_MONTH": "202610", "CODE_PERIOD_VALUE": p, "FX_TYPE": "SK (BS)",
                        "FX_RATE": "1.1", "FX_RATE_DIRECTION": "LCY_PER_EUR"} for p in ("202610", "202611", "202612")])
    return InMemoryGateway({
        ("fx", "DIM_3MFC_ENTITY"): entities,
        ("bootstrap", "DIM_3MFC_APP_COUNTERPARTY"): cps,
        ("weekly", "DIM_3MFC_WEEK_PERIOD"): week_dim,
        ("weekly", "FT_3MFC_APP_WEEKLY_INPUT_SEED"): seed_frame(),
        ("fx", "FT_3MFC_FX_3MONTH"): fx,
    })


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def gateway() -> InMemoryGateway:
    return make_gateway()


@pytest.fixture
def repo(gateway) -> ForecastRepository:
    return ForecastRepository(gateway)


@pytest.fixture
def ref(gateway, settings) -> ReferenceData:
    return ReferenceData(gateway, settings)


@pytest.fixture
def forecast_service(repo, ref) -> ForecastService:
    return ForecastService(repo, ref)


@pytest.fixture
def snapshot_service(repo, ref) -> SnapshotService:
    return SnapshotService(repo, ref)


@pytest.fixture
def hq() -> User:
    return User("hq@vitronic.example", Role.HQ, frozenset({"*"}))


@pytest.fixture
def entity_user() -> User:
    return User("cfo.us@vitronic.example", Role.ENTITY, frozenset({BU}))


@pytest.fixture
def other_entity_user() -> User:
    return User("cfo.de@vitronic.example", Role.ENTITY, frozenset({"V001DE"}))


def ar_ap_ids(bu: str = BU) -> list[str]:
    return [ar_ap_line_id(bu, m) for m in ("AR_CLOSING_BALANCE", "AP_CLOSING_BALANCE",
                                           "AR_OVERDUE_CLOSING_BALANCE", "AP_OVERDUE_CLOSING_BALANCE")]
