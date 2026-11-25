"""Minimum cash level per company (HQ treasury).

The levels (kEUR) and tolerances live in the Keboola table in.c-3mfc-app.DIM_3MFC_APP_MIN_CASH
(client data - never in the code). The forecast is judged against a band around the level:
    below   lowest weekly closing cash  <  minimum × (1 − lower tolerance)   -> too little cash
    above   lowest weekly closing cash  >  minimum × (1 + upper tolerance)   -> excess cash
    within  otherwise
The group minimum is the sum of the companies' minimum levels.
Levels are keyed by the 4-character company code (V001 -> V001DE).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

BELOW, WITHIN, ABOVE = "below", "within", "above"


@dataclass(frozen=True)
class MinCash:
    minimum: Decimal                   # kEUR
    lower: Decimal = Decimal("0.10")
    upper: Decimal = Decimal("0.20")

    @property
    def band(self) -> tuple[Decimal, Decimal]:
        return self.minimum * (1 - self.lower), self.minimum * (1 + self.upper)


Levels = dict[str, MinCash]            # CODE_ENTITY (V001) -> level


def level_of(bu: str, levels: Levels) -> MinCash | None:
    return levels.get(bu[:4])


def group_level(bus: list[str], levels: Levels) -> MinCash | None:
    """Σ minimums of the given companies, with the tolerances of the first one (all equal in practice)."""
    found = [lv for b in bus if (lv := level_of(b, levels)) is not None]
    if not found:
        return None
    return MinCash(sum((lv.minimum for lv in found), Decimal(0)), found[0].lower, found[0].upper)


def classify(lowest_keur: Decimal | None, level: MinCash | None) -> str | None:
    if lowest_keur is None or level is None:
        return None
    lo, hi = level.band
    if lowest_keur < lo:
        return BELOW
    if lowest_keur > hi:
        return ABOVE
    return WITHIN
