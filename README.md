# Twitter Indicator Bot

Monitors market and macro indicators, stores readings in SQLite, and posts to X/Twitter when **per-indicator rules** you define are triggered. A **posting engine** scores, groups, and rate-limits tweets (default cap: **8** regular posts/day; emergencies bypass the cap).

## Indicators (43)

Configured in `config.yaml`. Core groups:

| Group | Examples | Source |
|-------|----------|--------|
| Equities / ETFs | `sp500`, `nasdaq100`, `qqq`, bond/crypto ETFs | Yahoo |
| Volatility / FX / commodities | `vix`, `dxy`, `gold`, `silver`, `oil`, `move`, `hy_spread` | Yahoo / FRED |
| Crypto spot | `btc`, `eth`, `sol` | Yahoo (cross-verified vs **Kraken**) |
| Crypto derivatives | funding, basis, exchange spread, liquidations | OKX / Hyperliquid / Kraken+Coinbase |
| Sentiment | `fear_greed` | alternative.me |
| Macro / rates / housing | Fed funds, treasuries, CPI, M2, Case-Shiller, etc. | FRED |
| Dark pool | `dark_pool_spy` | FINRA Reg SHO |
| On-chain whales | `eth_whale` | Etherscan via **market-memory** |

## On-chain whales (market-memory Etherscan)

Each bot poll can ingest watched addresses through `market_memory.etherscan` and queue **whale transfer** alerts into the normal posting engine.

```bash
# 1. Install market-memory with the etherscan package (sibling checkout recommended)
pip install -e ../market-memory

# 2. Set API key in twitter-bot/.env
ETHERSCAN_API_KEY=your_key

# 3. Edit watched addresses
#    data/etherscan_watchlist.yaml

# 4. Run the bot as usual (DRY_RUN=1 to print tweets)
python run.py
```

Config (`config.yaml` → `etherscan:`):

| Key | Meaning |
|-----|---------|
| `enabled` | Master switch |
| `ingest_on_poll` | Call Etherscan each run |
| `post_whales` | Enqueue `eth_whale` alerts |
| `watchlist_path` | YAML/JSON/TXT address list |
| `whale_threshold_eth` | Minimum size (default 100) |
| `major_eth` / `emergency_eth` | Tier cutoffs for scoring / daily-cap bypass |

Flow: **watchlist → Etherscan ingest → SQLite (`etherscan.db`) → whale hook → `pending_alerts` → compose (`🐋` tweet + explorer link)**.

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

**`cooldown_hours`** — per indicator; prevents repeat *alerts* for the same metric during fetch cycles. Emergency escalation uses **magnitude** (`|value|` vs last alert × multiplier) so crashes and spikes can both break cooldown.

## Posting engine

Alerts are **queued and batched**, not tweeted instantly.

1. **Score** each alert: `(Magnitude × 45%) + (Rarity × 30%) + (Audience × 25%)` (weights in `posting.score_weights`)
2. **Buffer** market alerts 30 min (configurable) so BTC/ETH/SOL don't become 3 separate tweets
3. **Macro recap** batch flushes after 4:15 PM ET
4. **Decide**: standalone tweet if score ≥ `high_single_threshold` (75) or `standalone_major`; multi-indicator when cluster score clears `multi_threshold`. Alerts below threshold stay queued.
5. **Daily cap**: max **8 regular posts/day** — emergency/black-swan posts **do not count** toward the limit
6. **Cooldown**: same indicator not posted again within 36h unless emergency escalation
7. **Diversity**: after 2 crypto tweets in a row, skip non-emergency crypto and post a non-crypto alternative if one qualifies. Emergencies still go out.
8. **Off-hours equities**: US-session indicators that fire after the close are **queued** for the next 9:30–16:00 ET window (not dropped). VIX major/emergency may still post immediately.

