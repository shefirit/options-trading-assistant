"""The app's design system: "Fresh Growth" - modern, friendly, accessible.

- Emerald-green brand on a soft green-tinted canvas with clean white cards.
- Inter typeface, generous sizing, and HIGH-CONTRAST text throughout - secondary
  text is deliberately dark (not the faded grey that fails accessibility).
- Alive but calm: smooth hovers, clear focus rings, subtle depth.

Accessibility targets (WCAG AA):
- Body text 17px, primary ink #0B1F16 on white ~ 16:1 contrast.
- Secondary #35463D ~ 9:1, muted #4E625A ~ 5.6:1 - both pass AA, no washed-out grey.
- Color is never the only signal (icons + labels accompany it); visible focus rings.
"""

from __future__ import annotations

import html as _html
import re as _re

import streamlit as st

# ---------------- palette (Fresh Growth) ----------------
ACCENT = "#0B7A54"        # deep emerald - buttons, slider, links, focus (white text passes AA)
ACCENT_DARK = "#0A6042"   # hover / pressed
ACCENT_BRIGHT = "#10B981" # decorative only (chart lines, small fills) - never text
INK = "#0B1F16"           # primary text - near-black, green undertone, very high contrast
SECONDARY = "#35463D"     # secondary text in dense cards (~9:1)
CAPTION = "#182A21"       # instructional captions - near-black, reads as text (~13:1)
MUTED = "#4E625A"         # rare true hints
PLACEHOLDER = "#55685F"   # input placeholder - a readable hint, ~5:1
BORDER = "#DAE7E0"        # soft green-grey hairline
BORDER_STRONG = "#C1D5CB"
CANVAS = "#F2F9F5"        # soft green-tinted canvas
CARD = "#FFFFFF"
TILE = "#EEF7F1"          # metric-tile fill
GREEN = "#0B7A54"         # success / good
AMBER = "#B45309"         # warning
RED = "#C02A1B"           # danger

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ---------------- global type ---------------- */
html, body, .stApp {{
    font-size: 17px;
    color: {INK};
    font-feature-settings: 'tnum' 1, 'cv05' 1;   /* aligned numbers */
}}
.stApp {{ background: {CANVAS}; }}
.stApp *:not(code):not(pre):not([data-testid="stIconMaterial"]):not([class*="material"]) {{
    font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif !important;
}}
h1, h2, h3, h4 {{ font-family: 'Inter', sans-serif !important; letter-spacing: -0.02em;
                  color: {INK}; }}
h1 {{ font-size: 2rem !important;   font-weight: 800 !important; }}
h2 {{ font-size: 1.45rem !important; font-weight: 750 !important; }}
h3 {{ font-size: 1.15rem !important; font-weight: 700 !important; }}
p, li, label, .stMarkdown {{ line-height: 1.6; }}

/* Captions carry real instructions in this app - render them as solid dark text
   (near body ink), not faded grey, at close to body size. */
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {{
    color: {CAPTION} !important;
    font-size: 0.98rem !important;
    line-height: 1.6 !important;
}}

/* Field labels (Strategy, Symbol, Contracts...) - dark and semibold for emphasis. */
[data-testid="stWidgetLabel"] label, [data-testid="stWidgetLabel"] p,
.stSelectbox label, .stMultiSelect label, .stNumberInput label, .stTextInput label,
.stRadio label, .stSlider label {{
    color: {INK} !important; font-weight: 600 !important; font-size: 1rem !important;
}}

/* Placeholders - readable hint, not a whisper. */
input::placeholder, textarea::placeholder {{ color: {PLACEHOLDER} !important; opacity: 1 !important; }}
[data-baseweb="select"] [class*="placeholder"] {{ color: {PLACEHOLDER} !important; }}

/* ---------------- hide Streamlit chrome ---------------- */
#MainMenu, footer {{ visibility: hidden; }}
[data-testid="stToolbar"] {{ display: none; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.2rem; max-width: 1180px; }}

/* ---------------- sidebar ---------------- */
section[data-testid="stSidebar"] {{
    background: {CARD};
    border-right: 1px solid {BORDER};
}}

/* ---------------- cards (bordered containers) ---------------- */
[data-testid="stVerticalBlockBorderWrapper"] {{
    background: {CARD};
    border: 1px solid {BORDER} !important;
    border-radius: 16px !important;
    box-shadow: 0 1px 2px rgba(11, 122, 84, 0.04), 0 6px 20px rgba(11, 122, 84, 0.05);
}}

