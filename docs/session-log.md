# Session log

**Keep this file under 100 lines.** Newest entry at the top; delete anything
below the third. It exists to resume unfinished work, not to be a history — git
log already holds that, at no cost to context.

Do not write here what belongs elsewhere: a durable source constraint goes to
`source-gotchas.md`, a convention goes to `CLAUDE.md`, a completed change is
described by its own commit message.

Format: one entry per session, four short lines.

---

## 2026-09-03

**State.** Seven tabs live: brief, chart, stocks, bds, signals, gex, flows.
Warehouse holds 1.44M quarterly fundamentals (2018-Q1→2026-Q2), 264 trading days
across 879 tickers, 47,343 property listings, 679 days of ETF flow.

**Verified.** GEX cross-checked against a live third-party feed — gamma flip
66,710 vs 66,379, max pain identical. SSI and Vietcap agree to 0.0007% on
market-wide foreign net. ETF parser reconciles 650/650 days against the source's
own Total column.

**Open.** Not built: replay/training, quant backtester, wealth tracker, wiki.
Repo is local-only — no GitHub remote, so no cron is running yet.

**Next.** Push to GitHub + enable Pages so the five workflows start; then decide
between the remaining four tools.
