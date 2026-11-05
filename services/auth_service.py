"""Authentication & authorisation.

Identity comes from the Keboola Data App auth proxy header `X-Kbc-User-Email`
(the app must be deployed with authentication enabled). Roles and entity scope
come from table DIM_3MFC_APP_USER_ACCESS, maintained by Admin. Every service
method calls `require_*` - hiding UI controls is never the only protection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from domain.models import Role
from services.config import Settings


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


def resolve_user(headers: Mapping[str, str], access: pd.DataFrame, settings: Settings,
                 bootstrap_admins: frozenset[str] = frozenset()) -> User | None:
    if settings.is_dev:
        return User(settings.dev_user, Role(settings.dev_role), frozenset({"*"}))
    email = (headers.get("X-Kbc-User-Email") or "").strip().lower()
    if not email:
        return None
    if not access.empty:
        rows = access[(access["USER_EMAIL"].str.lower() == email) & (access["IS_ACTIVE"].isin(["1", "true", "TRUE"]))]
        if not rows.empty:
            r = rows.iloc[0]
            bus = frozenset(b.strip() for b in str(r["BUSINESS_UNITS"]).split(",") if b.strip())
            return User(email, Role(r["ROLE"]), bus)
    if email in bootstrap_admins:
        return User(email, Role.ADMIN, frozenset({"*"}))
    return None
