
import csv
import os
import sys
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Trade:
    portfolio_id: str
    security_id: str
    action: str
    quantity: str
    price: str
    security_type: str = ""


@dataclass
class Decision:
    decision: str
    reason: str


# ---------------------------------------------------------------------------
# Loading portfolio reference data from CSV
# ---------------------------------------------------------------------------
def load_portfolio_reference(csv_path: str) -> dict:
    """
    Reads portfolio_reference.csv and returns:
        {
            portfolio_id: {
                "restricted_securities": set(...),
                "max_position_pct": float,
                "current_holdings": {security_id: (quantity, market_price)},
            },
            ...
        }
    cash_balance is deliberately NOT part of this structure - it is
    collected interactively at runtime (see prompt_cash_balances()).
    """
    portfolios = {}
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row["portfolio_id"].strip()
            if pid not in portfolios:
                restricted_raw = row.get("restricted_securities", "") or ""
                restricted = {
                    s.strip() for s in restricted_raw.split(";") if s.strip()
                }
                try:
                    max_pct = float(row.get("max_position_pct", "") or 100.0)
                except ValueError:
                    max_pct = 100.0
                portfolios[pid] = {
                    "restricted_securities": restricted,
                    "max_position_pct": max_pct,
                    "current_holdings": {},
                }

            sec_id = (row.get("security_id") or "").strip()
            if sec_id:
                try:
                    qty = float(row.get("held_quantity", "") or 0)
                    mkt_price = float(row.get("held_market_price", "") or 0)
                except ValueError:
                    qty, mkt_price = 0.0, 0.0
                portfolios[pid]["current_holdings"][sec_id] = (qty, mkt_price)

    return portfolios


# ---------------------------------------------------------------------------
# Interactive cash balance entry
# ---------------------------------------------------------------------------
def prompt_cash_balance(portfolio_id: str) -> float:
    """
    Prompts the user for a non-negative numeric cash balance for the
    given portfolio_id. Re-prompts on invalid input until a valid
    value is entered.
    """
    while True:
        raw = input(
            f"Enter cash balance for portfolio '{portfolio_id}': "
        ).strip()
        try:
            value = float(raw)
        except ValueError:
            print("  -> Invalid entry: please enter a numeric value (e.g. 10000 or 10000.50).")
            continue
        if value < 0:
            print("  -> Invalid entry: cash balance cannot be negative.")
            continue
        return value


def prompt_cash_balances_for_trades(trades: list) -> dict:
    """
    Walks the trades in order and asks for a cash balance once per
    distinct portfolio_id the first time it is encountered.
    Returns {portfolio_id: cash_balance}.
    """
    cash_balances = {}
    seen_order = []
    for t in trades:
        if t.portfolio_id not in cash_balances:
            cash_balances[t.portfolio_id] = None
            seen_order.append(t.portfolio_id)

    print(f"\nFound {len(seen_order)} distinct portfolio(s) in the trades file.")
    print("Please enter the current cash balance for each one.\n")
    for pid in seen_order:
        cash_balances[pid] = prompt_cash_balance(pid)
    print()
    return cash_balances


