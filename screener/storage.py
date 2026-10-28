"""SQLite storage: every scan is kept so you can calibrate the score later."""
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL,          -- ISO timestamp shared by one run
    mint TEXT NOT NULL,
    symbol TEXT, name TEXT,
    passed INTEGER NOT NULL,
    score REAL,
    gate_reasons TEXT, breakdown TEXT, warnings TEXT, metrics TEXT,
    price_usd REAL, mcap REAL, liquidity REAL, holders INTEGER
);
CREATE INDEX IF NOT EXISTS idx_scans_scan ON scans(scan_id);
CREATE INDEX IF NOT EXISTS idx_scans_mint ON scans(mint);
-- Cheap price snapshots, also for coins that dropped out of the feeds (used by analyze.py)
CREATE TABLE IF NOT EXISTS prices (
    mint TEXT NOT NULL, ts TEXT NOT NULL,
    price_usd REAL, liquidity REAL, mcap REAL
);
CREATE INDEX IF NOT EXISTS idx_prices_mint ON prices(mint, ts);
-- RugCheck results (already reduced to metrics) so daily scans don't re-fetch everything
-- Early tracker: young coins as first seen, plus hourly price snapshots
CREATE TABLE IF NOT EXISTS early_coins (
    mint TEXT PRIMARY KEY, symbol TEXT, first_seen TEXT NOT NULL, first_ms REAL NOT NULL,
    first_price REAL, signals TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS early_prices (
    mint TEXT NOT NULL, ts_ms REAL NOT NULL, price REAL, liquidity REAL, mcap REAL
);
CREATE INDEX IF NOT EXISTS idx_early_prices ON early_prices(mint, ts_ms);
-- Which coins were on the shortlist in each scan (for the monthly picks)
CREATE TABLE IF NOT EXISTS shortlist_log (
    scan_id TEXT NOT NULL, mint TEXT NOT NULL, PRIMARY KEY (scan_id, mint)
);
CREATE TABLE IF NOT EXISTS rug_cache (
    mint TEXT PRIMARY KEY, fetched_at REAL NOT NULL, metrics TEXT NOT NULL
);
"""

# Your own data: positions, alerts and bot state. In the cloud these live in a separate,
# encrypted database (private.db) so they never appear in the public repo.
PRIVATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS {p}positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mint TEXT NOT NULL, symbol TEXT,
    opened_at TEXT NOT NULL, amount_usd REAL NOT NULL, buy_price REAL,
    liquidity_at_buy REAL,
    initial_taken INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    closed_at TEXT, close_price REAL, note TEXT
);
-- Each alert kind is sent once per position
CREATE TABLE IF NOT EXISTS {p}alerts_sent (
    position_id INTEGER NOT NULL, kind TEXT NOT NULL, sent_at TEXT NOT NULL,
    PRIMARY KEY (position_id, kind)
);
CREATE TABLE IF NOT EXISTS {p}bot_state (key TEXT PRIMARY KEY, value TEXT);
"""
PRIVATE_TABLES = ("positions", "alerts_sent", "bot_state")

JSON_COLS = ("gate_reasons", "breakdown", "warnings", "metrics")


def connect(path, private_path=None):
    """Open the main database. With `private_path`, personal tables live in that separate file
    (attached as `priv`); any personal rows still in the main file are moved over."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    if not private_path:
        con.executescript(PRIVATE_SCHEMA.format(p=""))
        return con
    con.execute("ATTACH DATABASE ? AS priv", (private_path,))
    con.executescript(PRIVATE_SCHEMA.format(p="priv."))
    for t in PRIVATE_TABLES:  # unqualified names must resolve to priv, so main can't keep them
        if con.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name=?", (t,)).fetchone():
            con.execute(f"INSERT OR IGNORE INTO priv.{t} SELECT * FROM main.{t}")
            con.execute(f"DROP TABLE main.{t}")
    con.commit()
    return con


def prune(con, failed_days=14, cache_days=2, now=None):
    """Keep the database small: drop old *failed* scan rows (passing rows and prices are kept for
    calibration) and stale RugCheck cache entries, then compact the file."""
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=failed_days)).isoformat(timespec="seconds")
    con.execute("DELETE FROM scans WHERE passed = 0 AND scan_id < ?", (cutoff,))
    con.execute("DELETE FROM rug_cache WHERE fetched_at < ?", (now.timestamp() - cache_days * 86400,))
    con.commit()
    con.execute("VACUUM")


def get_state(con, key, default=None):
    r = con.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else default


def set_state(con, key, value):
    con.execute("INSERT OR REPLACE INTO bot_state VALUES (?, ?)", (key, str(value)))
    con.commit()


def save(con, scan_id, row):
    con.execute(
        """INSERT INTO scans (scan_id, mint, symbol, name, passed, score, gate_reasons,
           breakdown, warnings, metrics, price_usd, mcap, liquidity, holders)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (scan_id, row["mint"], row["symbol"], row["name"], int(row["passed"]), row.get("score"),
         json.dumps(row.get("gate_reasons", [])), json.dumps(row.get("breakdown", {})),
         json.dumps(row.get("warnings", [])), json.dumps(row.get("metrics", {})),
         row.get("price_usd"), row.get("mcap"), row.get("liquidity"), row.get("holders")),
    )
    con.commit()


