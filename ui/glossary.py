"""The plain-English glossary.

Rita is learning options as she trades them, so a word she does not know is not
a small annoyance - it is the difference between reading her own checklist and
guessing at it. The app explains terms well the first time they appear in prose,
but tables and metric labels drop DTE, delta, mid price and buying power cold.

This is the place to look any of them up, reachable from every tab. Rules for
anything added here:

  - Say what it MEANS to her, not what a textbook says. Every definition ends
    up somewhere she can act on.
  - Tie it to what she sees: thinkorswim shows a bought leg as +1 and a sold
    leg as -1, and her own numbers are $100,000 capital and 1 contract.
  - Real dollars beat percentages.
  - No jargon inside a definition unless that word is also in here.
"""

from __future__ import annotations

import html as _html
import re as _re

import streamlit as st

from ui import theme


def sop_numbers() -> dict[str, str]:
    """The SOP values the glossary quotes, read from config.

    The whole app is built so her rules live in config/ and the code follows.
    Spelling those numbers out in glossary prose broke that: change a delta in
    strategies.yaml and every checklist would follow while the glossary quietly
    kept teaching the old one. These placeholders keep it honest.
    """
    from src.engine.config_loader import get_strategy, load_settings

    def entry(key: str, field: str, default):
        try:
            return get_strategy(key)["entry"][field]
        except Exception:
            return default

    def leaving(key: str, field: str, default):
        try:
            return get_strategy(key)["exit"][field]
        except Exception:
            return default

    settings = load_settings()
    put_delta = float(entry("put_credit_spread", "short_leg_delta_max", 0.25))
    risk = settings.get("risk_limits", {})
    read = settings.get("market_read", {})
    widths = settings.get("spread_widths", {})
    profit = float(leaving("put_credit_spread", "profit_target_pct", 50))
    stop = float(leaving("put_credit_spread", "stop_loss_multiple", 2))
    example_credit = 400
    return {
        "put_delta": f"{put_delta:.2f}",
        # Delta doubles as a rough probability, so the odds sentence is derived
        # from the same number rather than written next to it as "1-in-4".
        "put_delta_pct": f"{put_delta * 100:.0f}",
        "call_delta": f"{float(entry('call_credit_spread', 'short_leg_delta_max', 0.10)):.2f}",
        "ic_delta": f"{float(entry('iron_condor', 'short_leg_delta_max', 0.15)):.2f}",
        "dte_target": f"{int(entry('put_credit_spread', 'dte_target', 45))}",
        "time_exit": f"{int(leaving('put_credit_spread', 'time_exit_dte', 21))}",
        "profit_pct": f"{profit:g}",
        "stop_mult": f"{stop:g}",
        "bp_limit": f"{float(risk.get('monthly_bp_limit', 50000)):,.0f}",
        "vix_low": f"{read.get('vix_zone_low', 13):g}",
        "vix_high": f"{read.get('vix_zone_high', 25):g}",
        "width_index": f"{float(widths.get('index', 25)):,.0f}",
        "width_stock": f"{float(widths.get('stock', 5)):,.0f}",
        # Worked examples, derived so they cannot contradict the rule above them.
        "eg_credit": f"{example_credit:,.0f}",
        "eg_keep": f"{example_credit * profit / 100:,.0f}",
        "eg_loss": f"{example_credit * stop:,.0f}",
        "eg_buyback": f"{example_credit * (1 + stop):,.0f}",
    }


