"""Maps a trade into the columns of Rita's "App Trades" tab (a copy of her
teacher's monthly M(1) sheet), so logged trades appear in her familiar format.

Her tab's columns (right-to-left Hebrew sheet; letters are the real column ids):
  A Ticker | B strategy code | C call strike | D put strike | E premium ($/contract)
  F contracts | G Profit % | H Profit $ (=G*F*E) | I commissions (=F*2.6)
  J BP | K P/BP (=H/J) | L..P per-strategy profit buckets | Q ROLL | R CLOSE
The app fills A-G + J; the Apps Script writes H, I, K, L-P as formulas.
"""

from __future__ import annotations

from typing import Any, Optional

from src.engine.models import Action, OptionType, Trade

# The app's 8 strategies -> the short codes her sheet's strategy dropdown uses.
# Both credit spreads share her single "CS" bucket; covered-call models share "CC".
STRATEGY_CODE = {
    "put_credit_spread": "CS",
    "call_credit_spread": "CS",
    "iron_condor": "IC",
    "cash_secured_put": "SP",
    "poor_mans_covered_call": "PMCC",
    "covered_call_model_1": "CC",
    "covered_call_model_2": "CC",
    "covered_call_model_3": "CC",
}


def strategy_code(strategy_key: str) -> str:
    return STRATEGY_CODE.get(strategy_key, "")


def _short_strike(trade: Trade, option_type: OptionType) -> Any:
    for leg in trade.legs:
        if leg.action == Action.SELL and leg.option_type == option_type:
            return leg.strike
    return ""


def mirror_fields(
    trade: Trade,
    sizing: dict[str, float],
    trade_id: str,
    expiration_iso: str = "",
) -> dict[str, Any]:
    """The values the Apps Script drops into the App Trades row for one trade.

    premium is the credit PER CONTRACT in dollars, so her Profit$ formula
    (Profit% x Contracts x Premium) gives the full credit at 100%.
    """
    contracts = max(int(trade.contracts), 1)
    credit_total = float(sizing.get("credit", 0.0))
    return {
        "ticker": trade.underlying,
        "code": strategy_code(trade.strategy_key),
        "call_strike": _short_strike(trade, OptionType.CALL),
        "put_strike": _short_strike(trade, OptionType.PUT),
        "premium": round(credit_total / contracts, 2),
        "contracts": contracts,
        "bp": round(float(sizing.get("buying_power", 0.0)), 2),
        "profit_pct": 1.0,                     # 100% target at entry
        "trade_id": trade_id,
        "expiration": expiration_iso,
        "dte": trade.dte if trade.dte is not None else "",
    }
