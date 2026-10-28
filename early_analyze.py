"""What did early signals predict? Run after the tracker has collected a week or more.

For every coin tracked at least --days ago: did it reach 2x / 5x / 10x (peak of hourly
snapshots), and was it dead at the end? Then the same stats split by each signal.

Read it like this: compare each bucket against the "ALL" baseline. A signal is useful only if
some bucket is clearly better than baseline AND has enough coins (n >= ~30) to trust.

Usage: python early_analyze.py --days 7
"""
import argparse
import time

import config
from screener import early, storage


def fmt(stats):
    return (f"{stats['n']:>5} {stats['hit2']:>6.0%} {stats['hit5']:>6.0%} {stats['hit10']:>6.0%} "
            f"{stats['dead']:>6.0%} {stats['median_final']:>8.2f}x")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=float, default=7)
    ap.add_argument("--db", default=config.DB_PATH)
    args = ap.parse_args()
    con = storage.connect(args.db, config.PRIVATE_DB_PATH)
    try:
        coins = storage.early_coins(con)
    finally:
        con.close()
    rows = early.matured(coins, args.days, time.time() * 1000)
    print(f"{len(coins)} coins tracked, {len(rows)} with a full {args.days:g}-day outcome.\n")
    if len(rows) < 30:
        print("Too few matured coins to conclude anything yet. Keep the hourly tracker running.")
        if not rows:
            return

    header = f"{'':22} {'n':>5} {'≥2x':>6} {'≥5x':>6} {'≥10x':>6} {'dead':>6} {'median end':>9}"
    print(header)
    print(f"{'ALL (baseline)':22} {fmt(early.summarize(rows))}\n")
    for name, groups in early.bucket_table(rows):
        print(name)
        for label, stats in groups:
            flag = "  ← few coins" if stats["n"] < 30 else ""
            print(f"  {label:20} {fmt(stats)}{flag}")
        print()
    print("Peaks come from hourly snapshots, so short spikes are missed. Not financial advice.")


if __name__ == "__main__":
    main()