# (term, definition). Order inside a section runs simple to advanced, because
# reading it top to bottom should teach, not just index. Anything in {braces}
# is a number that lives in config - see sop_numbers().
SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("The basics", [
        ("Option",
         "A contract about 100 shares of something, at a set price, until a set date. "
         "You can buy one or sell one. Selling is how you collect premium, which is "
         "what every strategy in your SOP does."),
        ("Call",
         "The right to BUY at the strike price. A call gains value when the price goes up."),
        ("Put",
         "The right to SELL at the strike price. A put gains value when the price goes down."),
        ("Strike",
         "The price the option is about. 'The 7200 put' means the put whose strike is 7200."),
        ("Expiration",
         "The date the option stops existing. After it, the contract is over."),
        ("Contract",
         "One option, covering 100 shares. Prices are quoted per share, so a premium of "
         "4.15 means $415 for one contract. You trade 1 contract by default."),
        ("Premium",
         "The price of the option itself. What you pay if you buy it, what you collect "
         "if you sell it. Your income is premium you collected and got to keep."),
        ("Leg",
         "One option inside a trade made of several. A put credit spread has two legs: "
         "the put you sold and the put you bought."),
        ("Underlying",
         "The thing the option is on - SPX, SPY, AAPL. The app calls it the underlying "
         "because it can be an index, an ETF or a stock."),
    ]),
    ("Buying, selling, and what thinkorswim shows you", [
        ("Long (bought)",
         "An option you BOUGHT. It shows as +1 in thinkorswim and it cost you money."),
        ("Short (sold)",
         "An option you SOLD. It shows as -1 in thinkorswim and it paid you money. The "
         "short leg is where your income comes from, and it is also the leg that can "
         "go wrong, which is why every rule in your SOP is about it."),
        ("Credit",
         "Money that arrives in your account when you OPEN the trade. Credit spreads, "
         "iron condors and cash secured puts are credit trades."),
        ("Debit",
         "Money that leaves your account when you open the trade. The long LEAPS call "
         "in a PMCC is a debit - real cash out, and your actual risk on that trade."),
        ("Fill",
         "The price your order actually traded at, which is not always the price you "
         "asked for. The fill is the number to type into Quick Log."),
        ("Bid and ask",
         "The most a buyer will pay (bid) and the least a seller will take (ask). The "
         "gap between them is a real cost every time you enter or exit."),
        ("Mid price",
         "Halfway between the bid and the ask. The app prices every setup at the mid, "
         "so expect your real fill to be a few cents worse and adjust the order."),
        ("Assignment",
         "When whoever is on the other side of an option you SOLD exercises it, and you "
         "have to deliver. Possible on stocks and ETFs, never on cash-settled indexes."),
        ("Cash-settled",
         "Settles in cash: no shares change hands, so assignment cannot happen. SPX, "
         "NDX, RUT and XSP are cash-settled, which is exactly why your SOP puts credit "
         "spreads and iron condors on them."),
    ]),
    ("The Greeks you actually see in this app", [
        ("Delta",
         "Two useful readings from one number. It is how much the option moves when the "
         "underlying moves $1, AND it is roughly the chance the option finishes in the "
         "money. A {put_delta} delta short put is about a {put_delta_pct}% chance the "
         "market reaches your strike. Lower delta is safer and pays less."),
        ("Theta",
         "How much value the option loses each day purely from time passing. When you "
         "sell premium, theta is the thing quietly working for you."),
        ("Gamma",
         "How fast delta changes. It turns violent close to expiration, so a trade that "
         "looked safe can go bad in a day. This is the whole reason your SOP closes at "
         "{time_exit} days instead of holding to the end."),
        ("Vega",
         "How much the option's price moves when volatility changes. Selling premium "
         "while volatility is high and watching it fall is a win that comes from vega."),
        ("Implied volatility (IV)",
         "How big a move the option market is pricing in. High IV means fat premiums and "
         "bigger expected swings - more income, more risk."),
        ("Rich / fair / thin premium",
         "The app's read on whether options are paying more than that name's own usual "
         "movement justifies. Rich is the good one for a seller."),
    ]),
    ("Risk and money", [
        ("Max loss",
         "The worst case on the trade. On a credit spread it is capped and known before "
         "you enter: the distance between your two strikes, minus the credit you took in."),
        ("Buying power",
         "Cash your broker freezes while the trade is open. Your SOP caps this at "
         "${bp_limit} across a whole month, and the app's checklist enforces it."),
        ("Breakeven",
         "The price at which the trade makes exactly zero at expiration. Past it you are "
         "losing, short of it you are winning."),
        ("Return on risk",
         "The credit divided by the max loss - what you earn per dollar you put at risk. "
         "Higher usually means a higher delta too, so read the two together."),
        ("Spread width",
         "The distance between the strike you sold and the strike you bought. Wider pays "
         "more credit and risks more. Your SOP defaults to ${width_index} on indexes and "
         "ETFs and ${width_stock} on individual stocks."),
        ("Position delta",
         "The whole position's delta added up, written as share-equivalents. It answers "
         "'if this were shares, how many would I own?' Your red flag is 90."),
        ("Liquidity",
         "How easily you can get in and out at a fair price. Thin options mean a wide "
         "bid/ask, and you pay that gap twice."),
        ("Open interest",
         "How many contracts of that exact option are currently held by someone. More "
         "means easier to trade."),
    ]),
    ("Your exit rules", [
        ("DTE (days to expiration)",
         "Days from today until the option expires. Your SOP enters at about {dte_target} "
         "and never holds past {time_exit}."),
        ("Profit target ({profit_pct}%)",
         "Close once you can buy the trade back for {profit_pct}% less than you sold it "
         "for, keeping the rest. On a ${eg_credit} credit that means buying it back for "
         "about ${eg_keep}."),
        ("Stop loss ({stop_mult}x credit)",
         "Close if the loss reaches {stop_mult} times the credit you collected. On a "
         "${eg_credit} credit that is an ${eg_loss} loss, which happens when buying it "
         "back costs about ${eg_buyback}. No rolling at that point - just close."),
        ("Time exit ({time_exit} DTE)",
         "Close no matter what once {time_exit} days to expiration are left, winning or "
         "losing. "
         "It exists to get you out before gamma turns dangerous."),
        ("Rolling",
         "Closing the trade you have and opening a similar one further out in time. Your "
         "SOP only allows it if the roll pays you a net credit. If it will not fill for "
         "a credit, close instead of forcing it."),
        ("In the money / out of the money",
         "In the money (ITM) means the option would be worth something if exercised right "
         "now. Out of the money (OTM) means it would not. You sell OTM options and hope "
         "they stay that way."),
    ]),
    ("The strategies in your playbook", [
        ("Credit spread",
         "Sell one option, buy a cheaper one further away as protection, keep the "
         "difference if price stays away from you. Your loss is capped by the gap "
         "between the strikes, which is why it is the beginner-safe way to sell."),
        ("Put credit spread",
         "A credit spread below the price. You win as long as the market does not fall "
         "hard. Your SOP sells the {put_delta} delta put."),
        ("Call credit spread",
         "A credit spread above the price. You win as long as the market does not rally "
         "hard. Your SOP is stricter here, {call_delta} delta, because markets drift up."),
        ("Iron condor",
         "A put credit spread and a call credit spread at once, so you collect from both "
         "sides. You win if price stays in the middle. Your SOP uses {ic_delta} delta "
         "per leg."),
        ("Cash secured put",
         "Sell a put and hold enough cash to buy the 100 shares if you get assigned. You "
         "get paid to wait for a price you would have been happy to buy at anyway."),
        ("Covered call",
         "You own 100 real shares and sell a call against them for income. If it gets "
         "called away, you sell your shares at the strike."),
        ("PMCC (poor man's covered call)",
         "Buy a deep in-the-money LEAPS call instead of buying the 100 shares, then sell "
         "short calls against it. Far less cash up front than owning the stock."),
        ("LEAPS",
         "An option expiring a year or more out. Used as a stand-in for owning shares."),
        ("LEAPS call (long call)",
         "You BUY a call a year or more out and sell it later for a gain. It costs about "
         "a third of what 100 shares would and can never lose more than you paid. It is "
         "the one strategy here that needs the stock to actually RISE - the others make "
         "money while it sits still."),
        ("Debit vs credit",
         "A credit means money comes IN when you open the trade (almost everything you "
         "do). A debit means money goes OUT - you are buying, not selling, and time is "
         "working against you instead of for you."),
        ("Intrinsic and extrinsic value",
         "Intrinsic is the part of an option's price that is already real: how far in "
         "the money it is. Extrinsic is the rest - what you pay for time and for the "
         "chance it moves further. Only extrinsic value decays away, which is why a "
         "deep in-the-money LEAPS call loses value so slowly."),
    ]),
    ("Market words the app uses", [
        ("VIX",
         "The market's fear gauge: how big a swing the S&P 500 is expected to make over "
         "the next 30 days. Your comfort zone is {vix_low} to {vix_high}. Below that "
         "premiums are thin, "
         "above it the swings get big."),
        ("Trend",
         "The app's read on direction over recent weeks - up, down or sideways. It drives "
         "which strategy the Market tab ranks first today."),
        ("FOMC",
         "The Federal Reserve's interest-rate decision. The biggest scheduled mover on "
         "the calendar."),
        ("CPI and PCE",
         "Two inflation reports. CPI is the headline one; PCE is the one the Fed watches "
         "most closely. A surprise in either can swing the whole market."),
        ("Opex",
         "Monthly options expiration, the third Friday. Prices often get pinned near big "
         "strikes and moves can be jumpy."),
        ("Index / ETF / stock",
         "An index (SPX) is a number, cash-settled, no shares. An ETF (SPY) is a real "
         "tradable basket of shares. A stock (AAPL) is one company. Which one you are on "
         "decides whether assignment is possible and which strategies your SOP allows."),
    ]),
]


