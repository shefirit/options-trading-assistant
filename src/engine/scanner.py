"""Turns a live option chain into candidate trades that fit your SOP.

For each strategy it walks the chain, builds real trades at your target delta and
timing, sizes them, runs them through the SAME validator the checklist uses, and
keeps only the ones that pass. Candidates are ranked by return-on-risk (how much
premium you collect per dollar at risk).
"""

from __future__ import annotations

from typing import Optional

from src.data.chain import OptionChain, OptionContract
from src.engine import sizing
from src.engine.config_loader import get_strategy
from src.engine.models import Action, Candidate, CheckStatus, Leg, OptionType, Trade
from src.engine.validator import validate_trade

# Families the multi-candidate scan() supports (credit spreads + cash secured puts).
SCANNABLE_FAMILIES = {"credit_spread", "single_leg"}
# Families the focused scan_setups() supports - adds covered calls and PMCC.
SETUP_FAMILIES = SCANNABLE_FAMILIES | {"covered_call", "diagonal"}

# Ignore far-out strikes that are almost worthless (avoids junk candidates).
MIN_SHORT_DELTA = 0.03

# Also show trades a touch OVER the delta limit (e.g. 0.12 when the rule is 0.10,
# or 0.31 when it is 0.30), clearly flagged, so you can see what sits just outside.
DELTA_NEAR_MISS = 0.03


def can_scan(strategy_key: str) -> bool:
    """True if the 'Find setups for me' scan supports this strategy."""
    return get_strategy(strategy_key).get("family") in SETUP_FAMILIES


def _auto_width(underlying_price: float, symbol: Optional[str] = None) -> float:
    """A sensible spread width if the user does not pick one, per the SOP:
    indexes and ETFs use ~$25-50, individual stocks ~$5-10."""
    if symbol is not None:
        from src.engine.config_loader import underlying_kind
        return 5.0 if underlying_kind(symbol) == "stock" else 25.0
    # Fallback by price when the symbol is unknown.
    if underlying_price >= 1000:
        return 25.0
    if underlying_price >= 100:
        return 5.0
    return 1.0


def _nearest_below(contracts: list[OptionContract], target_strike: float) -> Optional[OptionContract]:
    below = [c for c in contracts if c.strike <= target_strike + 1e-6]
    return max(below, key=lambda c: c.strike) if below else None


def _nearest_above(contracts: list[OptionContract], target_strike: float) -> Optional[OptionContract]:
    above = [c for c in contracts if c.strike >= target_strike - 1e-6]
    return min(above, key=lambda c: c.strike) if above else None


def _leg(role: str, action: Action, c: OptionContract) -> Leg:
    return Leg(
        role=role, action=action, option_type=c.option_type,
        strike=c.strike, delta=c.delta, premium=c.mid, dte=c.dte,
    )


def _make_candidate(
    trade: Trade, strategy: dict, delta_limit: Optional[float] = None,
) -> Optional[Candidate]:
    """Build a Candidate if the trade fits the SOP - or is a flagged near-miss.

    A near-miss is kept only when the ONLY broken rule is the short-leg delta,
    and it is within DELTA_NEAR_MISS of the limit. Anything else that fails
    (wrong DTE, debit, over budget...) is dropped.
    """
    report = validate_trade(trade)
    short_delta = max((l.abs_delta for l in trade.short_legs), default=0.0)

    if report.passed:
        fits_sop, note = True, ""
    else:
        fails = [r.name for r in report.results if r.status == CheckStatus.FAIL]
        only_delta_broken = fails and all("delta under" in n.lower() for n in fails)
        near = (delta_limit is not None
                and short_delta <= delta_limit + DELTA_NEAR_MISS + 1e-9)
        if not (only_delta_broken and near):
            return None
        fits_sop = False
        note = (f"Delta {short_delta:.2f} is a touch over your {delta_limit:.2f} limit - "
                "shown so you can compare, not to trade as-is.")

    size = sizing.estimate(trade, strategy)
    if size["max_loss"] <= 0:
        return None
    return Candidate(
        trade=trade,
        credit=size["credit"],
        max_loss=size["max_loss"],
        buying_power=size["buying_power"],
        return_on_risk=size["return_on_risk"],
        short_delta=round(short_delta, 3),
        dte=trade.dte,
        fits_sop=fits_sop,
        note=note,
    )


