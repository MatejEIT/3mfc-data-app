"""Storage gateways.

`Gateway` is the only place that touches Keboola. Two implementations:

* KeboolaGateway  - reads via Query Service SQL on the app workspace
                    (filtered, server-side), writes via Storage API
                    incremental table load (upsert on primary key).
* InMemoryGateway - same semantics on pandas frames; used by tests and the
                    offline demo mode.

The Storage API has no multi-table transactions. Consistency is guaranteed by
the revision commit protocol in `repository.py`, not by the gateway.
"""
from __future__ import annotations

import threading
from typing import Callable, Protocol

import pandas as pd

from services.schema import APP_TABLES, TableDef

Filter = dict[str, "str | list[str]"]


class Gateway(Protocol):
    def read_app(self, table: TableDef, where: Filter | None = None) -> pd.DataFrame: ...
    def upsert_app(self, table: TableDef, df: pd.DataFrame) -> None: ...
    def read_source(self, bucket: str, table: str, where: Filter | None = None,
                    columns: list[str] | None = None) -> pd.DataFrame: ...


class StorageWriteError(RuntimeError):
    pass


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _where_sql(where: Filter | None) -> str:
    if not where:
        return ""
    parts = []
    for col, val in where.items():
        qc = '"' + col.replace('"', '') + '"'
        if isinstance(val, (list, tuple, set)):
            vals = list(val)
            if not vals:
                return " WHERE 1=0"
            parts.append(f"{qc} IN ({', '.join(_sql_literal(v) for v in vals)})")
        else:
            parts.append(f"{qc} = {_sql_literal(val)}")
    return " WHERE " + " AND ".join(parts)


class KeboolaGateway:
    def __init__(self, kbc_url: str, kbc_token: str, app_bucket: str, staging_db: str):
        from keboola_streamlit import KeboolaStreamlit  # imported lazily: not needed in tests

        self._ks = KeboolaStreamlit(root_url=kbc_url, token=kbc_token)
        self._app_bucket = app_bucket
        self._db = staging_db

    def _fqn(self, bucket: str, table: str) -> str:
        return f'"{self._db}"."{bucket}"."{table}"'

    def _query(self, sql: str) -> pd.DataFrame:
        df = self._ks.query_service_execute_query(sql, return_df=True, convert_types=False)
        if df is None:
            raise StorageWriteError(f"Query failed: {sql[:200]}")
        return df.fillna("").astype(str)

    def read_app(self, table: TableDef, where: Filter | None = None) -> pd.DataFrame:
        cols = ", ".join(f'"{c}"' for c in table.columns)
        df = self._query(f"SELECT {cols} FROM {self._fqn(self._app_bucket, table.name)}{_where_sql(where)}")
        return df.reindex(columns=list(table.columns), fill_value="")

    def read_source(self, bucket: str, table: str, where: Filter | None = None,
                    columns: list[str] | None = None) -> pd.DataFrame:
        cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
        return self._query(f"SELECT {cols} FROM {self._fqn(bucket, table)}{_where_sql(where)}")

    def upsert_app(self, table: TableDef, df: pd.DataFrame) -> None:
        if df.empty:
            return
        out = df.reindex(columns=list(table.columns), fill_value="").fillna("").astype(str)
        err = self._ks.write_table(f"{self._app_bucket}.{table.name}", out, is_incremental=True)
        if err:
            raise StorageWriteError(f"Write to {table.name} failed: {err}")


class InMemoryGateway:
    """Thread-safe in-memory store with upsert-on-PK semantics."""

    def __init__(self, sources: dict[tuple[str, str], pd.DataFrame] | None = None):
        self._lock = threading.RLock()
        self.tables: dict[str, pd.DataFrame] = {t.name: pd.DataFrame(columns=list(t.columns)) for t in APP_TABLES}
        self.sources = sources or {}
        self.before_write: Callable[[TableDef, pd.DataFrame], None] | None = None  # test hook

    def read_app(self, table: TableDef, where: Filter | None = None) -> pd.DataFrame:
        with self._lock:
            return _filter(self.tables[table.name].copy(), where)

    def read_source(self, bucket: str, table: str, where: Filter | None = None,
                    columns: list[str] | None = None) -> pd.DataFrame:
        df = self.sources.get((bucket, table))
        if df is None:
            return pd.DataFrame(columns=columns or [])
        df = _filter(df.copy(), where)
        return df[columns] if columns else df

    def upsert_app(self, table: TableDef, df: pd.DataFrame) -> None:
        if df.empty:
            return
        if self.before_write:
            self.before_write(table, df)
        with self._lock:
            new = df.reindex(columns=list(table.columns), fill_value="").fillna("").astype(str)
            cur = self.tables[table.name]
            if table.append_only and not cur.empty:
                clash = new.merge(cur[list(table.primary_key)], on=list(table.primary_key), how="inner")
                if not clash.empty:
                    raise StorageWriteError(f"{table.name} is append-only; duplicate key")
            keys = list(table.primary_key)
            merged = pd.concat([cur, new], ignore_index=True).drop_duplicates(subset=keys, keep="last")
            self.tables[table.name] = merged.reset_index(drop=True)


def _filter(df: pd.DataFrame, where: Filter | None) -> pd.DataFrame:
    if not where or df.empty:
        return df
    mask = pd.Series(True, index=df.index)
    for col, val in where.items():
        if isinstance(val, (list, tuple, set)):
            mask &= df[col].astype(str).isin([str(v) for v in val])
        else:
            mask &= df[col].astype(str) == str(val)
    return df[mask]
