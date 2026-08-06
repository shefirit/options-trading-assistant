"""The small building blocks the trades forms are made of.

Money boxes, numbered step headers, one-line money rows. They are here rather
than in ui/components.py because they exist to serve the log-a-trade and
close-a-trade forms and nothing else uses them.

The dollar-sign rule that runs through this whole package: a raw pair of $
signs turns Streamlit's markdown into LaTeX and garbles the line. Inside HTML
use &#36; (or income_report._d); inside theme.note() use \\$.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from ui import theme
from ui.components import _dollars as money  # noqa: F401  (re-exported)


def money(x: float) -> str:
    return f"${x:,.0f}"


def _money_line(label: str, sub: str, amount: str, tone: str,
                strong: bool = False) -> str:
    """One row of the money panel: what it was, and what it came to."""
    import html as _h

    weight = 800 if strong else 700
    size = "1.15rem" if strong else "1.05rem"
    top = "2px solid " + theme.BORDER_STRONG if strong else "1px solid " + theme.BORDER
    return (
        f"<div style='display:flex;justify-content:space-between;align-items:baseline;"
        f"gap:14px;padding:9px 0;border-top:{top};'>"
        f"<div><div style='font-weight:{weight};color:{theme.INK};font-size:1rem;'>"
        f"{_h.escape(label)}</div>"
        + (f"<div style='color:{theme.CAPTION};font-size:0.93rem;line-height:1.5;"
           f"margin-top:2px;'>{_h.escape(sub)}</div>" if sub else "")
        + f"</div><div style='font-weight:800;color:{tone};font-size:{size};"
        f"white-space:nowrap;'>{_h.escape(amount)}</div></div>")


def _signed(amount: float) -> str:
    """+$150 / -$210 - the sign in front where she can see it."""
    return f"{'+' if amount >= 0 else '-'}${abs(amount):,.0f}"


def _step(number: int, title: str, sub: str = "") -> None:
    """A numbered step heading, so a form reads as a sequence not a wall.

    The roll form's first version was a flat stack of labels, and she said it
    was still hard to know what to fill: with nothing grouping them, all six
    boxes look equally urgent and equally unexplained.
    """
    import html as _h

    st.markdown(
        f"<div style='display:flex;gap:10px;align-items:baseline;"
        f"margin:14px 0 2px;border-top:1px solid {theme.BORDER};padding-top:12px;'>"
        f"<div style='background:{theme.ACCENT};color:#fff;font-weight:800;"
        f"border-radius:999px;min-width:24px;height:24px;display:flex;"
        f"align-items:center;justify-content:center;font-size:0.9rem;'>{number}</div>"
        f"<div><div style='font-weight:800;color:{theme.INK};font-size:1.05rem;'>"
        f"{_h.escape(title)}</div>"
        + (f"<div style='color:{theme.CAPTION};font-size:0.95rem;line-height:1.55;'>"
           f"{_h.escape(sub)}</div>" if sub else "")
        + "</div></div>", unsafe_allow_html=True)


def _fill_price_input(label: str, key: str, contracts: int, *,
                      default_total: Optional[float] = None,
                      allow_negative: bool = False,
                      live_echo: bool = True,
                      total_hint_above: Optional[float] = 100.0,
                      help: str = "") -> float:
    """A money box that takes the price the way thinkorswim prints it.

    Every other money box in the app asks for a dollar TOTAL, which means she
    has to work out 1.50 x 100 x contracts in her head before she can type
    anything - on the one form she had already told me was confusing. This
    takes the fill price straight off the statement and shows the total it
    comes to, so the arithmetic is the app's job and the number she types is
    the number she is looking at. Returns TOTAL dollars, so everything
    downstream (and the log) is unchanged.

    live_echo=False inside an st.form: a form holds its widget values until
    submit, so a running total there would show the PREVIOUS value while she
    types - worse than no total at all. Those forms confirm the money on their
    preview card instead, after the submit that makes the numbers real.

    total_hint_above guards the habit this breaks (every other box in the app
    used to want a dollar total). Pass None where a genuine price can be large:
    a LEAPS at 120.00 a share is ordinary, and warning about it would cry wolf
    on the one trade she owns most of.
    """
    per = max(contracts, 1) * 100.0
    default = round(float(default_total or 0.0) / per, 2)
    extra = {} if allow_negative else {"min_value": 0.0}
    price = float(st.number_input(label, step=0.05, format="%.2f", value=default,
                                  key=key, help=help, **extra))
    total = round(price * per, 2)

    if not live_echo:
        theme.note("Type it the way thinkorswim prints it - a price per share, "
                   "like 1.50, not the dollar total. The app multiplies by 100 "
                   "and by your contracts, and shows what it came to before you "
                   "save.")
        return total

    # Only once there is a price to convert - an empty form full of "= $0" rows
    # is the noise she was already complaining about.
    if price:
        word = "contract" if max(contracts, 1) == 1 else "contracts"
        st.markdown(
            f"<div style='margin:-6px 0 10px;color:{theme.CAPTION};font-size:1rem;'>"
            f"= <b style='color:{theme.INK};font-size:1.15rem;'>"
            f"&#36;{abs(total):,.0f}</b> on {max(contracts, 1)} {word} "
            f"&nbsp;<span style='color:{theme.MUTED};'>({price:.2f} &times; 100"
            + (f" &times; {contracts}" if max(contracts, 1) > 1 else "")
            + ")</span></div>", unsafe_allow_html=True)
    if total_hint_above is not None and abs(price) > total_hint_above:
        st.warning(f"{price:,.2f} looks like a dollar total rather than the fill "
                   f"price. thinkorswim prints a price per share - 1.50, not 150. "
                   f"Typed as it is, this records &#36;{abs(total):,.0f}.")
    return total


def _h_esc(text: str) -> str:
    import html as _h

    return _h.escape(str(text))


def _first_sentence(text: str, limit: int = 150) -> str:
    """The gist of an exit reason, for the Today list.

    The full reasoning runs to a paragraph and belongs in the detail card. Shown
    twice on one screen it just pushed the buttons off the bottom.
    """
    text = (text or "").strip()
    cut = text.find(". ")
    first = text[:cut + 1] if 0 < cut <= limit else text
    return first if len(first) <= limit else first[:limit].rsplit(" ", 1)[0] + "..."
