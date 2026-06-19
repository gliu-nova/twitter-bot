# Twitter Indicator Bot

Monitors market and macro indicators, stores readings in SQLite, and posts to X/Twitter when **per-indicator rules** you define are triggered.

## Indicators (25)

| Key | Name | Source |
|-----|------|--------|
| `sp500` | S&P 500 | Yahoo |
| `nasdaq100` | NASDAQ 100 | Yahoo |
| `vix` | VIX | Yahoo |
| `dxy` | US Dollar Index (DXY) | Yahoo |
| `gold` / `silver` | Gold, Silver | Yahoo |
| `oil` | WTI Crude Oil | FRED |
| `move` | MOVE Index | Yahoo |
| `hy_spread` | High Yield Credit Spread | FRED |
| `btc` / `eth` / `sol` | Bitcoin, Ethereum, Solana | CoinGecko |
| `fear_greed` | Crypto Fear & Greed Index | alternative.me |
| `fed_funds` | Fed Funds Rate | FRED |
| `treasury_10y` | 10Y Treasury Yield | FRED |
| `yield_curve` | Yield Curve (10Y − 2Y) | FRED |
| `jobless_claims` | Initial Jobless Claims | FRED |
| `pmi_manufacturing` | Manufacturing Production Index | FRED |
| `ism_services` | Chicago Fed Nonmfg Activity (ISM Services proxy) | FRED |
| `unemployment` | Unemployment Rate | FRED |
| `mortgage_30y` | 30Y Mortgage Rate | FRED |
| `consumer_sentiment` | Consumer Sentiment | FRED |
| `case_shiller` | Case-Shiller Home Prices | FRED |
| `cpi_yoy` | CPI Inflation (YoY %) | FRED (computed) |
| `m2` | M2 Money Supply | FRED |

## Custom rules per indicator

Edit `config.yaml`. **Each indicator has its own `rules` list** — not one global threshold.

```yaml
vix:
  name: VIX
  source: yahoo
  symbol: "^VIX"
  cooldown_hours: 6
  rules:
    - type: percent_change
      threshold: 15          # alert if VIX moves ±15% vs last reading
    - type: crosses_above
      value: 30              # alert when VIX crosses above 30

yield_curve:
  rules:
    - type: crosses_below
      value: 0               # inversion alert

btc:
  rules:
    - type: percent_from_baseline
      baseline: 50000
      threshold: 50            # ±50% from your chosen baseline
```

**Rule types:** `percent_change`, `percent_from_baseline`, `above`, `below`, `crosses_above`, `crosses_below`

**`cooldown_hours`** — per indicator; prevents repeat tweets for the same metric.

## Setup

```bash
cd twitter-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 1. FRED API key (free)

1. Go to https://fred.stlouisfed.org/docs/api/api_key.html
2. Create an account → request API key
3. Add to `.env`: `FRED_API_KEY=your_key`

### 2. Connect your X/Twitter account

You need **write access** to post tweets from your account.

1. **Developer account** — Apply at https://developer.x.com/ (may require describing your bot as automated market alerts).
2. **Create a Project + App** in the [Developer Portal](https://developer.x.com/en/portal/dashboard).
3. **App permissions** — Set to **Read and write** (not read-only).
4. **Generate credentials:**
   - API Key and Secret (Consumer Keys)
   - Access Token and Secret (for **your** account — click "Generate" under User authentication)
5. Add all four to `.env`:

```env
TWITTER_API_KEY=...
TWITTER_API_SECRET=...
TWITTER_ACCESS_TOKEN=...
TWITTER_ACCESS_TOKEN_SECRET=...
```

6. **Test in dry-run first** — keep `DRY_RUN=1` in `.env`. Run `python run.py` and confirm alerts print as `[DRY RUN] Would tweet:...`
7. **Go live** — set `DRY_RUN=0`, run again. A triggered rule posts to your account.

### 3. Schedule automatic checks

```cron
# Every hour during weekdays
0 * * * 1-5 cd /path/to/twitter-bot && .venv/bin/python run.py >> data/bot.log 2>&1
```

Or use `launchd` on macOS for a local always-on scheduler.

## Run

```bash
python run.py                    # all indicators
python run.py --indicator vix    # one indicator
```

## Notes

- **ISM Services PMI** is not on free FRED; `ism_services` uses the Chicago Fed Nonmanufacturing Activity Index as a proxy. Change `series` in `config.yaml` if you have another feed.
- **First poll** only stores data; percent/cross rules need a prior reading.
- Tweets are capped at 280 characters.