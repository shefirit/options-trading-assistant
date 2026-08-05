"""The design-system string builders.

kpi_card, track and legend_note are handed strategy names, notes and lessons
that came out of her own log, so they escape everything. And they all obey the
rule that bit this app before: a raw pair of dollar signs turns Streamlit's
markdown into LaTeX and garbles the line, so a $ reaching HTML has to already
be the &#36; entity.

Pure string functions - no Streamlit session needed to check any of this.
"""

from __future__ import annotations

from unittest import mock

import pytest

from ui import theme


def _rendered(fn, *args, **kwargs) -> str:
    """Call a theme renderer and hand back the HTML it wrote."""
    out = []
    with mock.patch.object(theme.st, "markdown", lambda html, **kw: out.append(html)):
        fn(*args, **kwargs)
    return out[-1]


# ------------------------------------------------------------------ escaping
def test_a_label_with_markup_in_it_cannot_break_out_of_the_card():
    html = theme.kpi_card("<script>x</script>", "1", "sub")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_dollar_signs_reach_the_page_as_entities_not_as_latex():
    """The LaTeX trap. "$1,240 of $3,500" in raw markdown renders as italic
    nonsense between the two signs."""
    html = theme.kpi_card("THIS MONTH", "$1,240", "of $3,500 goal")
    assert "$" not in html
    assert html.count("&#36;") == 2


def test_the_legend_escapes_its_body_too():
    html = _rendered(theme.legend_note, "practice & <b>real</b> costs $100")
    assert "<b>real</b>" not in html
    assert "&amp;" in html and "&#36;" in html


# --------------------------------------------------------------------- tone
def test_each_tone_gets_its_own_stripe_class():
    assert "ota-kpi-good" in theme.kpi_card("a", "b", tone="good")
    assert "ota-kpi-watch" in theme.kpi_card("a", "b", tone="watch")
    assert "ota-kpi-bad" in theme.kpi_card("a", "b", tone="bad")


def test_an_unknown_tone_falls_back_to_neutral_rather_than_raising():
    html = theme.kpi_card("a", "b", tone="chartreuse")
    assert "ota-kpi-card" in html
    assert "ota-kpi-good" not in html and "ota-kpi-bad" not in html


def test_behind_reads_as_watch_because_behind_pace_is_not_a_failure():
    """Her SOP has no rule for catching up. Amber says "look", red would say
    "you did something wrong", and being behind on day five is neither."""
    assert "ota-kpi-watch" in theme.kpi_card("a", "b", tone="behind")


# -------------------------------------------------------------------- track
def test_the_fill_clamps_at_both_ends():
    assert "width:0.00%" in _rendered(theme.track, 90_000, 100_000, 142_000)
    assert "width:100.00%" in _rendered(theme.track, 200_000, 100_000, 142_000)


def test_the_fill_is_the_share_of_the_span_not_of_the_goal():
    """$121,000 is halfway from $100,000 to $142,000, not 85% of anything."""
    assert "width:50.00%" in _rendered(theme.track, 121_000, 100_000, 142_000)


def test_the_pace_marker_lands_where_a_steady_plan_would_be():
    html = _rendered(theme.track, 101_200, 100_000, 142_000,
                     marker=100_677, marker_label="on pace")
    assert "ota-track-marker" in html
    assert "left:1.61%" in html             # 677 of the 42,000 span


def test_a_track_with_no_span_does_not_divide_by_zero():
    assert "width:0.00%" in _rendered(theme.track, 50, 100, 100)


# ------------------------------------------------- the existing note contract
def test_note_still_turns_the_escaped_dollar_into_a_real_one():
    """Guarding the behaviour every caller in the app relies on, while this
    file is being edited."""
    html = _rendered(theme.note, "You banked \\$1,240 of \\$3,500.")
    assert "$1,240" in html and "$3,500" in html


def test_note_still_bolds_and_still_escapes():
    html = _rendered(theme.note, "**Behind pace** on <b>day 5</b>")
    assert "<b>Behind pace</b>" in html
    assert "&lt;b&gt;day 5&lt;/b&gt;" in html


# ------------------------------------------------------------------- palette
def test_the_band_colours_live_in_the_palette_so_the_two_bands_cannot_drift():
    from ui import income_report as ir
    assert ir.BAND == theme.BAND
    assert ir.BAND_SUB == theme.BAND_SUB


def test_the_kpi_grid_is_a_grid_and_not_a_flex_row():
    """Six flex cards wrapping four-and-two stretch the last two to half the
    width each. A dashboard's top row has to be a row of equals."""
    assert "grid-template-columns: repeat(auto-fit, minmax(200px, 1fr))" in theme._CSS
    # Two a row on a phone, one only on the very narrow ones - six cards stacked
    # is a lot of scrolling to read six numbers.
    assert "@media (max-width: 640px)" in theme._CSS
    assert "@media (max-width: 340px)" in theme._CSS
