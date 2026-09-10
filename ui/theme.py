"""The app's design system: a trading statement, not a friendly green app.

ONE JOB PER COLOUR
------------------
The page used to be green on green on green - green canvas, green-tinted cards,
green borders, green shadows, green accent, green for profit. When everything
is the brand colour, nothing is emphasis. Now:

  ACCENT (emerald)  interaction only - buttons, the selected tab, focus rings
  GREEN / RED       the SIGN of a number, and nothing else
  AMBER             pace and warning
  surfaces          near-neutral canvas, white cards, one hairline border

Surfaces are quiet so the numbers can be loud: money renders with tabular
figures everywhere, so a column of dollars lines up and a value does not jump
sideways between reruns when a 1 becomes an 8.

Flat, not floating. Cards carry a hairline instead of a drop shadow and the tab
bar does not spring when touched - a page of soft shadowed pills reads as a
consumer app, and this is an instrument she runs a business on.

- Inter typeface, generous sizing, and HIGH-CONTRAST text throughout - secondary
  text is deliberately dark (not the faded grey that fails accessibility).

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
BORDER = "#E2E7E4"        # neutral hairline - the card edge, not a colour
BORDER_STRONG = "#CBD3CE"
CANVAS = "#F4F6F5"        # near-neutral canvas, the faintest green cast
CARD = "#FFFFFF"
TILE = "#F5F8F6"          # metric-tile fill - a shade off white, not a green
GREEN = "#0B7A54"         # success / good
AMBER = "#B45309"         # warning
RED = "#C02A1B"           # danger

# The dark results band. Deep navy rather than the app's green on purpose: a
# band of headline numbers should read as its own object - a page torn out of a
# monthly statement - and not as another panel of the tab it sits in.
BAND = "#12294A"
BAND_SUB = "#D8E6F7"      # labels and captions on the band
BAND_HERO = "#7DE8B0"     # the one number the band exists for

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
/* Desktop only, so the window is the constraint rather than a phone. Wide
   enough for the twelve-column open-positions table and a month of daily bars
   without a horizontal scrollbar. Streamlit's default side padding is 5rem
   each way, which is width the tables need more than the margins do.

   Prose does NOT follow the container out to this width - see .ota-prose. A
   1,600px line of text is unreadable no matter how much room there is. */
.block-container {{
    padding-top: 0.9rem;
    padding-left: 2.25rem;
    padding-right: 2.25rem;
    max-width: 1680px;
}}

/* Everything that is a SENTENCE stops at a readable measure. Tables, charts
   and card grids take the full width; paragraphs never do. */
.ota-prose {{ max-width: 84ch; }}

/* ---------------- sidebar ---------------- */
section[data-testid="stSidebar"] {{
    background: {CARD};
    border-right: 1px solid {BORDER};
}}

/* ---------------- cards (bordered containers) ---------------- */
[data-testid="stVerticalBlockBorderWrapper"] {{
    background: {CARD};
    border: 1px solid {BORDER} !important;
    border-radius: 10px !important;
}}

/* ---------------- metrics as tiles ---------------- */
[data-testid="stMetric"] {{
    background: {TILE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 12px 16px;
    transition: border-color .15s ease, box-shadow .15s ease;
}}
[data-testid="stMetric"]:hover {{ border-color: {BORDER_STRONG}; }}
[data-testid="stMetricValue"] {{ font-size: 1.6rem; font-weight: 800; color: {INK};
                                 font-variant-numeric: tabular-nums; }}
[data-testid="stMetricLabel"] {{ font-size: 0.82rem; font-weight: 600; color: {SECONDARY};
                                 text-transform: uppercase; letter-spacing: 0.05em; }}
[data-testid="stMetricDelta"] {{ font-weight: 700; }}

/* ---------------- buttons ---------------- */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
    border-radius: 8px;
    font-weight: 600;
    padding: 0.5rem 1.15rem;
    border: 1px solid {BORDER_STRONG};
    background: {CARD};
    color: {INK};
    transition: border-color .12s ease, background .12s ease;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    border-color: {ACCENT}; color: {ACCENT_DARK};
    background: {TILE};
}}
.stButton > button:active {{ transform: translateY(0) scale(.99); }}
[data-testid="stBaseButton-primary"] {{
    background: {ACCENT} !important;
    border: 1px solid {ACCENT} !important;
    color: #ffffff !important;
    box-shadow: 0 1px 3px rgba(11,122,84,.24);
}}
[data-testid="stBaseButton-primary"]:hover {{
    background: {ACCENT_DARK} !important; border-color: {ACCENT_DARK} !important;
    color: #fff !important; box-shadow: 0 2px 6px rgba(11,122,84,.28);
}}
button:focus-visible {{ outline: 3px solid rgba(11,122,84,.42) !important; outline-offset: 2px; }}

/* ---------------- tabs -> bold, full-width segmented nav ---------------- */
.stTabs [data-baseweb="tab-list"] {{
    display: flex !important;
    gap: 8px;
    width: 100% !important;
    background: {CARD};
    padding: 6px;
    border-radius: 10px;
    border: 1px solid {BORDER};
    margin-bottom: 0.7rem;
}}
/* No lift, no bounce, no glow. A tab bar that springs when you touch it reads
   as a consumer app; a trading page's navigation should be the quietest thing
   on it. Colour and weight carry the selected state, movement does not. */
.stTabs [data-baseweb="tab"] {{
    flex: 1 1 0 !important;
    width: auto !important;
    justify-content: center;
    min-height: 46px;
    border-radius: 8px;
    padding: 10px 14px;
    background: transparent;
    color: {SECONDARY};
    transition: background .13s ease, color .13s ease;
}}
.stTabs [data-baseweb="tab"] [data-testid="stMarkdownContainer"] p,
.stTabs [data-baseweb="tab"] p {{
    color: inherit !important; font-weight: 700 !important; font-size: 1.05rem !important;
    letter-spacing: -0.01em;
}}
.stTabs [data-baseweb="tab"]:hover {{
    background: {TILE};
    color: {ACCENT_DARK};
}}
.stTabs [aria-selected="true"] {{
    background: {ACCENT} !important;
    color: #FFFFFF !important;
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
    border-radius: 10px;
    background: {CARD};
}}
[data-testid="stExpander"] summary {{ font-weight: 600; }}
[data-testid="stExpander"] summary:hover {{ color: {ACCENT_DARK}; }}
.stTextInput input, .stNumberInput input, .stSelectbox > div > div,
.stMultiSelect > div > div {{ border-radius: 10px !important; }}
.stTextInput input:focus, .stNumberInput input:focus {{
    border-color: {ACCENT} !important; box-shadow: 0 0 0 3px rgba(11,122,84,.15) !important;
}}
[data-testid="stAlert"] {{ border-radius: 10px; }}
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
[data-testid="stDataFrame"] {{ border: 1px solid {BORDER}; border-radius: 10px; }}
[data-baseweb="slider"] [role="slider"] {{ background: {ACCENT} !important; }}
hr {{ border-color: {BORDER}; margin: 0.9rem 0; }}
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
    text-transform: uppercase; color: {ACCENT_DARK}; margin-top: 1.05rem;
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
    background: {TILE}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 10px 14px;
}}
.ota-tile-label {{ font-size: 0.78rem; font-weight: 700; color: {SECONDARY};
                   text-transform: uppercase; letter-spacing: 0.05em; }}
.ota-tile-value {{ font-size: 1.45rem; font-weight: 800; color: {INK};
                   line-height: 1.3; font-variant-numeric: tabular-nums; }}
.ota-tile-delta {{ font-size: 0.95rem; font-weight: 700; }}

/* ---------------- sector pulse (smaller tinted tiles, wraps on phones) ---------------- */
.ota-pulse-group {{ font-size: 0.8rem; font-weight: 800; letter-spacing: 0.12em;
                    text-transform: uppercase; color: {ACCENT_DARK}; margin: 12px 0 6px; }}
.ota-pulse {{ display: flex; flex-wrap: wrap; gap: 8px; }}
.ota-pulse-tile {{
    flex: 1 1 118px; min-width: 112px;
    background: {TILE}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 8px 12px;
}}
.ota-pulse-up   {{ background: #E6F6EE; border-color: #BCE5CF; }}
.ota-pulse-down {{ background: #FCEFEC; border-color: #F3CFC7; }}
.ota-pulse-label {{ font-size: 0.8rem; font-weight: 700; color: #213229; }}
.ota-pulse-sym  {{ font-size: 0.72rem; font-weight: 600; color: #35463D; }}
.ota-pulse-val  {{ font-size: 1.02rem; font-weight: 800; color: {INK};
                   font-variant-numeric: tabular-nums; }}

/* ---------------- one trade, told move by move ----------------
   A list, not a dataframe. The first version of this was a glide-grid with a
   "Running total" column, and on a PMCC that column reads -14,360 for twelve
   rows before turning positive at the close - which looks like a catastrophe
   rather than a trade that made money. What answers "did this work" is the
   paid/collected/result block at the bottom, and what answers "does it match
   my broker" is one readable line per fill. Neither needed a grid. */
.ota-story {{ border: 1px solid {BORDER}; border-radius: 10px; overflow: hidden;
              background: {CARD}; }}
.ota-story-head {{ background: {TILE}; border-bottom: 1px solid {BORDER};
                   padding: 14px 16px; }}
.ota-story-title {{ font-size: 1.12rem; font-weight: 800; color: {INK}; }}
.ota-story-when {{ font-size: 0.92rem; font-weight: 600; color: {SECONDARY};
                   margin-top: 2px; }}
.ota-story-result {{ font-size: 1.7rem; font-weight: 800; margin-top: 8px;
                     line-height: 1.25; }}
.ota-story-resultsub {{ font-size: 0.92rem; font-weight: 600; color: {CAPTION}; }}
.ota-story-row {{ display: flex; align-items: baseline; gap: 12px;
                  padding: 11px 16px; border-bottom: 1px solid {BORDER}; }}
.ota-story-row:last-child {{ border-bottom: none; }}
.ota-story-n {{ flex: 0 0 26px; font-size: 0.86rem; font-weight: 800;
                color: {SECONDARY}; }}
.ota-story-date {{ flex: 0 0 96px; font-size: 0.9rem; font-weight: 700;
                   color: {CAPTION}; }}
.ota-story-what {{ flex: 1 1 auto; font-size: 0.99rem; color: {INK};
                   font-weight: 600; }}
.ota-story-detail {{ font-size: 0.88rem; color: {SECONDARY}; font-weight: 500;
                     margin-top: 2px; }}
.ota-story-amt {{ flex: 0 0 auto; font-size: 1.02rem; font-weight: 800;
                  text-align: right; white-space: nowrap;
                  font-variant-numeric: tabular-nums; }}
/* Darker than the chip pair (#0A5C3F / #99271A), which land at ~7.3:1 on the
   tile fill - under her ~9:1 floor. These clear it on both backgrounds, and
   the amounts carry a + or - anyway so the colour is never the only signal. */
.ota-story-in  {{ color: #08492F; }}
.ota-story-out {{ color: #7A1E14; }}
.ota-story-sum {{ background: {TILE}; border-top: 2px solid {BORDER_STRONG}; }}
.ota-story-sum .ota-story-what {{ font-weight: 800; }}
.ota-story-final .ota-story-what,
.ota-story-final .ota-story-amt {{ font-size: 1.14rem; font-weight: 800; }}

/* ---------------- the KPI row (laptop first, phone second) ----------------
   A GRID, not the flex row .ota-tiles uses. With flex: 1 1 <basis> six cards
   wrapping four-and-two stretches the last two to half the width each, and a
   dashboard's top row has to be a row of equals. auto-fit + minmax gives
   6-up on a laptop, 4-up, 3-up, then 2-up as the window narrows, every card
   the same width at every step, with no media query for the middle sizes.

   The left border carries the tone. It is always paired with a word in the
   sub-line, because colour is never the only signal. */
.ota-kpi {{
    display: grid; gap: 12px; margin: 6px 0 4px;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
}}
/* A row of SIX cannot use auto-fit. Whatever the minmax, some window width
   fits five of them and leaves the sixth alone on its own line at full width,
   which reads as one card being more important than the other five.

   So the count is pinned rather than fitted: six across once there is room for
   six, three and three below that. Both are balanced; neither can orphan. */
.ota-kpi-3 {{ grid-template-columns: repeat(3, 1fr); }}
@media (min-width: 1180px) {{
    .ota-kpi-3 {{ grid-template-columns: repeat(6, 1fr); }}
}}
.ota-kpi-card {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px;
    border-left: 4px solid transparent; padding: 14px 16px;
    transition: border-color .15s ease, box-shadow .15s ease;
}}
.ota-kpi-card:hover {{ border-color: {BORDER_STRONG}; }}
.ota-kpi-label {{ font-size: 0.75rem; font-weight: 800; color: {SECONDARY};
                  text-transform: uppercase; letter-spacing: 0.06em; }}
/* tabular-nums so a value does not jump sideways between reruns when a 1
   becomes an 8 - the page re-renders on every interaction. */
.ota-kpi-value {{ font-size: 1.9rem; font-weight: 800; color: {INK};
                  line-height: 1.15; margin: 3px 0 1px;
                  font-variant-numeric: tabular-nums; }}
.ota-kpi-sub {{ font-size: 0.86rem; font-weight: 600; color: {SECONDARY};
                line-height: 1.45; }}
.ota-kpi-good  {{ border-left-color: {GREEN}; }}
.ota-kpi-watch {{ border-left-color: {AMBER}; }}
.ota-kpi-bad   {{ border-left-color: {RED}; }}

/* ---------------- the dark band (the report's own object) ----------------
   Deep navy rather than the app's green, so a results band reads as a page
   torn out of a statement and not as another panel of the tab. */
.ota-band {{ background: {BAND}; border-radius: 10px; padding: 22px 24px;
             margin: 6px 0 14px; }}
.ota-band-zones {{ display: flex; gap: 34px; flex-wrap: wrap; margin-top: 14px; }}
.ota-band-zone {{ min-width: 170px; }}
/* On a desktop window the three zones are a row of equals across the whole
   band. As a wrapping flex row they bunched at the left and dropped "needs a
   decision today" - the most urgent thing on the page - onto a second line
   with empty navy beside it. */
@media (min-width: 980px) {{
    .ota-band-zones {{ display: grid; grid-template-columns: 1.2fr 1fr 1.3fr;
                       gap: 28px; }}
    .ota-band-zone {{ min-width: 0; }}
}}
.ota-band-label {{ font-size: 0.8rem; font-weight: 800; color: {BAND_SUB};
                   letter-spacing: 0.06em; text-transform: uppercase; }}
.ota-band-hero {{ font-size: 2.6rem; font-weight: 800; color: {BAND_HERO};
                  line-height: 1.1; font-variant-numeric: tabular-nums; }}
.ota-band-value {{ font-size: 1.9rem; font-weight: 800; color: #FFFFFF;
                   line-height: 1.15; font-variant-numeric: tabular-nums; }}
.ota-band-sub {{ font-size: 0.9rem; font-weight: 600; color: {BAND_SUB}; }}
.ota-band-title {{ font-size: 1.45rem; font-weight: 800; color: #FFFFFF; }}

/* ---------------- the progress track (goal thermometer) ----------------
   A CSS rail rather than a one-bar chart: a chart would cost 200px of height,
   could not do the typography, and would need the axis-padding workaround the
   layered charts need. The marker is where a steady plan would be today. */
.ota-track {{ position: relative; margin: 16px 0 4px; }}
.ota-track-rail {{ height: 22px; background: {TILE};
                   border: 1px solid {BORDER_STRONG}; border-radius: 999px;
                   overflow: hidden; }}
.ota-track-fill {{ height: 100%; border-radius: 999px;
                   background: linear-gradient(90deg, {ACCENT}, {ACCENT_BRIGHT}); }}
.ota-track-marker {{ position: absolute; top: -7px; width: 2px; height: 36px;
                     background: {INK}; }}
.ota-track-flag {{ position: absolute; top: -30px; font-size: 0.78rem;
                   font-weight: 800; color: {INK}; white-space: nowrap;
                   transform: translateX(-50%); }}
.ota-track-scale {{ display: flex; justify-content: space-between;
                    margin-top: 7px; font-size: 0.84rem; font-weight: 700;
                    color: {SECONDARY}; }}
.ota-track-value {{ font-size: 1.05rem; font-weight: 800; color: {INK};
                    font-variant-numeric: tabular-nums; }}

/* ---------------- the faded-book legend ----------------
   Wherever a chart draws the other account behind the real one, this says so
   in words. A grey bar with no explanation is the fastest way to make her
   think practice money is being counted. */
.ota-legend {{ display: flex; gap: 10px; align-items: flex-start; margin: 8px 0 2px; }}
.ota-legend-swatch {{ flex: 0 0 16px; width: 16px; height: 16px; margin-top: 3px;
                      border-radius: 4px; background: {BORDER_STRONG}; opacity: .55; }}
.ota-legend-text {{ font-size: 0.95rem; font-weight: 600; color: {CAPTION};
                    line-height: 1.5; max-width: 84ch; }}

/* ---------------- market news (compact headline list) ---------------- */
.ota-news {{ display: flex; flex-direction: column; gap: 2px; }}
.ota-news-item {{ padding: 9px 0; border-bottom: 1px solid {BORDER}; }}
.ota-news-item:last-child {{ border-bottom: none; }}
.ota-news-title {{ font-size: 1.0rem; font-weight: 600; color: {ACCENT_DARK};
                   text-decoration: none; line-height: 1.4; }}
.ota-news-title:hover {{ text-decoration: underline; }}
.ota-news-meta {{ font-size: 0.8rem; font-weight: 600; color: {SECONDARY}; margin-top: 2px; }}

/* ---------------- the account switch: real money vs practice ----------------
   Which book she is looking at governs every number on the My trades tab, so
   it must never be something she has to look for. Streamlit turns a widget's
   key into an "st-key-<key>" class on its container, which is what lets these
   three switches be enlarged without touching any other radio in the app. */
.st-key-trades_account, .st-key-acct_scan, .st-key-acct_ql {{
    background: {TILE}; border: 2px solid {BORDER_STRONG}; border-radius: 10px;
    padding: 14px 18px 10px; margin: 4px 0 12px;
}}
/* the question above the choices */
.st-key-trades_account label[data-testid="stWidgetLabel"] p,
.st-key-acct_scan label[data-testid="stWidgetLabel"] p,
.st-key-acct_ql label[data-testid="stWidgetLabel"] p {{
    font-size: 1.05rem !important; font-weight: 800 !important;
    color: {INK} !important; letter-spacing: .01em;
}}
/* the two choices themselves - the words that must be unmissable */
.st-key-trades_account [role="radiogroup"] label p,
.st-key-acct_scan [role="radiogroup"] label p,
.st-key-acct_ql [role="radiogroup"] label p {{
    font-size: 1.45rem !important; font-weight: 800 !important;
    color: {INK} !important; line-height: 1.3;
}}
.st-key-trades_account [role="radiogroup"],
.st-key-acct_scan [role="radiogroup"],
.st-key-acct_ql [role="radiogroup"] {{ gap: 26px; }}
.st-key-trades_account [role="radiogroup"] label,
.st-key-acct_scan [role="radiogroup"] label,
.st-key-acct_ql [role="radiogroup"] label {{ align-items: center; }}
/* a bigger dot, so the selected side is obvious at a glance and not only from
   the text weight - colour is never the only signal */
.st-key-trades_account [role="radiogroup"] label > div:first-child,
.st-key-acct_scan [role="radiogroup"] label > div:first-child,
.st-key-acct_ql [role="radiogroup"] label > div:first-child {{
    transform: scale(1.4); margin-right: 6px;
}}

/* ---------------- sub-tabs: the second level of navigation ----------------
   A tab bar inside a tab bar has to be visibly the quieter of the two, or the
   page has two things claiming to be the main navigation. The outer rail is a
   solid emerald fill on white; this one is an underline on the canvas. */
.stTabs .stTabs [data-baseweb="tab-list"] {{
    background: transparent;
    border: none;
    border-bottom: 2px solid {BORDER};
    border-radius: 0;
    padding: 0;
    gap: 2px;
    margin-bottom: 1rem;
}}
.stTabs .stTabs [data-baseweb="tab"] {{
    min-height: 40px;
    border-radius: 0;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    padding: 8px 12px;
}}
.stTabs .stTabs [data-baseweb="tab"]:hover {{ background: {TILE}; }}
.stTabs .stTabs [aria-selected="true"] {{
    background: transparent !important;
    color: {ACCENT_DARK} !important;
    border-bottom-color: {ACCENT} !important;
}}
.stTabs .stTabs [data-baseweb="tab"] [data-testid="stMarkdownContainer"] p,
.stTabs .stTabs [data-baseweb="tab"] p {{ font-size: 0.98rem !important; }}

/* ---------------- the stat strip ----------------
   Denser than .ota-kpi and without its tone border: these are the supporting
   numbers under a hero, not six verdicts. Four across on a laptop, two on a
   phone, and the number is always the loudest thing in the card. */
.ota-stat {{
    display: grid; gap: 10px; margin: 4px 0 10px;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
}}
.ota-stat-card {{
    background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 11px 14px;
}}
.ota-stat-label {{ font-size: 0.72rem; font-weight: 800; color: {SECONDARY};
                   text-transform: uppercase; letter-spacing: 0.07em; }}
.ota-stat-value {{ font-size: 1.5rem; font-weight: 800; color: {INK};
                   line-height: 1.2; margin: 2px 0 0;
                   font-variant-numeric: tabular-nums; }}
.ota-stat-sub {{ font-size: 0.82rem; font-weight: 600; color: {SECONDARY};
                 line-height: 1.4; margin-top: 1px; }}
.ota-stat-up   {{ color: {GREEN}; }}
.ota-stat-down {{ color: {RED}; }}

/* ---------------- the profit hero ----------------
   One number, big, with what it is measured against directly under it. Every
   grain of the Profit page opens with this same object, so changing the zoom
   changes the number and never the shape of the page. */
.ota-hero-stat {{ background: {CARD}; border: 1px solid {BORDER};
                  border-radius: 10px; padding: 18px 20px; margin: 2px 0 12px; }}
.ota-hero-stat-label {{ font-size: 0.78rem; font-weight: 800; color: {SECONDARY};
                        text-transform: uppercase; letter-spacing: 0.08em; }}
.ota-hero-stat-value {{ font-size: 2.9rem; font-weight: 800; line-height: 1.08;
                        margin: 4px 0 2px; font-variant-numeric: tabular-nums;
                        letter-spacing: -0.02em; }}
.ota-hero-stat-sub {{ font-size: 0.98rem; font-weight: 600; color: {CAPTION};
                      line-height: 1.5; }}

/* ---------------- phones (Rita uses the app mobile-first) ---------------- */
@media (max-width: 640px) {{
    /* stacked, and still large - this is the last thing to shrink */
    .st-key-trades_account [role="radiogroup"],
    .st-key-acct_scan [role="radiogroup"],
    .st-key-acct_ql [role="radiogroup"] {{ gap: 10px; }}
    .st-key-trades_account [role="radiogroup"] label p,
    .st-key-acct_scan [role="radiogroup"] label p,
    .st-key-acct_ql [role="radiogroup"] label p {{ font-size: 1.25rem !important; }}
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
    /* The step number and the date stop being their own columns - on 375px
       that leaves about nine characters for the sentence that matters. */
    .ota-story-row {{ flex-wrap: wrap; gap: 4px 10px; padding: 10px 12px; }}
    .ota-story-n {{ flex: 0 0 auto; }}
    .ota-story-date {{ flex: 0 0 auto; }}
    .ota-story-what {{ flex: 1 1 100%; order: 3; }}
    .ota-story-amt {{ flex: 1 1 auto; order: 2; }}
    .ota-section-title {{ font-size: 1.35rem; }}
    /* Two KPI cards a row rather than auto-fit's one: six numbers should still
       be three swipes, not six. */
    .ota-kpi, .ota-kpi-3 {{ grid-template-columns: repeat(2, 1fr); gap: 8px; }}
    .ota-stat {{ grid-template-columns: repeat(2, 1fr); gap: 8px; }}
    .ota-stat-value {{ font-size: 1.25rem; }}
    .ota-hero-stat {{ padding: 14px 16px; }}
    .ota-hero-stat-value {{ font-size: 2.25rem; }}
    /* All FOUR sub-tabs must fit 375px without swiping. The main tab bar has
       six and earns its swipe; this one is the navigation for the page she is
       already on, and a destination she has to scroll sideways to discover is
       a destination she will not discover. Tighter padding and a smaller label
       buy the ~40px that "Journal" was overflowing by. */
    .stTabs .stTabs [data-baseweb="tab-list"] {{ gap: 0; }}
    .stTabs .stTabs [data-baseweb="tab"] {{
        padding: 7px 6px; min-height: 38px; flex: 1 1 0 !important;
    }}
    .stTabs .stTabs [data-baseweb="tab"] [data-testid="stMarkdownContainer"] p,
    .stTabs .stTabs [data-baseweb="tab"] p {{
        font-size: 0.86rem !important; white-space: nowrap;
    }}
    .ota-kpi-card {{ padding: 11px 13px; }}
    .ota-kpi-value {{ font-size: 1.45rem; }}
    .ota-kpi-label {{ font-size: 0.7rem; }}
    .ota-kpi-sub {{ font-size: 0.8rem; }}
    .ota-band {{ padding: 16px 16px; }}
    .ota-band-hero {{ font-size: 2.1rem; }}
    .ota-band-value {{ font-size: 1.55rem; }}
    .ota-band-zones {{ gap: 18px; }}
    .ota-band-zone {{ min-width: 130px; }}
    .ota-band-title {{ font-size: 1.2rem; }}
    .ota-track-rail {{ height: 18px; }}
    .ota-track-scale {{ font-size: 0.78rem; }}
}}

/* Only the very narrow phones drop to one card a row. At 375px - the common
   iPhone width - two still fit at about 169px each, and six cards one to a
   row is a lot of scrolling to read six numbers. */
@media (max-width: 340px) {{
    .ota-kpi, .ota-kpi-3 {{ grid-template-columns: 1fr; }}
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
        f"<div class='ota-prose' style='color:{CAPTION};font-size:0.98rem;"
        f"line-height:1.6;margin:2px 0 8px;'>{safe}</div>",
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


# ------------------------------------------------------------ money, once
# These three were redefined in four places - dashboard.py had _d/_m/_signed,
# income_report.py had its own _d, widgets.py had money(). Four definitions of
# "how do we print a dollar" is four chances for one page to disagree with
# itself about whether a loss is "-$640" or "$-640".
def money(x: float, decimals: int = 0) -> str:
    """Plain dollars, for anything that will be escaped downstream."""
    return f"${x:,.{decimals}f}"


def signed(x: float, decimals: int = 0) -> str:
    """The minus in front of the sign - "-$640", never "$-640"."""
    return (f"-{money(abs(x), decimals)}" if x < 0 else money(x, decimals))


def pct(x: float | None, decimals: int = 0) -> str:
    """A share as a percentage, or "-" when there is honestly no number."""
    return f"{x * 100:.{decimals}f}%" if x is not None else "-"


def stat(label: str, value: str, sub: str = "", tone: str = "neutral") -> str:
    """One supporting number in a stat strip.

    Smaller and quieter than kpi_card, and without its tone border: a kpi_card
    is a verdict, and these are the numbers underneath a hero that give it
    context. tone colours only the VALUE, and only ever to show the sign of a
    number - up | down, anything else neutral.
    """
    cls = {"up": " ota-stat-up", "down": " ota-stat-down"}.get(tone, "")
    sub_html = (f'<div class="ota-stat-sub">{_money_safe(sub)}</div>'
                if sub else "")
    return (f'<div class="ota-stat-card">'
            f'<div class="ota-stat-label">{_money_safe(label)}</div>'
            f'<div class="ota-stat-value{cls}">{_money_safe(value)}</div>'
            f'{sub_html}</div>')


def stat_row(cards: list[str]) -> None:
    """A strip of stat() cards - four across on a laptop, two on a phone."""
    st.markdown(f'<div class="ota-stat">{"".join(cards)}</div>',
                unsafe_allow_html=True)


def hero_stat(label: str, value: str, sub: str = "",
              tone: str = "neutral") -> None:
    """The one number a page exists to show, with what it is measured against.

    Every grain of the Profit page opens with this same object, so changing the
    zoom from Day to Month changes the number and never the shape of the page.
    """
    colour = {"up": GREEN, "down": RED}.get(tone, INK)
    sub_html = (f'<div class="ota-hero-stat-sub">{_money_safe(sub)}</div>'
                if sub else "")
    st.markdown(
        f'<div class="ota-hero-stat">'
        f'<div class="ota-hero-stat-label">{_money_safe(label)}</div>'
        f'<div class="ota-hero-stat-value" style="color:{colour};">'
        f'{_money_safe(value)}</div>{sub_html}</div>',
        unsafe_allow_html=True)


def explain(label: str, body: str, key: str | None = None) -> None:
    """The teaching, one tap away instead of always on screen.

    Every chart on this tab used to carry a paragraph under it explaining how
    to read it. All of them together are most of why the page felt long and
    hard to follow - but deleting them would take the teaching out of an app
    that is teaching her the job. So they move in here: the page is numbers,
    and the explanation is a tap when she wants it.

    body takes the same **bold** and \\$ escaping that note() does.
    """
    with st.popover(label, help=None):
        note(body)


# ---------------------------------------------------------------- dashboard
# The pieces below build the My trades dashboard. They return HTML strings or
# render directly, and all of them escape their input, because every one of
# them is handed a strategy name or a note that came out of her own log.
#
# One rule they all share: a $ sign arriving in HTML must already be the &#36;
# entity. A raw pair of them turns Streamlit's markdown into LaTeX and garbles
# the line. Inside note() the escape is \\$ instead.
_TONE_CLASS = {"good": "ota-kpi-good", "watch": "ota-kpi-watch",
               "bad": "ota-kpi-bad", "behind": "ota-kpi-watch"}


def _money_safe(text: str) -> str:
    """Escape for HTML, then turn any surviving dollar sign into its entity."""
    return _html.escape(str(text), quote=True).replace("$", "&#36;")


def kpi_card(label: str, value: str, sub: str = "", tone: str = "neutral",
             icon: str = "") -> str:
    """One card of the dashboard's headline row.

    tone sets the left border: good | watch | bad, anything else neutral. It is
    never the only signal - the sub-line always says in words what the colour
    is hinting, so the card still works for someone who cannot separate the
    two greens.
    """
    cls = _TONE_CLASS.get(tone, "")
    head = f"{_money_safe(icon)} " if icon else ""
    return (
        f'<div class="ota-kpi-card {cls}">'
        f'<div class="ota-kpi-label">{head}{_money_safe(label)}</div>'
        f'<div class="ota-kpi-value">{_money_safe(value)}</div>'
        f'<div class="ota-kpi-sub">{_money_safe(sub)}</div></div>')


def kpi_row(cards: list[str]) -> None:
    """The cards in a responsive grid, always equal width.

    A count divisible by three lays out three to a row so it can never leave a
    lone card stretched across the bottom; anything else auto-fits. See
    .ota-kpi for why this is a grid and not a flex row.
    """
    cls = "ota-kpi ota-kpi-3" if cards and len(cards) % 3 == 0 else "ota-kpi"
    st.markdown(f'<div class="{cls}">{"".join(cards)}</div>',
                unsafe_allow_html=True)


def track(value: float, start: float, goal: float, *,
          marker: float | None = None, marker_label: str = "",
          value_label: str = "", start_label: str = "",
          goal_label: str = "") -> None:
    """A progress track from `start` to `goal`, with an optional pace marker.

    Used for the year-one balance: the rail runs $100,000 to $142,000, the fill
    is where the account actually is, and the marker is where a steady plan
    would have it today. Both fill and marker clamp to the rail, so an account
    that beat the goal fills it rather than overflowing the page.
    """
    span = goal - start
    def pct(x: float) -> float:
        return 0.0 if span <= 0 else min(max((x - start) / span, 0.0), 1.0)

    flag = ""
    if marker is not None:
        left = pct(marker) * 100
        label = (f'<div class="ota-track-flag" style="left:{left:.2f}%;">'
                 f'{_money_safe(marker_label)}</div>' if marker_label else "")
        flag = label + f'<div class="ota-track-marker" style="left:{left:.2f}%;"></div>'

    st.markdown(
        f'<div class="ota-track">'
        f'{flag}'
        f'<div class="ota-track-rail">'
        f'<div class="ota-track-fill" style="width:{pct(value) * 100:.2f}%;"></div>'
        f'</div>'
        f'<div class="ota-track-scale">'
        f'<span>{_money_safe(start_label)}</span>'
        f'<span class="ota-track-value">{_money_safe(value_label)}</span>'
        f'<span>{_money_safe(goal_label)}</span>'
        f'</div></div>',
        unsafe_allow_html=True)


def legend_note(body: str) -> None:
    """A grey swatch and the sentence explaining it.

    Every chart that draws the other account faded behind the real one gets
    this underneath. An unexplained grey bar on an income chart is the fastest
    way to make her think practice money is being counted.
    """
    st.markdown(
        f'<div class="ota-legend"><span class="ota-legend-swatch"></span>'
        f'<span class="ota-legend-text">{_money_safe(body)}</span></div>',
        unsafe_allow_html=True)
