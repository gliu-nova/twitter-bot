# Twitter Indicator Bot

Monitors macro and market indicators, stores readings in SQLite, and posts to X/Twitter when configurable thresholds are crossed.

## Indicators tracked

| Key | Name | Source |
|-----|------|--------|
| `sp500` | S&P 500 | Yahoo Finance |
| `vix` | VIX | Yahoo Finance |
| `oil` | WTI Crude Oil | FRED |
| `btc` / `eth` / `sol` | Bitcoin, Ethereum, Solana | CoinGecko |
| `fed_funds` | Fed Funds Rate | FRED |
| `treasury_10y` | 10Y Treasury Yield | FRED |
| `jobless_claims` | Initial Jobless Claims | FRED |
| `pmi_manufacturing` | Manufacturing Production Index | FRED (`IPMAN`) |
| `unemployment` | Unemployment Rate | FRED |
| `mortgage_30y` | 30Y Mortgage Rate | FRED |
| `consumer_sentiment` | Consumer Sentiment | FRED |
| `case_shiller` | Case-Shiller Home Prices | FRED |
| `cpi_yoy` | CPI Inflation (YoY %) | FRED (computed) |
| `yield_curve` | Yield Curve (10Y − 2Y) | FRED |

## Thresholds

Edit `config.yaml`. Each indicator supports:

- **`threshold_percent`** — alert when value moves ±X% vs the last stored reading (your ±50% example; defaults vary per indicator)
- **`threshold_low` / `threshold_high`** — alert on absolute boundary crossings (e.g. yield curve below `0`)
- **`cooldown_hours`** — minimum hours between repeat alerts for the same indicator

On first run there is no prior reading, so percent-based alerts only fire after the second poll.

## Setup

```bash
cd twitter-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

1. **FRED API key** (free): https://fred.stlouisfed.org/docs/api/api_key.html → set `FRED_API_KEY` in `.env`
2. **Twitter/X API**: Create a project at https://developer.x.com/ with **tweet write** permission. Set the four `TWITTER_*` vars in `.env`.
3. Keep `DRY_RUN=1` while testing. Set `DRY_RUN=0` to post live tweets.

## Run

```bash
# All indicators (dry run by default)
python run.py

# Single indicator
python run.py --indicator vix
```

## Schedule (cron)

Poll every hour during market hours, or daily for slow-moving macro data:

```cron
0 * * * * cd /path/to/twitter-bot && .venv/bin/python run.py >> data/bot.log 2>&1
```

## Notes

- **PMI**: FRED `IPMAN` is industrial production, not the ISM PMI headline number. Change `series` in `config.yaml` if you have a preferred feed.
- **Percent thresholds** on slow monthly series (CPI, Case-Shiller) are intentionally high in the default config — tune per indicator.
- Tweets are capped at 280 characters.