# ---------------------------------------------------------------------------
# Core evaluation logic
# ---------------------------------------------------------------------------
def evaluate_trade(trade: Trade, portfolios: dict, cash_balances: dict) -> Decision:
    # Step 1: basic validation
    try:
        qty = float(trade.quantity)
        price = float(trade.price)
    except (TypeError, ValueError):
        return Decision("Rejected", "Invalid trade data.")

    if qty <= 0 or price <= 0 or trade.action not in ("Buy", "Sell"):
        return Decision("Rejected", "Invalid trade data.")

    portfolio = portfolios.get(trade.portfolio_id)
    if portfolio is None:
        # No reference row at all for this portfolio: treat as no
        # restrictions / no holdings / uncapped concentration.
        portfolio = {
            "restricted_securities": set(),
            "max_position_pct": 100.0,
            "current_holdings": {},
        }

    cash_balance = cash_balances.get(trade.portfolio_id, 0.0)

    # Step 2: restricted security check
    if trade.security_id in portfolio.get("restricted_securities", set()):
        return Decision("Rejected", "Security is restricted for this portfolio.")

    holdings = portfolio.get("current_holdings", {})
    held_qty, held_price = holdings.get(trade.security_id, (0, price))

    # Step 3: sell check
    if trade.action == "Sell":
        if qty > held_qty:
            return Decision("Rejected", "Insufficient holdings to sell.")
        # Sells never go through Step 5 (concentration check) -> Approved
        return Decision("Approved", "Trade passes all compliance checks.")

    # Step 4: buy affordability check
    cost = qty * price
    if cost > cash_balance:
        return Decision("Rejected", "Insufficient cash for this buy order.")

    # Step 5: position concentration check (Buy only)
    cash_after = cash_balance - cost
    new_qty_this_security = held_qty + qty
    value_this_security_after = new_qty_this_security * price

    other_holdings_value = sum(
        h_qty * h_price
        for sec_id, (h_qty, h_price) in holdings.items()
        if sec_id != trade.security_id
    )

    total_portfolio_value_after = (
        cash_after + value_this_security_after + other_holdings_value
    )

    if total_portfolio_value_after > 0:
        position_pct = 100.0 * value_this_security_after / total_portfolio_value_after
    else:
        position_pct = 0.0

    max_pct = portfolio.get("max_position_pct", 100.0)
    if position_pct > max_pct:
        return Decision(
            "Needs Manual Sign-off",
            "Exceeds maximum position concentration - requires approval.",
        )

    # Step 6: otherwise
    return Decision("Approved", "Trade passes all compliance checks.")


# ---------------------------------------------------------------------------
# CSV I/O for trades
# ---------------------------------------------------------------------------
def load_trades(csv_path: str) -> list:
    trades = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(
                Trade(
                    portfolio_id=row["portfolio_id"].strip(),
                    security_id=row["security_id"].strip(),
                    action=row["action"].strip(),
                    quantity=row["quantity"],
                    price=row["price"],
                    security_type=row.get("security_type", "").strip(),
                )
            )
    return trades


def run(
    trades_csv_path: str,
    portfolio_ref_csv_path: str,
    output_path: str,
    cash_balances: dict = None,
):
    trades = load_trades(trades_csv_path)
    portfolios = load_portfolio_reference(portfolio_ref_csv_path)

    if cash_balances is None:
        cash_balances = prompt_cash_balances_for_trades(trades)

    results = []
    for t in trades:
        decision = evaluate_trade(t, portfolios, cash_balances)
        results.append(
            {
                "portfolio_id": t.portfolio_id,
                "security_id": t.security_id,
                "action": t.action,
                "quantity": t.quantity,
                "price": t.price,
                "decision": decision.decision,
                "reason": decision.reason,
            }
        )

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "portfolio_id",
                "security_id",
                "action",
                "quantity",
                "price",
                "decision",
                "reason",
            ],
        )
        writer.writeheader()
        writer.writerows(results)

    return results


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    trades_csv = os.path.join(here, "trades_test.csv")
    ref_csv = os.path.join(here, "portfolio_reference.csv")
    out_csv = os.path.join(here, "trade_results.csv")

    if not os.path.exists(trades_csv):
        print(f"ERROR: could not find {trades_csv}")
        sys.exit(1)
    if not os.path.exists(ref_csv):
        print(f"ERROR: could not find {ref_csv}")
        sys.exit(1)

    results = run(trades_csv, ref_csv, out_csv)

    print(f"Evaluated {len(results)} trades. Results written to: {out_csv}\n")
    for r in results:
        print(
            f"{r['portfolio_id']:8} {r['security_id']:10} {r['action']:5} "
            f"qty={r['quantity']:>6} price={r['price']:>8} -> "
            f"{r['decision']:20} ({r['reason']})"
        )
