"""What her broker holds, against what her log says it holds.

The app can only see what reaches her Google Sheet. Everything it reports -
buying power, the monthly budget, the 50% target, her delta exposure - is
computed from that log, so a row that is missing or wrong is not a cosmetic
problem. It is a wrong number she trades on.

That is not hypothetical. Reconciling her paperMoney book by hand on
2026-09-18 found three disagreements in nine positions:

  * a second short SMH 615 call that existed at the broker and nowhere in the
    log. The app read the position as a covered PMCC holding no buying power,
    while thinkorswim held $6,563.50 against an uncovered call.
  * a RUT call wing logged at one contract when she had sold two, so its
    credit - and the 50% target measured against it - were half what she had
    actually collected.
  * an AAPL spread the app showed open that had never been opened at all.

Her own account of the SMH leg: "i sold it manually and forgot to log it. or
maybe it was mistake". Either way there was no way for her to find out, which
is the thing this fixes. On the paper book it cost $460 of pretend money; the
same gap on the real account is a naked call nobody knows about.

WHAT THIS PARSES, AND WHAT IT REFUSES TO GUESS
----------------------------------------------
Text copied out of the thinkorswim Positions pane. The leg lines carry an
unambiguous signature - a signed quantity, a date, a strike, a P or a C - and
those are what the comparison actually rests on. The per-position figures
(buying power especially) sit in fixed columns whose spelling varies with her
column layout, so they are read on a best-effort basis and simply left out when
the shape is not recognisable.

Nothing here is silent. A line that cannot be read comes back in `unparsed` so
the screen can show it, because a reconciliation that quietly drops half the
account is worse than no reconciliation at all.

Pure functions: no Streamlit, no network, fully unit-tested.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from src.engine.models import Action, OptionType

# "+3 Sep 30 (12d) 725 P"  /  "-2 Oct 16 (28d) 615 C"  /  "+1 Jun 17 (272d) 460 C"
_LEG = re.compile(
    r"^\s*(?P<sign>[+-])\s*(?P<qty>\d+)\s+"
    r"(?P<mon>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s*"
    r"(?:\(\s*(?P<dte>\d+)\s*d\s*\))?\s*"
    r"(?P<strike>\d+(?:\.\d+)?)\s*"
    r"(?P<kind>[PC])\b",
    re.IGNORECASE)

# A position header: a bare ticker, then a price. "XSP — 761.81 -1.97 -0.26%"
_HEAD = re.compile(r"^\s*(?P<sym>[A-Z][A-Z0-9.\-]{0,6})\s*[—\-–]\s*(?P<px>[\d,]+\.?\d*)")

# "(1,792.00$)" / "(22,500.00$)" / "0.00$" - thinkorswim brackets a debit.
_MONEY = re.compile(r"\(?\s*(?P<neg>-)?([\d,]+\.\d{2})\s*\$\s*\)?")


@dataclass
class BrokerLeg:
    """One option line as the broker prints it."""
    action: Action
    option_type: OptionType
    strike: float
    quantity: int
    dte: Optional[int] = None

    @property
    def key(self) -> tuple:
        return (self.action, self.option_type, round(self.strike, 4))


@dataclass
class BrokerPosition:
    """One underlying's worth of legs, as the broker shows them."""
    symbol: str
    legs: list[BrokerLeg] = field(default_factory=list)
    bp_effect: Optional[float] = None
    dte: Optional[int] = None


@dataclass
class Finding:
    """One disagreement, in the words the screen will use."""
    kind: str            # missing_in_log | missing_at_broker | size | legs | bp
    symbol: str
    headline: str
    detail: str
    trade_id: str = ""
    severity: str = "warn"        # warn | info


def _money(text: str) -> Optional[float]:
    """The LAST dollar figure on a line, unsigned.

    Buying power sits second from the right in her layout, but the columns she
    shows are hers to change, so this reads the rightmost money-shaped token
    rather than counting columns. Bracketing means a debit to thinkorswim; the
    sign is dropped because every caller here wants the magnitude the broker
    holds.
    """
    hits = _MONEY.findall(text)
    if not hits:
        return None
    return abs(float(hits[-1][1].replace(",", "")))


