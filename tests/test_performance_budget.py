"""Query Service round-trips are the dominant latency (~0.5-2 s each). These budgets keep it that way."""
from domain.validation import Edit
from tests.conftest import BU


def test_open_save_and_reopen_job_budget(snapshot_service, forecast_service, gateway, hq):
    snapshot_service.import_initial(hq, "202610", [BU])

    gateway.jobs = 0
    ws = forecast_service.open(hq, "202610", BU)
    assert gateway.jobs <= 5          # cold: + counterparties and FX into the reference cache

    gateway.jobs = 0
    out = forecast_service.save(hq, ws, [Edit("L_TAXES", "202610_W1", "1")], None, "", "perf")
    assert out.revision is not None
    assert gateway.jobs == 2          # one read + ONE transactional write

    gateway.jobs = 0
    forecast_service.open(hq, "202610", BU)
    assert gateway.jobs <= 3          # warm: head revision, revision data, submission status


def test_failed_save_is_all_or_nothing(snapshot_service, forecast_service, repo, gateway, hq):
    snapshot_service.import_initial(hq, "202610", [BU])
    ws = forecast_service.open(hq, "202610", BU)
    before = {name: len(df) for name, df in gateway.tables.items()}

    def fail_on_revision(table, df):
        if table.name == "FT_3MFC_APP_REVISION":      # last statement of the transaction
            raise RuntimeError("network")
    gateway.before_write = fail_on_revision
    try:
        forecast_service.save(hq, ws, [Edit("L_TAXES", "202610_W1", "1")], None, "", "f")
    except RuntimeError:
        pass
    gateway.before_write = None
    assert {name: len(df) for name, df in gateway.tables.items()} == before   # nothing half-written