def _decode(r):
    d = dict(r)
    for k in JSON_COLS:
        d[k] = json.loads(d[k]) if d.get(k) else ([] if k in ("gate_reasons", "warnings") else {})
    return d


def latest_scan(con):
    row = con.execute("SELECT scan_id FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None, []
    rows = con.execute("SELECT * FROM scans WHERE scan_id = ?", (row["scan_id"],)).fetchall()
    return row["scan_id"], [_decode(r) for r in rows]


def save_price(con, mint, ts, price, liquidity, mcap):
    con.execute("INSERT INTO prices VALUES (?,?,?,?,?)", (mint, ts, price, liquidity, mcap))
    con.commit()


def tracked_mints(con, since_iso):
    """Coins that passed the gates at least once since `since_iso` — keep following them."""
    rows = con.execute("SELECT DISTINCT mint FROM scans WHERE passed = 1 AND scan_id >= ?",
                       (since_iso,)).fetchall()
    return [r["mint"] for r in rows]


def cached_rug(con, mint, max_age_s, now_s):
    row = con.execute("SELECT fetched_at, metrics FROM rug_cache WHERE mint = ?", (mint,)).fetchone()
    if row and now_s - row["fetched_at"] <= max_age_s:
        return json.loads(row["metrics"])
    return None


def cache_rug(con, mint, metrics, now_s):
    con.execute("INSERT OR REPLACE INTO rug_cache VALUES (?,?,?)", (mint, now_s, json.dumps(metrics)))
    con.commit()


def previous_scores(con, scan_id):
    """{mint: score} from each coin's most recent passing scan before `scan_id`."""
    rows = con.execute(
        """SELECT mint, score FROM scans WHERE id IN
             (SELECT MAX(id) FROM scans WHERE scan_id < ? AND passed = 1 GROUP BY mint)""",
        (scan_id,)).fetchall()
    return {r["mint"]: r["score"] for r in rows}


def passed_before(con, scan_id):
    rows = con.execute("SELECT DISTINCT mint FROM scans WHERE passed = 1 AND scan_id < ?", (scan_id,))
    return {r["mint"] for r in rows}


def previous_row(con, mint, scan_id):
    """This coin's most recent scan row before `scan_id`, or None."""
    r = con.execute("SELECT * FROM scans WHERE mint = ? AND scan_id < ? ORDER BY id DESC LIMIT 1",
                    (mint, scan_id)).fetchone()
    return _decode(r) if r else None


def score_trends(con, scan_id, days):
    """{mint: (oldest passing score in the window, its scan_id)} for scans in the `days` before scan_id."""
    from datetime import datetime, timedelta
    since = (datetime.fromisoformat(scan_id) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = con.execute(
        """SELECT mint, score, scan_id FROM scans WHERE id IN
             (SELECT MIN(id) FROM scans WHERE passed = 1 AND scan_id >= ? AND scan_id < ? GROUP BY mint)""",
        (since, scan_id)).fetchall()
    return {r["mint"]: (r["score"], r["scan_id"]) for r in rows}


def latest_price(con, mint):
    r = con.execute("SELECT price_usd, liquidity, ts FROM prices WHERE mint = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
                    (mint,)).fetchone()
    return dict(r) if r else None


def latest_symbol(con, mint):
    r = con.execute("SELECT symbol FROM scans WHERE mint = ? ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
    return r["symbol"] if r else None


# ---- positions ----------------------------------------------------------------

def add_position(con, mint, amount_usd, buy_price, opened_at, symbol=None, liquidity=None, note=None):
    cur = con.execute(
        """INSERT INTO positions (mint, symbol, opened_at, amount_usd, buy_price, liquidity_at_buy, note)
           VALUES (?,?,?,?,?,?,?)""",
        (mint, symbol, opened_at, amount_usd, buy_price, liquidity, note))
    con.commit()
    return cur.lastrowid


def positions(con, status=None):
    q, args = "SELECT * FROM positions", ()
    if status:
        q, args = q + " WHERE status = ?", (status,)
    return [dict(r) for r in con.execute(q + " ORDER BY opened_at DESC, id DESC", args)]


def update_position(con, pid, **fields):
    allowed = {"symbol", "buy_price", "liquidity_at_buy", "initial_taken", "status", "closed_at", "close_price"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE positions SET {sets} WHERE id = ?", (*fields.values(), pid))
    con.commit()


def spent_in_month(con, month_prefix):
    """Total amount_usd of positions opened in a month ('2026-10')."""
    r = con.execute("SELECT COALESCE(SUM(amount_usd), 0) s FROM positions WHERE opened_at LIKE ?",
                    (month_prefix + "%",)).fetchone()
    return r["s"]


def alert_sent(con, pid, kind):
    return con.execute("SELECT 1 FROM alerts_sent WHERE position_id = ? AND kind = ?", (pid, kind)).fetchone() is not None


def mark_alert(con, pid, kind, ts):
    con.execute("INSERT OR IGNORE INTO alerts_sent VALUES (?,?,?)", (pid, kind, ts))
    con.commit()


# ---- shortlist history / monthly picks ---------------------------------------

def log_shortlist(con, scan_id, mints):
    con.executemany("INSERT OR IGNORE INTO shortlist_log VALUES (?, ?)", [(scan_id, m) for m in mints])
    # mark the scan as evaluated even if nothing was shortlisted
    con.execute("INSERT OR IGNORE INTO shortlist_log VALUES (?, '')", (scan_id,))
    con.commit()


def logged_scans(con):
    return {r["scan_id"] for r in con.execute("SELECT DISTINCT scan_id FROM shortlist_log")}


def scan_ids(con, since=None):
    q, a = "SELECT DISTINCT scan_id FROM scans", ()
    if since:
        q, a = q + " WHERE scan_id >= ?", (since,)
    return [r["scan_id"] for r in con.execute(q + " ORDER BY scan_id", a)]


def scan_rows(con, scan_id):
    return [_decode(r) for r in con.execute("SELECT * FROM scans WHERE scan_id = ?", (scan_id,))]


def shortlist_history(con, since):
    """({mint: [scan_ids it was shortlisted in]}, [all evaluated scan_ids]) since `since`."""
    hist, scans = {}, set()
    for r in con.execute("SELECT scan_id, mint FROM shortlist_log WHERE scan_id >= ?", (since,)):
        scans.add(r["scan_id"])
        if r["mint"]:
            hist.setdefault(r["mint"], []).append(r["scan_id"])
    return hist, sorted(scans)


def price_change(con, mint, days, now_iso):
    """% price change over ~`days` from our own snapshots, or None without enough history."""
    from datetime import datetime, timedelta
    now = datetime.fromisoformat(now_iso)
    target = (now - timedelta(days=days)).isoformat(timespec="seconds")
    old = con.execute("SELECT price_usd, ts FROM prices WHERE mint = ? AND ts >= ? AND price_usd > 0 "
                      "ORDER BY ts LIMIT 1", (mint, target)).fetchone()
    new = con.execute("SELECT price_usd FROM prices WHERE mint = ? AND ts <= ? AND price_usd > 0 "
                      "ORDER BY ts DESC LIMIT 1", (mint, now_iso)).fetchone()
    if not old or not new:
        return None
    span = (now - datetime.fromisoformat(old["ts"])).total_seconds() / 86400
    if span < days * 0.7:          # oldest snapshot too recent to say anything about a week
        return None
    return (new["price_usd"] / old["price_usd"] - 1) * 100


def score_at_start(con, mint, since):
    r = con.execute("SELECT score FROM scans WHERE mint = ? AND passed = 1 AND scan_id >= ? "
                    "ORDER BY scan_id LIMIT 1", (mint, since)).fetchone()
    return r["score"] if r else None


# ---- early tracker ------------------------------------------------------------

def early_known(con):
    return {r["mint"] for r in con.execute("SELECT mint FROM early_coins")}


def add_early(con, mint, symbol, first_seen, first_ms, first_price, signals):
    con.execute("INSERT OR IGNORE INTO early_coins VALUES (?,?,?,?,?,?)",
                (mint, symbol, first_seen, first_ms, first_price, json.dumps(signals)))
    con.commit()


def early_active(con, since_ms):
    return [r["mint"] for r in con.execute("SELECT mint FROM early_coins WHERE first_ms >= ?", (since_ms,))]


def add_early_price(con, mint, ts_ms, price, liquidity, mcap):
    con.execute("INSERT INTO early_prices VALUES (?,?,?,?,?)", (mint, ts_ms, price, liquidity, mcap))
    con.commit()


def early_coins(con):
    """All tracked coins with decoded signals and their snapshots [(ts_ms, price, liquidity)]."""
    snaps = {}
    for r in con.execute("SELECT mint, ts_ms, price, liquidity FROM early_prices ORDER BY ts_ms"):
        snaps.setdefault(r["mint"], []).append((r["ts_ms"], r["price"], r["liquidity"]))
    out = []
    for r in con.execute("SELECT * FROM early_coins ORDER BY first_ms DESC"):
        d = dict(r)
        d["signals"] = json.loads(d["signals"])
        d["snapshots"] = snaps.get(d["mint"], [])
        out.append(d)
    return out


def token_history(con, mint):
    rows = con.execute("SELECT * FROM scans WHERE mint = ? ORDER BY id", (mint,)).fetchall()
    return [_decode(r) for r in rows]
