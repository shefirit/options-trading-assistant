"""The app's design system: modern, clean, accessible.

- Inter typeface (the font used by Linear, Figma, GitHub - the modern standard)
- One indigo accent on a cool near-white canvas with white cards
- Streamlit's default chrome (menu, footer, toolbar) hidden
- Reusable pieces: hero header, section headings with eyebrows, chips

Accessibility: 17px base text, slate-900 on white (17:1 contrast), color is
never the only signal (labels and icons accompany it), visible focus rings.
"""

from __future__ import annotations

import streamlit as st

# ---------------- palette ----------------
ACCENT = "#4F46E5"        # indigo-600
ACCENT_DARK = "#4338CA"   # indigo-700
INK = "#0F172A"           # slate-900
MUTED = "#64748B"         # slate-500
BORDER = "#E2E8F0"        # slate-200
CANVAS = "#F8F9FC"
CARD = "#FFFFFF"
TILE = "#F8F9FC"
GREEN = "#059669"         # emerald-600
AMBER = "#D97706"         # amber-600
RED = "#DC2626"           # red-600

_CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

/* ---------------- global type ---------------- */
/* Force Inter everywhere except code blocks and Streamlit's icon glyphs
   (those use an icon font that must not be overridden). */
html, body, .stApp {{
    font-size: 17px;
    color: {INK};
    font-feature-settings: 'tnum' 1;   /* aligned numbers in tables/metrics */
}}
.stApp *:not(code):not(pre):not([data-testid="stIconMaterial"]):not([class*="material"]) {{
    font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif !important;
}}
h1, h2, h3, h4 {{ font-family: 'Inter', sans-serif !important; letter-spacing: -0.02em; }}
h1 {{ font-size: 2rem !important;   font-weight: 800 !important; }}
h2 {{ font-size: 1.45rem !important; font-weight: 750 !important; }}
h3 {{ font-size: 1.15rem !important; font-weight: 700 !important; }}
p, li, label {{ line-height: 1.6; }}

/* ---------------- hide Streamlit chrome ---------------- */
#MainMenu, footer {{ visibility: hidden; }}
[data-testid="stToolbar"] {{ display: none; }}
header[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ padding-top: 1.2rem; max-width: 1200px; }}

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
    box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
}}

/* ---------------- metrics as tiles ---------------- */
[data-testid="stMetric"] {{
    background: {TILE};
    border: 1px solid {BORDER};
    border-radius: 12px;
    padding: 10px 14px;
}}
[data-testid="stMetricValue"] {{ font-size: 1.55rem; font-weight: 800; }}
[data-testid="stMetricLabel"] {{ font-size: 0.85rem; font-weight: 600; color: {MUTED};
                                 text-transform: uppercase; letter-spacing: 0.04em; }}
[data-testid="stMetricDelta"] {{ font-weight: 600; }}

/* ---------------- buttons ---------------- */
.stButton > button, .stFormSubmitButton > button {{
    border-radius: 10px;
    font-weight: 600;
    padding: 0.5rem 1.1rem;
    border: 1px solid {BORDER};
    background: {CARD};
    color: {INK};
    transition: all .12s ease;
}}
.stButton > button:hover {{ border-color: {ACCENT}; color: {ACCENT};
                            transform: translateY(-1px);
                            box-shadow: 0 3px 10px rgba(79,70,229,.12); }}
[data-testid="stBaseButton-primary"] {{
    background: {ACCENT} !important;
    border: 1px solid {ACCENT} !important;
    color: #ffffff !important;
    box-shadow: 0 2px 8px rgba(79,70,229,.28);
}}
[data-testid="stBaseButton-primary"]:hover {{
    background: {ACCENT_DARK} !important; color: #fff !important;
}}
button:focus-visible {{ outline: 3px solid rgba(79,70,229,.4) !important; outline-offset: 2px; }}

/* ---------------- tabs -> modern segmented control ---------------- */
.stTabs [data-baseweb="tab-list"] {{
    gap: 4px;
    background: #EEF2F7;
    padding: 4px;
    border-radius: 12px;
    width: fit-content;
}}
.stTabs [data-baseweb="tab"] {{
    border-radius: 9px;
    padding: 6px 18px;
    font-weight: 600;
    background: transparent;
    color: {MUTED};
}}
.stTabs [aria-selected="true"] {{
    background: {CARD} !important;
    color: {INK} !important;
    box-shadow: 0 1px 4px rgba(15,23,42,.10);
}}
.stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{ display: none; }}

/* ---------------- expanders / inputs / alerts / tables ---------------- */
[data-testid="stExpander"] {{
    border: 1px solid {BORDER};
    border-radius: 14px;
    background: {CARD};
}}
[data-testid="stExpander"] summary {{ font-weight: 600; }}
.stTextInput input, .stNumberInput input, .stSelectbox > div > div,
.stMultiSelect > div > div {{ border-radius: 10px !important; }}
[data-testid="stAlert"] {{ border-radius: 12px; }}
[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 12px; }}
hr {{ border-color: {BORDER}; }}

/* ---------------- app-specific pieces ---------------- */
.ota-hero {{
    display: flex; justify-content: space-between; align-items: center;
    gap: 16px; flex-wrap: wrap; margin-bottom: 0.4rem;
}}
.ota-hero-title {{ font-size: 1.9rem; font-weight: 800; letter-spacing: -0.03em; }}
.ota-hero-sub {{ color: {MUTED}; font-size: 1rem; margin-top: 2px; }}

.ota-eyebrow {{
    font-size: 0.78rem; font-weight: 700; letter-spacing: 0.1em;
    text-transform: uppercase; color: {ACCENT}; margin-top: 1.4rem;
}}
.ota-section-title {{ font-size: 1.35rem; font-weight: 750; letter-spacing: -0.02em;
                      margin-bottom: 0.5rem; }}

.ota-chip {{
    display: inline-flex; align-items: center;
    padding: 4px 14px; border-radius: 999px;
    font-size: 0.95rem; font-weight: 600;
    border: 1px solid {BORDER}; background: {CARD}; color: {INK};
    margin-right: 8px;
}}
.ota-chip-green  {{ background: #ECFDF5; border-color: #A7F3D0; color: #065F46; }}
.ota-chip-red    {{ background: #FEF2F2; border-color: #FECACA; color: #991B1B; }}
.ota-chip-amber  {{ background: #FFFBEB; border-color: #FDE68A; color: #92400E; }}
.ota-chip-indigo {{ background: #EEF2FF; border-color: #C7D2FE; color: #3730A3; }}
</style>
"""


def inject() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def hero(title: str, subtitle: str, badge_text: str, badge_tone: str = "green") -> None:
    """The app's top header: name, one-line promise, and the data-mode badge."""
    st.markdown(
        f"""
        <div class="ota-hero">
          <div>
            <div class="ota-hero-title">{title}</div>
            <div class="ota-hero-sub">{subtitle}</div>
          </div>
          <div><span class="ota-chip ota-chip-{badge_tone}">{badge_text}</span></div>
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