/* ---------------- metrics as tiles ---------------- */
[data-testid="stMetric"] {{
    background: {TILE};
    border: 1px solid {BORDER};
    border-radius: 14px;
    padding: 12px 16px;
    transition: border-color .15s ease, box-shadow .15s ease;
}}
[data-testid="stMetric"]:hover {{
    border-color: {BORDER_STRONG};
    box-shadow: 0 4px 14px rgba(11,122,84,.07);
}}
[data-testid="stMetricValue"] {{ font-size: 1.6rem; font-weight: 800; color: {INK}; }}
[data-testid="stMetricLabel"] {{ font-size: 0.82rem; font-weight: 600; color: {SECONDARY};
                                 text-transform: uppercase; letter-spacing: 0.05em; }}
[data-testid="stMetricDelta"] {{ font-weight: 700; }}

/* ---------------- buttons ---------------- */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
    border-radius: 10px;
    font-weight: 600;
    padding: 0.5rem 1.15rem;
    border: 1px solid {BORDER_STRONG};
    background: {CARD};
    color: {INK};
    transition: transform .12s ease, box-shadow .12s ease, border-color .12s ease, background .12s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    border-color: {ACCENT}; color: {ACCENT_DARK};
    transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(11,122,84,.14);
}}
.stButton > button:active {{ transform: translateY(0) scale(.99); }}
[data-testid="stBaseButton-primary"] {{
    background: {ACCENT} !important;
    border: 1px solid {ACCENT} !important;
    color: #ffffff !important;
    box-shadow: 0 2px 10px rgba(11,122,84,.28);
}}
[data-testid="stBaseButton-primary"]:hover {{
    background: {ACCENT_DARK} !important; border-color: {ACCENT_DARK} !important;
    color: #fff !important; box-shadow: 0 6px 18px rgba(11,122,84,.32);
}}
button:focus-visible {{ outline: 3px solid rgba(11,122,84,.42) !important; outline-offset: 2px; }}

