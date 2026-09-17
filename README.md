# Stock Screener App

NASDAQ and KOSDAQ trend-following screener dashboard for Vercel.

## Architecture
- GitHub Actions runs the Python screeners.
- Supabase stores screening runs and results.
- Vercel hosts the Next.js dashboard.

## Execution
- NASDAQ and KOSDAQ run on demand from the dashboard.
- GitHub Actions workflows keep `workflow_dispatch` enabled and do not run on a daily schedule.

## Setup
1. Create a Supabase project.
2. Run `db/setup_supabase.sql` in the Supabase SQL editor for a fresh database.
   - If you prefer separate files, run `db/schema.sql` first, then any files in `db/migrations`.
3. Add these secrets to GitHub Actions:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
4. Add the same values to Vercel environment variables.
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `GITHUB_ACTIONS_TOKEN`
   - `GITHUB_REPOSITORY` (defaults to `ddss8905-sudo/stocke`)
   - `GITHUB_DISPATCH_BRANCH` (defaults to `main`)
   - `KIS_APP_KEY`
   - `KIS_APP_SECRET`
   - `KIS_BASE_URL` (defaults to Korea Investment production OpenAPI)
5. Install web dependencies and run locally:

```powershell
npm install
npm run dev
```

6. Test the Python jobs locally:

```powershell
cd jobs
py -m pip install -r requirements.txt
py run_market.py --market KOSDAQ
py run_market.py --market NASDAQ
```

If Supabase environment variables are not set, the Python job writes a local JSON payload under `jobs/data`.
If Vercel environment variables are not set, the web app displays sample rows.

## On-demand screening
The dashboard includes a `Run now` button. It calls `/api/run-screener`, which triggers the matching GitHub Actions workflow through `workflow_dispatch`.

Create a GitHub fine-grained token with access to this repository and Actions workflow permission, then add it to Vercel as:

```text
GITHUB_ACTIONS_TOKEN=...
```

The button starts the workflow. Results appear after the workflow finishes and uploads new rows to Supabase.

## KOSPI API market
`KOSPI_API` uses Korea Investment OpenAPI credentials from GitHub Actions secrets:

```text
KIS_APP_KEY
KIS_APP_SECRET
KIS_BASE_URL
```

Before using `KOSPI_API`, run `db/migrations/001_add_kospi_api_market.sql` in Supabase SQL Editor so the existing market check constraints accept the new market value.

## Risk and regime filters
The shared screener logic applies to `NASDAQ`, `KOSDAQ`, and `KOSPI_API`.

- NASDAQ selection intersects the official listing with the official current-liquidity feed, prefilters the top 1,000 names, and keeps the top 500 by 20-day average dollar volume.
- Leadership score uses cross-sectional percentile ranks of 3-, 6-, and 12-month returns ending one month ago, weighted 20%, 30%, and 50%.
- Market exposure is capped at 80% when the benchmark is above its 200-day average and its 50-day average is above the 200-day average, 40% when only the first condition passes, and 0% otherwise.
- A buy signal requires a new 55-day closing breakout with volume above the prior 50-day average. Pullback entries are disabled.
- The initial stop is 2.5 ATR below the signal close. Position size risks 0.5% of account equity and is capped at 10% per name; at most 10 new signals are emitted per run.
- After a position reaches 2R, its 3 ATR closing trail remains active and never moves down. An MA50 close break or a risk-off market is an exit condition.

Run `db/migrations/002_add_risk_regime_entry_columns.sql` in Supabase SQL Editor before uploading new runs that include these fields.
Run `db/migrations/003_add_trend_following_plan_columns.sql` before uploading runs from the enhanced trend-following engine.

## Exit tracking model
Exit rules need position state, not just a daily screener row. A position tracker should store the entry price, initial stop, highest close since entry, whether the 2R trail was activated, current trailing stop, and latest action. Each daily run can then update that state with fresh OHLCV data.

The shared Python module includes `evaluate_position_exit`, which preserves trail activation and the previous stop supplied by a position tracker. It returns `hold`, `trim_or_watch`, or `hard_exit` from the active stop, 50-day moving average, and market exposure. The next practical step is adding a `positions` table and a job that calls this function for held tickers after the screener finishes.

