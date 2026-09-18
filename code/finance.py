#!/usr/bin/env python3
"""Company finances rebuilt from the mobile API, with the maths done here.

The web "Finances" pages (financial summary, taxes, cash flow, accounting book,
banks) all have mobile twins under `finance/*`, recovered from the APK's
il2cpp metadata (`Api.FinanceCalls`) and verified live 2026-09-18:

    finance/summary            7-day flight profits, maintenance, salaries,
                               structural profit, loans, valorization
    finance/summary/taxes      the progressive income-tax brackets, credits
    finance/cashFlow           yesterday / today / tomorrow cash flow
    finance/accounting/history the 7-day accounting book, by category
    finance/bank               banks, rates, loan capacity, credit rating
    finance/statements/today   the latest 30 transactions (paging unknown:
                               `page=` and path segments are ignored or 404)

`build()` recomputes the two derived figures the game shows, and both match it
to the dollar, so any drift means the game changed the formula:

    structural profit = flights - maintenance - salaries
                        - (income tax + weekly loan payments + rent) / 7
                        (with the charges in force that day)
    next income tax   = (brackets(flights - maintenance - salaries over 7 days)
                         - tax credit - cargo bonus) * (1 - globalTaxRate%)

    python3 code/finance.py          # print the rebuilt summary as JSON
"""

import json
import math
from datetime import date, timedelta

READS = {
    "summary": "finance/summary",
    "taxes": "finance/summary/taxes",
    "cashflow": "finance/cashFlow",
    "ledger": "finance/accounting/history",
    "banks": "finance/bank",
    "statements": "finance/statements/today",
}


def fetch(client) -> dict:
    """The six raw reads, keyed as in READS. Six paced GETs, ~5s."""
    return {key: client._request("GET", ep) for key, ep in READS.items()}


def _day(value) -> str:
    return (value or {}).get("date", "")[:10]


def _series(rows) -> dict:
    return {_day(r["date"]): int(float(r["amount"])) for r in rows or []}


def income_tax(taxable: float, brackets: list) -> int:
    """Gross tax: each bracket's rate on (top - min) of its slice, floored per
    bracket. That reproduces every one of the game's `airlineTax` figures."""
    tax = 0
    for b in brackets:
        lo, hi = b["min"], b.get("max")
        if taxable < lo:
            break
        top = taxable if hi is None else min(taxable, hi)
        tax += math.floor((top - lo) * b["percentage"] / 100)
    return tax


