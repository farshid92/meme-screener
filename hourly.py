"""Hourly cloud job: Telegram commands, early tracker, quick price alerts for your positions.

The full safety checks and the digest stay in the daily scan (scan.py).
Usage: python hourly.py
"""
import logging
import time
from datetime import datetime, timezone

import config
import early_track
from scan import position_alerts
from screener import bot, net, sources, storage, telegram
from screener.metrics import dex_metrics


def live_price(mint):
    """(price, liquidity, symbol) from DexScreener right now, or None."""
    pairs = sources.dex_pairs([mint]).get(mint)
    if not pairs:
        return None
    m = dex_metrics(pairs, time.time() * 1000)
    return m["price_usd"], m["liquidity_usd"], m["symbol"]


def price_alerts(con, ts):
    """Price-only checks for open positions: 2x, -50/-70%, liquidity pulled, pairs gone."""
    open_pos = storage.positions(con, "open")
    if not open_pos:
        return []
    pairs = sources.dex_pairs([p["mint"] for p in open_pos])
    now_ms = time.time() * 1000
    rows = []
    for p in open_pos:
        if pairs.get(p["mint"]):
            m = dex_metrics(pairs[p["mint"]], now_ms)
            rows.append({"mint": p["mint"], "symbol": m["symbol"], "price_usd": m["price_usd"],
                         "liquidity": m["liquidity_usd"], "gate_reasons": [], "metrics": {}})
    # previous_row needs a scan id; hourly has none, so safety deltas wait for the daily scan
    return position_alerts(con, ts, rows, pairs, include_deltas=False)


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    if not net.wait_for_network():
        raise SystemExit("No network after 5 minutes")
    settings = telegram.load_settings()
    con = storage.connect(config.DB_PATH, config.PRIVATE_DB_PATH)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        if settings:
            try:
                n = bot.poll(con, settings, live_price)
                print(f"Telegram commands handled: {n}")
            except Exception as e:  # never let a bad command stop tracking
                print(f"Telegram command polling failed: {e}")
        early_track.run(con)
        alerts = price_alerts(con, ts)
        for a in alerts:
            print("ALERT:", a)
            if settings:
                telegram.send(a, settings)
    finally:
        con.close()


if __name__ == "__main__":
    main()
