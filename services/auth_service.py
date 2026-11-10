"""Authentication & authorisation.

Identity: the Keboola Data App proxy authenticates the user (OIDC / Microsoft Entra ID SSO)
and forwards the e-mail in `X-Kbc-User-Email`; as a fallback the e-mail claim of the
proxy-signed `X-Kbc-App-User-Token` is used (the proxy strips both headers from browser requests).

Authorisation (first match wins):
  1. DIM_3MFC_APP_USER_ACCESS   - explicit app roles maintained in the app bucket (ADMIN / HQ / ENTITY)
  2. VIT_AR_REPORT_COMMENTS_ACCESS - the group's existing access list:
       Entity_code = MGMT          -> HQ, all companies
       Entity_code = V0xxXX        -> ENTITY for every company where the e-mail is listed
                                      (Finance person `email_address` or Country director `email_address_1`)
  3. APP_BOOTSTRAP_ADMINS        - emergency admins from the environment
Every service method calls `require_*` - hiding UI controls is never the only protection.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from domain.models import Role

ALL_ACCESS_CODE = "MGMT"
_EMAIL_SPLIT = re.compile(r"[;,\s]+")


class AccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class User:
    email: str
    role: Role
    business_units: frozenset[str]   # {"*"} = all

    @property
    def is_hq(self) -> bool:
        return self.role in (Role.HQ, Role.ADMIN)

    def can_view(self, bu: str) -> bool:
        return self.is_hq or bu in self.business_units

    def can_edit(self, bu: str, submitted: bool) -> bool:
        if self.is_hq:
            return True
        return bu in self.business_units and not submitted

    def require_view(self, bu: str) -> None:
        if not self.can_view(bu):
            raise AccessDenied(f"{self.email} has no access to {bu}")

    def require_edit(self, bu: str, submitted: bool) -> None:
        if not self.can_edit(bu, submitted):
            reason = "forecast is already submitted" if submitted and bu in self.business_units else "no access"
            raise AccessDenied(f"{self.email} cannot edit {bu}: {reason}")

    def require_hq(self) -> None:
        if not self.is_hq:
            raise AccessDenied("HQ role required")

    def require_admin(self) -> None:
        if self.role != Role.ADMIN:
            raise AccessDenied("Admin role required")


def _emails(cell: object) -> set[str]:
    return {e.strip().lower() for e in _EMAIL_SPLIT.split(str(cell or "")) if "@" in e}


def identity_from_headers(headers: Mapping[str, str]) -> str:
    """E-mail of the signed-in user, '' when the request carries no identity."""
    email = (headers.get("X-Kbc-User-Email") or "").strip()
    if not email:
        token = headers.get("X-Kbc-App-User-Token") or ""
        parts = token.split(".")
        if len(parts) == 3:
            try:
                payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
                email = str(payload.get("email") or payload.get("preferred_username") or payload.get("upn") or "")
            except (ValueError, json.JSONDecodeError):
                email = ""
    return email.strip().lower()


def resolve_user(email: str, app_access: pd.DataFrame, group_access: pd.DataFrame,
                 bootstrap_admins: frozenset[str] = frozenset()) -> User | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    # 1. explicit app roles
    if not app_access.empty:
        rows = app_access[(app_access["USER_EMAIL"].str.strip().str.lower() == email)
                          & (app_access["IS_ACTIVE"].isin(["1", "true", "TRUE"]))]
        if not rows.empty:
            r = rows.iloc[0]
            bus = frozenset(b.strip() for b in str(r["BUSINESS_UNITS"]).split(",") if b.strip())
            return User(email, Role(r["ROLE"]), bus)
    # 2. group access list (VIT_AR_REPORT_COMMENTS_ACCESS)
    if not group_access.empty:
        mine = group_access[group_access.apply(
            lambda r: email in _emails(r.get("email_address")) | _emails(r.get("email_address_1")), axis=1)]
        codes = {str(c).strip().upper() for c in mine.get("Entity_code", [])}
        if ALL_ACCESS_CODE in codes:
            return User(email, Role.HQ, frozenset({"*"}))
        if codes:
            return User(email, Role.ENTITY, frozenset(codes))
    # 3. emergency admins
    if email in bootstrap_admins:
        return User(email, Role.ADMIN, frozenset({"*"}))
    return None
