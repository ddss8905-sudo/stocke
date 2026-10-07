# Weekly Trend and Bottom Reversal (v1)

Daily strategies are unchanged. Each existing market workflow collects data once
and additionally evaluates both weekly strategies. These thresholds are application
design choices, not Jeffrey Neumann's published weekly rules. There is no weekly
profitability backtest. This is a research screen, not an automatic trading system.

## Bars and Freshness

- Aggregate Monday-Friday: first open, maximum high, minimum low, last close,
  sum volume and actual trading value when available. Never average OHLCV.
- Admit a week only after Friday 16:15 America/New_York or 16:45 Asia/Seoul.
  These conservative application cutoffs also cover holidays and early closes;
  a holiday-Friday week becomes eligible at that cutoff using its last trading day.
- Use Friday as the weekly candle label; separately show the actual price date.
  This week is excluded Monday-Thursday and before Friday's cutoff. A requested
  historical date also limits eligible weeks. Stock and benchmark price dates must
  match, including holiday weeks, to avoid scoring stale or suspended stocks.
- Reject invalid/duplicate daily bars. Discard the first potentially partial input
  week. Use the last 504 completed daily bars and require 56 weekly bars. Fetching
  800 calendar days supports these windows; it does not support a 200-week average.
- Korean adjusted integer OHLC can put a high/low one won inside the open/close.
  Widen only such <=1 KRW rounding discrepancies, preserving open/close. Larger
  discrepancies and inverted high/low ranges are rejected. US prices get no such
  tolerance. This weekly-only normalization is included in the chart snapshots.
- Liquidity is the daily 20-session average trading value through the completed
  week's price date, NOT mean weekly trading value. The same currency/thresholds
  as daily screening apply. Weekly ATR14 uses 14 actual weekly ranges; reject
  ATR/close above 20% on either weekly strategy.
- Sector periods remain 5/10/21 TRADING DAYS, ending at the weekly price date;
  the existing 1-week/2-week/month labels never mean 5/10/21 weekly bars.

## Trend Following

- Four-, ten-, thirty-, forty-week averages replace the daily 20/50/150/200
  horizons. Template: close > MA40W, MA10W > MA40W, MA40W rising versus four
  weeks earlier, and positive 26-to-4-week momentum.
- Rank skipped-recent-month momentum at 13/26/52 weeks with a four-week skip,
  weighted 20/30/50%. Final and RS ranks must meet the existing market limits.
- Apply the existing daily liquidity, minimum price, MA10W extension and maximum
  45% ten-week base-depth filters. Weekly regime independently uses MA10W/MA40W:
  80% indicative maximum exposure for a strong trend, 40% above MA40W only,
  otherwise risk-off. Candidates require regime >=40.
- Fresh signal: first close above the PREVIOUS eleven-week high, volume >= the
  previous ten-week mean times the market's entry multiplier, extension 0-5%,
  structural risk <= the existing 10% limit. Prior-week breakout prevents repeat
  entry signals. Limit actual triggers to the existing maximum position count.
- Stop = latest two-week low minus 0.5 weekly ATR14. Do not clamp stops. Candidates
  above the risk limit remain watch-only with zero position size. All other watch
  rows also have zero size. Confirmed signals use 0.5% portfolio risk / stop risk,
  capped at 10%. 2R is an indicative reference, not a forecast or guaranteed fill.
- Weekly accumulation/VCP scores are not computed and are left null rather than
  populated with meaningless percentile ranks.

## Bottom Reversal

The same price-based conditions as the daily strategy run on weekly bars, using:

- Confirmed pivot highs with two weeks on each side, all before the signal week.
  Anchors >=13 bars apart; first >=25 weeks old; second within 17 weeks. Second
  high <=85% of first. The existing line support and longest-anchor rules apply.
- Negative log-close slope over weeks -26 through -5. At least 25% decline from
  anchor-period high to the latest 13-week low; rebound 4-40% from that low.
- Latest two-week low >=98% of the preceding four-week low. Close >MA4W with
  MA4W rising versus the previous week. Setup within -5%/+15% of resistance;
  an already-broken line needs a crossing within the last two weekly bars.
- Fresh resistance +1% crossing, volume >=1.5x PRIOR ten-week mean, upper-half
  closing location, <=5% extension, <=10% structural risk and regime >=40 are
  all required for a confirmed signal. Old crossings and excess risk stay watch-only.
- Stop = two-week low minus 0.5 weekly ATR14. Confirmed sizing uses the existing
  reversal 0.25% portfolio risk / stop risk, capped at 5%; watch-only size is zero.
- Existing reversal score weights and catalyst/share-structure limitations apply.
  Downtrend duration is displayed in weekly bars, not mislabeled trading days.

## Persistence and Charts

`weekly` in the existing private sector snapshot stores candidates, the full
scoreboard, reversals, sectors and versioned closed-bar metadata. No SQL schema
change or new credentials are needed. Older snapshots show analysis unavailable,
not daily prices relabeled as weekly or zero results attributed to screening.

`{run_id}-weekly-charts.json` contains the identical aggregated OHLCV. Weekly
charts use those snapshots only, display MA10W/MA30W/MA40W and plot reversal
resistance using the same weekly anchor indices. Missing snapshots return a clear
error, never a daily/live substitute. Market, strategy and timeframe survive
navigation and refresh. One workflow run refreshes all four combinations.

Technical-analysis background (not a source for these custom thresholds):
https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/sma
https://www.fidelity.com/products/atbt/help/ActiveTraderTools_Chart_Help.html
