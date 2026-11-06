"""Read-only reference data from the existing Keboola staging backend.

Nothing here writes to staging tables. Sources (verified 2026-11-05):
  DIM_3MFC_ENTITY, FT_3MFC_FX_3MONTH          (Entity Dictionary & Prior-Month FX)
  DIM_3MFC_APP_METRIC, DIM_3MFC_APP_COUNTERPARTY (App Backend Bootstrap)
  DIM_3MFC_WEEK_PERIOD, FT_3MFC_APP_WEEKLY_INPUT_SEED (Weekly Cash Flow Facts)
"""
from __future__ import annotations

import copy
import hashlib
import threading
import time
from datetime import date
from decimal import Decimal

import pandas as pd

from domain.calendar import propose_month, snapshot_periods
from domain.metrics import AR_AP_METRICS, METRICS
from domain.models import CalendarStatus, Line, RowType, WeekPeriod
from services import schema as S
from services.config import Settings
from services.keboola_client import Gateway, Read, read_source

SEED_TABLE = "FT_3MFC_APP_WEEKLY_INPUT_SEED"


def ar_ap_line_id(bu: str, metric: str) -> str:
    return hashlib.sha256(f"{bu}|{metric}|MONTH_STATE".encode()).hexdigest()


def _d(v: str) -> date:
    return date.fromisoformat(str(v)[:10])


