# Meme Screener

Scans Solana meme coins that are getting attention on DexScreener, runs each one through
RugCheck, and gives it a **0–100 safety/health score** with plain-English warnings.

A high score means "worth researching further", not "will go up". Not financial advice.

## How it works

1. **Collect** — several hundred candidates from:
   - Jupiter's verified token list (~4,000 tokens, pre-filtered locally to ~150-250 tradable non-DeFi coins)
   - DexScreener boosted and newly profiled tokens
   - GeckoTerminal: top Solana pools by 24h volume, top PumpSwap pools, trending pools (~1 min, rate-limited)
   - Watchlist: every coin that passed in the last 30 days is re-checked
   - Majors and stablecoins (SOL, USDC, JUP…) are skipped; `scan.py --extra` adds your own mints.
   RugCheck results are cached for 12 hours, so repeat scans are much faster.
2. **Hard gates** — any failure excludes the coin:
   liquidity < $30K · younger than 48h · mint or freeze authority active · main pool < 80% locked ·
   insider networks > 10% · flagged rugged · RugCheck "danger" risk.
3. **Score (0–100)** for coins that pass:

   | Category | Points | Full points when |
   |---|---|---|
   | Holders | 25 | top 10 (excl. pools) < 15%, no wallet > 3% |
   | Insiders | 20 | linked insider wallets < 2% |
   | Creator | 10 | creator wallet < 1% |
   | Liquidity | 15 | liquidity ≥ 8% of market cap |
   | Age | 10 | 14+ days old |
   | Trading | 10 | volume 1–10× liquidity, avg trade ≥ $50, balanced buys/sells |
   | Community | 10 | X + website + Telegram linked, 5K+ holders (proxy until X integration) |

4. **Save** every scan to `screener.db` (SQLite), and keep tracking prices of coins that passed.
5. **Calibrate** with `analyze.py`: do high scores really survive more often?

All thresholds and weights are in `config.py`.

## Setup (Windows)

```powershell
cd meme-screener
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m unittest discover tests     # 8 offline tests, should all pass
```

## Use

```powershell
python scan.py                 # full scan: first run ~3-5 min, later runs faster (cache)
python scan.py --extra MINT    # also check a specific token address
python scan.py --only MINT     # check just one token
python app.py                  # dashboard at http://127.0.0.1:5000 (filter by score, mcap, age; sort by score change)
python analyze.py --days 7     # calibration, once you have a week+ of daily scans
```

## Shortlist (what's worth buying now)

The score measures **safety**. The ⭐ Shortlist adds "is it alive right now", using Jupiter data
fetched for every coin:

