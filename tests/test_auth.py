"""Identity from the SSO proxy and access from VIT_AR_REPORT_COMMENTS_ACCESS (fictitious e-mails)."""
import base64
import json

import pandas as pd

from domain.models import Role
from services.auth_service import identity_from_headers, resolve_user

GROUP = pd.DataFrame([
    {"Entity_code": "MGMT", "email_address": "Boss@Group.example", "email_address_1": ""},
    {"Entity_code": "V008PL", "email_address": "fin.pl@vitronic.example", "email_address_1": "dir.pl@vitronic.example"},
    {"Entity_code": "V017EE", "email_address": "fin.pl@vitronic.example", "email_address_1": ""},
    {"Entity_code": "V002US", "email_address": "a@vitronic.example; b@vitronic.example", "email_address_1": ""},
])
NO_APP = pd.DataFrame(columns=["USER_EMAIL", "ROLE", "BUSINESS_UNITS", "IS_ACTIVE"])


def test_mgmt_rows_get_hq_and_all_companies():
    u = resolve_user("boss@group.example", NO_APP, GROUP)
    assert u.role == Role.HQ and u.can_view("V019CA")


def test_entity_user_gets_all_listed_companies_case_insensitive():
    u = resolve_user("FIN.PL@vitronic.example", NO_APP, GROUP)
    assert u.role == Role.ENTITY and u.business_units == {"V008PL", "V017EE"}
    assert not u.can_view("V002US")
    assert resolve_user("dir.pl@vitronic.example", NO_APP, GROUP).business_units == {"V008PL"}
    assert resolve_user("b@vitronic.example", NO_APP, GROUP).business_units == {"V002US"}


def test_unknown_or_missing_identity_has_no_access():
    assert resolve_user("someone@else.example", NO_APP, GROUP) is None
    assert resolve_user("", NO_APP, GROUP) is None


def test_app_table_overrides_and_bootstrap_admin():
    app = pd.DataFrame([{"USER_EMAIL": "fin.pl@vitronic.example", "ROLE": "HQ", "BUSINESS_UNITS": "*", "IS_ACTIVE": "1"}])
    assert resolve_user("fin.pl@vitronic.example", app, GROUP).role == Role.HQ
    assert resolve_user("ops@x.example", NO_APP, GROUP, frozenset({"ops@x.example"})).role == Role.ADMIN


def test_identity_from_proxy_headers():
    assert identity_from_headers({"X-Kbc-User-Email": " Jane@Vitronic.example "}) == "jane@vitronic.example"
    claims = base64.urlsafe_b64encode(json.dumps({"email": "Joe@Vitronic.example"}).encode()).decode().rstrip("=")
    assert identity_from_headers({"X-Kbc-App-User-Token": f"h.{claims}.s"}) == "joe@vitronic.example"
    assert identity_from_headers({}) == ""
