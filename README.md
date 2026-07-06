# cape_market_data

GitHub Actions data pipeline for Baltic Exchange Capesize market data.

The first-stage pipeline fetches C5TC plus Capesize route values C3 and C5 from the Baltic API, calculates C3/C5 ratio and C3 minus C5, then publishes the latest record and a daily history file in this repository.

## Outputs

- `data/cape_latest.json`: latest normalized Baltic Capesize record.
- `data/cape_daily.csv`: date-sorted daily history. If the same date is fetched again, the latest record replaces the older row.
- `logs/fetch_log.txt`: successful fetch log entries. It does not contain the API key.

Output fields:

```text
date,C5TC,C3,C5,C3_C5_ratio,C3_minus_C5,source,fetch_time
```

## GitHub Secrets

Create a repository secret named `BALTIC_API_KEY`:

1. Open the GitHub repository `mymvigor/cape_market_data`.
2. Go to `Settings` -> `Secrets and variables` -> `Actions`.
3. Add a new repository secret named `BALTIC_API_KEY`.
4. Paste the Baltic API key as the secret value.

Do not commit `.env`, `.streamlit/secrets.toml`, or any real API key.

## GitHub Actions

The workflow runs every weekday at 09:30 UTC:

```yaml
cron: "30 9 * * 1-5"
```

To run it manually:

1. Open `Actions` in GitHub.
2. Select `Fetch Baltic Capesize Data`.
3. Click `Run workflow`.

## Local Test

Install dependencies and run the pipeline with an environment variable:

```powershell
pip install -r requirements.txt
$env:BALTIC_API_KEY="your_api_key_here"
python scripts/main.py
```

The script exits with a non-zero status if the API request, transformation, or validation fails. Existing data is not overwritten until validation has passed.

## Raw Data Links

ChatGPT or other clients should read one of these raw files after the workflow has run:

- JSON latest: [data/cape_latest.json](https://raw.githubusercontent.com/mymvigor/cape_market_data/main/data/cape_latest.json)
- CSV history: [data/cape_daily.csv](https://raw.githubusercontent.com/mymvigor/cape_market_data/main/data/cape_daily.csv)