- score ≥ 70
- **organic score** ≥ 60 (Jupiter's estimate of real, non-bot trading; below 30 also caps the score at 60)
- **holders not shrinking** over 24h
- **score trend** not falling more than 3 points (applies once a coin has 3+ days of history)

The dashboard opens on the Shortlist tab; each coin shows what it's missing if it isn't on it.
The Telegram digest leads with the shortlist. An empty shortlist means "nothing to buy today".
Rules live in `SHORTLIST` in `config.py`.

Non-meme projects are gated out using Jupiter tags (DeFi, infra, stocks, ...) and
`NON_MEME_SYMBOLS` in `config.py`. Add a symbol there when you spot a project slipping through.

## 🧪 Early tracker (paper only)

Can early signals predict which young coins pump? Instead of guessing, this measures it.
It buys nothing.

- `python early_track.py`, run **hourly**, records young coins that just graduated from a
  launchpad (under 72h old, $30K–$5M market cap) from Jupiter's trending/traded/organic lists,
  with their signals at first sight: organic score, holder growth, top-holder %, dev history
  (how many tokens the dev launched before), RugCheck insiders, socials. It then re-prices
  every tracked coin for 8 days.
- `python early_analyze.py --days 7` (or the **🧪 Early tracker** page on the dashboard) shows,
  for each signal, how often coins reached 2x / 5x / 10x and how often they died, compared
  with the baseline of all coins.

In the cloud this runs hourly automatically (see below). Locally, run it by hand when you like.
You need ~30+ matured coins before reading anything into it, and a few hundred to be confident.

## 🎯 Monthly picks (the buying decision, automated except X)

On the **last day of every month** the daily scan sends a Telegram message with what to buy
(send `/picks` to the bot anytime for the current version; also on the dashboard's 🎯 page):

1. **Consistency:** on the shortlist on ≥ 50% of the last 14 days (min 3 days), score not falling.
2. **No chasing:** not up more than 50% (or down more than 40%) in the last 7 days.
3. **Stricter safety:** insiders ≤ 5%, no wallet > 5%, creator ≤ 3%, top holders not connected
   (RugCheck's network data stands in for Bubblemaps, which has no free API).
4. **Allocation:** the remaining monthly budget goes to #1, or is split over #1 and #2 when they're
   within 5 points. If nothing passes, the message says to skip the month.

Each pick comes with its X link (**the one check you do yourself**: real people posting?),
chart and RugCheck links, and the exact `/buy` command to send after buying.
All thresholds are in `PICKS` in `config.py`.

## ☁️ Running in the cloud (GitHub Actions)

Nothing needs your laptop. `.github/workflows/run.yml` runs on GitHub's servers:

- **Every hour** (`hourly.py`): handles your Telegram commands, runs the early tracker,
  and sends price alerts for your positions (2x, -50%, -70%, liquidity pulled, pairs gone).
- **Every day at ~08:47 Cyprus time** (`scan.py`): full scan, safety alerts, Telegram digest.
- **After every run**: the dashboard is rebuilt as static pages (`freeze.py`) and published to
  GitHub Pages, so you can open it on any device. Positions are never on these pages.

The database lives on the `data` branch, rewritten each run. Your positions, alerts and bot state
are in `private.db.enc`, encrypted with the `POSITIONS_KEY` secret, so the public repo can't read them.

### Positions from Telegram
| Command | What it does |
|---|---|
| `/buy <token address> <amount $> [price]` | record a buy (price defaults to the current price) |
| `/initial <id>` | you took your initial stake out |
| `/sold <id>` | you sold everything |
| `/positions` | open positions with current value |

Commands are processed at the next hourly run.

### One-time setup
1. Repo **Settings → Secrets and variables → Actions → New repository secret**, add:
   `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` (values from your local `telegram.json`) and
   `POSITIONS_KEY` (any long passphrase you make up; losing it means losing positions).
2. **Settings → Pages → Build and deployment → Source: GitHub Actions**.
3. **Actions → meme-screener → Run workflow** (tick "Also run the full daily scan") to test.
4. Turn off the local Windows tasks so you don't get two digests.

Running locally still works exactly as before (`python scan.py`, `python app.py`).

## Telegram digest and alerts

1. In Telegram, open **@BotFather**, send `/newbot`, follow the steps and copy the token.
2. Run `python telegram_setup.py`, paste the token, then send your new bot any message.
3. Done: every `python scan.py` now sends you the daily digest (top coins, new passes, big
   score moves, your positions, budget) plus alerts. `--no-telegram` skips sending.

`telegram.json` holds your bot token. Keep it private.

## Positions (after you buy on your phone)

Open http://127.0.0.1:5000/positions (or "I bought this" on a coin's page) and add the token,
the amount in $, and optionally the buy price (empty = latest scanned price).
Every scan then checks your open positions and alerts you once per event:

- 💰 2x reached and initial not taken out yet → sell your initial stake, then click "Took initial out"
- 📉 down 50% / down 70% from your buy
- 🚨 liquidity below half of what it was when you bought, trading pairs gone, or a safety check now failing
- ⚠️ creator wallet selling, insider share rising

The page also shows this month's spending against `MONTHLY_BUDGET_USD` (default $20).
Thresholds are in `ALERTS` in `config.py`.

## Run it daily (Windows Task Scheduler)

Create a basic task that runs daily (e.g. 09:00) with:
- Program: `C:\path\to\meme-screener\run_daily.bat`
- Start in: `C:\path\to\meme-screener`

Output goes to `scan_log.txt`. Your PC must be on at that time (enable "Run task as soon as
possible after a scheduled start is missed" in the task's Settings tab).

## Known limits / next steps

- "Survived a 50%+ dip" isn't scored yet (no free price-history API); daily snapshots will allow it later.
- Community is a proxy (links + holder count). Next: summarize each coin's recent X posts with an LLM.
- Bubblemaps-style clustering isn't included beyond RugCheck's insider networks.
- APIs are free and rate-limited; if you see HTTP 429s, raise `RUGCHECK_DELAY_S` in `config.py`.
