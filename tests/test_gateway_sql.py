import pandas as pd

from services import schema as S
from services.keboola_client import _where_sql, upsert_statements


def test_upsert_is_delete_then_insert_and_escapes_quotes():
    df = pd.DataFrame([{"CODE_SNAPSHOT": "202610", "CODE_BUSINESS_UNIT": "V002US", "STATUS": "DRAFT",
                        "SUBMITTED_BY": "o'brien@x"}])
    st = upsert_statements('"DB"."in.c-3mfc-app"."FT_3MFC_APP_SUBMISSION"', S.SUBMISSION, df)
    assert len(st) == 2
    assert st[0].startswith('DELETE FROM "DB"."in.c-3mfc-app"."FT_3MFC_APP_SUBMISSION" WHERE '
                            '("CODE_SNAPSHOT", "CODE_BUSINESS_UNIT") IN ((\'202610\', \'V002US\'))')
    assert st[1].startswith('INSERT INTO "DB"."in.c-3mfc-app"."FT_3MFC_APP_SUBMISSION" ("CODE_SNAPSHOT"')
    assert "'o''brien@x'" in st[1]


def test_append_only_table_is_insert_only_and_chunked():
    df = pd.DataFrame([{"EVENT_ID": str(i)} for i in range(5)])
    st = upsert_statements("T", S.AUDIT, df, chunk=2)
    assert len(st) == 3 and all(s.startswith("INSERT INTO T") for s in st)


def test_where_sql_escapes_and_handles_lists():
    assert _where_sql({"A": "x'; DROP TABLE y; --"}) == " WHERE \"A\" = 'x''; DROP TABLE y; --'"
    assert _where_sql({"A": ["1", "2"]}) == " WHERE \"A\" IN ('1', '2')"
    assert _where_sql({"A": []}) == " WHERE 1=0"


def test_per_revision_tables_are_insert_only():
    for table in (S.REVISION, S.INPUT, S.IC_DETAIL, S.LINE_LABEL, S.AUDIT):
        df = pd.DataFrame([{c: "x" for c in table.columns}])
        st = upsert_statements("T", table, df)
        assert all(s.startswith("INSERT INTO T") for s in st), table.name
    st = upsert_statements("T", S.SUBMISSION, pd.DataFrame([{c: "x" for c in S.SUBMISSION.columns}]))
    assert st[0].startswith("DELETE FROM T")       # mutable status row is still upserted
