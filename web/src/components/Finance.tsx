import { fetchFinance } from "../api";
import { EMPTY, dateTime, integer, shortDate, shortMoney, wholeMoney } from "../format";
import { ErrorState, LoadingState } from "./PageStates";
import { SectionHeader } from "./SectionHeader";
import { useApi } from "../useApi";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

/** Signed money, red when negative: every ledger in here mixes both. */
function Money({ value, bold }: { value: number | null | undefined; bold?: boolean }) {
  if (value == null) return <span className="is-dim">{EMPTY}</span>;
  const text = value < 0 ? `-${wholeMoney.format(-value)}` : wholeMoney.format(value);
  return <span className={value < 0 ? "is-negative" : undefined}>{bold ? <strong>{text}</strong> : text}</span>;
}

const signedShort = (value: number) => (value < 0 ? `-${shortMoney(-value)}` : shortMoney(value));

const CASHFLOW_ROWS = [
  ["ca", "Turnover"], ["flightCost", "Flight costs"], ["marketing", "Marketing"],
  ["loan", "Loan repayments"], ["sellBuy", "Purchases / sales"], ["other", "Other"],
] as const;

// The refresh token only grows on Reload, so a token this page has not seen
// yet means "re-read from the game"; a plain tab switch reuses the cache.
let seenRefreshToken = 0;

