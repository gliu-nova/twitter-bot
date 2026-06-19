# Twitter Indicator Bot

Monitors market and macro indicators, stores readings in SQLite, and posts to X/Twitter when **per-indicator rules** you define are triggered. A **posting engine** scores, groups, and rate-limits tweets so you get 2 high-quality posts per day (more for emergencies).

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
| `btc` / `eth` / `sol` | Bitcoin, Ethereum, Solana | Yahoo (verified vs Binance) |
| `fear_greed` | Crypto Fear & Greed Index | alternative.me |
| `fed_funds` | Fed Funds Rate | FRED |
| `treasury_10y` | 10Y Treasury Yield | FRED |
| `yield_curve` | Yield Curve (10Y − 2Y) | FRED |
| `jobless_claims` | Initial Jobless Claims | FRED |
| `pmi_manufacturing` | Philly Fed Manufacturing Index (ISM PMI proxy) | FRED |
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
btc:
  normal_alert: 5          # triggers at ±5% move
  major_alert: 8           # tier: major
  emergency_alert: 12      # tier: emergency (bypasses daily cap)

fed_funds:
  alert_unit: absolute     # rates use pp/bps, not percent change
  normal_alert: 0.25
  major_alert: 0.50
  emergency_alert: 0.75

cpi_yoy:
  alert_unit: absolute
  normal_alert: 0.3
  rules:
    - type: crosses_above
      value: 3.0
    - type: percent_change
      threshold: 12

yield_curve:
  rules:
    - type: crosses_below
      value: 0               # normal → inverted
    - type: crosses_above
      value: 0               # inverted → normal
```

**Tier fields:** `normal_alert`, `major_alert`, `emergency_alert` (+ optional `alert_unit: absolute`)

**Rule types:** `percent_change`, `absolute_change`, `crosses_above`, `crosses_below`, `above`, `below`

**`cooldown_hours`** — per indicator; prevents repeat *alerts* for the same metric during fetch cycles.

## Posting engine

Alerts are **queued and batched**, not tweeted instantly.

1. **Score** each alert: `(Magnitude × 40%) + (Rarity × 30%) + (Audience × 20%) + (Freshness × 10%)`
2. **Buffer** market alerts 30 min (configurable) so BTC/ETH/SOL don't become 3 separate tweets
3. **Macro recap** batch flushes after 4:15 PM ET
4. **Decide**: standalone tweet if score ≥ 85 (CPI, Fed, VIX>30, yield curve, etc.) OR multi-indicator tweet when 3+ alerts share a theme
5. **Daily cap**: 2 posts/day (emergencies with score ≥ 90 bypass the cap)
6. **Cooldown**: same indicator not posted again within 36h unless emergency
7. **Diversity**: avoids 3 crypto tweets in a row — prefers macro when possible

Edit thresholds in `config.yaml` under `posting:`:

```yaml
posting:
  daily_post_cap: 2
  emergency_threshold: 90
  high_single_threshold: 85
  multi_threshold: 120
  market_buffer_minutes: 30
  indicator_cooldown_hours: 36
```

Per-indicator **themes** (for grouping) live in `posting.indicator_themes`. Threshold **rules** stay under each `indicators:` entry.

```bash
DRY_RUN=1 python run.py          # logs [DRY RUN] Would tweet:...
python run.py --force-post       # flush queue immediately (testing)
```

## Charts on tweets

Graphics are attached automatically for:

- **Emergency single alerts** — 6-month line chart (green gain / red loss) with threshold lines and 6M high/low context
- **All multi-indicator tweets** — summary leaderboard card sorted by move size

Charts save to `data/charts/` and upload via Twitter media API. Under ~2MB for fast posting.

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

## Data quality safeguards

Before saving or tweeting, each reading passes:

- **API health** — pings FRED, Yahoo, CoinGecko, Fear & Greed at run start
- **Type/NaN check** — rejects null, NaN, or non-numeric values
- **Staleness** — per-indicator `max_stale_hours` (auto by source: crypto 12h, equities 48h, macro 720h)
- **Cross-verification** — optional second source must agree within `tolerance_pct` (configured for SP500, NASDAQ, DXY, gold, BTC, ETH, SOL)
- **Market hours** — `us_equity` indicators suppress **alerts** outside 9:30–16:00 ET Mon–Fri (data still saved)

```bash
python run.py --health   # API health only
```

**Crypto (BTC/ETH/SOL)** uses Yahoo Finance hourly, cross-checked against Binance public API.

## Polling schedule

The bot **ticks every 5 minutes** but each indicator fetches on its own tier (posting rules unchanged — still max 2 tweets/day + buffer):

| Tier | Indicators | Poll interval |
|------|------------|---------------|
| Crypto 24/7 | BTC, ETH, SOL, Fear & Greed | every 10 min |
| US equity / volatile | SPY, QQQ, VIX, DXY, Gold, Silver, Oil | every 10 min (market hours), 60 min off-hours |
| Rates & FX | 10Y, yield curve, Fed funds, MOVE, HY spread | every 30 min |
| Macro (FRED) | CPI, unemployment, PMI proxies, M2, etc. | every 6 hours |
| Housing / monthly | Case-Shiller, 30Y mortgage | every 24 hours |

Edit `config.yaml` → `scheduler:` to tune intervals.

## macOS schedule (launchd)

```bash
./scripts/install-schedule.sh   # re-install after updates (5-min tick)
# Logs: data/bot.log
# `state = not running` in launchctl is normal between ticks
# Uninstall: launchctl bootout gui/$(id -u)/com.georgeliu.twitter-bot
```

## Run

```bash
python run.py                    # all indicators
python run.py --indicator vix    # one indicator
```

## Notes

- **ISM PMI data was removed from FRED in 2016.** `pmi_manufacturing` uses the Philadelphia Fed Manufacturing diffusion index; `ism_services` uses the Chicago Fed Nonmanufacturing Activity Index. Both names include "proxy" in tweets.
- **First poll** only stores data; percent/cross rules need a prior reading.
- Tweets are capped at 280 characters.