def parse_tos(text: str) -> tuple[list[BrokerPosition], list[str]]:
    """Positions out of pasted thinkorswim text, plus the lines it could not read.

    A header line starts a position and every leg line under it belongs to that
    position, which is exactly how the pane is laid out. A leg before any header
    is kept under "?" rather than dropped - it still says a contract exists that
    the log may not know about, which is the entire point.
    """
    positions: list[BrokerPosition] = []
    unparsed: list[str] = []
    current: Optional[BrokerPosition] = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        # Chrome and the desktop app both paste the totals row; it is a sum,
        # not a position, and reading it as one invents a holding.
        if re.match(r"^\s*(totals?|cash)\b", line, re.IGNORECASE):
            continue

        leg = _LEG.match(line)
        if leg:
            if current is None:
                current = BrokerPosition(symbol="?")
                positions.append(current)
            qty = int(leg.group("qty"))
            current.legs.append(BrokerLeg(
                action=Action.BUY if leg.group("sign") == "+" else Action.SELL,
                option_type=(OptionType.CALL if leg.group("kind").upper() == "C"
                             else OptionType.PUT),
                strike=float(leg.group("strike")),
                quantity=qty,
                dte=int(leg.group("dte")) if leg.group("dte") else None))
            continue

        head = _HEAD.match(line)
        if head:
            current = BrokerPosition(symbol=head.group("sym").upper(),
                                     bp_effect=_money(line))
            dte = re.search(r"\s(\d{1,4})\s+-?\d+\.\d+\s", line)
            if dte:
                current.dte = int(dte.group(1))
            positions.append(current)
            continue

        unparsed.append(line.strip())

    # `is not None`, not truthiness. A bought call ties up NOTHING and her
    # broker prints a plain 0.00 for it - her AVGO LEAPS is exactly that. A
    # falsy test dropped those positions, and the comparison below then
    # reported them as "in your log and not at your broker", which is the most
    # alarming thing this screen can say and would have been wrong every time.
    return [p for p in positions
            if p.legs or p.bp_effect is not None], unparsed


def _app_legs(position) -> dict[tuple, int]:
    """The log's legs as {(*side, type, strike*): contracts}.

    Leg `quantity` is per one unit of the position, so the real contract count
    is that times `contracts` - which is the number the broker prints and the
    only one the two sides can be compared on.
    """
    out: dict[tuple, int] = {}
    mult = max(int(position.contracts or 1), 1)
    for leg in position.legs:
        key = (leg.action, leg.option_type, round(leg.strike, 4))
        out[key] = out.get(key, 0) + max(int(leg.quantity or 1), 1) * mult
    return out


def _broker_legs(position: BrokerPosition) -> dict[tuple, int]:
    out: dict[tuple, int] = {}
    for leg in position.legs:
        out[leg.key] = out.get(leg.key, 0) + leg.quantity
    return out


def _describe(key: tuple, qty: int) -> str:
    action, kind, strike = key
    sign = "+" if action is Action.BUY else "-"
    return f"{sign}{qty} x {strike:g} {kind.value}"


