# Stock Screener App

NASDAQ and KOSDAQ trend-following screener dashboard for Vercel.

## Architecture
- GitHub Actions runs the Python screeners.
- Supabase stores screening runs and results.
- Vercel hosts the Next.js dashboard.

## Schedules
- NASDAQ: 21:00 KST, stored as `0 12 * * 1-5` UTC in GitHub Actions.
- KOSDAQ: 15:30 KST, stored as `30 6 * * 1-5` UTC in GitHub Actions.

## Setup
1. Create a Supabase project.
2. Run `db/schema.sql` in the Supabase SQL editor.
3. Add these secrets to GitHub Actions:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
4. Add the same values to Vercel environment variables.
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
