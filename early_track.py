"""Early tracker (paper trading only, buys nothing). Run hourly.

Each run:
  1. pulls Jupiter's trending / most traded / top organic lists,
  2. records young graduated coins we haven't seen yet, with their signals at that moment
     (organic score, holder growth, top holders, dev history, RugCheck insiders...),
  3. re-prices every coin tracked in the last 8 days.

After a week or more, `python early_analyze.py` shows which early signals actually led to
2x/10x vs. dead coins.

Usage: python early_track.py [-v]
"""
import argparse
import logging
import time
from datetime import datetime, timezone

import config
from screener import early, sources, storage
from screener.metrics import dex_metrics, rug_metrics


def collect(con, now_ms):
    tokens = []
    for cat, interval in config.EARLY["lists"]:
        try:
            tokens += sources.jupiter_top(cat, interval)
        except Exception as e:
            logging.warning("Jupiter %s/%s failed: %s", cat, interval, e)
    return early.candidates(tokens, now_ms, known=storage.early_known(con))


def record_new(con, new, now_ms, now_iso):
    added = 0
    for t in new[:config.EARLY["max_new_per_run"]]:
        rug = storage.cached_rug(con, t["id"], config.RUGCHECK_CACHE_HOURS * 3600, now_ms / 1000)
        if rug is None:
            try:
                rug = rug_metrics(sources.rugcheck_report(t["id"]))
                storage.cache_rug(con, t["id"], rug, now_ms / 1000)
            except Exception as e:
                logging.info("RugCheck failed for %s: %s", t["id"], e)
        sig = early.signals(t, now_ms, rug)
        storage.add_early(con, t["id"], t.get("symbol"), now_iso, now_ms, t.get("usdPrice"), sig)
        storage.add_early_price(con, t["id"], now_ms, t.get("usdPrice"), t.get("liquidity"), t.get("mcap"))
        added += 1
    return added


def reprice(con, now_ms, skip):
    since = now_ms - config.EARLY["track_days"] * 86_400_000
    mints = [m for m in storage.early_active(con, since) if m not in skip]
    if not mints:
        return 0
    pairs = sources.dex_pairs(mints)
    for m in mints:
        if pairs.get(m):
            d = dex_metrics(pairs[m], now_ms)
            storage.add_early_price(con, m, now_ms, d["price_usd"], d["liquidity_usd"], d["mcap"])
        else:  # no pairs left: delisted or rugged
            storage.add_early_price(con, m, now_ms, 0.0, 0.0, 0.0)
    return len(mints)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")

    con = storage.connect(args.db, config.PRIVATE_DB_PATH)
    try:
        run(con)
    finally:
        con.close()


def run(con):
    now_ms = time.time() * 1000
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = collect(con, now_ms)
    added = record_new(con, new, now_ms, now_iso)
    repriced = reprice(con, now_ms, skip={t["id"] for t in new[:added]})
    print(f"{now_iso}  early tracker: +{added} new coins, re-priced {repriced}, "
          f"{len(storage.early_known(con))} tracked in total")

if __name__ == "__main__":
    main()
