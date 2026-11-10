"""Core value objects and enumerations."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum


class RowType(str, Enum):
    DETAIL = "DETAIL"
    SUBTOTAL = "SUBTOTAL"
    TOTAL = "TOTAL"
    BALANCE = "BALANCE"


class ValueState(str, Enum):
    """Explicit state of an input cell. NULL and zero are never conflated.

    ENTERED     a number the user (or Excel) explicitly provided, including 0
    BLANK       an optional detail line left empty; contributes 0 to sums,
                is displayed empty and is never reported as "0 entered"
    MISSING     a REQUIRED input that is empty; blocks SUBMIT
    CALCULATED  derived by the calculation engine; never editable
    """

    ENTERED = "ENTERED"
    BLANK = "BLANK"
    MISSING = "MISSING"
    CALCULATED = "CALCULATED"


class ValueOrigin(str, Enum):
    EXCEL_IMPORT = "EXCEL_IMPORT"
    ROLLOVER = "ROLLOVER"
    CARRY_FORWARD = "CARRY_FORWARD"
    MANUAL = "MANUAL"
    HQ_ADJUSTMENT = "HQ_ADJUSTMENT"
    CALCULATED = "CALCULATED"


class ChangeType(str, Enum):
    ENTITY_EDIT = "ENTITY_EDIT"
    ENTITY_SUBMIT = "ENTITY_SUBMIT"
    HQ_ADJUSTMENT = "HQ_ADJUSTMENT"
    IC_CHANGE = "IC_CHANGE"
    ROLLOVER = "ROLLOVER"
    CARRY_FORWARD = "CARRY_FORWARD"
    SYSTEM_CORRECTION = "SYSTEM_CORRECTION"


class RevisionKind(str, Enum):
    IMPORT = "IMPORT"            # initial load from Excel seed
    ROLLOVER = "ROLLOVER"        # created by HQ "new snapshot" (rolling 2+1)
    DRAFT_SAVE = "DRAFT_SAVE"    # entity save
    SUBMIT = "SUBMIT"            # entity submit (immutable reference)
    HQ_ADJUSTMENT = "HQ_ADJUSTMENT"
    SYSTEM = "SYSTEM"


class SubmissionStatus(str, Enum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"


class Severity(str, Enum):
    BLOCKING = "BLOCKING"
    WARNING = "WARNING"
    INFO = "INFO"


class Role(str, Enum):
    ENTITY = "ENTITY"
    HQ = "HQ"
    ADMIN = "ADMIN"


class CalendarStatus(str, Enum):
    SOURCE_EXCEL = "SOURCE_EXCEL"
    CONFIRMED = "CONFIRMED"
    PROPOSED_CONFIRM_REQUIRED = "PROPOSED_CONFIRM_REQUIRED"


MONTH_SLOT_SUFFIX = "_M"


def month_slot(period: str) -> str:
    """Pseudo week code used for monthly state metrics (AR/AP balances)."""
    return f"{period}{MONTH_SLOT_SUFFIX}"


def is_month_slot(week_code: str) -> bool:
    return week_code.endswith(MONTH_SLOT_SUFFIX)


@dataclass(frozen=True)
class WeekPeriod:
    code_period_value: str          # YYYYMM
    week_index: int                 # 1..4
    code_week_period: str           # YYYYMM_Wn
    start: date
    end: date
    status: CalendarStatus = CalendarStatus.SOURCE_EXCEL

    @property
    def needs_confirmation(self) -> bool:
        return self.status == CalendarStatus.PROPOSED_CONFIRM_REQUIRED


@dataclass(frozen=True)
class Line:
    """One row of the forecast template for one entity (stable LINE_ID)."""

    line_id: str
    metric_code: str
    row_type: RowType
    label: str
    source_row: int
    order: int = 0                  # position within the same template row (IC partner rows follow the base row)
    ic_partner: str = ""            # CODE_BUSINESS_UNIT of the IC partner for partner rows


@dataclass(frozen=True)
class Provenance:
    source_snapshot: str = ""
    source_period: str = ""
    source_week: str = ""
    source_line_id: str = ""
    source_revision_id: str = ""
    modified_by: str = ""
    modified_at_utc: str = ""


@dataclass(frozen=True)
class InputCell:
    """A persisted user/import input. Calculated values are never stored here."""

    line_id: str
    metric_code: str
    code_period_value: str
    code_week_period: str           # YYYYMM_Wn, or YYYYMM_M for month state metrics
    amount: Decimal | None
    state: ValueState
    origin: ValueOrigin
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def key(self) -> tuple[str, str]:
        return (self.line_id, self.code_week_period)


@dataclass(frozen=True)
class IcDetail:
    """Counterparty split of INTERCOMPANY_OTHER_OUTFLOW (template row 89)."""

    detail_id: str
    code_period_value: str
    code_week_period: str
    counterparty: str               # CODE_BUSINESS_UNIT of counterparty
    amount: Decimal
    comment: str = ""
    origin: ValueOrigin = ValueOrigin.MANUAL


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    message: str
    business_unit: str = ""
    period: str = ""
    week: str = ""
    line_id: str = ""
    metric_code: str = ""