def filled_sections() -> list[tuple[str, list[tuple[str, str]]]]:
    """SECTIONS with every {placeholder} replaced by the live config value.

    Everything that reads the glossary goes through here, so there is no path
    that can render a stale number.
    """
    vals = sop_numbers()

    def fill(text: str) -> str:
        try:
            return text.format(**vals)
        except (KeyError, IndexError, ValueError):
            # A typo in a placeholder must not blank the glossary - better a
            # visible brace than a missing definition.
            return text

    return [(title, [(fill(term), fill(definition)) for term, definition in rows])
            for title, rows in SECTIONS]


def _term_matches(term: str, definition: str, needle: str) -> bool:
    return needle in term.lower() or needle in definition.lower()


def _render_rows(rows: list[tuple[str, str]]) -> None:
    """One HTML block per section - far fewer elements than a call per term, and
    it keeps the term and its definition on the same line at high contrast.

    Dollar signs go out as &#36; because a raw $...$ pair makes Streamlit try to
    render LaTeX and garbles the text.
    """
    parts = []
    for term, definition in rows:
        safe_term = _html.escape(term)
        safe_def = _html.escape(definition).replace("$", "&#36;")
        parts.append(
            f"<div style='margin:0 0 10px;line-height:1.6;'>"
            f"<b style='color:{theme.INK};'>{safe_term}</b>"
            f"<span style='color:{theme.CAPTION};'> - {safe_def}</span></div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def render() -> None:
    """The glossary expander. Collapsed by default, on screen from every tab."""
    with st.expander("📖 What does this word mean? - plain-English glossary"):
        theme.note("Every options word this app puts on screen, explained in plain "
                   "English and tied to what you see in thinkorswim. Type below to "
                   "jump straight to one.")
        needle = st.text_input(
            "Find a word", key="glossary_search",
            placeholder="delta, credit, buying power, gamma...").strip().lower()

        shown = 0
        for title, rows in filled_sections():
            hits = ([r for r in rows if _term_matches(r[0], r[1], needle)]
                    if needle else rows)
            if not hits:
                continue
            shown += len(hits)
            st.markdown(f"**{title}**")
            _render_rows(hits)

        if needle and not shown:
            theme.note(f"Nothing here matches **{needle}**. Clear the box to see every "
                       "term, and tell Claude the word you were looking for so it can "
                       "be added.")


def all_terms() -> list[str]:
    """Every defined term, for tests and for anything that wants to cross-check
    that a word used on screen is actually explained somewhere."""
    return [term for _title, rows in filled_sections() for term, _definition in rows]
