"""Stock Price Calculator - what should I pay to earn the return I want?

The idea is simple arithmetic run backwards. Pick a company, estimate what it
will earn in a few years, decide what the market will pay for those earnings,
and that gives you a future share price. Then discount that future price back
to today at the return you insist on. Whatever comes out is the most you can
pay and still get your number.

    future earnings  =  today's EPS grown for N years
    future price     =  future earnings x the P/E you expect then
    buy below        =  future price discounted back at your required return

Two things make this genuinely useful rather than a toy:

  * The answer is only as good as the two guesses inside it (growth and exit
    P/E), so we always show a grid of both instead of one falsely precise
    number.
  * We run it in reverse too. Given today's actual price, what growth rate is
    the market already assuming? That "implied expectations" number tells you
    whether you are being asked to believe something reasonable or something
    heroic - and it is the same reverse-DCF logic professionals use.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ValuationInputs(BaseModel):
    symbol: str = ""
    eps: float = 0.0                    # trailing twelve-month earnings per share
    growth_pct: float = 8.0             # expected annual earnings growth
    years: int = 5
    exit_pe: float = 18.0               # what the market pays for it at the end
    required_return_pct: float = 12.0   # the return you insist on
    dividend_yield_pct: float = 0.0
    current_price: Optional[float] = None


class ValuationResult(BaseModel):
    inputs: ValuationInputs
    future_eps: float = 0.0
    future_price: float = 0.0
    dividends_collected: float = 0.0
    buy_below: float = 0.0              # the price that delivers your return
    current_price: Optional[float] = None

    discount_pct: Optional[float] = None    # how far below (or above) fair the price is
    margin_of_safety_pct: Optional[float] = None
    verdict: str = "n/a"                    # "buy" | "fair" | "expensive"

    implied_return_pct: Optional[float] = None   # what today's price actually earns you
    implied_growth_pct: Optional[float] = None   # what today's price already assumes

    summary: str = ""
    reads: list[str] = Field(default_factory=list)


def _compound(base: float, rate_pct: float, years: int) -> float:
    return base * (1 + rate_pct / 100.0) ** years


def project(inputs: ValuationInputs) -> ValuationResult:
    """Run the maths forward, then back to today."""
    r = ValuationResult(inputs=inputs, current_price=inputs.current_price)
    years = max(1, inputs.years)

    r.future_eps = _compound(inputs.eps, inputs.growth_pct, years)
    r.future_price = r.future_eps * inputs.exit_pe

    # Dividends, roughly: today's yield on today's price, collected each year.
    # Deliberately not compounded - this is a sanity figure, not a bond model.
    if inputs.dividend_yield_pct and inputs.current_price:
        r.dividends_collected = (inputs.dividend_yield_pct / 100.0
                                 * inputs.current_price * years)

    total_future_value = r.future_price + r.dividends_collected
    r.buy_below = total_future_value / (1 + inputs.required_return_pct / 100.0) ** years

    price = inputs.current_price
    if price and price > 0 and r.buy_below > 0:
        r.discount_pct = (r.buy_below / price - 1) * 100
        r.margin_of_safety_pct = (1 - price / r.buy_below) * 100
        r.implied_return_pct = ((total_future_value / price) ** (1 / years) - 1) * 100
        r.implied_growth_pct = _implied_growth(price, inputs, years)
        if r.discount_pct >= 15:
            r.verdict = "buy"
        elif r.discount_pct >= -5:
            r.verdict = "fair"
        else:
            r.verdict = "expensive"

    r.reads = _reads(r)
    r.summary = _summary(r)
    return r


def _implied_growth(price: float, inputs: ValuationInputs, years: int) -> Optional[float]:
    """Reverse the sum: what earnings growth does today's price already assume,
    if you demand your required return and the exit P/E holds?"""
    if inputs.eps <= 0 or inputs.exit_pe <= 0 or price <= 0:
        return None
    needed_future_price = price * (1 + inputs.required_return_pct / 100.0) ** years
    needed_future_price = max(needed_future_price - inputs.dividend_yield_pct / 100.0
                              * price * years, 0.01)
    needed_future_eps = needed_future_price / inputs.exit_pe
    if needed_future_eps <= 0:
        return None
    return ((needed_future_eps / inputs.eps) ** (1 / years) - 1) * 100


def sensitivity(inputs: ValuationInputs,
                growth_range: Optional[list[float]] = None,
                pe_range: Optional[list[float]] = None) -> dict:
    """A grid of buy-below prices across growth and exit P/E.

    One number invites false confidence. The grid shows immediately whether
    your answer survives being a bit wrong about the two guesses - which,
    being guesses about the future, it will be.
    """
    g = inputs.growth_pct
    p = inputs.exit_pe
    growth_range = growth_range or [max(0.0, g + d) for d in (-6, -3, 0, 3, 6)]
    pe_range = pe_range or [max(1.0, p + d) for d in (-6, -3, 0, 3, 6)]

    rows = []
    for growth in growth_range:
        cells = []
        for exit_pe in pe_range:
            trial = inputs.model_copy(update={"growth_pct": growth, "exit_pe": exit_pe})
            value = project(trial).buy_below
            cell = {"exit_pe": exit_pe, "buy_below": round(value, 2)}
            if inputs.current_price:
                cell["vs_price_pct"] = round((value / inputs.current_price - 1) * 100, 1)
            cells.append(cell)
        rows.append({"growth_pct": growth, "cells": cells})
    return {"growth_range": growth_range, "pe_range": pe_range, "rows": rows}


def _reads(r: ValuationResult) -> list[str]:
    i = r.inputs
    out = [
        f"Starting from ${i.eps:.2f} of earnings per share and growing {i.growth_pct:.0f}% "
        f"a year, it earns ${r.future_eps:.2f} per share in {i.years} years.",
        f"At a {i.exit_pe:.0f}x multiple that is a ${r.future_price:,.2f} share price"
        + (f", plus about ${r.dividends_collected:,.2f} of dividends collected along "
           "the way." if r.dividends_collected else "."),
        f"To earn {i.required_return_pct:.0f}% a year from that, you cannot pay more than "
        f"${r.buy_below:,.2f} today.",
    ]
    if r.current_price and r.discount_pct is not None:
        if r.discount_pct >= 0:
            out.append(f"It trades at ${r.current_price:,.2f} - about "
                       f"{abs(r.discount_pct):.0f}% BELOW that, so the maths works with "
                       f"{r.margin_of_safety_pct:.0f}% of room to spare.")
        else:
            out.append(f"It trades at ${r.current_price:,.2f} - about "
                       f"{abs(r.discount_pct):.0f}% ABOVE the most you should pay. "
                       "At this price the sums do not give you the return you asked for.")
    if r.implied_return_pct is not None:
        out.append(f"Put another way: buying at today's price and hitting these "
                   f"assumptions earns you about {r.implied_return_pct:.1f}% a year.")
    if r.implied_growth_pct is not None:
        gap = r.implied_growth_pct - i.growth_pct
        judgement = ("less than you expect, so the price is not demanding much"
                     if gap < -1 else
                     "roughly what you expect - the price is fair on your own numbers"
                     if abs(gap) <= 1 else
                     "more than you expect, so you would be paying for growth you do "
                     "not believe in")
        out.append(f"Today's price already assumes about {r.implied_growth_pct:.1f}% annual "
                   f"earnings growth to deliver your {i.required_return_pct:.0f}%. "
                   f"That is {judgement}.")
    return out


def _summary(r: ValuationResult) -> str:
    i = r.inputs
    if i.eps <= 0:
        return ("This company has no positive earnings to grow, so an earnings-based "
                "calculation cannot say anything useful. Judge it on sales, cash flow "
                "or simply leave it alone.")
    if r.current_price is None:
        return (f"On your assumptions, pay no more than ${r.buy_below:,.2f} to earn "
                f"{i.required_return_pct:.0f}% a year over {i.years} years.")

    head = {
        "buy": (f"On your assumptions {i.symbol or 'this stock'} is worth up to "
                f"${r.buy_below:,.2f} and trades at ${r.current_price:,.2f} - there is "
                "room here."),
        "fair": (f"On your assumptions {i.symbol or 'this stock'} is worth about "
                 f"${r.buy_below:,.2f} and trades at ${r.current_price:,.2f} - roughly "
                 "fair, with no margin for being wrong."),
        "expensive": (f"On your assumptions {i.symbol or 'this stock'} is worth up to "
                      f"${r.buy_below:,.2f} but trades at ${r.current_price:,.2f} - you "
                      "would be overpaying for the return you said you wanted."),
    }.get(r.verdict, "")
    return (head + " Remember the whole answer rests on two guesses - the growth rate and "
            "the exit multiple. Move either one and the number moves a long way, which is "
            "exactly what the grid below is for.")
