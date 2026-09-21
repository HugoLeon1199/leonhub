# Chart decision panel: BTC / ETH / XAU

User requirement recorded 2026-09-21. This is the current product target for the
next implementation session. Read `CLAUDE.md` and `HANDOFF.md` first.

## Requested outcome

Opening `https://leonquant.com/hub/?tab=chart` should immediately show a current
assessment for **BTC, ETH and XAU**: **MUA / BAN / CHO** (Vietnamese UI accents:
**MUA / BÁN / CHỜ**). Each assessment must explain its technical and macro reasons
using the actual data collected or fetched by LEON, including source and age.

The user wants a clear decision summary, not a collection of indicators that
they must interpret unaided. The intended use is deciding when to participate
with the existing EA, preparing for breakouts, and waiting for pullbacks after
an extended move. This does not request automatic order placement.

This requirement supersedes an observer-only dashboard as the final product.
Observation/shadow mode remains a development and validation stage; the target
is an explicit, evidence-backed MUA / BÁN / CHỜ conclusion.

## Work already completed

- Research and source audit:
  [2026-09-21 report](trend-gate-research-2026-09-21.md).
- Reproducible bounded read-only audit:
  [audit.py](research/trend-gate-2026-09-21/audit.py).
- Timestamped public-data/source/report evidence:
  [evidence.json](research/trend-gate-2026-09-21/evidence.json).
- Checked Chart/Hub production HTML against local source: matched at the audit
  time. Read the actual BTC/ETH/XAU EA branches in `D:\MT5\10pair_edit`.
- Identified indicator reuse, missing broker XAU adapter, H3/H6 interval gaps,
  closed-bar/MTF-refresh gaps, mixed EA entry families and stale context feeds.
- Recorded the validation and execution boundaries in the research report.

**Not completed:** decision engine, panel UI, broker data connection, refreshed
context pipeline, signal history, gated-EA comparison, forward validation or
deployment. The public Chart does not yet contain this requested feature.

## Visible behavior

Show three compact cards above or beside the chart, one per asset. All three
assessments are visible without manually changing symbol. Selecting a card
opens that asset in the existing chart and its detailed explanation. Preserve
normal chart tools and symbol/timeframe navigation.

Each card/detail must show:

| Field | Required meaning |
|---|---|
| Asset and instrument | BTC/ETH/XAU plus actual venue/symbol; distinguish XAUUSD from COMEX or tokenized gold |
| Action | MUA / BÁN / CHỜ |
| Current setup/state | Breakout, trend pullback, compression, choppy range, extension or unavailable data |
| Horizon | The decision timeframe and context timeframe; do not imply an unqualified direction across all horizons |
| Evaluation time | When the decision was calculated, the last completed decision bar, and price observation time |
| Technical explanation | Numeric inputs, structural levels, rule thresholds and why they pass/fail |
| Macro/context explanation | Applicable collected figures, whether supportive/opposing/mixed/unavailable, and observation dates |
| What changes the verdict | Specific confirmation or invalidation condition; for CHỜ, what to wait for |
| Data status | Per-source freshness, missing sources and venue mismatch |
| EA relevance | Which branch/family the setup applies to; no universal permission for every PP |

Use Vietnamese user-facing copy. Keep formulas and source details expandable
so the initial view remains readable. Numeric inputs must be accessible without
depending on color or an unexplained score.

## Action semantics

- **MUA:** the versioned long-entry conditions are satisfied using eligible data
  at the displayed decision time, and required data/execution checks pass.
- **BÁN:** the corresponding short-entry conditions are satisfied. In this EA
  context it means a short setup, not an instruction to liquidate every long.
- **CHỜ:** no qualifying setup, conflicting required conditions, excessive
  extension, event/execution restriction, or missing/stale mandatory inputs.
  Say which one. Missing data must never be described as measured sideways.

An upward trend alone must not create MUA if price is already too extended or
there is no entry setup. A compressed range can show **CHỜ — chờ phá biên** and
the two relevant levels. An upward trend awaiting a retracement can show
**CHỜ — chờ hồi trong xu hướng tăng**. The early-breakout path must not be
blocked automatically by an established-trend ADX requirement.

Macro context must influence the explanation and, where justified by an
explicit tested rule, permission. It must not be decorative prose. Conversely,
not every slow or missing optional macro series should force permanent CHỜ:
declare required versus optional inputs in the rule version, omit ineligible
optional factors from the calculation, and show what was unavailable.

## Use the data we actually have

