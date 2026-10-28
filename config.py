"""All tunable rules live here. Change thresholds and weights without touching the code."""

CHAIN = "solana"
import os

DB_PATH = os.environ.get("MEME_DB", "screener.db")
# Set in the cloud: positions/alerts go to this separate (encrypted at rest) database.
# Locally it's empty, so everything stays in screener.db as before.
PRIVATE_DB_PATH = os.environ.get("MEME_PRIVATE_DB") or None

# HTTP behaviour
HTTP_TIMEOUT = 20
RUGCHECK_DELAY_S = 1.5          # pause between RugCheck calls (be polite, avoid 429s)
USER_AGENT = "meme-screener/0.1 (personal research tool)"
MAX_RUGCHECK_CALLS = 300       # per scan; only tokens that pass the DexScreener gates are checked
RUGCHECK_CACHE_HOURS = 12       # reuse a coin's RugCheck result if it's newer than this

# ---- Candidate sources -----------------------------------------------------
# GeckoTerminal free API: ~30 calls/min, 20 pools per page, max 10 pages per list.
GECKO_DELAY_S = 2.2
GECKO_LISTS = [
    # (path under /networks/solana/, query string, pages)
    ("pools", "sort=h24_volume_usd_desc", 10),                 # whole chain by 24h volume
    ("dexes/pumpswap/pools", "sort=h24_volume_usd_desc", 10),  # graduated pump.fun coins
    ("trending_pools", "duration=24h", 5),
]
WATCHLIST_DAYS = 30             # keep re-checking any coin that passed in this window

# Jupiter verified token list (one call, ~4,000 tokens with mcap/liquidity/age/volume).
# Pre-filtered locally with the same gates, so only realistic candidates move on.
JUPITER_ENABLED = True
JUPITER_MIN_VOLUME_24H = 10_000          # skip tokens nobody trades
JUPITER_EXCLUDE_TAGS = {                 # not meme coins
    "stable", "defi", "stocks", "prestocks", "rwa", "yield", "yb", "lst", "major",
    "infra", "equities", "xstocks", "ondo", "commodities", "original-lst",
}

# ---- Meme-only rule --------------------------------------------------------
# A coin counts as a meme unless Jupiter tags it as DeFi/infra/stocks/etc. (JUPITER_EXCLUDE_TAGS)
# or its symbol is a known non-meme project below. Add symbols here when you spot one.
REQUIRE_MEME = True
NON_MEME_SYMBOLS = {"ZEC", "ORE", "GRASS", "IO", "GEOD", "2Z", "AUTO", "PBTC", "NOS", "ARX", "HONEY", "MOBILE",
                    "CODEC", "ALCH", "SONIC", "KMNO", "DRIFT", "TNSR", "CLOUD", "ZEUS", "MET", "BIO"}

# ---- Shortlist: "worth buying now" on top of the safety score --------------
SHORTLIST = {
    "min_score": 70,
    "min_organic": 60,          # Jupiter organic score (0-100): share of real, non-bot trading
    "min_holder_change": 0.0,   # % change in holders over 24h; below 0 = community shrinking
    "max_trend_drop": 3,        # with 3+ days of history: score may not have fallen more than this
    "trend_days": 7,
    "max": 5,                   # how many to show in the digest
}
ORGANIC_BOT_CAP = 30

# ---- Monthly picks: the end-of-month buying shortlist, fully automatic except checking X ----
PICKS = {
    "window_days": 14,          # look back this far
    "min_consistency": 0.5,     # on the shortlist in at least half of the scans in the window...
    "min_days": 3,              # ...and on at least this many different days
    "max_trend_drop": 3,        # score may not have fallen more than this over the window
    "max_pump_7d": 50,          # % — already up more than this in 7 days: don't chase
    "max_dump_7d": -40,         # % — down more than this in 7 days: something is wrong
    "max_insider_pct": 5,       # stricter than the scan gate (10%)
    "max_single_wallet_pct": 5,
    "max_creator_pct": 3,
    "split_within_points": 5,   # if #2 is this close to #1, split the budget over both
    "max_show": 3,
}
TIMEZONE = "Asia/Nicosia"       # for "last day of the month"            # organic score below this also caps the total score at BOT_SCORE_CAP

# Majors, stablecoins and LSTs that show up in volume lists but aren't meme coins.
EXCLUDE_SYMBOLS = {
    "SOL", "WSOL", "USDC", "USDT", "USDG", "PYUSD", "USD1", "USDS", "EURC", "JUP", "JLP",
    "JITOSOL", "MSOL", "BSOL", "JUPSOL", "INF", "CBBTC", "WBTC", "WETH", "ETH", "BTC",
    "RAY", "ORCA", "PYTH", "JTO", "W", "RENDER", "HNT", "ZBTC", "XBTC", "SYRUPUSDC",
}

