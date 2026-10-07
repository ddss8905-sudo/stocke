# Early Bottom Reversal (v1)

This is a price-based screen inspired by Jeffrey Neumann's interview in Jack
Schwager's *Unknown Market Wizards* (Korean title: 초격차 투자법), not a complete
replica of his discretionary investing. The author's interview discusses nascent
themes, product research, chart timing and tight risk:
https://static1.squarespace.com/static/5325c4b3e4b05fc1fc6f32ed/t/603e0883a9ba4e0ef0524548/1614678148201/2021-02-28_JackSchwager.pdf

The book describes connecting progressively lower swing highs and buying a
descending-trendline breakout earlier than a horizontal-base breakout. It also
requires sector, catalyst and clean share structure. This application DOES NOT
verify catalysts, warrants, convertible securities or share dilution.

## Operational Definitions

All numbers below are application design choices, NOT rules quoted from the book.
The liquid universe is the same as trend following. Both analyses run together.
History extends to 800 calendar days; analysis uses at most 504 trading sessions
and needs at least 260 valid daily bars. No minimum 12-month RS rank or price
above the 200-day average is imposed on reversal candidates.

- Downtrend: negative log-price slope over sessions -126 through -21.
- Resistance: confirmed swing highs (five bars on each side), falling at least
  15%, anchors at least 63 sessions apart and first at least 125 sessions old.
  Second anchor is within 84 sessions. At least 95% of intervening highs must be
  no more than 2% above the line. Reject a closing breakout before the latest
  ten-session setup. Prefer the longest qualifying line, then latest second pivot.
- Decline: at least 25% from the anchor-period high to the latest 63-session low.
- Base: latest ten-session low >= 98% of the preceding twenty-session low;
  rebound 4-40%, close above MA20, MA20 rising versus five sessions earlier.
- Setup: price within -5%/+15% of descending resistance. If already above the
  1% breakout buffer, a fresh crossing must have occurred within ten sessions.
- Today's confirmed signal: fresh crossing of resistance +1%, volume >=1.5x
  the PREVIOUS twenty-session mean, close in the upper half of today's range,
  extension <=5%, structural stop risk <=10%, regime score >=40.
- Stop: latest ten-session low minus 0.5 ATR14. Never move a structural stop
  upward just to satisfy a risk limit. High-risk setups remain watch-only.
- Indicative sizing: 0.25% portfolio risk / stop fraction, capped at 5% per
  position, and zero for watch-only setups. 2R is a reference, not a profit forecast.
  Signals are end-of-day observations, not guaranteed fills at that close.

## Score and Data

0-100 score: decline depth 25, stable base 20, resistance proximity/break 25,
volume 20, one-month sector strength 10. Missing sector classification earns no
sector points. This score is unrelated to the trend-following momentum rank.

Reject corrupt, duplicate, insufficient and stale bars. Future bars are cut off
at the benchmark's last trading date. Five post-pivot bars must be available
before the signal day; no centered-window lookahead is used for today's signal.

Results are versioned in the existing private Supabase sector snapshot under
`reversals` and `reversal_analysis`. No database migration is required. Older runs
display analysis unavailable, not zero candidates. Chart snapshots preserve the
OHLCV used by the screener; overlays appear only on matching snapshots. External
fallback prices can differ and do not get algorithm overlays.

No reversal backtest or out-of-sample profitability validation has been performed.
The liquid, current universe excludes many small speculative companies and
historical delisted stocks. Treat this as a research watchlist, not an automatic
trading recommendation.
