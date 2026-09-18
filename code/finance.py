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
    finance/statements/{period}/{grouped}/{page}
                               the transaction statement, 30 rows a page.
                               period: today | yesterday | all (back to
                               Feb 2026, older months rolled up); grouped:
                               true folds each day's flights into one
                               "Flights of the day" row (today ~83 rows vs
                               ~1600). Any other shape, including `?page=`,
                               is error 99 or silently page 1.

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
import time
from datetime import date, datetime, timedelta, timezone

# How long each read can be trusted, as a bucket width in seconds: a read is
# reused until the clock crosses into the next bucket. Flights settle on the
# game's 15-minute grid, so anything that moves with them is good until the
# next quarter hour; the summary, taxes and banks roll over once a UTC day.
# Anything the player does in-game (a loan, a purchase) needs force=True,
# which the web app's Reload button sends.
QUARTER, DAY = 900, 86400
READS = {
    "summary": ("finance/summary", DAY),
    "taxes": ("finance/summary/taxes", DAY),
    "banks": ("finance/bank", DAY),
    "cashflow": ("finance/cashFlow", QUARTER),
    "ledger": ("finance/accounting/history", QUARTER),
}


def _same_bucket(fetched_at: float, now: float, width: int) -> bool:
    return int(fetched_at // width) == int(now // width)


def statements(client, period: str, grouped: bool = True, known=frozenset()) -> list:
    """One statement period, newest first, following the pager to the end or
    stopping at the first page that reaches a row id in `known`. Id 0 is the
    grouped "Flights of the day" line, which is re-read every time."""
    rows, page = [], 1
    while True:
        body = client._request("GET", f"finance/statements/{period}/{str(grouped).lower()}/{page}")
        batch = body.get("statements", [])
        new = [r for r in batch if not r["id"] or r["id"] not in known]
        rows += new
        if len(new) < len(batch) or page >= int((body.get("paging") or {}).get("pageCount") or 1):
            return rows
        page += 1


class _Counted:
    """The client, counting the mobile calls a load actually made."""

    def __init__(self, client):
        self.client, self.calls = client, 0

    def _request(self, *args, **kwargs):
        self.calls += 1
        return self.client._request(*args, **kwargs)


def fetch(client, force: bool = False, now: float = None) -> dict:
    """Every read build() needs, from the api_cache table where it is still
    good. A warm quarter hour costs 0 mobile calls; crossing into a new one
    costs 3 (cash flow, ledger, today's new statement rows); the first load
    of a UTC day about 10."""
    from db import cache_get, cache_put
    now = now or time.time()
    counted = _Counted(client)
    raw, stamps = {}, []
    for key, (ep, width) in READS.items():
        hit = None if force else cache_get(f"finance:{key}")
        if hit and _same_bucket(hit[0], now, width):
            raw[key] = hit[1]
            stamps.append(hit[0])
        else:
            raw[key] = counted._request("GET", ep)
            cache_put(f"finance:{key}", raw[key], now)
            stamps.append(now)

    # Statements are cached per UTC date and topped up with only the rows
    # added since (newest first, so the pager stops at the first known id).
    # A day read after it ended is final and never fetched again.
    # ponytail: a row back-dated below the newest known one would be missed
    # until a Reload (force) re-reads the whole day.
    today = datetime.fromtimestamp(now, timezone.utc).date()
    today_start = int(now // DAY) * DAY
    rows = []
    for day, period in ((today, "today"), (today - timedelta(days=1), "yesterday")):
        key = f"finance:statements:{day.isoformat()}"
        hit = None if force else cache_get(key)
        final = period == "yesterday" and hit and hit[0] >= today_start
        if hit and (final or (period == "today" and _same_bucket(hit[0], now, QUARTER))):
            day_rows = hit[1]
        else:
            old = hit[1] if hit else []
            new = statements(counted, period, known={r["id"] for r in old if r["id"]})
            day_rows = new + [r for r in old if r["id"]]
            cache_put(key, day_rows, now)
        rows += day_rows
    raw["statements"] = {"statements": rows}

    if counted.calls and client.last_resources:
        cache_put("finance:resources", client.last_resources, now)
    cached = cache_get("finance:resources")
    raw["resources"] = cached[1] if cached else None
    raw["meta"] = {"mobile_calls": counted.calls, "oldest_read": min(stamps),
                   "cash_at": cached[0] if cached else None}
    return raw


def _utc(epoch):
    """Epoch seconds as the game's own UTC timestamp format."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if epoch else None


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

    cash = (raw.get("resources") or raw["summary"].get("ressources") or {}).get("dollar")
    meta = raw.get("meta") or {}
    return {
        "as_of": _utc(meta.get("cash_at")) or (_day(now) and now["date"][:19]),
        "cash": cash,
        "mobile_calls": meta.get("mobile_calls"),
        "oldest_read": _utc(meta.get("oldest_read")),
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
