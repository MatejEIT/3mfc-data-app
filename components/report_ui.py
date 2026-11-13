"""Visual building blocks of the HQ report (pure HTML/CSS/SVG strings, rendered with st.markdown).

Design: dark gradient hero, glass KPI cards with micro-charts (sparkline, gauge, ring), pill tabs,
Excel-like but modern tables with sticky header / first column and row hover.
"""
from __future__ import annotations

import html
from decimal import Decimal

from components import theme as T

E = html.escape


def k(v, dash_zero: bool = True) -> str:
    """Whole thousands with thin-space grouping and a true minus sign; '–' for ~0."""
    if v is None:
        return ""
    if dash_zero and abs(v) < Decimal("0.5"):
        return "–"
    s = f"{abs(v):,.0f}".replace(",", " ")
    return f"−{s}" if v < 0 else s


# --------------------------------------------------------------------------- CSS
CSS = f"""
<style>
@keyframes rp-rise {{ from {{ opacity:0; transform:translateY(8px); }} to {{ opacity:1; transform:none; }} }}
@keyframes rp-pulse {{ 0% {{ box-shadow:0 0 0 0 rgba(52,211,153,.55); }} 100% {{ box-shadow:0 0 0 10px rgba(52,211,153,0); }} }}
.rp-hero {{ position:relative; overflow:hidden; border-radius:22px; padding:26px 30px 22px; margin:2px 0 14px;
  color:#fff; background:
    radial-gradient(900px 260px at 92% -40%, rgba(30,144,255,.55), transparent 60%),
    radial-gradient(600px 240px at -10% 130%, rgba(99,102,241,.35), transparent 60%),
    linear-gradient(120deg, #0B1640 0%, {T.DARK_NAVY} 45%, #1A4FA8 100%);
  box-shadow:0 18px 40px -18px rgba(23,38,94,.55); animation:rp-rise .45s ease-out both; }}
.rp-hero:after {{ content:""; position:absolute; inset:0; pointer-events:none; opacity:.18;
  background-image:linear-gradient(rgba(255,255,255,.07) 1px, transparent 1px),
                   linear-gradient(90deg, rgba(255,255,255,.07) 1px, transparent 1px);
  background-size:28px 28px; mask-image:linear-gradient(90deg, transparent, #000 60%); }}
.rp-eyebrow {{ font-size:11px; letter-spacing:.18em; text-transform:uppercase; color:#9CC7FF; font-weight:600; }}
.rp-title {{ font-size:30px; font-weight:750; letter-spacing:-.02em; margin:4px 0 2px; line-height:1.15; }}
.rp-title span {{ background:linear-gradient(90deg,#fff,#A9D2FF); -webkit-background-clip:text; color:transparent; }}
.rp-sub {{ color:#C9D8F5; font-size:13.5px; }}
.rp-hero-r {{ position:absolute; right:30px; top:24px; text-align:right; z-index:1; }}
.rp-hero-r .lb {{ font-size:11px; letter-spacing:.14em; text-transform:uppercase; color:#9CC7FF; font-weight:600; }}
.rp-hero-r .big {{ font-size:40px; font-weight:780; letter-spacing:-.03em; line-height:1.05; margin:4px 0 2px;
  font-variant-numeric:tabular-nums; }}
.rp-hero-r .big small {{ font-size:14px; color:#C9D8F5; font-weight:600; margin-left:6px; letter-spacing:0; }}
.rp-hero-r .chg {{ font-size:12.5px; color:#C9D8F5; }}
.rp-hero-r svg {{ position:static; margin-top:6px; }}
@media (max-width:1100px) {{ .rp-hero-r {{ display:none; }} }}
.rp-meta {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; position:relative; z-index:1; }}
.rp-tag {{ display:inline-flex; align-items:center; gap:7px; padding:5px 11px; border-radius:999px; font-size:12px;
  background:rgba(255,255,255,.09); border:1px solid rgba(255,255,255,.16); backdrop-filter:blur(6px); color:#E6EEFF; }}
.rp-dot {{ width:8px; height:8px; border-radius:50%; display:inline-block; }}
.rp-final {{ background:rgba(16,185,129,.18); border-color:rgba(52,211,153,.55); color:#D1FAE5; font-weight:650; }}
.rp-final .rp-dot {{ background:#34D399; animation:rp-pulse 1.8s infinite; }}
.rp-open {{ background:rgba(245,158,11,.16); border-color:rgba(251,191,36,.5); color:#FEF3C7; font-weight:650; }}
.rp-open .rp-dot {{ background:#FBBF24; }}

.rp-kpis {{ display:grid; grid-template-columns:repeat(6, minmax(0,1fr)); gap:12px; margin:4px 0 18px; }}
@media (max-width:1300px) {{ .rp-kpis {{ grid-template-columns:repeat(3, minmax(0,1fr)); }} }}
.rp-kpi {{ position:relative; background:linear-gradient(180deg,#fff, #FAFCFF); border:1px solid #E3EAF5;
  border-radius:16px; padding:14px 16px 12px; min-height:128px; overflow:hidden;
  box-shadow:0 1px 2px rgba(16,24,40,.04), 0 8px 24px -14px rgba(23,38,94,.25);
  transition:transform .18s ease, box-shadow .18s ease; animation:rp-rise .5s ease-out both; }}
.rp-kpi:hover {{ transform:translateY(-2px); box-shadow:0 14px 32px -14px rgba(23,38,94,.35); }}
.rp-kpi:before {{ content:""; position:absolute; left:0; top:0; right:0; height:3px; background:var(--acc,{T.PRIMARY}); }}
.rp-kpi.dark {{ border:none; color:#fff;
  background:radial-gradient(260px 120px at 100% 0%, rgba(30,144,255,.55), transparent 70%),
             linear-gradient(135deg, #0B1640, {T.DARK_NAVY} 60%, #1A4FA8); box-shadow:0 16px 34px -18px rgba(23,38,94,.8); }}
.rp-kpi.dark:before {{ background:linear-gradient(90deg,#4DA8FF,#A78BFA); }}
.rp-kpi.dark .rp-kpi-h, .rp-kpi.dark .rp-kpi-f, .rp-kpi.dark .rp-kpi-v small {{ color:#C9D8F5; }}
.rp-kpi.dark .rp-kpi-v {{ color:#fff; }}
.rp-kpi.dark .rp-ico {{ background:rgba(255,255,255,.12); color:#9CC7FF; }}
.rp-kpi-h {{ display:flex; align-items:center; gap:8px; color:{T.MUTED}; font-size:11px; letter-spacing:.08em;
  text-transform:uppercase; font-weight:650; }}
.rp-ico {{ width:26px; height:26px; border-radius:8px; display:grid; place-items:center;
  background:color-mix(in srgb, var(--acc,{T.PRIMARY}) 12%, white); color:var(--acc,{T.PRIMARY}); }}
.rp-kpi-v {{ font-size:27px; font-weight:750; letter-spacing:-.02em; margin:8px 0 2px; color:{T.TEXT};
  font-variant-numeric:tabular-nums; }}
.rp-kpi-v small {{ font-size:12px; font-weight:600; color:{T.MUTED}; margin-left:4px; letter-spacing:0; }}
.rp-kpi-f {{ font-size:11.5px; color:{T.MUTED}; display:flex; align-items:center; gap:6px; }}
.rp-delta {{ font-weight:700; padding:1px 7px; border-radius:999px; font-size:11px; }}
.rp-up {{ color:#047857; background:#D1FAE5; }} .rp-down {{ color:#B91C1C; background:#FEE2E2; }}
.rp-spark {{ position:absolute; right:10px; bottom:10px; opacity:.95; }}
.rp-kpi:has(svg.rp-spark) .rp-kpi-f {{ padding-right:74px; }}
.rp-kpi-f, .rp-delta {{ white-space:nowrap; }}
.rp-kpi-f {{ overflow:hidden; text-overflow:ellipsis; }}
.rp-gauge {{ height:6px; border-radius:99px; background:#EEF2F7; margin-top:9px; position:relative; overflow:hidden; }}
.rp-gauge i {{ position:absolute; left:0; top:0; bottom:0; border-radius:99px; }}

.rp-card {{ background:#fff; border:1px solid #E3EAF5; border-radius:18px; padding:16px 18px 10px;
  box-shadow:0 10px 30px -20px rgba(23,38,94,.35); animation:rp-rise .55s ease-out both; }}
.rp-card-h {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:2px; }}
.rp-card-t {{ font-weight:700; font-size:15px; color:{T.TEXT}; display:flex; align-items:center; gap:9px; }}
.rp-badge {{ width:28px; height:28px; border-radius:9px; display:grid; place-items:center; color:#fff;
  background:linear-gradient(135deg, #1E90FF, #1A4FA8 60%, #17265E); box-shadow:0 6px 14px -6px rgba(30,144,255,.8); }}
.rp-foot {{ display:flex; justify-content:space-between; gap:12px; margin-top:22px; padding:12px 18px; border-radius:14px;
  font-size:11.5px; color:#C9D8F5; letter-spacing:.02em;
  background:radial-gradient(500px 90px at 90% -60%, rgba(30,144,255,.5), transparent 70%),
             linear-gradient(120deg, #0B1640, {T.DARK_NAVY} 60%, #1A4FA8); }}
.rp-card-s {{ color:{T.MUTED}; font-size:12px; }}

/* pill tabs on a dark gradient ribbon (Streamlit >= 1.5x: react-aria role=tablist / data-testid=stTab) */
[role="tablist"] {{ gap:6px !important; padding:6px !important; border-radius:16px !important; width:fit-content !important;
  border:1px solid rgba(255,255,255,.08) !important;
  background:radial-gradient(420px 120px at 85% -40%, rgba(30,144,255,.55), transparent 70%),
             linear-gradient(120deg, #0B1640 0%, {T.DARK_NAVY} 55%, #1A4FA8 100%) !important;
  box-shadow:0 12px 28px -16px rgba(23,38,94,.7) !important; }}
[data-testid="stTab"] {{ height:34px !important; padding:0 16px !important; border-radius:11px !important;
  background:transparent !important; color:#C9D8F5 !important; transition:background .15s ease, color .15s ease; }}
[data-testid="stTab"]:hover {{ color:#fff !important; background:rgba(255,255,255,.10) !important; }}
[data-testid="stTab"] p {{ font-weight:650 !important; font-size:13.5px !important; }}
[data-testid="stTab"][aria-selected="true"] {{ background:#fff !important; color:{T.DARK_NAVY} !important;
  box-shadow:0 4px 14px -4px rgba(30,144,255,.65) !important; }}
[role="tablist"] .react-aria-SelectionIndicator {{ display:none !important; }}

/* tables */
.rp-tw {{ overflow:auto; max-height:640px; border-radius:14px; border:1px solid #E3EAF5; background:#fff; }}
.rp-t {{ width:100%; border-collapse:separate; border-spacing:0; font-size:12.5px; font-variant-numeric:tabular-nums; }}
.rp-t th, .rp-t td {{ padding:6px 9px; text-align:right; white-space:nowrap; border-bottom:1px solid #F0F3F8; }}
.rp-t thead th {{ position:sticky; top:0; z-index:2; background:#F6F9FE; color:{T.MUTED}; font-weight:650;
  font-size:11.5px; letter-spacing:.02em; }}
.rp-t thead tr:nth-child(2) th {{ top:31px; }}
.rp-t thead tr:first-child th {{ background:linear-gradient(120deg, #0B1640, {T.DARK_NAVY} 60%, #1A4FA8); color:#E6EEFF; }}
.rp-t thead tr:first-child th.lbl {{ background:#0B1640; color:#9CC7FF; letter-spacing:.12em; }}
.rp-t th.mh {{ text-align:center; color:#fff !important; font-size:12.5px; letter-spacing:.02em;
  border-bottom:2px solid #4DA8FF; border-left:1px solid rgba(255,255,255,.08); }}
.rp-t thead tr:nth-child(2) th {{ background:#EEF5FF; color:{T.DARK_NAVY}; }}
.rp-t thead tr:nth-child(2) th.t {{ background:#DCEBFF; }}
.rp-t td.lbl, .rp-t th.lbl {{ position:sticky; left:0; z-index:1; background:#fff; text-align:left; min-width:250px; }}
.rp-t thead th.lbl {{ z-index:3; background:#F6F9FE; }}
.rp-t td.m, .rp-t th.m {{ background:#F4F7FC; font-weight:650; }}
.rp-t td.t, .rp-t th.t {{ background:#EAF3FF; font-weight:750; color:{T.DARK_NAVY}; }}
.rp-t tbody tr:hover td {{ background:#FFFBEA !important; }}
.rp-t tr.sec td {{ text-align:left; font-size:11px; letter-spacing:.14em; text-transform:uppercase; font-weight:750;
  color:{T.DARK_NAVY}; padding-top:14px; background:#fff; border-bottom:1px solid #DCE6F5; }}
.rp-t tr.sec td:first-child {{ box-shadow:inset 3px 0 0 {T.PRIMARY}; }}
.rp-t tr.sum td {{ font-weight:650; background:#FAFBFD; }}
.rp-t tr.total td {{ font-weight:750; background:linear-gradient(90deg,#EEF4FF,#F7FAFF); border-top:1px solid #D6E2F5; }}
.rp-t tr.total td.lbl {{ background:#EEF4FF; }}
.rp-t tr.memo td {{ color:{T.MUTED}; font-style:italic; }}
.rp-t tr.grand td {{ font-weight:800; color:#fff !important; border:none;
  background:linear-gradient(120deg, #11225C, #1A4FA8) !important; }}
.rp-t tr.grand td.lbl {{ background:#11225C !important; }}
.rp-t tr.grand td.neg {{ color:#FFB4B4 !important; }}
.rp-t tr.grand td.z {{ color:#7C93C3 !important; }}
.rp-t tr.grand:hover td {{ background:linear-gradient(120deg, #11225C, #1E5BC6) !important; }}
.rp-t tr.check td {{ font-weight:650; }}
.rp-t td.neg {{ color:{T.NEGATIVE}; }}
.rp-t td.z {{ color:#C3CBD8; }}
.rp-bar {{ display:inline-block; height:6px; border-radius:99px; vertical-align:middle; margin-left:8px; }}

/* entity tiles */
.rp-tiles {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(215px,1fr)); gap:10px; }}
.rp-tile {{ border:1px solid #E3EAF5; border-radius:14px; padding:11px 13px; background:#fff; position:relative;
  transition:transform .15s ease, box-shadow .15s ease; }}
.rp-tile:hover {{ transform:translateY(-2px); box-shadow:0 10px 24px -16px rgba(23,38,94,.45); }}
.rp-tile b {{ font-size:13px; color:{T.TEXT}; }} .rp-tile .nm {{ color:{T.MUTED}; font-size:11.5px; margin:2px 0 7px;
  white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.rp-st {{ display:inline-flex; align-items:center; gap:6px; font-size:11px; font-weight:650; padding:2px 8px;
  border-radius:999px; }}
.rp-tile .fx {{ position:absolute; right:12px; top:11px; font-size:11px; color:{T.MUTED}; font-variant-numeric:tabular-nums; }}

/* findings */
.rp-find {{ display:flex; gap:12px; align-items:flex-start; padding:11px 14px; border:1px solid #E8EDF5; border-radius:12px;
  margin-bottom:8px; background:#fff; border-left:4px solid var(--c); }}
.rp-find .ic {{ width:24px; height:24px; border-radius:7px; display:grid; place-items:center; color:#fff;
  background:var(--c); font-size:13px; font-weight:800; flex:none; }}
.rp-find .ar {{ font-size:11px; color:{T.MUTED}; font-weight:650; letter-spacing:.04em; }}
.rp-find .ms {{ font-size:13px; color:{T.TEXT}; }}
.rp-ok {{ padding:22px; text-align:center; border:1px dashed #BFE3CF; border-radius:14px; color:#047857; background:#F0FBF5; }}
.rp-steps {{ counter-reset:s; list-style:none; padding:0; margin:0; }}
.rp-steps li {{ counter-increment:s; position:relative; padding:10px 12px 10px 46px; border-bottom:1px solid #F0F3F8;
  font-size:13px; color:{T.TEXT}; }}
.rp-steps li:before {{ content:counter(s); position:absolute; left:10px; top:9px; width:24px; height:24px;
  border-radius:8px; background:#EAF3FF; color:{T.DARK_NAVY}; font-weight:750; font-size:12px; display:grid;
  place-items:center; }}
</style>
"""