NEAR_MISS_SLOTS = 3   # how many flagged near-misses to show alongside the fits


def _rank(out: list[Candidate], max_candidates: int) -> list[Candidate]:
    """SOP-passing trades first (richest premium first), then a few flagged
    near-misses so you can always see what sits just over your delta limit.
    """
    fits = sorted((c for c in out if c.fits_sop),
                  key=lambda c: c.return_on_risk, reverse=True)[:max_candidates]
    near = sorted((c for c in out if not c.fits_sop),
                  key=lambda c: c.return_on_risk, reverse=True)[:NEAR_MISS_SLOTS]
    return fits + near


def _resolve_dte(chain: OptionChain, entry: dict, target_dte: Optional[int]) -> Optional[int]:
    """Which expiration to scan: the user's pick if given, else the strategy default."""
    want = int(target_dte) if target_dte is not None else int(entry.get("dte_target", 30))
    return chain.nearest_dte(want)


def _scan_vertical(
    chain: OptionChain, strategy_key: str, option_type: OptionType,
    width: float, contracts: int, max_candidates: int, target_dte: Optional[int] = None,
) -> list[Candidate]:
    strategy = get_strategy(strategy_key)
    entry = strategy["entry"]
    limit = float(entry["short_leg_delta_max"])
    dte = _resolve_dte(chain, entry, target_dte)
    if dte is None:
        return []
    legs_of_type = chain.by(option_type, dte)

    out: list[Candidate] = []
    for short in legs_of_type:
        if not (MIN_SHORT_DELTA <= short.abs_delta <= limit + DELTA_NEAR_MISS + 1e-9):
            continue
        # Long (protection) leg sits `width` further out of the money.
        target = short.strike - width if option_type == OptionType.PUT else short.strike + width
        finder = _nearest_below if option_type == OptionType.PUT else _nearest_above
        long = finder(legs_of_type, target)
        if long is None or abs(long.strike - short.strike) < 1e-6:
            continue
        short_role = "short_put" if option_type == OptionType.PUT else "short_call"
        long_role = "long_put" if option_type == OptionType.PUT else "long_call"
        trade = Trade(
            strategy_key=strategy_key, underlying=chain.underlying, contracts=contracts,
            underlying_price=chain.underlying_price,
            legs=[_leg(short_role, Action.SELL, short), _leg(long_role, Action.BUY, long)],
        )
        cand = _make_candidate(trade, strategy, delta_limit=limit)
        if cand:
            out.append(cand)

    return _rank(out, max_candidates)