export function Finance({ refreshToken }: { refreshToken: number }) {
  const { data: f, error } = useApi(() => {
    const refresh = refreshToken !== seenRefreshToken;
    seenRefreshToken = refreshToken;
    return fetchFinance(refresh);
  }, [refreshToken]);

  if (error) return <ErrorState title="Finances unavailable" message={error} />;
  if (!f) return <LoadingState />;

  const { week, tax, ledger } = f;
  const periods = ["yesterday", "today", "tomorrow"] as const;

  return (
    <div className="finance-layout">
      <section className="fleet-summary">
        <div className="fleet-stat tone-green"><span>Cash</span><div><strong>{shortMoney(f.cash)}</strong><small>as of {dateTime(f.as_of)} · {f.mobile_calls ? `${f.mobile_calls} mobile calls` : "from cache"}</small></div></div>
        <div className="fleet-stat tone-cyan"><span>Valorization</span><div><strong>{shortMoney(f.valorization)}</strong><small>credit {f.credit_rating ?? EMPTY}</small></div></div>
        <div className="fleet-stat tone-green"><span>Structural profit / 7 days</span><div><strong>{signedShort(week.structural)}</strong><small>{signedShort(week.run_rate)} at current charges</small></div></div>
        <div className="fleet-stat tone-amber"><span>Next income tax</span><div><strong>{shortMoney(tax.next)}</strong><small>{tax.effective_pct}% of margin</small></div></div>
        <div className="fleet-stat tone-violet"><span>Loans outstanding</span><div><strong>{shortMoney(f.loans_total.remaining)}</strong><small>{shortMoney(f.loans_total.weekly)} / week</small></div></div>
      </section>

      <section className="flat-section">
        <SectionHeader kicker="Financial summary" title="Last 7 days" count={<>{wholeMoney.format(week.flights)} flight profit</>} />
        <div className="grid-table-wrap">
          <Table className="grid-table">
            <TableHeader><TableRow>
              <TableHead>Day</TableHead><TableHead className="is-numeric">Flight profit</TableHead><TableHead className="is-numeric">Maintenance</TableHead>
              <TableHead className="is-numeric">Salaries</TableHead><TableHead className="is-numeric">Margin</TableHead>
              <TableHead className="is-numeric">Tax + loans</TableHead><TableHead className="is-numeric">Structural profit</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {f.days.map((d) => (
                <TableRow key={d.date}>
                  <TableCell>{shortDate(d.date)}</TableCell>
                  <TableCell className="is-numeric"><Money value={d.flights} /></TableCell>
                  <TableCell className="is-numeric"><Money value={-d.maintenance} /></TableCell>
                  <TableCell className="is-numeric"><Money value={-d.salary} /></TableCell>
                  <TableCell className="is-numeric"><Money value={d.margin} /></TableCell>
                  <TableCell className="is-numeric"><Money value={-d.fixed} /></TableCell>
                  <TableCell className="is-numeric"><Money value={d.structural} bold /></TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell><strong>7 days</strong></TableCell>
                <TableCell className="is-numeric"><Money value={week.flights} bold /></TableCell>
                <TableCell className="is-numeric"><Money value={-week.maintenance} bold /></TableCell>
                <TableCell className="is-numeric"><Money value={-week.salary} bold /></TableCell>
                <TableCell className="is-numeric"><Money value={week.margin} bold /></TableCell>
                <TableCell className="is-numeric"><Money value={week.structural - week.margin} bold /></TableCell>
                <TableCell className="is-numeric"><Money value={week.structural} bold /></TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <p className="table-footnote">
          Structural profit = margin less a seventh of the week's income tax ({wholeMoney.format(week.income_tax_last)}),
          loan repayments ({wholeMoney.format(week.loans)}){week.rental ? ` and rent (${wholeMoney.format(week.rental)})` : ""}.
          Older days carry the charges in force then; at today's charges the week runs {wholeMoney.format(week.run_rate)}.
        </p>
      </section>

      <div className="finance-pair">
        <section className="flat-section">
          <SectionHeader kicker="Cash flow" title="Yesterday to tomorrow" />
          <div className="grid-table-wrap">
            <Table className="grid-table is-compact">
              <TableHeader><TableRow><TableHead />{periods.map((p) => <TableHead key={p} className="is-numeric">{p}</TableHead>)}</TableRow></TableHeader>
              <TableBody>
                {CASHFLOW_ROWS.map(([key, label]) => (
                  <TableRow key={key}><TableCell>{label}</TableCell>
                    {periods.map((p) => <TableCell key={p} className="is-numeric"><Money value={f.cashflow[p][key]} /></TableCell>)}
                  </TableRow>
                ))}
                <TableRow><TableCell><strong>Total</strong></TableCell>
                  {periods.map((p) => <TableCell key={p} className="is-numeric"><Money value={f.cashflow[p].total} bold /></TableCell>)}
                </TableRow>
              </TableBody>
            </Table>
          </div>
          <p className="table-footnote">Tomorrow is the game's own forecast.</p>
        </section>

        <section className="flat-section">
          <SectionHeader kicker="Taxes" title="Next income tax" count={tax.weekly_payment ? "paid weekly" : "paid thrice"} />
          <div className="grid-table-wrap">
            <Table className="grid-table is-compact">
              <TableBody>
                <TableRow><TableCell>Taxable margin (7 days)</TableCell><TableCell className="is-numeric"><Money value={tax.taxable} /></TableCell></TableRow>
                <TableRow><TableCell>Progressive brackets</TableCell><TableCell className="is-numeric"><Money value={tax.gross} /></TableCell></TableRow>
                <TableRow><TableCell>Tax credit</TableCell><TableCell className="is-numeric"><Money value={-tax.credit} /></TableCell></TableRow>
                <TableRow><TableCell>Cargo bonus</TableCell><TableCell className="is-numeric"><Money value={-tax.cargo_bonus} /></TableCell></TableRow>
                <TableRow><TableCell>Research discount {tax.discount_pct}%</TableCell><TableCell className="is-numeric"><Money value={tax.next - (tax.gross - tax.credit - tax.cargo_bonus)} /></TableCell></TableRow>
                <TableRow><TableCell><strong>Next income tax</strong><small>game says {wholeMoney.format(tax.next_game)}</small></TableCell><TableCell className="is-numeric"><Money value={tax.next} bold /></TableCell></TableRow>
              </TableBody>
            </Table>
          </div>
          <p className="table-footnote">
            Top bracket {tax.brackets[tax.brackets.length - 1]?.pct}% above {shortMoney(tax.brackets[tax.brackets.length - 1]?.min)};
            {" "}{tax.brackets.filter((b) => b.tax > 0).length} brackets in use, effective rate {tax.effective_pct}%.
          </p>
        </section>
      </div>

      <section className="flat-section">
        <SectionHeader kicker="Accounting book" title="By category" count={<><Money value={ledger.net_total} /> net over {ledger.dates.length} days</>} />
        <div className="grid-table-wrap">
          <Table className="grid-table">
            <TableHeader><TableRow>
              <TableHead>Category</TableHead>
              {ledger.dates.map((d) => <TableHead key={d} className="is-numeric">{shortDate(d)}</TableHead>)}
              <TableHead className="is-numeric">Total</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {ledger.rows.map((r) => (
                <TableRow key={r.key}>
                  <TableCell>{r.label}</TableCell>
                  {r.values.map((v, i) => <TableCell key={i} className="is-numeric">{v ? <Money value={v} /> : <span className="is-dim">{EMPTY}</span>}</TableCell>)}
                  <TableCell className="is-numeric"><Money value={r.total} bold /></TableCell>
                </TableRow>
              ))}
              <TableRow>
                <TableCell><strong>Net</strong></TableCell>
                {ledger.net.map((v, i) => <TableCell key={i} className="is-numeric"><Money value={v} bold /></TableCell>)}
                <TableCell className="is-numeric"><Money value={ledger.net_total} bold /></TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <p className="table-footnote">The first column is today so far.</p>
      </section>

      <section className="flat-section">
        <SectionHeader kicker="Banks" title="Loans" count={<>{f.loans.length} open · {shortMoney(f.loans_total.interest)} interest</>} />
        <div className="grid-table-wrap">
          <Table className="grid-table">
            <TableHeader><TableRow>
              <TableHead>Bank</TableHead><TableHead className="is-numeric">Borrowed</TableHead><TableHead className="is-numeric">Rate</TableHead>
              <TableHead className="is-numeric">Weekly</TableHead><TableHead className="is-numeric">Remaining</TableHead>
              <TableHead className="is-numeric">Repaid</TableHead><TableHead>Ends</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {f.loans.map((l) => (
                <TableRow key={l.id}>
                  <TableCell><strong>{l.bank}</strong><small>since {shortDate(l.issued)}</small></TableCell>
                  <TableCell className="is-numeric"><Money value={l.amount} /></TableCell>
                  <TableCell className="is-numeric">{l.rate}%</TableCell>
                  <TableCell className="is-numeric"><Money value={l.weekly} /></TableCell>
                  <TableCell className="is-numeric"><Money value={l.remaining} bold /></TableCell>
                  <TableCell className="is-numeric">{l.progress_pct}%</TableCell>
                  <TableCell>{shortDate(l.ends)}<small>{integer.format(l.weeks_left)} weeks</small></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <div className="grid-table-wrap">
          <Table className="grid-table">
            <TableHeader><TableRow>
              <TableHead>Bank</TableHead><TableHead className="is-numeric">Rate now</TableHead><TableHead className="is-numeric">Range</TableHead>
              <TableHead className="is-numeric">Express loan free</TableHead><TableHead className="is-numeric">Market loan max</TableHead>
              <TableHead className="is-numeric">Owed</TableHead><TableHead className="is-numeric">Term</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {f.banks.map((b) => (
                <TableRow key={b.name}>
                  <TableCell><strong>{b.name}</strong></TableCell>
                  <TableCell className="is-numeric">{b.rate}%</TableCell>
                  <TableCell className="is-numeric">{b.min_rate}–{b.max_rate}%</TableCell>
                  <TableCell className="is-numeric">{b.express_available ? <Money value={b.express_available} /> : <span className="is-dim">none</span>}</TableCell>
                  <TableCell className="is-numeric"><Money value={b.market_max} /></TableCell>
                  <TableCell className="is-numeric"><Money value={b.owed} /></TableCell>
                  <TableCell className="is-numeric">{b.weeks[0]}–{b.weeks[1]} wk</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="table-footnote">Read-only. Borrowing and early repayment stay in the game.</p>
      </section>

      <section className="flat-section">
        <SectionHeader kicker="Statement" title="Today and yesterday" count={<>{integer.format(f.statements.length)} transactions</>} />
        <div className="grid-table-wrap">
          <Table className="grid-table">
            <TableHeader><TableRow><TableHead>Time</TableHead><TableHead>Transaction</TableHead><TableHead>Category</TableHead><TableHead className="is-numeric">Amount</TableHead></TableRow></TableHeader>
            <TableBody>
              {f.statements.map((s) => (
                <TableRow key={`${s.id}-${s.date}`}>
                  <TableCell>{dateTime(s.date)}</TableCell>
                  <TableCell>{s.name}</TableCell>
                  <TableCell>{s.category}</TableCell>
                  <TableCell className="is-numeric"><Money value={s.amount} /></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="table-footnote">Every transaction of both days; each day's flights are folded into one "Flights of the day" line, as in the game's grouped view.</p>
      </section>
    </div>
  );
}
