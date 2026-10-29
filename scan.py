"""Run one scan: collect candidates, gate, score, save, print.

Usage:
    python scan.py                      # full scan: DexScreener + GeckoTerminal + watchlist
    python scan.py --extra MINT MINT    # also check specific token addresses
    python scan.py --only MINT          # check just these tokens
"""
import argparse
from html import escape
import logging
import re
import time
from datetime import datetime, timedelta, timezone

import config
from screener import net, picks, scoring, shortlist, sources, storage, telegram
from screener.metrics import dex_metrics, jup_metrics, rug_metrics
from screener.positions import build_digest, check_position


def evaluate(pairs, report_fn, now_ms, rug_fn=None, jup=None):
    """Evaluate one token.
    report_fn(mint) returns a raw RugCheck report (lets tests inject data);
    rug_fn(mint), if given, returns ready-made RugCheck metrics instead (used for caching);
    jup is the token's Jupiter info (organic score, holder change, tags) or None."""
    m = dex_metrics(pairs, now_ms)
    m.update(jup_metrics(m["mint"], jup))
    row = {"mint": m["mint"], "symbol": m["symbol"], "name": m["name"],
           "price_usd": m["price_usd"], "mcap": m["mcap"], "liquidity": m["liquidity_usd"]}

    fails = scoring.dex_gates(m)
    if fails:
        return {**row, "passed": False, "gate_reasons": fails, "metrics": m}

    m.update(rug_fn(m["mint"]) if rug_fn else rug_metrics(report_fn(m["mint"])))
    row["holders"] = m["total_holders"]
    fails = scoring.rug_gates(m)
    if fails:
        return {**row, "passed": False, "gate_reasons": fails, "metrics": m}

    total, breakdown, warns = scoring.score(m)
    return {**row, "passed": True, "score": total, "breakdown": breakdown,
            "warnings": warns, "metrics": m}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extra", nargs="*", default=[], help="extra token mints to include")
    ap.add_argument("--only", nargs="*", default=None, help="scan only these token mints")
    ap.add_argument("--db", default=config.DB_PATH)
    ap.add_argument("--no-telegram", action="store_true", help="don't send the digest or alerts")
    ap.add_argument("--picks", action="store_true", help="also send the monthly picks now")
    ap.add_argument("--if-due", action="store_true",
                    help="only scan if today's scan hasn't happened yet (for frequent schedulers)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    if args.if_due:
        con = storage.connect(args.db, config.PRIVATE_DB_PATH)
        try:
            last, _ = storage.latest_scan(con)
        finally:
            con.close()
        due, why = scan_due(last)
        print(f"Daily scan {'due' if due else 'not due'}: {why}")
        if not due:
            return
    if not net.wait_for_network():
        print("No network after 5 minutes; giving up.")
        raise SystemExit(1)
    try:
        run_scan(args)
    except Exception as e:
        if not args.no_telegram:
            telegram.send(f"⚠️ Daily meme scan failed: {escape(type(e).__name__)}: {escape(str(e)[:300])}")
        raise


def scan_due(last_scan_id, now=None):
    """One scan per day, on the first run after DAILY_SCAN_HOUR local time.
    Works no matter which (if any) of the scheduled runs actually fire."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(config.TIMEZONE)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    if not last_scan_id:
        return True, "no scan yet"
    last = datetime.fromisoformat(last_scan_id).astimezone(tz)
    if last.date() >= now.date():
        return False, f"already scanned today at {last:%H:%M}"
    if now.hour < config.DAILY_SCAN_HOUR:
        return False, f"waiting until {config.DAILY_SCAN_HOUR}:00 local"
    return True, f"last scan {last:%Y-%m-%d %H:%M}"


def run_scan(args):
    con = storage.connect(args.db, config.PRIVATE_DB_PATH)
    held = [p["mint"] for p in storage.positions(con, "open")]
    mints = held + (args.only if args.only else collect_candidates(con) + args.extra)
    mints = list(dict.fromkeys(mints))
    print(f"Candidates: {len(mints)}  (fetching DexScreener data...)")
    pairs_by_mint = sources.dex_pairs(mints)
    print(f"Fetching Jupiter organic scores for {len(pairs_by_mint)} coins...")
    jup_by_mint = sources.jupiter_info(list(pairs_by_mint))

    scan_id = datetime.now(timezone.utc).isoformat(timespec="seconds")
    now_ms = time.time() * 1000
    now_s = now_ms / 1000
    stats = {"rug_calls": 0, "cache_hits": 0, "limit_hit": 0, "excluded": 0}
    results = []

    def rug_fn(addr):
        cached = storage.cached_rug(con, addr, config.RUGCHECK_CACHE_HOURS * 3600, now_s)
        if cached is not None:
            stats["cache_hits"] += 1
            return cached
        if stats["rug_calls"] >= config.MAX_RUGCHECK_CALLS:
            stats["limit_hit"] += 1
            raise RuntimeError("RugCheck call limit for this scan reached")
        stats["rug_calls"] += 1
        metrics = rug_metrics(sources.rugcheck_report(addr))
        storage.cache_rug(con, addr, metrics, now_s)
        return metrics

    for i, mint in enumerate(mints, 1):
        pairs = pairs_by_mint.get(mint)
        if not pairs:
            continue
        symbol = ((pairs[0].get("baseToken") or {}).get("symbol") or "").upper()
        if symbol in config.EXCLUDE_SYMBOLS:
            stats["excluded"] += 1
            continue
        try:
            row = evaluate(pairs, None, now_ms, rug_fn=rug_fn, jup=jup_by_mint.get(mint))
        except Exception as e:
            logging.info("Skipping %s: %s", mint, e)
            continue
        storage.save(con, scan_id, row)
        storage.save_price(con, mint, scan_id, row["price_usd"], row["liquidity"], row["mcap"])
        results.append(row)
        if row["passed"]:
            print(f"  [{i}/{len(mints)}] {row['score']:5.1f}  {row['symbol'][:12]:12}  {mint}")
        elif i % 50 == 0:
            print(f"  [{i}/{len(mints)}] ...")

    track_previous(con, scan_id, {r["mint"] for r in results})
    print(f"\nRugCheck: {stats['rug_calls']} fetched, {stats['cache_hits']} from cache"
          + (f", {stats['limit_hit']} skipped (limit {config.MAX_RUGCHECK_CALLS} reached; run again later)"
             if stats["limit_hit"] else "")
          + f". Excluded {stats['excluded']} majors/stablecoins.")
    print_summary(results)
    print(f"\nSaved scan {scan_id} to {args.db}. Run `python app.py` to open the dashboard.")

    alerts = position_alerts(con, scan_id, results, pairs_by_mint)
    for a in alerts:
        print("ALERT:", re.sub(r"<[^>]+>", "", a))
    digest = make_digest(con, scan_id, results)
    if not args.no_telegram:
        settings = telegram.load_settings()
        if not settings:
            print("Telegram not set up (run `python telegram_setup.py` to get the digest on your phone).")
        else:
            sent = all([telegram.send(a, settings) for a in alerts] + [telegram.send(digest, settings)])
            print("Telegram: digest" + (f" + {len(alerts)} alert(s)" if alerts else "") + (" sent." if sent else " had errors."))
            if picks.is_last_day_of_month() or args.picks:
                telegram.send(monthly_picks_message(con), settings)
                print("Telegram: monthly picks sent.")


def monthly_picks_message(con):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    left = config.MONTHLY_BUDGET_USD - storage.spent_in_month(con, month)
    return picks.message(picks.compute(con), max(0, left), picks.month_label())


def position_alerts(con, scan_id, results, pairs_by_mint, include_deltas=True):
    """Check every open position against this scan; return new alert messages (each kind sent once)."""
    by_mint = {r["mint"]: r for r in results}
    out = []
    for p in storage.positions(con, "open"):
        cur = by_mint.get(p["mint"])
        if cur:  # fill in details we didn't know when the position was entered
            fill = {}
            if not p.get("symbol"):
                fill["symbol"] = p["symbol"] = cur["symbol"]
            if not p.get("buy_price") and cur.get("price_usd"):
                fill["buy_price"] = p["buy_price"] = cur["price_usd"]
            if not p.get("liquidity_at_buy") and cur.get("liquidity"):
                fill["liquidity_at_buy"] = p["liquidity_at_buy"] = cur["liquidity"]
            storage.update_position(con, p["id"], **fill)
        prev = storage.previous_row(con, p["mint"], scan_id) if include_deltas else None
        for kind, msg in check_position(p, cur, prev, has_pairs=bool(pairs_by_mint.get(p["mint"]))):
            if not storage.alert_sent(con, p["id"], kind):
                storage.mark_alert(con, p["id"], kind, scan_id)
                out.append(msg)
    return out


def make_digest(con, scan_id, results):
    open_pos = storage.positions(con, "open")
    prices = {}
    for p in open_pos:
        lp = storage.latest_price(con, p["mint"])
        if lp and lp["price_usd"]:
            prices[p["mint"]] = lp["price_usd"]
    short = shortlist.annotate(results, storage.score_trends(con, scan_id, config.SHORTLIST["trend_days"]), scan_id)
    storage.log_shortlist(con, scan_id, [r["mint"] for r in short])
    print(f"Shortlist: {len(short)} coin(s)" + (": " + ", ".join(r["symbol"] for r in short[:10]) if short else ""))
    return build_digest(scan_id, results, storage.previous_scores(con, scan_id),
                        storage.passed_before(con, scan_id), open_pos, prices,
                        storage.spent_in_month(con, scan_id[:7]), config.MONTHLY_BUDGET_USD,
                        shortlist=short)


def collect_candidates(con):
    """DexScreener boosts/profiles + GeckoTerminal pool lists + watchlist of past passes."""
    dex = sources.candidate_tokens()
    print(f"DexScreener boosts/profiles: {len(dex)}")
    print("Loading GeckoTerminal pool lists (about 1 minute)...")
    gecko = sources.gecko_tokens()
    print(f"GeckoTerminal: {len(gecko)}")
    jup = []
    if config.JUPITER_ENABLED:
        try:
            tokens = sources.jupiter_verified()
            jup = sources.jupiter_candidates(tokens, time.time() * 1000)
            print(f"Jupiter verified: {len(tokens)} tokens, {len(jup)} pass pre-filters")
        except Exception as e:
            print(f"Jupiter list failed, continuing without it: {e}")
    since = (datetime.now(timezone.utc) - timedelta(days=config.WATCHLIST_DAYS)).isoformat(timespec="seconds")
    watch = storage.tracked_mints(con, since)
    print(f"Watchlist (passed in last {config.WATCHLIST_DAYS} days): {len(watch)}")
    return watch + jup + dex + gecko


def track_previous(con, scan_id, already_seen):
    """Record prices of coins that passed in the last 30 days but weren't in this scan.
    A coin with no pairs left is stored with zero liquidity (counts as dead in analyze.py)."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    mints = [m for m in storage.tracked_mints(con, since) if m not in already_seen]
    if not mints:
        return
    try:
        pairs_by_mint = sources.dex_pairs(mints)
    except Exception as e:
        logging.warning("Price tracking skipped: %s", e)
        return
    now_ms = time.time() * 1000
    for mint in mints:
        pairs = pairs_by_mint.get(mint)
        if pairs:
            m = dex_metrics(pairs, now_ms)
            storage.save_price(con, mint, scan_id, m["price_usd"], m["liquidity_usd"], m["mcap"])
        else:
            storage.save_price(con, mint, scan_id, 0.0, 0.0, 0.0)
    print(f"Tracked prices for {len(mints)} previously passing coins")


def print_summary(results):
    passed = sorted((r for r in results if r["passed"]), key=lambda r: -r["score"])
    print(f"\n{len(passed)} passed / {len(results)} checked\n")
    for r in passed:
        warn = "; ".join(r["warnings"][:3]) or "no warnings"
        print(f"{r['score']:5.1f}  {r['symbol'][:12]:12}  mcap ${r['mcap']/1e6:6.2f}M  {warn}")


if __name__ == "__main__":
    main()
