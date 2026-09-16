# E2 / QLD dashboard: what to monitor and how to show it

Research note, 2026-09-16. Read-only study of `docs/dashboard/index.html` and `data.js`, plus `CONTEXT.md`, `AUDIT.md`, `log/*.jsonl` and `reference/*.csv`.
Labels used below:
- **[P]** primary source, quoted.
- **[S]** secondary source only.
- **[I]** my inference.
- **[D]** computed by me from repo data.

## TL;DR

1. **Question order.** A daily monitor answers these, in order: (a) is it running and are the data current, (b) what is the rule saying and how far is it from flipping, (c) is the position what the rule says, (d) how is it doing against QLD buy and hold, (e) is live still inside the backtest's range. Everything else goes below the fold. [I, framed by Few and Google SRE]
2. **Exceptions only.** Few: *"the only time that red appears is when something is wrong"* [P]. SRE: *"Every page should be actionable"* [P]. The page should be grey when healthy. Stale, HALT, gap, failure and drift outside the band get colour and a banner, and only when they occur.
3. **Add distance-to-flip.** Show px/SMA200 − 1 (today +9.1%) and vol20 / p90 (today 0.26 / 0.64 = 41%) as bullet graphs (Few's spec [P]). This replaces the pass/fail flowchart. [D]
4. **Add active return and drawdown.** Show E2 − QLD return (today −5.88% vs −5.95%, +0.06 pp), current and max drawdown with its duration, and an underwater strip. All of it comes from `CLOSE`. [D]
5. **Label Sharpe as noise.** Lo (2002): SE(SR) ≈ √((1+½SR²)/T) [P]. After 30 sessions an annualized Sharpe is noise, so either don't show it or show it with its CI. Showing it as a headline KPI would mislead. [I]
6. **Compare live with the backtest.** Use the `reference/e2_backtest_daily.csv` range: backtest vol 30.9% vs live 22.9%, backtest maxDD −37.9%, and the live 29-session return sits at the 17th percentile of backtest 29-session windows. [D] Carver compares live with backtest the same way (realized vs expected vol, costs) [P].
7. **Log fill prices.** Execution quality for MOC orders is fill rate plus (fill − official close). The fill rate caused every incident so far (partial or zero fills on 08-05 through 08-13). `data.js` has no fill price or filled quantity, so this is the most important data gap.
8. **Remove decoration.** Take out glows, pulses, gradients, the radial background, `▮ ◆ ✓ ✗ △` glyphs, rise-in animations, the violet/cyan brand colours, the "LIVE" lamp on a paper account, the SHA-256 column above the fold and the scrolling feeds. Few's pitfall #11 is *"Cluttering the Screen with Useless Decoration"* [P].
9. **Typography.** Use a proportional UI face with `tabular-nums lining-nums` [P, MDN] and right-aligned numbers [P, GOV.UK]. Use a true minus sign (U+2212), fixed decimals per metric, and signed deltas. Keep monospace only for hashes and IDs.
10. **Fix a stale claim now.** The Limitations block still says *"Equity is a 12:00 ET mark, not the official close"* (index.html, `renderLimits`). Commit 4f5965e moved the comparison to official closes, so the claim is now false. [observed]

---

## 1. What PMs and risk managers monitor

| Area | What's monitored | Source basis |
|---|---|---|
| Returns vs benchmark | Time-weighted return, benchmark return, active return | GIPS 2020 1.A.35: *"The firm must present time-weighted returns unless certain criteria are met."* It also says the benchmark *"must reflect the investment mandate… must not use a price-only benchmark."* [P] The repo's `qld_tr` is total-return, which complies. |
| Active risk | Tracking error = std(rP − rB); IR = active return / TE | Grinold & Kahn via study notes: *"Active risk = Std (active return) = Std(rP – rB)"* [S; book not accessed]. quantstats computes IR as *"active return … divided by the tracking error"*, not annualized [P, `quantstats/stats.py`]. |
| Risk-adjusted stats | Sharpe, Sortino, annual vol, max DD, Calmar, tail ratio | empyrical: `APPROX_BDAYS_PER_YEAR = 252`. Sharpe = mean/std(ddof=1)·√252. Tail ratio = \|p95\|/\|p5\| [P, `empyrical/periods.py`, `stats.py`]. pyfolio's `SIMPLE_STAT_FUNCS` lists annual_return, annual_volatility, sharpe, calmar, max_drawdown, sortino, tail_ratio, VaR, and adds daily turnover and gross leverage when positions are given [P, `pyfolio/timeseries.py`]. |
| Drawdown | Depth, duration, underwater curve | pyfolio has `plot_drawdown_underwater` and a drawdown table with a `Duration` column [P]. quantstats reports "Max Drawdown %" and "Longest DD Days" [P]. |
| Rolling behaviour | Rolling vol, rolling beta, rolling Sharpe | pyfolio: `returns.rolling(w).std() * np.sqrt(252)`. Rolling beta uses a 126-day default [P]. |
| Statistical meaning | Sharpe SE, minimum track record | Lo 2002: *"SE(SR) ≈ √((1 + ½SR²)/T)"*, and serial correlation can overstate annual SR *"by as much as 65 percent"* [P]. Bailey & López de Prado: *"if a track record is shorter than MinTRL, we do not have enough confidence that the observed SR is above the designated threshold"* [P]. |
| Backtest vs live | Is live inside the backtest's range? | Carver's year-review posts compare realized with expected vol and live with backtest costs. He writes *"expected vol is less than realised vol it would seem"* and *"A year of data on top of a 50 year backtest is meaningless"* [P, qoppac 2023-05]. López de Prado says repeated testing *"will likely lead to a false discovery"* [P, SSRN 3104816]. No primary "strategy is broken" test from Carver was found. |
| Execution | Implementation shortfall; slippage against the benchmark price | Perold 1988's own wording is paywalled. It is commonly summarized as *"the difference between the calculated performance of a paper portfolio and the actual performance of an identical real portfolio"* [S]. For MOC orders the natural benchmark is the official close. Nasdaq: *"execute the cross at a single price called the Nasdaq Official Close Price"* [P]. NYSE Arca Rule 1.1: *"the Official Closing Price is the price established in a Closing Auction of one round lot or more"* [P]. QLD's listing venue was not verified. |
| Risk platforms | TE, VaR, scenarios, attribution | The Bloomberg PORT brochure lists *"Tracking Error », Value-at-Risk », Scenario Analysis », Performance Attribution"* [P, marketing]. MSCI/Axioma docs were not reviewed. |
| Ops health | Staleness, missed runs, halts | SRE book: *"what's broken, and why?"*, and alerts should be *"urgent, actionable, and actively or imminently user-visible"* [P]. |

What matters for a one-asset, three-state rule [I]:
- Factor exposures, VaR and attribution add almost nothing. Exposure is just realized allocation × 2x Nasdaq-100.
- What matters is:
  - signal proximity to a flip
  - realized vs signal allocation (drift)
  - fill completeness
  - active return vs QLD
  - drawdown
  - whether live vol and returns sit inside the backtest distribution
  - ops liveness

## 2. Presentation principles

**Single screen, at a glance.**
- Few: *"A dashboard is a visual display of the most important information needed to achieve one or more objectives; consolidated and arranged on a single screen so the information can be monitored at a glance."* [P, Rich_Data_Poor_Data.pdf]
- His pitfalls list opens with *"Exceeding the boundaries of a single screen"*, *"Supplying inadequate context for the data"* and *"Displaying excessive detail or precision"* [P, Common_Pitfalls.pdf].

**Colour is for exceptions.**
- Few: *"For highlighting to work, it must be used sparingly"*, and a bright colour's attention effect *"diminishes to the degree that other bright colors are also present"* [P].
- The current page uses six saturated hues all the time, so an amber HALT has to compete. [observed + I]

**Encode quantities as position or length.**
- Few: *"Only two of the preattentive attributes can be accurately used to encode quantitative values: 2-D location … and line length."* [P]
- The bullet graph was *"developed to replace the meters and gauges… linear and no-frills"*. It has five parts: label, scale, featured measure, comparative measure, and *"two to five ranges"* [P, Bullet_Graph_Design_Spec.pdf].

**Sparklines.**
- Tufte: *"A sparkline is a small intense, simple, word-sized graphic with typographic resolution."* [P, edwardtufte.com, via summarizer]
- The data-ink ratio (*"proportion of a graphic's ink devoted to the non-redundant display of data-information"*) was confirmed only through a NASA report paraphrase [S].
- No primary text for small multiples was found online.

**Numbers.**
- MDN: `tabular-nums` means *"numbers are all of the same size, allowing them to be easily aligned like in tables"* [P].
- Butterick: tabular figures are *"essential for one purpose: vertically aligned columns"* [P]. He accepts an en dash as a minus in tables [P]. A primary source recommending U+2212 was not found; preferring it is my choice [I].
- GOV.UK: *"align the numbers to the right in table cells"* [P].

**Red/green and colour blindness.**
- Okabe & Ito: red and green of similar brightness are hard to tell apart, so *"use magenta (purple) instead of red"* [P].
- Datawrapper (reader quote): *"The only bulletproof solution is to encode your data with a second visual variable: position, shape, patterns"* [P].
- Bloomberg's accessibility story reportedly uses *"a blue and red scheme for 'up' and 'down'"* while keeping amber for non-semantic information, with deuteranopia and protanomaly schemes available [P, but seen only as a search snippet because bloomberg.com returned 403; not verified word for word].

**Status indicators.**
- Carbon: status indicators *"should rely on at least two of the following elements: color, shape, or symbol"*, with *"at least a 3:1 contrast"* [P]. The same page also says three elements are needed for WCAG, which is internally inconsistent.
- WCAG 1.4.11 requires 3:1 for graphical objects, and 1.4.3 requires 4.5:1 for text [P].

**Hierarchy and sprawl.**
- Grafana: *"A dashboard should tell a story or answer a question"*, *"Dashboards should reduce cognitive load, not add to it"*, and use *"Hierarchical dashboards with drill-downs"* [P].

**Motion.**
- WCAG 2.3.3: *"Some users experience distraction or nausea from animated content"* [P].
- No dashboard-specific primary source against animation was found. The case against pulses rests on Few's decoration pitfall and the exceptions-only principle. [I]

**Dark theme.**
- Material: base surface *"typically a dark gray with the hex value #121212"* [P, codelab].
- Carbon: sequential palettes flip lightness direction between light and dark themes [P].
- No primary source says dark beats light for data displays.

**Not found or unverified:**
- the Bloomberg typeface story
- the Economist style guide PDF (blocked; a secondary source says horizontal gridlines only)
- FT gridline guidance (the FT Visual Vocabulary README was confirmed, but it covers chart choice only)
- quotable Datadog design guidance

## 3. Recommendations for this dashboard (prioritized)

### P0: correctness and signal-to-noise
1. **Fix the false limitation.** The "12:00 ET mark" line is wrong since 4f5965e. The equity *KPI* reads `CLOSE`, but `TRD.equity` in the blotter is still the noon mark. Label that column "equity @ run".
2. **Remove the "LIVE" lamp.** Replace it with a neutral `PAPER` text label. Status gets colour only when abnormal.
3. **Make exceptions conditional.** Show one full-width banner slot above everything, empty when healthy. Priority order: `HALT active` > `STALE` > `FAILURE today` > `GAP (n)` > `DRIFT > band` > `ORDER NOT FULLY FILLED`.
   - Each banner has an icon, a word and a colour (Carbon's two-element rule), a date, and a one-line action pointer ("RUNBOOK §x").
   - When healthy, show one grey line: `OK · last run 2026-09-16 16:06 UTC · n/N sessions logged`.
   - Note: today's `COVERAGE` has 2 `gap` entries (08-18, 09-07). 09-07 was Labor Day, so it is likely a holiday false positive unless it is logged as market-closed. Verify before making gaps red. [observed, I]
4. **Strip decoration:**
   - `radial-gradient` body background
   - panel `linear-gradient`s
   - `.kpi::before` colour bars
   - `box-shadow` glows
   - `@keyframes pulse` and `rise`
   - the `▮ ◆ ✓ ✗ △ ◦ !` glyphs
   - the violet `.alloc-out` block
   - the connector gradients
   - 8px radii (use 0–2px)
   - uppercase letter-spaced micro-labels at 9–9.5px, which are below comfortable reading size

   Borders become 1px hairlines or whitespace only.

### P1: information hierarchy (above the fold, about 1280×800)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ E2 · QLD   paper                        as of Tue 2026-09-15 close · run 16:06 UTC │
│ [exception banner — only if HALT / STALE / FAILURE / GAP / DRIFT / PARTIAL FILL]   │
├───────────────┬───────────────┬───────────────┬───────────────┬──────────────────┤
│ SIGNAL        │ REALIZED      │ E2 SINCE BASE │ QLD B&H       │ DRAWDOWN         │
│ 100%          │ 99.6%  1,078sh│ −5.88%        │ −5.95%        │ −7.9%  max −7.9% │
│ no change live│ tgt 1,082 ±1% │ active +0.06pp│ same closes   │ 22 sess from peak│
├───────────────┴───────────────┴───────────────┴───────────────┴──────────────────┤
│ DISTANCE TO FLIP (bullet graphs, grey ranges, one black marker)                    │
│ Trend   px/SMA200 −1   ├──────────0────────■───────────┤  +9.1%   flips at 0       │
│ Vol     vol20 / p90    ├──────■─────────────┼──────────┤  0.26 / 0.64 → 50% at 1.0 │
│ (if trend false:  px/SMA20 −1  and  vol20 / 0.9·volmax60  for re-entry)            │
├──────────────────────────────────────────────────┬───────────────────────────────┤
│ EQUITY vs QLD B&H, indexed 100 @ 2026-08-04       │ LIVE vs BACKTEST               │
│ two thin lines, E2 dark, QLD grey; no dots        │           live   backtest      │
│ allocation step strip underneath (0/50/100)       │ ann vol   22.9%  30.9%         │
│ underwater strip underneath (drawdown %)          │ max DD    −7.9%  −37.9%        │
│ segment boundaries as vertical hairlines          │ 29d ret   −5.9%  p17 of dist.  │
│                                                   │ Sharpe    n/a (T=29; SE≈±X)    │
└──────────────────────────────────────────────────┴───────────────────────────────┘
below fold: blotter (orders only, with fill qty/px when logged) · signal table ·
ops log (collapsed; failures/manual/halt only) · coverage calendar · provenance · limitations
```

Why this order:
- **Signal and position first.** The rule's decision and whether the account matches it are the only controllable facts.
- **Performance next.** It is relative to QLD because that is the stated comparison.
- **Distance to flip.** It tells the PM what tomorrow could look like.
- **Backtest range last.** It answers "is it broken?" [I]

### P1: metrics to add (all supported by current data)

| Metric | Formula | From |
|---|---|---|
| Distance to trend flip | px/sma200 − 1 | `SIG` |
| Vol headroom | vol / hi (flip to 50% at ≥1) | `SIG` |
| Re-entry distance (when out) | px/sma20 − 1; vol / (0.9·volmax60) | `SIG`, matching existing `renderRule` logic |
| Realized allocation, drift | curQty·refPx / equity; vs alloc; band 1% | `TRD` (noon mark; label as such) |
| Active return | (eq_T/eq_0 − 1) − (qldTr_T − 1) | `CLOSE` |
| Drawdown now / max / duration | running peak on `CLOSE.equity` | `CLOSE` |
| Realized vol (since base; 20d once n ≥ 20) | std(daily r, ddof=1)·√252 (empyrical convention) | `CLOSE` |
| Tracking error | std(r_E2 − r_QLD)·√252 (today 8.6%) | `CLOSE` |
| Days since last signal change / last order | — | `SIG_DAYS`, `TRD` |
| Backtest range | vol, maxDD, percentile of live N-day return among rolling backtest N-day windows | `reference/e2_backtest_daily.csv` (not currently in data.js; the build script would need to emit aggregates) |

Don't headline Sharpe, Sortino, Calmar or IR while n < about 250 sessions.
- If shown, show them with Lo's SE. For example, SR = 1 over 1 year of daily data gives SE ≈ 1.2 annualized [P, derived].
- Tail ratio and VaR need far more observations. [I]

### P2: charts
- **Main chart.**
  - Indexed-to-100 lines, not dollars: the two series share a base, so the index is honest and the axis stays short.
  - Remove the per-point circles.
  - Label line ends directly instead of using a legend.
  - Draw horizontal gridlines only, at round values. This follows the Economist convention, which is secondary-sourced.
- **Allocation strip.** A step chart (0/50/100) under the equity chart sharing the x-axis, so a de-risking is visible against price.
- **Underwater strip.** A thin filled area below 0, grey. It turns colour only past a threshold, e.g. worse than the backtest p90 drawdown for that horizon. [I]
- **Signal table sparklines.** Inline sparklines for px/SMA200 and vol/p90 on the last 60 sessions. [Tufte P]
- **Coverage.** A GitHub-style calendar row of small squares. Grey means logged, hollow means market closed, colour appears only for a gap. No glyphs inside the squares.

### P2: type, grid, palette, numbers
- **Grid.**
  - 12 columns, max-width about 1280px, 24px gutters, 8px spacing unit.
  - KPI row is 5 equal cells. Chart is 8 columns and the backtest table 4.
  - Stacks to a single column under 900px.
- **Type.**
  - One proportional family with good tabular figures (system UI or Inter) plus `font-feature-settings: "tnum","lnum"`.
  - Scale: 12 (labels, sentence case, no letter-spacing), 14 (body and tables), 20 (secondary values), 28 (KPI values).
  - Two weights only (400/600).
  - Monospace only for SHA and order IDs.
- **Numbers.**
  - Right-align.
  - Fixed decimals per metric: returns 2dp, pp 2dp, vol 1dp %, allocation 0dp % (1dp for realized), prices 2dp, dollars 0dp in KPIs and 2dp in tables.
  - Always sign deltas (+/−) and use U+2212 for minus.
  - Never mix "%" and "pp" for the same concept.
  - Dates are ISO `YYYY-MM-DD` everywhere; drop the "Sep 16, 2026" and "09/16" mix.
  - Times carry a zone.
- **Palette.** Near-monochrome, with semantic colour reserved for state.

  | token | light | dark |
  |---|---|---|
  | bg | #FFFFFF | #121212 (Material [P]) |
  | surface/rule | #F4F4F4 / #E0E0E0 | #1C1C1C / #2E2E2E |
  | text / secondary | #161616 / #6F6F6F | #EDEDED / #9A9A9A |
  | series E2 / QLD | #161616 / #A8A8A8 | #EDEDED / #6F6F6F |
  | negative | #B8003C-ish magenta-red | #FF6B8B |
  | positive | #0F62FE-ish blue | #78A9FF |
  | warning (HALT) | #8A5A00 on #FFF4D6 | #F1C21B |
  | critical (STALE/FAIL/GAP) | #A2191F on #FFEBEB | #FF8389 |

  Notes on the palette:
  - Up/down uses blue and magenta-red, not green and red, following Okabe-Ito and the reported Bloomberg CVD scheme.
  - Up/down colour applies to sign-bearing numbers only, not to labels.
  - Check every pair for 4.5:1 text contrast and 3:1 graphic contrast (WCAG). The hex values are starting points, not verified ratios. [I]
  - Implement the theme with `prefers-color-scheme`.

### P3: remove or demote
- **Remove:**
  - The "nothing on this page is estimated" and "mechanical output — same inputs, same number" copy. This is marketing tone; the audit claim belongs in provenance.
  - Raw `SIG` duplicate runs in the main signal table. Show `SIG_DAYS` and put the determinism evidence in provenance as "n runs, all hashes agree".
- **Move below the fold:** go-live hero, limitations, provenance hashes.
- **Collapse by default:** the ops feed. Show only `failure`, `manual`, `halt` and `resume`, and hide `note` and `market-closed` unless expanded.

## 4. Data gaps (what the data cannot honestly support)

| Wanted | Status | Fix (a human decision; engine and logs are protected) |
|---|---|---|
| **Fill qty and avg price per order; MOC slippage vs official close; fill rate** | Missing. Fills appear only as free text in `OPS` notes (e.g. "filled_qty=109 of 1081 @ 90.79"). | Add an order-outcome record in a new read-only log (e.g. in `close_mark.py`, which is a script, not the engine) from Alpaca `filled_qty` and `filled_avg_price`. Store the official close alongside. |
| Realized allocation at the close | Only at the noon run (`TRD.curQty·refPx/equity`). The close log lacks qty. | Add `qty` to `close_log.jsonl` going forward. |
| Live vs backtest *same-day* reconciliation | `reference/` ends 2026-07-28; live starts 08-04, so no overlap. | Only a distributional comparison is possible. A day-by-day comparison would need a re-run of the frozen rule, which counts as a research action. |
| Backtest aggregates in the page | Not in `data.js`. | Have `build_dashboard_data.py` emit derived aggregates only (vol, DD percentiles, N-day return quantiles). |
| Meaningful Sharpe/IR/Sortino | T = 29 daily returns. SE dominates. | Wait; show with CI or omit. |
| Segment boundaries | Implied by `OPS` `manual` events (08-07, 08-10 resets), not an explicit field. | Derive from `manual` events, or add an explicit segment marker. CONTEXT.md forbids comparing curves across segments, so the chart should mark them. |
| Holiday calendar | Staleness and gap logic infer holidays from `market-closed` ops records. An unlogged holiday reads as a gap (probably 09-07). | Emit the exchange calendar from the build script. |
| Turnover, costs | Derivable only from fills, which are missing. Paper has no commissions. | Follows from the fill log. |
| Environment/library version per signal | Not recorded (AUDIT.md §4). | Already planned. |

## Sources

Primary:
- empyrical `periods.py`, `stats.py`: https://github.com/quantopian/empyrical
- pyfolio `timeseries.py`, `tears.py`: https://github.com/quantopian/pyfolio
- quantstats `stats.py`, `reports.py`: https://github.com/ranaroussi/quantstats
- GIPS 2020 for firms: https://www.gipsstandards.org/wp-content/uploads/2021/03/2020_gips_standards_firms.pdf
- Lo (2002), The Statistics of Sharpe Ratios: https://traders.studentorg.berkeley.edu/papers/The-Statistics-of-Sharpe-Ratios.pdf
- Bailey & López de Prado, Deflated Sharpe Ratio: https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
- Bailey & López de Prado, Sharpe Ratio Efficient Frontier (MinTRL): https://www.davidhbailey.com/dhbpapers/sharpe-frontier.pdf
- López de Prado, The 10 Reasons Most ML Funds Fail: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3104816
- Carver, year-review post: https://qoppac.blogspot.com/2023/05/trading-and-investing-performance-year.html
- Nasdaq Closing Cross FAQ: https://www.nasdaqtrader.com/content/productsservices/Trading/ClosingCrossfaq.pdf
- NYSE Arca Rule 1.1: https://www.nyse.com/publicdocs/nyse/regulation/nyse-arca/NYSE_Arca_Rule_1.1.pdf
- Bloomberg PORT brochure (marketing): https://data.bloomberglp.com/professional/sites/4/Portfolio_and_Risk_Analytics_Brochure4.pdf
- Few, Rich Data Poor Data: https://www.perceptualedge.com/articles/Whitepapers/Rich_Data_Poor_Data.pdf
- Few, Common Pitfalls: https://www.perceptualedge.com/articles/Whitepapers/Common_Pitfalls.pdf
- Few, Bullet Graph Design Spec: https://www.perceptualedge.com/articles/misc/Bullet_Graph_Design_Spec.pdf
- Few, visual perception article: https://www.perceptualedge.com/articles/ie/visual_perception.pdf
- Tufte, sparklines: https://www.edwardtufte.com/notebook/sparkline-theory-and-practice-edward-tufte/
- MDN font-variant-numeric: https://developer.mozilla.org/en-US/docs/Web/CSS/font-variant-numeric
- MDN prefers-reduced-motion: https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion
- Butterick, alternate figures: https://practicaltypography.com/alternate-figures.html
- Butterick, hyphens and dashes: https://practicaltypography.com/hyphens-and-dashes.html
- GOV.UK Design System tables: https://design-system.service.gov.uk/components/table/
- Okabe & Ito: https://jfly.uni-koeln.de/color/
- Datawrapper colorblindness series: https://www.datawrapper.de/blog/colorblindness-part2/
- FT Visual Vocabulary: https://github.com/Financial-Times/chart-doctor/tree/main/visual-vocabulary
- Carbon status indicator pattern (source): https://github.com/carbon-design-system/carbon-website/blob/main/src/pages/patterns/status-indicator-pattern/index.mdx
- Carbon data-viz colour palettes (source): https://github.com/carbon-design-system/carbon-website/blob/main/src/pages/data-visualization/color-palettes/index.mdx
- Grafana dashboard best practices: https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/best-practices/
- Google SRE book, Monitoring Distributed Systems: https://sre.google/sre-book/monitoring-distributed-systems/
- WCAG 2.2 Understanding 2.3.3 (animation): https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html
- WCAG 2.2 Understanding 1.4.3 (contrast): https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
- WCAG 2.2 Understanding 1.4.11 (non-text contrast): https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html
- Material dark theme codelab: https://codelabs.developers.google.com/codelabs/design-material-darktheme

Primary but not verified word for word (403):
- Bloomberg, Designing the Terminal for color accessibility: https://www.bloomberg.com/company/stories/designing-the-terminal-for-color-accessibility/
- Bloomberg, How Terminal UX designers conceal complexity: https://www.bloomberg.com/company/stories/how-bloomberg-terminal-ux-designers-conceal-complexity

Secondary only:
- Perold IS definition: https://www.quantitativebrokers.com/blog/a-brief-history-of-implementation-shortfall
- Grinold & Kahn definitions: https://people.brandeis.edu/~yanzp/Study%20Notes/Active%20Portfolio%20Management.pdf
- Tufte data-ink paraphrase: https://www.nas.nasa.gov/assets/nas/pdf/techreports/1994/nas-94-002.pdf
- Economist style: https://medium.com/data-science/making-economist-style-plots-in-matplotlib-e7de6d679739

Not found:
- Carver "strategy broken" test
- Economist style guide PDF (primary)
- FT gridline guidance
- Datadog design guidance
- Bloomberg typeface story
- a primary source recommending U+2212

Repo data ([D] figures above, computed 2026-09-16):
- `log/close_log.jsonl` (30 sessions, 2026-08-04 → 09-15)
- `reference/e2_backtest_daily.csv` (`New_ret`, 4,784 returns, 2006-06-21 → 2026-07-28)
