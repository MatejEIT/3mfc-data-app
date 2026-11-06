"""Storage gateways.

`Gateway` is the only place that touches Keboola. Two implementations:

* KeboolaGateway  - Query Service SQL on the Data App's Storage-access workspace.
                    `read_many` sends several SELECTs as ONE Query Service job,
                    `write_many` sends all upserts of a save as ONE transactional job
                    (DELETE matching keys + INSERT per table), so a save is atomic.
* InMemoryGateway - same semantics on pandas frames (incl. rollback); tests and offline mode.

Every Query Service job costs ~0.5-2 s of latency, so callers batch their reads/writes.
"""
from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

import pandas as pd

from services.schema import APP_TABLES, TableDef

Filter = dict[str, "str | list[str]"]


@dataclass(frozen=True)
class Read:
    """One SELECT: an app table (`table`) or a staging/source table (`bucket` + `name`)."""
    table: TableDef | None = None
    bucket: str = ""
    name: str = ""
    where: Filter | None = None
    columns: tuple[str, ...] | None = None


class Gateway(Protocol):
    def read_many(self, reads: list[Read]) -> list[pd.DataFrame]: ...
    def write_many(self, writes: list[tuple[TableDef, pd.DataFrame]]) -> None: ...


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


# convenience wrappers so call sites stay short ---------------------------------------
def read_app(gw: Gateway, table: TableDef, where: Filter | None = None) -> pd.DataFrame:
    return gw.read_many([Read(table=table, where=where)])[0]


def read_source(gw: Gateway, bucket: str, name: str, where: Filter | None = None,
                columns: list[str] | None = None) -> pd.DataFrame:
    return gw.read_many([Read(bucket=bucket, name=name, where=where,
                              columns=tuple(columns) if columns else None)])[0]


class KeboolaGateway:
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

    def _run(self, statements: list[str], transactional: bool):
        try:
            return self._client.execute_query(self._branch, self._workspace, statements, transactional=transactional)
        except Exception as exc:  # surface the error, never silently ignore
            raise StorageWriteError(f"Query Service error: {exc}") from exc

    def _select(self, r: Read) -> str:
        if r.table is not None:
            cols = r.columns or r.table.columns
            src = self._fqn(self._app_bucket, r.table.name)
        else:
            cols = r.columns
            src = self._fqn(r.bucket, r.name)
        col_sql = ", ".join(f'"{c}"' for c in cols) if cols else "*"
        return f"SELECT {col_sql} FROM {src}{_where_sql(r.where)}"

    def read_many(self, reads: list[Read]) -> list[pd.DataFrame]:
        if not reads:
            return []
        results = self._run([self._select(r) for r in reads], transactional=False)
        frames = []
        for r, res in zip(reads, results):
            df = pd.DataFrame(res.data, columns=[c.name for c in res.columns]).fillna("").astype(str)
            if r.table is not None:
                df = df.reindex(columns=list(r.columns or r.table.columns), fill_value="")
            frames.append(df)
        return frames

    def write_many(self, writes: list[tuple[TableDef, pd.DataFrame]]) -> None:
        statements: list[str] = []
        for table, df in writes:
            if not df.empty:
                statements += upsert_statements(self._fqn(self._app_bucket, table.name), table, df, self.CHUNK)
        if statements:
            self._run(statements, transactional=True)


def upsert_statements(fqn: str, table: TableDef, df: pd.DataFrame, chunk: int = 400) -> list[str]:
    """DELETE rows with the same primary key, then INSERT (run inside one transaction).
    Append-only tables are insert-only."""
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
    """Thread-safe in-memory store with upsert-on-PK semantics and all-or-nothing write_many."""

    def __init__(self, sources: dict[tuple[str, str], pd.DataFrame] | None = None):
        self._lock = threading.RLock()
        self.tables: dict[str, pd.DataFrame] = {t.name: pd.DataFrame(columns=list(t.columns)) for t in APP_TABLES}
        self.sources = sources or {}
        self.before_write: Callable[[TableDef, pd.DataFrame], None] | None = None  # test hook
        self.jobs = 0                                                              # Query Service job counter

    def read_many(self, reads: list[Read]) -> list[pd.DataFrame]:
        self.jobs += 1 if reads else 0
        with self._lock:
            out = []
            for r in reads:
                if r.table is not None:
                    df = _filter(self.tables[r.table.name].copy(), r.where)
                    out.append(df[list(r.columns)] if r.columns else df)
                else:
                    src = self.sources.get((r.bucket, r.name))
                    if src is None:
                        out.append(pd.DataFrame(columns=list(r.columns or [])))
                        continue
                    df = _filter(src.copy(), r.where)
                    out.append(df[list(r.columns)] if r.columns else df)
            return out

    def write_many(self, writes: list[tuple[TableDef, pd.DataFrame]]) -> None:
        writes = [(t, df) for t, df in writes if not df.empty]
        if not writes:
            return
        self.jobs += 1
        with self._lock:
            backup = copy.copy(self.tables)
            try:
                for table, df in writes:
                    if self.before_write:
                        self.before_write(table, df)
                    self._upsert(table, df)
            except Exception:
                self.tables = backup              # transaction rollback
                raise

    def _upsert(self, table: TableDef, df: pd.DataFrame) -> None:
        new = df.reindex(columns=list(table.columns), fill_value="").fillna("").astype(str)
        cur = self.tables[table.name]
        keys = list(table.primary_key)
        if table.append_only and not cur.empty:
            clash = new.merge(cur[keys], on=keys, how="inner")
            if not clash.empty:
                raise StorageWriteError(f"{table.name} is append-only; duplicate key")
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
