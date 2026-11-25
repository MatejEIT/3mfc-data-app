"""Minimum cash band and HQ commentary. Levels here are made-up test values (real ones live in Keboola)."""
from decimal import Decimal

import pandas as pd
import pytest

from domain.business_checks import check_min_cash
from domain.calc import calculate, ordered_weeks
from domain.min_cash import ABOVE, BELOW, WITHIN, MinCash, classify, group_level, level_of
from services import schema as S
from services.auth_service import AccessDenied
from services.report_service import ReportService
from tests.conftest import BU, CAL_202610, TEMPLATE, cell

D = Decimal
WEEKS = ordered_weeks(CAL_202610)
LEVELS = {"V901": MinCash(D(1000)), "V902": MinCash(D(500))}


def test_band_and_group_level():
    lv = level_of("V901XX", LEVELS)
    assert lv.minimum == 1000 and level_of("V999XX", LEVELS) is None
    assert classify(D(899), lv) == BELOW and classify(D(900), lv) == WITHIN
    assert classify(D(1200), lv) == WITHIN and classify(D(1201), lv) == ABOVE
    assert group_level(["V901XX", "V902XX", "V999XX"], LEVELS).minimum == D(1500)
    assert group_level(["V999XX"], LEVELS) is None


def _result(opening, taxes_w2):
    cells = {c.key: c for c in [cell("L_OPENING_CASH_BALANCE", "202610_W1", opening),
                                cell("L_TAXES", "202610_W2", taxes_w2)]}
    return calculate(TEMPLATE, CAL_202610, cells)


def test_entity_warning_below_minimum_and_info_above():
    rates = {p: D("2") for p in ("202610", "202611", "202612")}          # 2 LCY per EUR
    lv = MinCash(D(1000))                                                # band 900 .. 1200 kEUR
    low = check_min_cash("V901XX", _result(2_400_000, 800_000), WEEKS, rates, "XXX", lv)   # 1.6M LCY = 800 kEUR
    assert [i.code for i in low] == ["W_BELOW_MIN_CASH"] * 3
    assert "below the minimum cash level 1,000 kEUR" in low[0].message
    high = check_min_cash("V901XX", _result(5_000_000, 0), WEEKS, rates, "XXX", lv)        # 2 500 kEUR
    assert [i.code for i in high] == ["I_ABOVE_TARGET_CASH"]
    assert check_min_cash("V901XX", _result(1, 0), WEEKS, rates, "EUR", None) == []


def test_levels_are_read_from_keboola_and_used_by_the_report(snapshot_service, repo, ref, gateway, hq):
    assert ref.min_cash_levels() == {}                                  # table missing / empty -> no checks
    gateway.tables[S.MIN_CASH.name] = pd.DataFrame(columns=list(S.MIN_CASH.columns))
    gateway.write_many([(S.MIN_CASH, pd.DataFrame([{
        "CODE_ENTITY": BU[:4], "MIN_CASH_KEUR": "9000", "LOWER_TOLERANCE": "0.10", "UPPER_TOLERANCE": "0.20",
        "UPDATED_BY": "test", "UPDATED_AT_UTC": "2026-11-25"}]))])
    ref.invalidate()
    assert ref.min_cash_levels()[BU[:4]].minimum == D(9000)
    snapshot_service.import_initial(hq, "202610", [BU])
    rep = ReportService(repo, ref).build("202610")
    assert any(ch["Topic"] == "Minimum cash" and ch["Area"] == BU for ch in rep.checks)       # ~2.8 MEUR < 8 100
    assert any(ch["Topic"] == "Minimum cash" and ch["Area"] == "Group" for ch in rep.checks)


def test_hq_commentary_is_versioned_and_hq_only(snapshot_service, repo, hq, entity_user):
    snapshot_service.save_comment(hq, "202610", "- first point")
    snapshot_service.save_comment(hq, "202610", "- first point\n- second point")
    versions = repo.snapshot_comments("202610")
    assert len(versions) == 2 and "second" in versions[0]["text"] and versions[0]["by"] == hq.email
    assert repo.snapshot_comments("202611") == []
    with pytest.raises(AccessDenied):
        snapshot_service.save_comment(entity_user, "202610", "x")
