"""Calibration: did higher scores actually do better?

For every coin's first passing scan, compare its price N days later.
"Survived" = still has $5K+ liquidity and hasn't lost 90%+.

Usage: python analyze.py --days 7
"""
import argparse
from datetime import datetime, timedelta
from statistics import median

import config
from screener import storage

BUCKETS = [(0, 50), (50, 60), (60, 70), (70, 80), (80, 101)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--db", default=config.DB_PATH)
    args = ap.parse_args()
    con = storage.connect(args.db, config.PRIVATE_DB_PATH)

    firsts = con.execute(
        """SELECT mint, symbol, score, price_usd, scan_id FROM scans
           WHERE passed = 1 AND price_usd > 0 AND id IN
             (SELECT MIN(id) FROM scans WHERE passed = 1 GROUP BY mint)""").fetchall()

    outcomes = []  # (score, return, survived)
    for f in firsts:
        target = (datetime.fromisoformat(f["scan_id"]) + timedelta(days=args.days)).isoformat(timespec="seconds")
        later = con.execute("SELECT price_usd, liquidity FROM prices WHERE mint = ? AND ts >= ? ORDER BY ts LIMIT 1",
                            (f["mint"], target)).fetchone()
        if not later:
            continue
        ret = (later["price_usd"] or 0) / f["price_usd"] - 1
        survived = (later["liquidity"] or 0) >= 5_000 and ret > -0.9
        outcomes.append((f["score"], ret, survived))

    if not outcomes:
        print(f"Not enough history yet: need scans at least {args.days} days apart. Keep scanning daily.")
        return

    print(f"{len(outcomes)} coins with a {args.days}-day outcome\n")
    print(f"{'score':>8} {'coins':>6} {'survived':>9} {'median return':>14}")
    for lo, hi in BUCKETS:
        group = [o for o in outcomes if lo <= o[0] < hi]
        if not group:
            continue
        surv = sum(o[2] for o in group) / len(group)
        print(f"{lo:>3}-{min(hi, 100):<4} {len(group):>6} {surv:>8.0%} {median(o[1] for o in group):>13.0%}")
    print("\nIf higher buckets don't survive more often, adjust WEIGHTS/CURVES in config.py.")


if __name__ == "__main__":
    main()