class ReferenceData:
    """Read-only reference data with a short in-process cache.

    Entities, template lines, calendar and FX change at most once a month, but the page
    needs them on every open; caching them for TTL seconds removes most Query Service jobs.
    """

    TTL_SECONDS = 600

    def __init__(self, gw: Gateway, settings: Settings, clock=time.monotonic):
        self.gw = gw
        self.s = settings
        self._clock = clock
        self._cache: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def _cached(self, key: tuple, loader):
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return copy.deepcopy(hit[1])
        value = loader()
        with self._lock:
            self._cache[key] = (now + self.TTL_SECONDS, value)
        return copy.deepcopy(value)

    def invalidate(self) -> None:
        """Drop cached reference data (e.g. after HQ changes a calendar)."""
        with self._lock:
            self._cache.clear()

    # ---------------------------------------------------------------- entities
    def entities(self) -> pd.DataFrame:
        def load():
            df = read_source(self.gw, self.s.bucket_fx, "DIM_3MFC_ENTITY")
            if df.empty:
                return pd.DataFrame(columns=["CODE_BUSINESS_UNIT", "CODE_ENTITY", "NAME_ENTITY", "COUNTRY",
                                             "CODE_REGION", "CODE_LOCAL_CURRENCY"])
            return df.sort_values("CODE_BUSINESS_UNIT")
        return self._cached(("entities",), load)

    def counterparties(self) -> pd.DataFrame:
        def load():
            df = read_source(self.gw, self.s.bucket_bootstrap, "DIM_3MFC_APP_COUNTERPARTY")
            return df[df["IS_ACTIVE"].astype(str).isin(["1", "1.0", "1.00000"])] if "IS_ACTIVE" in df else df
        return self._cached(("counterparties",), load)

    def metric_dictionary(self) -> list[dict]:
        return read_source(self.gw, self.s.bucket_bootstrap, "DIM_3MFC_APP_METRIC").to_dict("records")

    # ---------------------------------------------------------------- calendar
    def calendar(self, snapshot: str) -> list[WeekPeriod]:
        """HQ-maintained app calendar wins; else staging calendar; else a proposal."""
        return self._cached(("calendar", snapshot), lambda: self._load_calendar(snapshot))

    def _load_calendar(self, snapshot: str) -> list[WeekPeriod]:
        periods = snapshot_periods(snapshot)
        app, staging = self.gw.read_many([
            Read(table=S.CALENDAR, where={"CODE_PERIOD_VALUE": periods}),
            Read(bucket=self.s.bucket_weekly, name="DIM_3MFC_WEEK_PERIOD", where={"CODE_PERIOD_VALUE": periods})])
        out: list[WeekPeriod] = []
        for p in periods:
            a = app[app["CODE_PERIOD_VALUE"] == p]
            if not a.empty:
                a = a.assign(_v=a["CALENDAR_VERSION"].astype(int))
                a = a[a["_v"] == a["_v"].max()]
                out += [WeekPeriod(p, int(r["WEEK_INDEX"]), r["CODE_WEEK_PERIOD"], _d(r["PERIOD_START_DATE"]),
                                   _d(r["PERIOD_END_DATE"]), CalendarStatus(r["CALENDAR_STATUS"]))
                        for _, r in a.iterrows()]
                continue
            st_rows = staging[staging["CODE_PERIOD_VALUE"] == p] if not staging.empty else staging
            if not st_rows.empty:
                # prefer rows that came from an Excel header over generated proposals
                st_rows = st_rows.assign(_pref=(st_rows["CALENDAR_STATUS"] != "SOURCE_EXCEL").astype(int))
                st_rows = st_rows.sort_values(["_pref", "CODE_SNAPSHOT"]).drop_duplicates("WEEK_INDEX")
                out += [WeekPeriod(p, int(float(r["WEEK_INDEX"])), r["CODE_WEEK_PERIOD"], _d(r["PERIOD_START_DATE"]),
                                   _d(r["PERIOD_END_DATE"]), CalendarStatus(r["CALENDAR_STATUS"]))
                        for _, r in st_rows.iterrows()]
                continue
            out += propose_month(p)
        return sorted(out, key=lambda w: (w.code_period_value, w.week_index))

    # ---------------------------------------------------------------- lines
    def lines(self, bu: str) -> list[Line]:
        """Template lines of an entity. App line table wins; else Excel seed."""
        return self._cached(("lines", bu), lambda: self._load_lines(bu))

    def _load_lines(self, bu: str) -> list[Line]:
        app, seed = self.gw.read_many([
            Read(table=S.LINE, where={"CODE_BUSINESS_UNIT": bu}),
            Read(bucket=self.s.bucket_weekly, name=SEED_TABLE, where={"CODE_BUSINESS_UNIT": bu},
                 columns=("LINE_ID", "METRIC_CODE", "ROW_TYPE", "LINE_DESCRIPTION", "SOURCE_ROW"))])
        if not app.empty:
            app = app[app["IS_ACTIVE"] == "1"]
            lines = [Line(r["LINE_ID"], r["METRIC_CODE"], RowType(r["ROW_TYPE"]), r["LINE_LABEL"],
                          int(float(r["SOURCE_ROW"] or 0))) for _, r in app.iterrows()]
        else:
            seed = seed.drop_duplicates("LINE_ID")
            lines = [Line(r["LINE_ID"], r["METRIC_CODE"], RowType(r["ROW_TYPE"]),
                          str(r["LINE_DESCRIPTION"]).strip() or METRICS[r["METRIC_CODE"]].label,
                          int(float(r["SOURCE_ROW"] or 0)))
                     for _, r in seed.iterrows() if r["METRIC_CODE"] in METRICS]
        have = {ln.metric_code for ln in lines}
        for metric in AR_AP_METRICS:
            if metric not in have:
                m = METRICS[metric]
                lines.append(Line(ar_ap_line_id(bu, metric), metric, RowType.BALANCE, m.label, m.template_row))
        return sorted(lines, key=lambda ln: (ln.source_row, ln.line_id))

    def seed_rows(self, bu: str, snapshot: str) -> pd.DataFrame:
        return read_source(self.gw, self.s.bucket_weekly, SEED_TABLE,
                           {"CODE_BUSINESS_UNIT": bu, "CODE_SNAPSHOT": snapshot})

    # ---------------------------------------------------------------- FX
    def _fx(self, bu: str, snapshot: str) -> pd.DataFrame:
        return self._cached(("fx", bu, snapshot), lambda: read_source(
            self.gw, self.s.bucket_fx, "FT_3MFC_FX_3MONTH",
            {"CODE_BUSINESS_UNIT": bu, "CODE_FORECAST_START_MONTH": snapshot}))

    def fx_rates(self, bu: str, snapshot: str) -> dict[str, Decimal | None]:
        """LCY per 1 EUR, IDL 'SK (BS)' of the month before the snapshot, all 3 months."""
        rates: dict[str, Decimal | None] = {p: None for p in snapshot_periods(snapshot)}
        for _, r in self._fx(bu, snapshot).iterrows():
            v = str(r["FX_RATE"]).strip()
            rates[r["CODE_PERIOD_VALUE"]] = Decimal(v) if v not in ("", "nan", "None") else None
        return rates

    def fx_reference(self, bu: str, snapshot: str) -> str:
        df = self._fx(bu, snapshot)
        if df.empty:
            return "missing"
        r = df.iloc[0]
        return f"{r['FX_TYPE']} {r['FX_REFERENCE_MONTH']}"