def _scan_iron_condor(
    chain: OptionChain, width: float, contracts: int, max_candidates: int,
    target_dte: Optional[int] = None,
) -> list[Candidate]:
    strategy = get_strategy("iron_condor")
    entry = strategy["entry"]
    limit = float(entry["short_leg_delta_max"])
    dte = _resolve_dte(chain, entry, target_dte)
    if dte is None:
        return []
    puts = chain.by(OptionType.PUT, dte)
    calls = chain.by(OptionType.CALL, dte)

    def _pick_shorts(rows: list[OptionContract]) -> list[OptionContract]:
        """Best few short strikes: mostly rule-fitting ones, plus a near-miss or two.

        Picked per group so near-misses (delta just over the limit) never crowd
        out the strikes that actually obey your rules.
        """
        fitting = sorted(
            (c for c in rows if MIN_SHORT_DELTA <= c.abs_delta <= limit + 1e-9),
            key=lambda c: c.abs_delta, reverse=True,
        )[:4]
        near = sorted(
            (c for c in rows if limit < c.abs_delta <= limit + DELTA_NEAR_MISS + 1e-9),
            key=lambda c: c.abs_delta,
        )[:2]
        return fitting + near

    short_puts = _pick_shorts(puts)
    short_calls = _pick_shorts(calls)

    out: list[Candidate] = []
    for sp in short_puts:
        lp = _nearest_below(puts, sp.strike - width)
        for sc in short_calls:
            lc = _nearest_above(calls, sc.strike + width)
            if not lp or not lc:
                continue
            trade = Trade(
                strategy_key="iron_condor", underlying=chain.underlying, contracts=contracts,
                underlying_price=chain.underlying_price,
                legs=[
                    _leg("long_put", Action.BUY, lp),
                    _leg("short_put", Action.SELL, sp),
                    _leg("short_call", Action.SELL, sc),
                    _leg("long_call", Action.BUY, lc),
                ],
            )
            cand = _make_candidate(trade, strategy, delta_limit=limit)
            if cand:
                out.append(cand)
    return _rank(out, max_candidates)


def _scan_cash_secured_put(
    chain: OptionChain, contracts: int, max_candidates: int, target_dte: Optional[int] = None,
) -> list[Candidate]:
    strategy = get_strategy("cash_secured_put")
    entry = strategy["entry"]
    limit = float(entry["short_leg_delta_max"])   # ~0.30 for CSP
    dte = _resolve_dte(chain, entry, target_dte)
    if dte is None:
        return []
    out: list[Candidate] = []
    for put in chain.by(OptionType.PUT, dte):
        if not (MIN_SHORT_DELTA <= put.abs_delta <= limit + DELTA_NEAR_MISS + 1e-9):
            continue
        trade = Trade(
            strategy_key="cash_secured_put", underlying=chain.underlying, contracts=contracts,
            underlying_price=chain.underlying_price,
            legs=[_leg("short_put", Action.SELL, put)],
        )
        cand = _make_candidate(trade, strategy, delta_limit=limit)
        if cand:
            out.append(cand)
    return _rank(out, max_candidates)


def scan(
    strategy_key: str,
    chain: OptionChain,
    width: Optional[float] = None,
    contracts: int = 1,
    max_candidates: int = 15,
    target_dte: Optional[int] = None,
) -> list[Candidate]:
    """Find candidate trades for a strategy on one underlying's chain.

    target_dte: the days-to-expiration you want (from the UI slider). If omitted,
    the strategy's default from strategies.yaml is used.
    """
    strategy = get_strategy(strategy_key)
    family = strategy.get("family")
    if family not in SCANNABLE_FAMILIES:
        raise ValueError(
            f"'{strategy.get('name', strategy_key)}' is not scannable yet - "
            "use the checklist to validate a trade you build yourself."
        )
    w = width if width is not None else _auto_width(chain.underlying_price, chain.underlying)

    if strategy_key == "put_credit_spread":
        return _scan_vertical(chain, strategy_key, OptionType.PUT, w, contracts, max_candidates, target_dte)
    if strategy_key == "call_credit_spread":
        return _scan_vertical(chain, strategy_key, OptionType.CALL, w, contracts, max_candidates, target_dte)
    if strategy_key == "iron_condor":
        return _scan_iron_condor(chain, w, contracts, max_candidates, target_dte)
    if strategy_key == "cash_secured_put":
        return _scan_cash_secured_put(chain, contracts, max_candidates, target_dte)
    raise ValueError(f"No scanner wired up for '{strategy_key}'.")


# ------------------------------------------------------------------
#  Focused "best setups" scan: a few trades at the SOP-target delta,
#  spread across a handful of expirations (21-44 days). This is what
#  the "Find setups for me" button uses - a short, decision-ready list.
# ------------------------------------------------------------------
def _target_short_delta(strategy: dict) -> float:
    """The delta your SOP aims the short leg at (the limit for spreads, 0.30 for CSP)."""
    entry = strategy.get("entry", {})
    if "short_leg_delta_max" in entry:
        return float(entry["short_leg_delta_max"])
    if "short_call_delta" in entry:
        return float(entry["short_call_delta"])
    return 0.30