When a threshold-crossing alert is actually posted, the bot also computes an **event scorecard** from whatever of these are relevant: absolute change, rolling percentile, z-score, historical rarity, velocity, persistence, cross-asset confirmation, and data confidence. Strong reasons replace the generic context line:

```
event_score = 87 / 100
severity = HIGH

Reasons:
+ 98th percentile 1h move
+ largest move in 41 days
+ confirmed by VIX
+ Treasury volatility elevated
```

## Outcome ledger

Every fired alert (queued, not only posted) is written to an **outcome ledger**. On later runs the bot records what happened next:

- **4h / 24h / 5d** percent move on the **same series**
- whether a **related series confirmed** it (e.g. VIX up after SPX down), using local readings first and **market-memory** events as fallback
- **time-to-confirmation** in hours

Headline stats use the 24h window (5d for macro). A move in the expected direction is a hit; flat or opposite is a false alarm.

Each run rewrites a simple page and JSON:

```
data/outcome_ledger.html
data/outcome_ledger.json
```

The same summary is pushed to ops-hub and shown on the public Twitter bot dashboard (`/bots/twitter`). Regenerate without fetching:

```bash
python run.py --outcome-ledger
```

Edit thresholds in `config.yaml` under `posting:`:

```yaml
posting:
  daily_post_cap: 8
  emergency_threshold: 90
  high_single_threshold: 75
  multi_threshold: 120
  market_buffer_minutes: 30
  indicator_cooldown_hours: 36
  alert_max_age_hours: 36
```

Per-indicator **themes** (for grouping) live in `posting.indicator_themes`. Threshold **rules** stay under each `indicators:` entry.