def compare(app_positions: list[Any], broker: list[BrokerPosition],
            bp_tolerance: float = 25.0) -> list[Finding]:
    """Every way the log and the broker disagree, worst first.

    Matched on the underlying, because that is the only identifier both sides
    carry - the broker knows nothing about her Trade IDs. An underlying she
    holds twice at the broker collapses into one comparison, which is right for
    her book (thinkorswim groups by symbol too) and is called out when the leg
    sets cannot be lined up.

    `bp_tolerance` exists because the two will never agree to the penny: her
    CRWD condor reads $3,005 at the broker against a $3,000 width, and chasing
    five dollars would bury the findings that matter in noise.
    """
    findings: list[Finding] = []

    by_symbol: dict[str, list[Any]] = {}
    for p in app_positions:
        by_symbol.setdefault(p.underlying.upper(), []).append(p)
    broker_by: dict[str, list[BrokerPosition]] = {}
    for b in broker:
        broker_by.setdefault(b.symbol.upper(), []).append(b)

    # ---- at the broker, not in the log. The dangerous direction.
    for sym, rows in broker_by.items():
        if sym in by_symbol or sym == "?":
            continue
        legs = ", ".join(_describe(k, q) for r in rows
                         for k, q in _broker_legs(r).items())
        findings.append(Finding(
            kind="missing_in_log", symbol=sym, severity="warn",
            headline=f"{sym} is open at your broker and not in your log",
            detail=(f"Your account holds {legs or 'a position'} that the app "
                    f"has no row for, so none of its numbers include it - not "
                    f"its buying power, not its delta, not its risk. If you "
                    f"traded it, log it. If you did not, find out who did.")))

    # ---- in the log, not at the broker.
    for sym, rows in by_symbol.items():
        if sym in broker_by:
            continue
        for p in rows:
            findings.append(Finding(
                kind="missing_at_broker", symbol=sym, severity="warn",
                trade_id=p.trade_id,
                headline=f"{sym} is open in your log and not at your broker",
                detail=(f"The app is holding ${p.bp_effect:,.0f} of buying "
                        f"power against a position your account does not have. "
                        f"Usually this is a trade you closed without logging "
                        f"the close - or one that was never really opened.")))

    # ---- both sides have it: do the legs and the money agree?
    for sym, rows in by_symbol.items():
        if sym not in broker_by:
            continue
        app_all: dict[tuple, int] = {}
        for p in rows:
            for k, q in _app_legs(p).items():
                app_all[k] = app_all.get(k, 0) + q
        brk_all: dict[tuple, int] = {}
        for b in broker_by[sym]:
            for k, q in _broker_legs(b).items():
                brk_all[k] = brk_all.get(k, 0) + q
        tid = rows[0].trade_id if len(rows) == 1 else ""

        if brk_all:
            for key in sorted(set(app_all) | set(brk_all), key=lambda k: k[2]):
                a, b = app_all.get(key, 0), brk_all.get(key, 0)
                if a == b:
                    continue
                if a and b:
                    findings.append(Finding(
                        kind="size", symbol=sym, severity="warn", trade_id=tid,
                        headline=f"{sym} {_describe(key, b)} - your log says {a}",
                        detail=(f"The broker has {b} contract(s) at this strike "
                                f"and the log has {a}. Everything measured "
                                f"against the credit - your 50% target above "
                                f"all - is out by that ratio.")))
                elif b:
                    findings.append(Finding(
                        kind="legs", symbol=sym, severity="warn", trade_id=tid,
                        headline=f"{sym} {_describe(key, b)} is not in your log",
                        detail=("A contract you hold that the app cannot see. "
                                "If it is a short leg with nothing bought "
                                "against it, the app is reporting a defined "
                                "risk you do not actually have.")))
                else:
                    findings.append(Finding(
                        kind="legs", symbol=sym, severity="warn", trade_id=tid,
                        headline=f"{sym} {_describe(key, a)} is in your log only",
                        detail=("The app thinks you hold this and your account "
                                "does not. Most often a leg closed without the "
                                "close being logged.")))

        brk_bp = sum(b.bp_effect or 0.0 for b in broker_by[sym])
        app_bp = sum(p.bp_effect for p in rows)
        if brk_bp and abs(app_bp - brk_bp) > bp_tolerance:
            findings.append(Finding(
                kind="bp", symbol=sym, severity="info", trade_id=tid,
                headline=(f"{sym} buying power: broker ${brk_bp:,.0f}, "
                          f"app ${app_bp:,.0f}"),
                detail=(f"A ${abs(app_bp - brk_bp):,.0f} difference. Your SOP "
                        f"says thinkorswim is right, so this is the number to "
                        f"type into the trade's BP Effect if the legs above "
                        f"already agree.")))

    order = {"missing_in_log": 0, "legs": 1, "size": 2,
             "missing_at_broker": 3, "bp": 4}
    return sorted(findings, key=lambda f: (order.get(f.kind, 9), f.symbol))


def summary(app_positions: list[Any], broker: list[BrokerPosition],
            findings: list[Finding]) -> dict[str, Any]:
    """The one-line verdict above the list."""
    app_bp = sum(p.bp_effect for p in app_positions)
    brk_bp = sum(b.bp_effect or 0.0 for b in broker)
    return {
        "app_count": len(app_positions),
        "broker_count": len([b for b in broker if b.symbol != "?"]),
        "app_bp": round(app_bp, 2),
        "broker_bp": round(brk_bp, 2),
        "gap": round(app_bp - brk_bp, 2),
        "clean": not findings,
    }
