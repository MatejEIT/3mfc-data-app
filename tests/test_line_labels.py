"""Customer / project names typed by the entity (Excel column B)."""
from domain.models import ValueState
from domain.validation import Edit
from tests.conftest import BU


def _import(snapshot_service, hq):
    snapshot_service.import_initial(hq, "202610", [BU])


def test_entity_names_a_free_slot_and_it_is_audited(snapshot_service, forecast_service, repo, entity_user, hq):
    _import(snapshot_service, hq)
    ws = forecast_service.open(entity_user, "202610", BU)
    out = forecast_service.save(entity_user, ws, [Edit("L_AR_PH1", "202610_W2", "1000")], None, "new customer",
                                "n1", label_edits={"L_AR_PH1": "  ACME   GmbH "})
    assert out.revision is not None
    ws2 = forecast_service.open(entity_user, "202610", BU)
    assert ws2.label(ws2.lines_by_id["L_AR_PH1"]) == "ACME GmbH"
    audit = repo.audit("202610", BU)
    ev = audit[audit["OLD_VALUE_STATE"] == "LABEL"].iloc[0]
    assert "Project name and Customer name" in ev["COMMENT"] and "ACME GmbH" in ev["COMMENT"]
    assert not any(i.code == "W_UNNAMED_LINE" for i in ws2.issues)


def test_fixed_rows_cannot_be_renamed(snapshot_service, forecast_service, hq):
    _import(snapshot_service, hq)
    ws = forecast_service.open(hq, "202610", BU)
    out = forecast_service.save(hq, ws, [], None, "", "x", label_edits={"L_TAXES": "Something"})
    assert out.revision is None and out.issues[0].code == "E_LABEL_FIXED"


def test_values_without_customer_name_warn(snapshot_service, forecast_service, entity_user, hq):
    _import(snapshot_service, hq)
    ws = forecast_service.open(entity_user, "202610", BU)
    out = forecast_service.save(entity_user, ws, [Edit("L_AR_PH1", "202610_W1", "10")], None, "", "u1")
    assert any(i.code == "W_UNNAMED_LINE" for i in out.issues)


def test_names_belong_to_revision_and_roll_over(snapshot_service, forecast_service, repo, entity_user, hq):
    _import(snapshot_service, hq)
    ws = forecast_service.open(entity_user, "202610", BU)
    forecast_service.save(entity_user, ws, [Edit("L_AR_PH1", "202611_W1", "5")], None, "", "r1",
                          label_edits={"L_AR_PH1": "Customer A"})
    first = repo.revisions("202610", BU)[0]
    assert repo.load_state("202610", BU, first).labels == {}          # older revision keeps the template text

    snapshot_service.create_rollover(hq, "202611", [BU])
    nov = repo.load_state("202611", BU)
    assert nov.labels == {"L_AR_PH1": "Customer A"}
    assert nov.cells[("L_AR_PH1", "202611_W1")].state == ValueState.ENTERED