```bash
DRY_RUN=1 python run.py          # logs [DRY RUN] Would tweet:...
python run.py --force-post       # flush queue immediately (testing)
python -m unittest discover -s tests -v
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

### 3. Run 24/7 with GitHub Actions (recommended)

Your Mac does **not** need to stay awake. GitHub runs the bot on a schedule in the cloud.

#### Step 1 — Add repository secrets

In GitHub: **your repo → Settings → Secrets and variables → Actions → New repository secret**

Add each of these (copy values from your local `.env`):

| Secret name | Value |
|-------------|-------|
| `FRED_API_KEY` | FRED API key |
| `TWITTER_API_KEY` | Twitter API key (consumer) |
| `TWITTER_API_SECRET` | Twitter API secret |
| `TWITTER_ACCESS_TOKEN` | Your account access token |
| `TWITTER_ACCESS_TOKEN_SECRET` | Your account access token secret |

Do **not** commit `.env` to git.

#### Step 2 — Enable Actions

1. Go to **Actions** tab in your repo
2. If prompted, click **Enable workflows**
3. Select **Twitter Bot** workflow
4. Click **Run workflow** once to test manually

**Required:** set up external hourly dispatch — see [`scripts/EXTERNAL_CRON.md`](scripts/EXTERNAL_CRON.md) (cron-job.org → `workflow_dispatch` every hour at `:31` UTC).

#### Step 3 — Verify

- **Actions** tab → latest run should be green
- Expand **Run bot** step to see indicator fetches
- When a threshold fires, the bot tweets from your account (`DRY_RUN=0` in the workflow)
- Hourly `external-cron` ticks still run unit tests, but a test failure does **not** skip the bot (manual **Run workflow** still fails the job if tests fail)

#### State persistence

SQLite (`data/indicators.db`) is restored/saved via the Actions cache between runs so readings, cooldowns, and daily post counts survive. **Cache misses or eviction reset bot memory** (duplicate or missed posts possible) — treat cache-hit logs as operationally important. First run starts fresh; history builds over time. Readings older than ~400 days are pruned each run.

#### Private repo note

GitHub Free private repos include **2,000 Actions minutes/month**. At 15-min intervals (~2,900 runs/month), a private repo may still be tight depending on run duration. Options:

- Make the repo **public** (Actions free for public repos)
- Upgrade GitHub plan for more minutes
- Slow the cron in `.github/workflows/bot.yml` (e.g. `*/10 * * * *`)

#### Optional: stop local Mac scheduler

If you used launchd before, disable it so you don't double-post:

```bash
launchctl bootout gui/$(id -u)/com.georgeliu.twitter-bot
```

## Data quality safeguards

Before saving or tweeting, each reading passes:

- **API health** — pings FRED, Yahoo, Kraken, Fear & Greed, OKX, Hyperliquid, Coinbase, FINRA at run start
- **Type/NaN check** — rejects null, NaN, or non-numeric values
- **Staleness** — per-indicator `max_stale_hours` (auto by source: crypto 12h, equities 48h, macro 720h)
- **Cross-verification** — optional second source must agree within `tolerance_pct` (configured for SP500, NASDAQ, DXY, gold, BTC, ETH, SOL vs Kraken, funding/basis, etc.)
- **Market hours** — `us_equity` indicators suppress **alerts** outside 9:30–16:00 ET Mon–Fri (data still saved)

```bash
python run.py --health   # API health only
```

**Crypto (BTC/ETH/SOL)** uses Yahoo Finance, cross-checked against **Kraken** (see `quality.verify` in config).

## Polling schedule

GitHub Actions **ticks every hour** (external cron at `:31` UTC); each indicator fetches on its own tier when due (posting rules unchanged — still max 8 regular tweets/day + buffer):

| Tier | Indicators | Poll interval |
|------|------------|---------------|
| Crypto 24/7 | BTC, ETH, SOL, Fear & Greed | every 10 min |
| US equity / volatile | SPY, QQQ, VIX, DXY, Gold, Silver, Oil | every 10 min (market hours), 60 min off-hours |
| Rates & FX | 10Y, yield curve, Fed funds, MOVE, HY spread | every 30 min |
| Macro (FRED) | CPI, unemployment, PMI proxies, M2, etc. | every 6 hours |
| Housing / monthly | Case-Shiller, 30Y mortgage | every 24 hours |

Edit `config.yaml` → `scheduler:` to tune intervals.

## Local Mac schedule (optional)

For development only — use GitHub Actions for 24/7 production.

```bash
./scripts/install-schedule.sh
launchctl bootout gui/$(id -u)/com.georgeliu.twitter-bot   # uninstall
```

## Run

```bash
python run.py --validate         # smoke test: secrets, APIs, Twitter auth
python run.py                    # all indicators
python run.py --indicator vix    # one indicator
```

## Troubleshooting

### GitHub Actions exit code 1

Older runs failed if **any single indicator** errored (e.g. stale MOVE data). Runs now exit 0 for partial failures; check logs for `Warning: N indicator error(s)`.

### No tweets on X

1. Run `python run.py --validate` — must show `OK: Twitter credentials valid (@yourhandle)`
2. Run `python run.py --test-post` (with `DRY_RUN=0`) — confirms posting works end-to-end
3. If `401 Unauthorized` → regenerate **Access Token & Secret** (app needs **Read and write**)
4. If `402 Payment Required` → your X API plan has **no post credits**. Add credits or upgrade at [developer.x.com](https://developer.x.com/) — auth can work while posting is blocked
5. **Zero tweets is normal** on quiet days — thresholds must fire first (bot needs 2+ readings before % rules apply)

### Validate in CI

Every GitHub Actions run calls `python run.py --validate` first. If this step fails, secrets are missing or Twitter auth is broken — fix before expecting live posts.

## Notes

- **ISM PMI data was removed from FRED in 2016.** `pmi_manufacturing` uses the Philadelphia Fed Manufacturing diffusion index; `ism_services` uses the Chicago Fed Nonmanufacturing Activity Index. Both names include "proxy" in tweets.
- **First poll** only stores data; percent/cross rules need a prior reading.
- Tweets are capped at 280 characters.