# ---- Early tracker (paper only: records young coins, measures what happened) -----
EARLY = {
    # Where candidates come from: Jupiter top lists (category, interval)
    "lists": [("toptrending", "1h"), ("toptraded", "1h"), ("toporganicscore", "1h"),
              ("toptrending", "6h"), ("toporganicscore", "6h")],
    "max_age_h": 72,             # "early": first pool created within the last 3 days
    "min_mcap": 30_000,          # skip pre-graduation dust
    "max_mcap": 5_000_000,       # above this it's no longer early
    "min_liquidity": 5_000,
    "require_graduated": True,   # launchpad coins must have left the bonding curve
    "max_new_per_run": 40,       # RugCheck calls per run (1.5s each)
    "track_days": 8,             # keep re-pricing each coin this long
    "dead_liquidity": 5_000,     # below this (or -90%) a coin counts as dead
}
# Signal buckets used by early_analyze.py / the /early page: (signal, [bucket edges])
EARLY_BUCKETS = [
    ("organic_score",      [0, 30, 50, 70, 101]),
    ("insider_pct",        [0, 2, 5, 10, 101]),
    ("top_holders_pct",    [0, 15, 25, 40, 101]),
    ("dev_mints",          [0, 2, 10, 100, 1e9]),   # how many tokens this dev has launched before
    ("holder_change_1h",   [-1e9, 0, 20, 100, 1e9]),
    ("liq_to_mcap",        [0, 0.05, 0.1, 0.2, 10]),
    ("mcap",               [0, 100_000, 500_000, 2_000_000, 1e12]),
    ("age_h",              [0, 6, 24, 72]),
    ("has_twitter",        [0, 1, 2]),
]

# Public dashboard address (set in the cloud workflow); shown at the bottom of the digest
SITE_URL = os.environ.get("MEME_SITE_URL", "")

# ---- Telegram + positions --------------------------------------------------
TELEGRAM_SETTINGS = "telegram.json"   # created by `python telegram_setup.py` (keep it private)
DIGEST_TOP_N = 5
DIGEST_BIG_MOVE = 10                  # score change (points) worth mentioning
MONTHLY_BUDGET_USD = 20

ALERTS = {
    "take_profit_x": 2.0,             # price multiple -> "take out your initial stake"
    "drawdowns": [0.5, 0.7],          # alert once at -50% and once at -70%
    "liquidity_drop": 0.5,            # liquidity below 50% of what it was when you bought
    "creator_sell_pts": 1.0,          # creator wallet share fell by 1+ point since last scan
    "insider_rise_pts": 3.0,          # insider share rose by 3+ points since last scan
}

# ---- Hard gates: failing any of these excludes the coin --------------------
GATES = {
    "min_liquidity_usd": 30_000,
    "min_age_hours": 48,
    "min_lp_locked_pct": 80,     # main pool; a Pump.fun bonding curve counts as locked
    "max_insider_pct": 10,       # linked insider wallets; above this a good score would mislead
    "min_mcap_usd": 500_000,     # tiny coins are too fragile; a few sellers can wipe them out
}

# ---- Score caps / penalties ------------------------------------------------
BOT_AVG_TRADE_USD = 5            # average trade below this = volume is mostly bots
BOT_SCORE_CAP = 60               # ...and the total score can't go above this
CORRELATION_HOLDER_FACTOR = 0.5  # RugCheck "holder correlation" halves the holder points

# ---- Score weights (must add up to 100) ------------------------------------
WEIGHTS = {
    "holders": 25,
    "insiders": 20,
    "creator": 10,
    "liquidity": 15,
    "age": 10,
    "trading": 10,
    "community": 10,
}

# ---- Scoring curves --------------------------------------------------------
# Each curve is a list of (value, fraction_of_points) points; values in between
# are interpolated linearly, values outside are clamped to the nearest end.
CURVES = {
    "top10_pct":      [(15, 1.0), (25, 0.5), (40, 0.0)],
    "max_single_pct": [(3, 1.0), (5, 0.5), (10, 0.0)],
    "insider_pct":    [(2, 1.0), (5, 0.5), (10, 0.0)],
    "creator_pct":    [(1, 1.0), (3, 0.5), (6, 0.0)],
    "liq_to_mcap":    [(0.02, 0.0), (0.05, 0.5), (0.08, 1.0)],
    "age_days":       [(2, 0.0), (7, 0.5), (14, 1.0)],
    "vol_to_liq":     [(0.1, 0.0), (0.5, 0.6), (1, 1.0), (10, 1.0), (30, 0.3)],
    "avg_trade_usd":  [(10, 0.0), (25, 0.5), (50, 1.0)],
    "organic_score":  [(30, 0.0), (50, 0.5), (80, 1.0)],
    "buy_sell_ratio": [(0.3, 0.0), (0.7, 1.0), (2, 1.0), (4, 0.3)],
    "total_holders":  [(300, 0.0), (1000, 0.3), (5000, 1.0)],
}

# ---- Warning thresholds (shown next to the score) --------------------------
WARN = {
    "creator_pct": 3,
    "insider_pct": 5,
    "top10_pct": 25,
    "max_single_pct": 5,
    "vol_to_liq": 30,
    "avg_trade_usd": 15,
    "age_days": 7,
}
