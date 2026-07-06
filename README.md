# cape_market_data

GitHub Actions data pipeline for Baltic Exchange Capesize market data.

The pipeline uses the current Baltic API feed `FDSZ5H4HS31QCF5TN6OLWZJMBBC1QPIU` as its only data source. It expands every `shortCode` series into columns, keeps a cumulative history, and writes the latest available day as JSON.

## Fetch Rules

- First run: fetches the latest 3 months of data to initialize history.
- Later runs: fetches a rolling 14-day window and merges it into `data/cape_daily.csv`.
- Existing history is preserved. Rows are deduplicated by `date`, keeping the newest values.
- If the API latest date is not newer than the local latest date, the run exits normally and logs `No new data`.
- Missing core fields produce warnings, not a crash.

## Outputs

- `data/cape_daily.csv`: cumulative date-sorted history.
- `data/cape_latest.json`: latest row from the cumulative history.
- `logs/fetch_log.txt`: run status log. It does not contain the API key.

Core output columns appear first, followed by derived metrics, then any other Baltic `shortCode` columns in alphabetical order, then `fetch_time`.

## GitHub Secrets

Create a repository secret named `BALTIC_API_KEY`:

1. Open the GitHub repository `mymvigor/cape_market_data`.
2. Go to `Settings` -> `Secrets and variables` -> `Actions`.
3. Add a new repository secret named `BALTIC_API_KEY`.
4. Paste the Baltic API key as the secret value.

Do not commit `.env`, `.streamlit/secrets.toml`, or any real API key.

## GitHub Actions

The workflow runs daily at 09:30 UTC and also supports manual runs.

To run it manually:

1. Open `Actions` in GitHub.
2. Select `Fetch Baltic Capesize Data`.
3. Click `Run workflow`.

## Local Test

```powershell
pip install -r requirements.txt
$env:BALTIC_API_KEY="your_api_key_here"
python scripts/main.py
```

## Raw Data Links

- JSON latest: [data/cape_latest.json](https://raw.githubusercontent.com/mymvigor/cape_market_data/main/data/cape_latest.json)
- CSV history: [data/cape_daily.csv](https://raw.githubusercontent.com/mymvigor/cape_market_data/main/data/cape_daily.csv)
