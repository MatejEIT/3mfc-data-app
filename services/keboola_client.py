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
    """Query Service gateway on the Data App's Storage-access workspace.

    Reads use fully qualified Snowflake names. Writes go to app tables granted
    to the workspace via `unload_strategy: direct-grant`; each upsert is ONE
    transactional Query Service job (DELETE matching keys + INSERT), so a
    table write is atomic.
    """

    CHUNK = 400

    def __init__(self, query_service_url: str, token_provider: Callable[[], str], branch_id: str,
                 workspace_id: str, app_bucket: str, staging_db: str):
        from keboola_query_service import Client  # imported lazily: not needed in tests

        self._client = Client(base_url=query_service_url, token=token_provider())
        self._branch = branch_id
        self._workspace = workspace_id
        self._app_bucket = app_bucket
        self._db = staging_db

    def _fqn(self, bucket: str, table: str) -> str:
        return f'"{self._db}"."{bucket}"."{table}"'

    def _run(self, statements: list[str]):
        try:
            return self._client.execute_query(self._branch, self._workspace, statements, transactional=True)
        except Exception as exc:  # surface the SQL context, never silently ignore
            raise StorageWriteError(f"Query Service error: {exc}") from exc

    def _query(self, sql: str) -> pd.DataFrame:
        res = self._run([sql])[0]
        df = pd.DataFrame(res.data, columns=[c.name for c in res.columns])
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
        self._run(upsert_statements(self._fqn(self._app_bucket, table.name), table, df, self.CHUNK))


def upsert_statements(fqn: str, table: TableDef, df: pd.DataFrame, chunk: int = 400) -> list[str]:
    """DELETE rows with the same primary key, then INSERT - executed in one transaction.
    Append-only tables are insert-only (a duplicate key is a bug and fails loudly in tests)."""
    out = df.reindex(columns=list(table.columns), fill_value="").fillna("").astype(str)
    out = out.drop_duplicates(subset=list(table.primary_key), keep="last")
    cols = ", ".join(f'"{c}"' for c in table.columns)
    pk = list(table.primary_key)
    statements: list[str] = []
    for start in range(0, len(out), chunk):
        part = out.iloc[start:start + chunk]
        if not table.append_only:
            keys = ", ".join("(" + ", ".join(_sql_literal(r[c]) for c in pk) + ")" for _, r in part.iterrows())
            key_cols = ", ".join(f'"{c}"' for c in pk)
            statements.append(f"DELETE FROM {fqn} WHERE ({key_cols}) IN ({keys})")
        values = ", ".join("(" + ", ".join(_sql_literal(v) for v in row) + ")"
                           for row in part.itertuples(index=False, name=None))
        statements.append(f"INSERT INTO {fqn} ({cols}) VALUES {values}")
    return statements


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