def _setup_dte_window(strategy: dict, dte_min: int, dte_max: int) -> tuple[int, int]:
    """The days-to-expiration span to sample. Credit spreads / CSP use their
    21-44 window; covered calls / PMCC use a band around the short-call target."""
    entry = strategy.get("entry", {})
    if "dte_min" in entry and "dte_max" in entry:
        return int(entry["dte_min"]), int(entry["dte_max"])
    if "short_call_dte_target" in entry:
        t = int(entry["short_call_dte_target"])
        return max(14, t - 7), t + 14
    return dte_min, dte_max


def _pick_target_short(options: list[OptionContract], target: float) -> Optional[OptionContract]:
    """The short strike nearest the SOP delta: richest one still within the rule,
    or the closest just-over if none qualify."""
    fits = [o for o in options if MIN_SHORT_DELTA <= o.abs_delta <= target + 1e-9]
    if fits:
        return max(fits, key=lambda o: o.abs_delta)   # closest to target from below
    near = [o for o in options if target < o.abs_delta <= target + DELTA_NEAR_MISS + 1e-9]
    return min(near, key=lambda o: o.abs_delta) if near else None


def _sample_evenly(values: list[int], k: int) -> list[int]:
    """Up to k values spread across a sorted list (e.g. 21, 28, 35, 44)."""
    if len(values) <= k:
        return list(values)
    if k == 1:
        return [values[len(values) // 2]]
    step = (len(values) - 1) / (k - 1)
    idxs = sorted({round(i * step) for i in range(k)})
    return [values[i] for i in idxs]


def _setup_at_dte(strategy_key: str, chain: OptionChain, dte: int, target: float,
                  width: float, contracts: int,
                  leaps_chain: Optional[OptionChain] = None) -> Optional[Candidate]:
    strategy = get_strategy(strategy_key)
    family = strategy.get("family")
    limit = float(strategy["entry"].get("short_leg_delta_max",
                                        strategy["entry"].get("short_call_delta", 0.30)))

    # Covered call: just the short call to sell against your 100 shares.
    if family == "covered_call":
        short = _pick_target_short(chain.by(OptionType.CALL, dte), target)
        if not short:
            return None
        trade = Trade(strategy_key=strategy_key, underlying=chain.underlying, contracts=contracts,
                      underlying_price=chain.underlying_price,
                      legs=[_leg("short_call", Action.SELL, short)])
        return _make_candidate(trade, strategy, delta_limit=None)

    # PMCC: a deep in-the-money LEAPS (stock stand-in) plus a short call for income.
    if family == "diagonal":
        if leaps_chain is None:
            return None
        long_min = float(strategy["entry"].get("long_leg_delta_min", 0.80))
        leaps_calls = [c for c in leaps_chain.contracts
                       if c.option_type == OptionType.CALL and c.abs_delta >= long_min - 0.05
                       and c.mid > 0]
        if not leaps_calls:
            return None
        leaps = min(leaps_calls, key=lambda c: abs(c.abs_delta - 0.85))
        short = _pick_target_short(chain.by(OptionType.CALL, dte), target)
        if not short:
            return None
        trade = Trade(strategy_key=strategy_key, underlying=chain.underlying, contracts=contracts,
                      underlying_price=chain.underlying_price,
                      legs=[_leg("long_call_leaps", Action.BUY, leaps),
                            _leg("short_call", Action.SELL, short)])
        return _make_candidate(trade, strategy, delta_limit=None)

    if strategy_key in ("put_credit_spread", "call_credit_spread"):
        ot = OptionType.PUT if strategy_key == "put_credit_spread" else OptionType.CALL
        legs = chain.by(ot, dte)
        short = _pick_target_short(legs, target)
        if not short:
            return None
        tgt = short.strike - width if ot == OptionType.PUT else short.strike + width
        finder = _nearest_below if ot == OptionType.PUT else _nearest_above
        long = finder(legs, tgt)
        if not long or abs(long.strike - short.strike) < 1e-6:
            return None
        sr, lr = (("short_put", "long_put") if ot == OptionType.PUT
                  else ("short_call", "long_call"))
        trade = Trade(strategy_key=strategy_key, underlying=chain.underlying, contracts=contracts,
                      underlying_price=chain.underlying_price,
                      legs=[_leg(sr, Action.SELL, short), _leg(lr, Action.BUY, long)])
        return _make_candidate(trade, strategy, delta_limit=limit)

    if strategy_key == "iron_condor":
        puts, calls = chain.by(OptionType.PUT, dte), chain.by(OptionType.CALL, dte)
        sp, sc = _pick_target_short(puts, target), _pick_target_short(calls, target)
        if not sp or not sc:
            return None
        lp, lc = _nearest_below(puts, sp.strike - width), _nearest_above(calls, sc.strike + width)
        if not lp or not lc:
            return None
        trade = Trade(strategy_key="iron_condor", underlying=chain.underlying, contracts=contracts,
                      underlying_price=chain.underlying_price,
                      legs=[_leg("long_put", Action.BUY, lp), _leg("short_put", Action.SELL, sp),
                            _leg("short_call", Action.SELL, sc), _leg("long_call", Action.BUY, lc)])
        return _make_candidate(trade, strategy, delta_limit=limit)

    if strategy_key == "cash_secured_put":
        short = _pick_target_short(chain.by(OptionType.PUT, dte), target)
        if not short:
            return None
        trade = Trade(strategy_key="cash_secured_put", underlying=chain.underlying,
                      contracts=contracts, underlying_price=chain.underlying_price,
                      legs=[_leg("short_put", Action.SELL, short)])
        return _make_candidate(trade, strategy, delta_limit=limit)
    return None


def scan_setups(
    strategy_key: str,
    chain: OptionChain,
    width: Optional[float] = None,
    contracts: int = 1,
    dte_min: int = 21,
    dte_max: int = 44,
    max_setups: int = 10,
    leaps_chain: Optional[OptionChain] = None,
) -> list[Candidate]:
    """A short list of the best setups: one trade per sampled expiration, each at
    the SOP-target delta. Sorted soonest-expiry first.

    leaps_chain: a far-dated chain (~7 months out) needed for PMCC's long LEAPS.
    """
    strategy = get_strategy(strategy_key)
    if strategy.get("family") not in SETUP_FAMILIES:
        raise ValueError(
            f"'{strategy.get('name', strategy_key)}' is not scannable yet - "
            "use the checklist to validate a trade you build yourself.")
    w = width if width is not None else _auto_width(chain.underlying_price, chain.underlying)
    target = _target_short_delta(strategy)
    lo, hi = _setup_dte_window(strategy, dte_min, dte_max)
    # US-style stocks/ETFs use their own DTE window (avoid the ~21-DTE early-assignment
    # zone, but still reach the real ~monthly expiration); indices may enter at 21.
    from src.engine.config_loader import is_european_style
    entry = strategy.get("entry", {})
    if not is_european_style(chain.underlying):
        lo = max(lo, int(entry.get("dte_min_us_style", lo)))
        hi = int(entry.get("dte_max_us_style", hi))

    dtes = sorted(d for d in chain.dtes() if lo <= d <= hi)
    if not dtes:
        nearest = chain.nearest_dte((lo + hi) // 2)
        dtes = [nearest] if nearest is not None else []

    out: list[Candidate] = []
    for dte in _sample_evenly(dtes, max_setups):
        cand = _setup_at_dte(strategy_key, chain, dte, target, w, contracts, leaps_chain)
        if cand:
            out.append(cand)
    out.sort(key=lambda c: (c.dte if c.dte is not None else 0))
    return out
