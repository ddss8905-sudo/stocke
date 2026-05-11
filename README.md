# Stock Screener App

NASDAQ, KOSDAQ, and KOSPI_API trend-following screener dashboard for Vercel.

## Architecture
- GitHub Actions runs the Python screeners on demand.
- Supabase stores screening runs and results.
- Vercel hosts the Next.js dashboard.

## Execution
- NASDAQ, KOSDAQ, and KOSPI_API run on demand from the dashboard.
- GitHub Actions workflows keep `workflow_dispatch` enabled and do not run on a daily schedule.

## Setup
1. Create a Supabase project.
2. Run `db/schema.sql` in the Supabase SQL editor for a fresh database.
3. If your Supabase tables already exist, run `db/migrations/001_add_kospi_api_market.sql` in the Supabase SQL editor.
4. Add these secrets to GitHub Actions:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `KIS_APP_KEY`
   - `KIS_APP_SECRET`
   - `KIS_BASE_URL` (optional; defaults to Korea Investment production OpenAPI)
5. Add these values to Vercel environment variables:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `GITHUB_ACTIONS_TOKEN`
   - `GITHUB_REPOSITORY` (defaults to `ddss8905-sudo/stocke`)
   - `GITHUB_DISPATCH_BRANCH` (defaults to `main`)
6. Install web dependencies and run locally:

```powershell
npm install
npm run dev
```

7. Test the Python jobs locally:

```powershell
cd jobs
py -m pip install -r requirements.txt
py run_market.py --market KOSDAQ
py run_market.py --market NASDAQ
py run_market.py --market KOSPI_API
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

## KOSPI_API market
`KOSPI_API` uses Korea Investment OpenAPI credentials from GitHub Actions secrets:

```text
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_BASE_URL=https://openapi.koreainvestment.com:9443
```

`KIS_BASE_URL` can be omitted for the production OpenAPI URL. Set it explicitly only when using a different Korea Investment endpoint.

The current implementation uses Korea Investment daily chart data and the existing trend-following scoring logic. To make intraday button clicks use the current quote as the latest price, add the Korea Investment current-price endpoint as a second step after the daily chart download.