ICONS = {
    "wallet": '<path d="M3 7a2 2 0 0 1 2-2h12v4H5a2 2 0 0 1-2-2Zm0 0v10a2 2 0 0 0 2 2h14V9H5" /><circle cx="16" cy="14" r="1.3"/>',
    "flow": '<path d="M4 17l5-5 4 4 7-8"/><path d="M15 8h5v5"/>',
    "flag": '<path d="M5 21V4m0 0h11l-2 4 2 4H5"/>',
    "low": '<path d="M4 7l6 6 4-4 6 6"/><path d="M15 15h5v-5"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
    "bars": '<path d="M5 20V10M10 20V4M15 20v-7M20 20v-11"/>',
    "wave": '<path d="M3 15c2.5 0 2.5-6 5-6s2.5 8 5 8 2.5-10 5-10 2 4 3 4"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
}


def icon(name: str) -> str:
    return (f'<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')


def sparkline(values: list[float], w: int = 92, h: int = 30, color: str = T.PRIMARY, cls: str = "rp-spark") -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    pts = [(i * (w - 4) / (len(values) - 1) + 2, h - 3 - (v - lo) / span * (h - 6)) for i, v in enumerate(values)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = f"2,{h} {line} {w - 2},{h}"
    gid = f"g{abs(hash((tuple(values), color))) % 10**8}"
    lx, ly = pts[-1]
    return (f'<svg class="{cls}" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f'<defs><linearGradient id="{gid}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="{color}" '
            f'stop-opacity=".28"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="{area}" fill="url(#{gid})"/><polyline points="{line}" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="2.8" fill="{color}" stroke="#fff" stroke-width="1.5"/></svg>')


def ring(share: float, color: str, size: int = 42) -> str:
    r, c = 16, 2 * 3.14159 * 16
    return (f'<svg class="rp-spark" width="{size}" height="{size}" viewBox="0 0 40 40">'
            f'<circle cx="20" cy="20" r="{r}" fill="none" stroke="#EEF2F7" stroke-width="5"/>'
            f'<circle cx="20" cy="20" r="{r}" fill="none" stroke="{color}" stroke-width="5" stroke-linecap="round" '
            f'stroke-dasharray="{c * share:.1f} {c:.1f}" transform="rotate(-90 20 20)"/></svg>')


def hero(eyebrow: str, title: str, subtitle: str, tags: list[str], right: str = "") -> str:
    return (f'<div class="rp-hero">{right}<div class="rp-eyebrow">{E(eyebrow)}</div>'
            f'<div class="rp-title"><span>{E(title)}</span></div><div class="rp-sub">{E(subtitle)}</div>'
            f'<div class="rp-meta">{"".join(tags)}</div></div>')


def tag(text: str, kind: str = "") -> str:
    dot = '<span class="rp-dot"></span>' if kind in ("final", "open") else ""
    return f'<span class="rp-tag rp-{kind}">{dot}{E(text)}</span>'


def kpi(label: str, value: str, foot: str, ico: str, accent: str = T.PRIMARY, unit: str = "kEUR",
        extra: str = "", delay: float = 0, dark: bool = False) -> str:
    return (f'<div class="rp-kpi{" dark" if dark else ""}" style="--acc:{accent};animation-delay:{delay:.2f}s">'
            f'<div class="rp-kpi-h"><span class="rp-ico">{icon(ico)}</span>{E(label)}</div>'
            f'<div class="rp-kpi-v">{E(value)}<small>{E(unit)}</small></div>'
            f'<div class="rp-kpi-f">{foot}</div>{extra}</div>')


def gauge(share: float, color: str) -> str:
    return f'<div class="rp-gauge"><i style="width:{max(3, min(100, share * 100)):.0f}%;background:{color}"></i></div>'


def footer(left: str, right: str) -> str:
    return f'<div class="rp-foot"><span>{E(left)}</span><span>{E(right)}</span></div>'


def card_head(title: str, sub: str = "", ico: str = "") -> str:
    badge = f'<span class="rp-badge">{icon(ico)}</span>' if ico else ""
    return (f'<div class="rp-card-h"><div class="rp-card-t">{badge}{E(title)}</div>'
            f'<div class="rp-card-s">{E(sub)}</div></div>')


def table(head_rows: list[str], body_rows: list[str], height: int | None = None) -> str:
    style = f' style="max-height:{height}px"' if height else ""
    return (f'<div class="rp-tw"{style}><table class="rp-t"><thead>{"".join(head_rows)}</thead>'
            f'<tbody>{"".join(body_rows)}</tbody></table></div>')


def num_cell(v, cls: str = "", red_negative: bool = True) -> str:
    txt = k(v)
    extra = " z" if txt == "–" else (" neg" if red_negative and v is not None and v < 0 else "")
    return f'<td class="{cls}{extra}">{txt}</td>'


STATUS_STYLE = {
    "Consolidated": ("#047857", "#D1FAE5", "●"),
    "Not expected": (T.MUTED, "#EEF2F7", "○"),
    "Missing": ("#B91C1C", "#FEE2E2", "●"),
    "No FX rate": ("#B91C1C", "#FEE2E2", "●"),
    "No values": ("#B45309", "#FEF3C7", "●"),
}


def status_pill(status: str) -> str:
    fg, bg, dot = STATUS_STYLE.get(status, (T.MUTED, "#EEF2F7", "●"))
    return f'<span class="rp-st" style="color:{fg};background:{bg}">{dot} {E(status)}</span>'


def tile(code: str, name: str, currency: str, rate: str, status: str, revision: str) -> str:
    rev = f' · {E(revision)}' if revision else ""
    return (f'<div class="rp-tile"><span class="fx">{E(currency)} {E(rate)}</span><b>{E(code)}</b>'
            f'<div class="nm" title="{E(name)}">{E(name)}{rev}</div>{status_pill(status)}</div>')


SEVERITY_STYLE = {"Critical": (T.NEGATIVE, "!"), "Check": (T.WARNING, "?"), "Info": (T.PRIMARY, "i")}


def finding(severity: str, area: str, topic: str, message: str) -> str:
    color, sym = SEVERITY_STYLE[severity]
    return (f'<div class="rp-find" style="--c:{color}"><div class="ic">{sym}</div><div>'
            f'<div class="ar">{E(severity.upper())} · {E(area)} · {E(topic)}</div>'
            f'<div class="ms">{E(message)}</div></div></div>')
