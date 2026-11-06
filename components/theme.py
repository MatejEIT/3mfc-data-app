"""Visual identity: tones of the existing VITRONIC/ITIS data app (sampled from its screenshot)
combined with the conventions of the entity Excel template "FC Vxxx"."""
from __future__ import annotations

import html

import streamlit as st

# existing ITIS data app (sampled): primary button, sidebar tint, text, dividers
PRIMARY = "#1E90FF"
PRIMARY_TINT = "#E6F2FF"
NAVY = "#1A5FB4"          # darker shade of PRIMARY for text accents
DARK_NAVY = "#17265E"
BG = "#F7F9FC"
CARD = "#FFFFFF"
BORDER = "#D3D3D4"
MUTED = "#64748B"
TEXT = "#222529"
POSITIVE = "#15803D"
WARNING = "#D97706"
NEGATIVE = "#DC2626"

# entity Excel template "FC Vxxx" (exact fills from the file, week header softened)
XL_INPUT = "#FFFFDE"       # "Please fill only light-yellow cells"
XL_SUBTOTAL = "#F2F2F2"
XL_TOTAL = "#D9D9D9"
XL_MONTH = "#E7E9EC"
XL_WEEK_HEAD = "#FBE4D6"
XL_GRID = "#E5E7EB"
XL_NA = "#E5E5E5"

# value-state tints used consistently in grids and legends
INPUT_BG = XL_INPUT
CALC_BG = XL_SUBTOTAL
CARRY_BG = "#FFE7A8"
HQ_BG = "#DCEBFF"
PENDING_BG = "#C9E2FF"
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
  .itis-instr {{ background:{XL_INPUT}; border:1px solid #EFE7A0; border-left:4px solid {PRIMARY};
                 border-radius:8px; padding:9px 14px; font-size:13px; color:{TEXT}; margin:4px 0 8px; }}
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
    items = [("Input – fill only light-yellow cells", XL_INPUT), ("Calculated", XL_SUBTOTAL),
             ("Month total", XL_MONTH), ("Carry-forward – please review", CARRY_BG), ("HQ adjusted", HQ_BG),
             ("Unsaved change", PENDING_BG)]
    st.markdown('<div class="legend">' + "".join(f'<span style="--c:{c}">{html.escape(t)}</span>' for t, c in items)
                + "</div>", unsafe_allow_html=True)


def instructions(currency: str, scale: int) -> None:
    unit = f"thousands of {currency} (k{currency})" if scale == 1000 else f"full {currency}"
    st.markdown(
        f'<div class="itis-instr"><b>Please fill only light-yellow cells.</b> Values in {html.escape(unit)} · '
        'weekly intervals are the input, months are calculated · type customer / project names directly in the '
        'first column · empty is not zero – leave a cell empty only if there is nothing to report.</div>',
        unsafe_allow_html=True)


def fmt_money(v, decimals: int = 0) -> str:
    if v is None:
        return "–"
    return f"{v:,.{decimals}f}".replace(",", " ")