/* ---------------- tabs -> bold, full-width segmented nav ---------------- */
.stTabs [data-baseweb="tab-list"] {{
    display: flex !important;
    gap: 8px;
    width: 100% !important;
    background: {CARD};
    padding: 8px;
    border-radius: 18px;
    border: 1px solid {BORDER};
    box-shadow: 0 2px 12px rgba(11,122,84,.07);
    margin-bottom: 0.7rem;
}}
.stTabs [data-baseweb="tab"] {{
    flex: 1 1 0 !important;
    width: auto !important;
    justify-content: center;
    min-height: 50px;
    border-radius: 13px;
    padding: 10px 14px;
    background: transparent;
    color: {SECONDARY};
    transition: transform .18s cubic-bezier(.34,1.56,.64,1),
                background .16s ease, color .16s ease, box-shadow .16s ease;
}}
.stTabs [data-baseweb="tab"] [data-testid="stMarkdownContainer"] p,
.stTabs [data-baseweb="tab"] p {{
    color: inherit !important; font-weight: 700 !important; font-size: 1.05rem !important;
    letter-spacing: -0.01em;
}}
.stTabs [data-baseweb="tab"]:hover {{
    background: #E4F5EC;
    color: {ACCENT_DARK};
    transform: translateY(-2px);
}}
.stTabs [aria-selected="true"] {{
    background: {ACCENT} !important;
    color: #FFFFFF !important;
    box-shadow: 0 8px 20px rgba(11,122,84,.32);
    transform: translateY(-2px);
}}
.stTabs [aria-selected="true"]:hover {{ background: {ACCENT_DARK} !important; color: #fff !important; }}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none; }}
.stTabs [data-baseweb="tab-list"] button:focus-visible {{
    outline: 3px solid rgba(11,122,84,.42) !important; outline-offset: 2px;
}}

/* ---------------- radios / segmented -> pill toggle ---------------- */
[data-testid="stRadio"] [role="radiogroup"] label {{ font-weight: 600; }}

/* ---------------- expanders / inputs / alerts / tables ---------------- */
[data-testid="stExpander"] {{
    border: 1px solid {BORDER};
    border-radius: 14px;
    background: {CARD};
}}
[data-testid="stExpander"] summary {{ font-weight: 600; }}
[data-testid="stExpander"] summary:hover {{ color: {ACCENT_DARK}; }}
.stTextInput input, .stNumberInput input, .stSelectbox > div > div,
.stMultiSelect > div > div {{ border-radius: 10px !important; }}
.stTextInput input:focus, .stNumberInput input:focus {{
    border-color: {ACCENT} !important; box-shadow: 0 0 0 3px rgba(11,122,84,.15) !important;
}}
[data-testid="stAlert"] {{ border-radius: 12px; }}
/* Streamlit's own alert text (st.success/warning/error/info) ships at ~4.1-4.5:1
   on its tinted backgrounds - under AA and far under this app's floor. Darken to
   the same accessible tones the chips use. */
[data-testid="stAlertContentSuccess"], [data-testid="stAlertContentSuccess"] p,
[data-testid="stAlertContentSuccess"] div {{ color: #0A5C3F !important; }}
[data-testid="stAlertContentWarning"], [data-testid="stAlertContentWarning"] p,
[data-testid="stAlertContentWarning"] div {{ color: #7A4207 !important; }}
[data-testid="stAlertContentError"], [data-testid="stAlertContentError"] p,
[data-testid="stAlertContentError"] div {{ color: #99271A !important; }}
[data-testid="stAlertContentInfo"], [data-testid="stAlertContentInfo"] p,
[data-testid="stAlertContentInfo"] div {{ color: #0B5566 !important; }}
/* st.metric deltas: Streamlit's green/red also sit just under AA on the tile
   fill - darken each direction (VIX no longer uses an inverse metric). */
[data-testid="stMetricDelta"]:has([data-testid="stMetricDeltaIcon-Up"]) {{
    color: #0A5C3F !important; }}
[data-testid="stMetricDelta"]:has([data-testid="stMetricDeltaIcon-Down"]) {{
    color: #A6301C !important; }}
[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 12px; }}
[data-baseweb="slider"] [role="slider"] {{ background: {ACCENT} !important; }}
hr {{ border-color: {BORDER}; }}
a {{ color: {ACCENT_DARK}; }}

/* ---------------- app-specific pieces ---------------- */
.ota-hero {{
    display: flex; justify-content: space-between; align-items: center;
    gap: 16px; flex-wrap: wrap; margin-bottom: 0.4rem;
}}
.ota-hero-title {{ font-size: 1.95rem; font-weight: 800; letter-spacing: -0.03em; color: {INK}; }}
.ota-hero-sub {{ color: {SECONDARY}; font-size: 1.02rem; margin-top: 3px; }}

.ota-eyebrow {{
    font-size: 0.82rem; font-weight: 800; letter-spacing: 0.14em;
    text-transform: uppercase; color: {ACCENT_DARK}; margin-top: 1.5rem;
}}
.ota-section-title {{ font-size: 1.62rem; font-weight: 800; letter-spacing: -0.025em;
                      margin: 2px 0 0.55rem; color: {INK}; line-height: 1.2; }}

.ota-chip {{
    display: inline-flex; align-items: center;
    padding: 4px 14px; border-radius: 999px;
    font-size: 0.95rem; font-weight: 600;
    border: 1px solid {BORDER}; background: {CARD}; color: {INK};
    margin-right: 8px; margin-bottom: 4px;
}}
.ota-chip-green  {{ background: #E3F5EC; border-color: #B4E3CC; color: #0A5C3F; }}
.ota-chip-red    {{ background: #FDECE9; border-color: #F6C7BF; color: #99271A; }}
.ota-chip-amber  {{ background: #FBF0DA; border-color: #F1D8A5; color: #874A08; }}
.ota-chip-indigo {{ background: #E1F0F3; border-color: #B7DBE3; color: #0B5566; }}

/* ---------------- market tiles (HTML flex - wraps 2-up on phones) ---------------- */
.ota-tiles {{ display: flex; flex-wrap: wrap; gap: 10px; }}
.ota-tile {{
    flex: 1 1 150px; min-width: 140px;
    background: {TILE}; border: 1px solid {BORDER}; border-radius: 14px;
    padding: 10px 14px;
}}
.ota-tile-label {{ font-size: 0.78rem; font-weight: 700; color: {SECONDARY};
                   text-transform: uppercase; letter-spacing: 0.05em; }}
.ota-tile-value {{ font-size: 1.45rem; font-weight: 800; color: {INK}; line-height: 1.3; }}
.ota-tile-delta {{ font-size: 0.95rem; font-weight: 700; }}

/* ---------------- sector pulse (smaller tinted tiles, wraps on phones) ---------------- */
.ota-pulse-group {{ font-size: 0.8rem; font-weight: 800; letter-spacing: 0.12em;
                    text-transform: uppercase; color: {ACCENT_DARK}; margin: 12px 0 6px; }}
.ota-pulse {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.ota-pulse-tile {{
    flex: 1 1 118px; min-width: 112px;
    background: {TILE}; border: 1px solid {BORDER}; border-radius: 12px;
    padding: 8px 12px;
}}
.ota-pulse-up   {{ background: #E6F6EE; border-color: #BCE5CF; }}
.ota-pulse-down {{ background: #FCEFEC; border-color: #F3CFC7; }}
.ota-pulse-label {{ font-size: 0.8rem; font-weight: 700; color: #213229; }}
.ota-pulse-sym  {{ font-size: 0.72rem; font-weight: 600; color: #35463D; }}
.ota-pulse-val  {{ font-size: 1.02rem; font-weight: 800; color: {INK}; }}

/* ---------------- market news (compact headline list) ---------------- */
.ota-news {{ display: flex; flex-direction: column; gap: 2px; }}
.ota-news-item {{ padding: 9px 0; border-bottom: 1px solid {BORDER}; }}
.ota-news-item:last-child {{ border-bottom: none; }}
.ota-news-title {{ font-size: 1.0rem; font-weight: 600; color: {ACCENT_DARK};
                   text-decoration: none; line-height: 1.4; }}
.ota-news-title:hover {{ text-decoration: underline; }}
.ota-news-meta {{ font-size: 0.8rem; font-weight: 600; color: {SECONDARY}; margin-top: 2px; }}

/* ---------------- phones (Rita uses the app mobile-first) ---------------- */
@media (max-width: 640px) {{
    .block-container {{ padding-left: 0.9rem; padding-right: 0.9rem; padding-top: 0.5rem; }}
    .ota-hero-title {{ font-size: 1.4rem; }}
    .ota-hero-sub {{ font-size: 0.95rem; }}
    /* the tab bar becomes a swipeable strip instead of six squeezed slivers */
    .stTabs [data-baseweb="tab-list"] {{
        overflow-x: auto; -webkit-overflow-scrolling: touch;
        padding: 6px; gap: 6px; scrollbar-width: none;
    }}
    .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {{ display: none; }}
    .stTabs [data-baseweb="tab"] {{
        flex: 0 0 auto !important; min-height: 44px; padding: 8px 13px;
    }}
    .stTabs [data-baseweb="tab"] [data-testid="stMarkdownContainer"] p,
    .stTabs [data-baseweb="tab"] p {{ font-size: 0.98rem !important; }}
    .ota-tile {{ flex: 1 1 42%; min-width: 42%; }}
    .ota-pulse-tile {{ flex: 1 1 30%; min-width: 30%; }}
    .ota-section-title {{ font-size: 1.35rem; }}
}}
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def note(text: str) -> None:
    """Render guidance/help text as solid, near-black, readable copy - our own
    element so nothing (Streamlit's faded caption grey) can override it.
    Supports **bold**; renders at close to body size."""
    safe = _html.escape(text).replace("\\$", "$")
    safe = _re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe)
    st.markdown(
        f"<div style='color:{CAPTION};font-size:0.98rem;line-height:1.6;margin:2px 0 8px;'>"
        f"{safe}</div>",
        unsafe_allow_html=True)


def hero(title: str, subtitle: str, badges: list[tuple[str, str]]) -> None:
    """The app's top header: name, one-line promise, and status badges.

    badges: [(text, tone), ...] - e.g. the data mode and where trades log to,
    so both are visible on the phone where the sidebar can't be opened."""
    chips = "".join(chip(text, tone) for text, tone in badges)
    st.markdown(
        f"""
        <div class="ota-hero">
          <div>
            <div class="ota-hero-title">{title}</div>
            <div class="ota-hero-sub">{subtitle}</div>
          </div>
          <div>{chips}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section(title: str, eyebrow: str) -> None:
    """A modern section heading: small uppercase eyebrow + strong title."""
    st.markdown(
        f'<div class="ota-eyebrow">{eyebrow}</div>'
        f'<div class="ota-section-title">{title}</div>',
        unsafe_allow_html=True,
    )


def chip(text: str, tone: str = "neutral") -> str:
    """Inline pill badge HTML. tone: neutral | green | red | amber | indigo."""
    cls = f"ota-chip ota-chip-{tone}" if tone != "neutral" else "ota-chip"
    return f'<span class="{cls}">{text}</span>'