| Input | Intended contribution | Handling |
|---|---|---|
| Completed OHLC, EMA/ATR/ADX, efficiency/Choppiness, range levels | Direction, strength, compression, breakout, extension | Core decision data; match instrument and timeframe |
| BTC/ETH ETF net flows | Slower demand context, latest daily and rolling sums | Show actual session dates; exclude stale values from a current verdict |
| Funding and OI | Positioning/crowding context | A change requires multiple timestamped samples; a single snapshot is not an OI trend |
| GEX/flip/strikes | Optional options-related level context | Display model assumptions, source and age; no unconditional GEX-sign action rule |
| CVD/taker flow/Big Tape | Optional participation evidence | Show venue and coverage window; tab-only history cannot become all-day flow |
| DXY and COMEX daily gold | Macro reference context, particularly for XAU | Delayed daily values are not broker execution quotes |
| Collected crypto context such as MVRV/Fear & Greed | Slow context where relevant | Source-specific observation dates; not an intraday direction shortcut |
| CPI/NFP/FOMC calendar | Event timing and possible entry restrictions | Requires a calendar source; do not imply an existing collector already supplies it |

Only use data relevant to the asset. Existing VN equity or real-estate data do
not become BTC/ETH/XAU evidence merely because they are in the warehouse.
Do not generate explanations from current news headlines without referenced
facts. Prefer deterministic text derived from the same values/rules that
produced the action. If narrative generation is added, it cannot invent inputs,
override the action, or conceal contradictory evidence.

Explanation templates use placeholders, not fabricated example market values:

- Technical: `ADX({period}) = {value}; ngưỡng {threshold}. Nến {tf} đóng
  {close} so với biên {level}; khoảng cách {distance_atr} ATR.`
- Macro: `ETF {asset}: {net_flow} trong phiên {session}; tổng {window} phiên
  {sum_flow}. Nguồn {source}, quan sát đến {as_of}.`
- Counterevidence: `Yếu tố trái chiều: {factor} = {value}, quy tắc {rule}.`
- Wait: `CHỜ — {reason}. Đánh giá lại khi {condition}.`

Keep measured inputs separate from inferred implications. Do not label an
uncalibrated aggregate score as a probability of winning.

## Implementation order

1. Refresh the source inventory and health. The Sep 21 audit found ETF sessions
   ending Sep 4 and crypto context built Sep 8; these are dated findings, not
   timeless current status. Trace/update permitted collectors and preserve
   honest failure states before consuming the data in live explanations.
2. Establish the XAUUSD broker adapter and exact asset identities. Until then,
   show XAU **CHỜ — thiếu dữ liệu XAUUSD của broker**. Do not silently replace it
   with GC=F/PAXG/XAUT. Align all required native EA timeframes and bar closes.
3. Implement a pure versioned decision evaluator returning action, direction,
   setup, horizon, technical/context evidence, reasons, invalidation, timestamps
   and quality flags. Fix MTF freshness/closed-bar handling; reuse verified
   indicator formulas. Apply the small experiment set from the research report.
4. Implement the three-card panel and evidence drawer on the existing Chart
   route. Refresh decisions on relevant bar closes and source updates. Prevent
   a delayed response for one symbol from overwriting another symbol's verdict.
5. Persist evaluation snapshots and denied/allowed opportunities for replay.
   Test correctness and full-EA outcomes at fixed sizing; then collect locked
   forward observations/demo evidence. A label appearing in the UI is not
   proof that its trading rules are profitable.
6. Verify mobile/desktop layout, all three assets, source outages, stale data,
   incomplete bars, timeframe transitions and rule parity. Run relevant repo
   syntax/data gates and record deployment status explicitly.

## Acceptance criteria for the feature

- Opening Chart shows BTC, ETH and XAU with one explicit action each, including
  an honest CHỜ while required data is missing or being loaded.
- Every displayed explanation traces to actual values with source, observation
  time and the exact rule version. No synthetic macro narrative or hidden stale
  fallback is accepted.
- Buying/selling permission is distinguishable from a bullish/bearish context.
  WAIT includes a concrete reason and the next condition to observe.
- Closed bars and historical as-of joins prevent future data from affecting a
  past decision. Missing optional context is visible and never silently zeroed.
- A verdict can be replayed from its stored evidence. Simultaneous panels and
  chart navigation remain consistent with instrument/source identity.
- No EA/account settings or orders are changed by viewing this panel. A future
  EA integration must separately gate entries while preserving management.

The three assets are the requested product scope. This is not evidence to
increase position sizes, reallocate the ten-asset risk budget, or declare
one common trend gate suitable for all of their PP branches.
