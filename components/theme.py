"""ITIS Holding visual identity (palette from the brief; verify against brand assets)."""
from __future__ import annotations

import html

import streamlit as st

NAVY = "#283B89"
DARK_NAVY = "#17265E"
BG = "#F6F7FB"
CARD = "#FFFFFF"
BORDER = "#E2E8F0"
MUTED = "#64748B"
TEXT = "#0F172A"
POSITIVE = "#15803D"
WARNING = "#D97706"
NEGATIVE = "#DC2626"

# value-state tints used consistently in grids and legends
INPUT_BG = "#FFFFFF"
CALC_BG = "#F1F5F9"
CARRY_BG = "#FEF3C7"
HQ_BG = "#E0E7FF"
MISSING_BG = "#FEE2E2"

_CSS = f"""
<style>
  .block-container {{ padding-top: 4.2rem; padding-bottom: 3rem; max-width: 1600px; }}
  header[data-testid="stHeader"] {{ background: {CARD}; border-bottom: 1px solid {BORDER}; }}
  .itis-bar {{ display:flex; align-items:center; justify-content:space-between; gap:16px;
              background:{CARD}; border:1px solid {BORDER}; border-radius:12px; padding:14px 20px;
              margin-bottom:18px; }}
  .itis-brand {{ display:flex; align-items:center; gap:12px; }}
  .itis-mark {{ width:38px; height:38px; border-radius:9px; background:{NAVY}; color:#fff; font-weight:700;
               display:flex; align-items:center; justify-content:center; letter-spacing:.5px; font-size:13px; }}
  .itis-title {{ font-size:17px; font-weight:650; color:{TEXT}; line-height:1.15; }}
  .itis-sub {{ font-size:12.5px; color:{MUTED}; }}
  .itis-user {{ font-size:12.5px; color:{MUTED}; text-align:right; }}
  .itis-chip {{ display:inline-block; padding:2px 9px; border-radius:999px; font-size:11.5px; font-weight:600;
               border:1px solid {BORDER}; background:{BG}; color:{TEXT}; margin-left:6px; }}
  .chip-ok {{ background:#DCFCE7; border-color:#BBF7D0; color:{POSITIVE}; }}
  .chip-warn {{ background:#FEF3C7; border-color:#FDE68A; color:#92400E; }}
  .chip-bad {{ background:#FEE2E2; border-color:#FECACA; color:{NEGATIVE}; }}
  .chip-info {{ background:#E0E7FF; border-color:#C7D2FE; color:{DARK_NAVY}; }}
  .kpi {{ background:{CARD}; border:1px solid {BORDER}; border-radius:12px; padding:14px 16px; height:100%; }}
  .kpi-label {{ font-size:12px; color:{MUTED}; text-transform:uppercase; letter-spacing:.04em; }}
  .kpi-value {{ font-size:22px; font-weight:650; color:{TEXT}; margin-top:4px; font-variant-numeric: tabular-nums; }}
  .kpi-foot {{ font-size:12px; color:{MUTED}; margin-top:2px; }}
  .legend {{ display:flex; flex-wrap:wrap; gap:14px; font-size:12px; color:{MUTED}; margin:6px 0 10px; }}
  .legend span::before {{ content:""; display:inline-block; width:11px; height:11px; border-radius:3px;
                          margin-right:6px; vertical-align:-1px; border:1px solid {BORDER}; background:var(--c); }}
  div[data-testid="stMetric"] {{ background:{CARD}; border:1px solid {BORDER}; border-radius:12px; padding:12px 14px; }}
</style>
"""


def apply_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def header(user_email: str, role: str, context: str = "") -> None:
    st.markdown(
        f"""<div class="itis-bar">
              <div class="itis-brand">
                <div class="itis-mark">ITIS</div>
                <div><div class="itis-title">3M Cash Flow Forecast</div>
                     <div class="itis-sub">VITRONIC / ITIS Holding · rolling 2+1 · {html.escape(context)}</div></div>
              </div>
              <div class="itis-user">{html.escape(user_email)}<span class="itis-chip chip-info">{html.escape(role)}</span></div>
            </div>""",
        unsafe_allow_html=True,
    )


def chip(text: str, kind: str = "") -> str:
    return f'<span class="itis-chip chip-{kind}">{html.escape(text)}</span>'


def kpi_card(label: str, value: str, foot: str = "", tone: str = "") -> str:
    color = {"pos": POSITIVE, "neg": NEGATIVE, "warn": WARNING}.get(tone, TEXT)
    return (f'<div class="kpi"><div class="kpi-label">{html.escape(label)}</div>'
            f'<div class="kpi-value" style="color:{color}">{html.escape(value)}</div>'
            f'<div class="kpi-foot">{html.escape(foot)}</div></div>')


def legend() -> None:
    items = [("Input", INPUT_BG), ("Calculated", CALC_BG), ("Carry-forward (review)", CARRY_BG),
             ("HQ adjusted", HQ_BG), ("Missing required", MISSING_BG)]
    st.markdown('<div class="legend">' + "".join(f'<span style="--c:{c}">{html.escape(t)}</span>' for t, c in items)
                + "</div>", unsafe_allow_html=True)


def fmt_money(v, decimals: int = 0) -> str:
    if v is None:
        return "–"
    return f"{v:,.{decimals}f}".replace(",", " ")