def build(raw: dict) -> dict:
    s = raw["summary"]["summary"]
    t = raw["taxes"]["summaryTaxes"]
    loans = s["loan"]
    loan_weekly = int(loans["weeklyAmount"])
    rental = int(s.get("rental") or 0)
    last_tax = int(float(s["incomeTax"]))

    flights = _series(s["flights"]["daily"])
    maint = _series(s["maintenanceCosts"])
    salary = _series(s["salary"])
    structural = _series(s["structuralProfit"])
    # The game deducts the fixed charges that applied on each day, so older
    # days carry last week's tax and loans; `fixed` is what it actually took.
    # `run_rate` is ours: this week's margin less the charges in force now.
    fixed_now = (last_tax + loan_weekly + rental) / 7
    days = []
    for d in sorted(flights, reverse=True):
        margin = flights[d] - maint.get(d, 0) - salary.get(d, 0)
        game = structural.get(d)
        days.append({"date": d, "flights": flights[d], "maintenance": maint.get(d, 0),
                     "salary": salary.get(d, 0), "margin": margin,
                     "fixed": margin - game if game is not None else round(fixed_now),
                     "structural": game if game is not None else round(margin - fixed_now)})

    week = {k: sum(x[k] for x in days) for k in ("flights", "maintenance", "salary", "margin", "structural")}
    week.update(loans=loan_weekly, rental=rental, income_tax_last=last_tax,
                fixed_now=round(fixed_now * 7), run_rate=round(week["margin"] - fixed_now * 7))

    brackets = t["nextIncomeTaxDetails"]
    taxable = week["margin"]
    gross = income_tax(taxable, brackets)
    discount = float(t.get("globalTaxRate") or 0)
    after = (gross - t["creditTax"] - t["cargoBonus"]) * (1 - discount / 100)
    tax = {
        "taxable": taxable, "gross": gross, "credit": t["creditTax"],
        "cargo_bonus": t["cargoBonus"], "discount_pct": round(discount, 2),
        "next": round(after), "next_game": round(-float(t["nextIncomeTax"])),
        "effective_pct": round(100 * after / taxable, 2) if taxable > 0 else 0,
        "weekly_payment": bool((s.get("taxeResearch") or {}).get("taxeWeekPayment")),
        "brackets": [{"min": b["min"], "max": b.get("max"), "pct": b["percentage"],
                      "tax": b["airlineTax"]} for b in brackets],
    }

    # The accounting book's columns run newest first from the game's "today".
    now = raw["summary"].get("inGameDateTime")
    today = date.fromisoformat(_day(now)) if now else date.today()
    book = raw["ledger"]
    labels = {x["key"]: x["translation"] for x in book.get("translation", [])}
    rows, totals = [], {}
    for r in book["accounting"]:
        if "values" not in r:
            totals[r["key"]] = r["amount"]
            continue
        rows.append({"key": r["key"], "label": labels.get(r["key"], r["key"]),
                     "values": r["values"], "total": sum(r["values"])})
    ncols = len(rows[0]["values"]) if rows else 0
    sums = {r["key"]: r["values"] for r in rows if r["key"].startswith("finances.")}
    body = [r for r in rows if not r["key"].startswith("finances.")]
    net = [sum(r["values"][i] for r in body) for i in range(ncols)]
    ledger = {
        "dates": [(today - timedelta(days=i)).isoformat() for i in range(ncols)],
        "rows": sorted(body, key=lambda r: -abs(r["total"])),
        "credit": sums.get("finances.creditSum"), "debit": sums.get("finances.debitSum"),
        "net": net, "net_total": sum(net),
        "incoming_total": totals.get("incomingTotal"), "outgoing_total": totals.get("outcomeTotal"),
    }

    loan_list = [{
        "id": x["id"], "bank": x["bank"]["name"], "amount": x["amount"], "rate": round(x["rate"], 2),
        "interest": x["interest"], "repaid": x["repaidAmount"], "remaining": x["toRepayAmount"],
        "weekly": x["weeklyAmount"], "weeks_left": x["nbRemainingWeeks"],
        "issued": _day(x["issuedDate"]), "ends": _day(x["endDate"]),
        "progress_pct": round(100 * x["repaidAmount"] / (x["amount"] + x["interest"]), 1),
    } for x in loans["list"]]
    loan_list.sort(key=lambda x: x["ends"])
    banks = raw["banks"]
    bank_list = [{
        "name": b["name"], "rate": b["currentRate"], "min_rate": b["minRate"], "max_rate": b["maxRate"],
        "express_available": b["expressLoanMaxAmountAvailable"], "express_min": b["expressLoanMinAmountAllowed"],
        "market_max": b["maxStockMarketLoan"], "owed": b["currentAppointmentLoan"],
        "weeks": [b["minNumberOfWeek"], b["maxNumberOfWeek"]], "unlocked": b["isUnlocked"],
    } for b in banks["list"]]

    cash = (raw["summary"].get("ressources") or {}).get("dollar")
    return {
        "as_of": _day(now) and now["date"][:19],
        "cash": cash,
        "valorization": s["valorization"],
        "days": days, "week": week, "tax": tax,
        "cashflow": raw["cashflow"]["cashFlow"],
        "ledger": ledger,
        "loans": loan_list,
        "loans_total": {"remaining": sum(x["remaining"] for x in loan_list), "weekly": loan_weekly,
                        "interest": sum(x["interest"] for x in loan_list)},
        "banks": bank_list,
        "credit_rating": (banks.get("airlineScoresInStockMarket") or {}).get("score"),
        "statements": [{"id": x["id"], "date": x["date"], "name": x["name"], "category": x["categoryname"],
                        "amount": x["amount"], "line_id": x.get("lineid")}
                       for x in raw["statements"].get("statements", [])],
    }


def main():
    from mobile_api import AMClient, AMSession
    client = AMClient(AMSession.load())
    try:
        print(json.dumps(build(fetch(client)), indent=1))
    finally:
        client.close()


if __name__ == "__main__":
    main()
