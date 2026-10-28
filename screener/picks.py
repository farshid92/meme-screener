"""Monthly picks: the end-of-month buying decision, automated except for checking X.

Steps (all automatic):
  1. consistency: on the shortlist on most days of the last 2 weeks, score not falling
  2. no chasing: not already pumped (or dumped) hard in the last 7 days
  3. safety: stricter limits on insiders, single wallets, creator wallet, connected holders
  4. allocation: one coin gets the budget, or two split it when they're neck and neck
"""
from datetime import datetime, timedelta, timezone
from html import escape

import config
from screener import shortlist, storage


def _day(scan_id):
    return scan_id[:10]


def ensure_logged(con, since):
    """Record shortlist membership for scans that have Jupiter data but weren't logged yet."""
    done = storage.logged_scans(con)
    for sid in storage.scan_ids(con, since):
        if sid in done:
            continue
        rows = storage.scan_rows(con, sid)
        if not any(r["passed"] and r["metrics"].get("organic_score") is not None for r in rows):
            continue  # scanned before organic scores existed: can't judge, don't count the day
        short = shortlist.annotate(rows, storage.score_trends(con, sid, config.SHORTLIST["trend_days"]), sid)
        storage.log_shortlist(con, sid, [r["mint"] for r in short])


def check(row, days_on, total_days, trend, change_7d):
    """Return (rank, [reasons it's excluded]) for one coin that passed today's scan."""
    p, m = config.PICKS, row.get("metrics") or {}
    why = []
    consistency = days_on / total_days if total_days else 0
    if days_on < p["min_days"] or consistency < p["min_consistency"]:
        why.append(f"on shortlist only {days_on}/{total_days} days")
    if trend is not None and trend < -p["max_trend_drop"]:
        why.append(f"score falling ({trend:+.0f})")
    if change_7d is not None and change_7d > p["max_pump_7d"]:
        why.append(f"already up {change_7d:.0f}% in 7d (don't chase)")
    if change_7d is not None and change_7d < p["max_dump_7d"]:
        why.append(f"down {abs(change_7d):.0f}% in 7d")
    if (m.get("insider_pct") or 0) > p["max_insider_pct"]:
        why.append(f"insiders {m['insider_pct']:.1f}%")
    if (m.get("max_single_pct") or 0) > p["max_single_wallet_pct"]:
        why.append(f"one wallet {m['max_single_pct']:.1f}%")
    if (m.get("creator_pct") or 0) > p["max_creator_pct"]:
        why.append(f"creator {m['creator_pct']:.1f}%")
    if m.get("holder_correlation"):
        why.append("top holders look connected")
    return row["score"] + 10 * consistency, why


def allocate(picks, budget_left):
    """[(row, $)]: all to #1, or split when #2 is within `split_within_points`."""
    if not picks or budget_left <= 0:
        return []
    if len(picks) >= 2 and picks[0]["rank"] - picks[1]["rank"] <= config.PICKS["split_within_points"]:
        half = round(budget_left / 2, 2)
        return [(picks[0], half), (picks[1], half)]
    return [(picks[0], budget_left)]


def compute(con, now_iso=None):
    """Picks from the latest scan. Returns dict with picks, allocation inputs and rejections."""
    p = config.PICKS
    scan_id, rows = storage.latest_scan(con)
    if not scan_id:
        return {"scan_id": None, "picks": [], "rejected": [], "total_days": 0}
    now_iso = now_iso or scan_id
    since = (datetime.fromisoformat(scan_id) - timedelta(days=p["window_days"])).isoformat(timespec="seconds")
    ensure_logged(con, since)
    hist, scans = storage.shortlist_history(con, since)
    total_days = len({_day(s) for s in scans})
    picks, rejected = [], []
    for r in rows:
        if not r["passed"]:
            continue
        days_on = len({_day(s) for s in hist.get(r["mint"], [])})
        if days_on == 0:
            continue  # never shortlisted in the window: not a candidate at all
        start = storage.score_at_start(con, r["mint"], since)
        r["trend"] = round(r["score"] - start, 1) if start is not None else None
        r["change_7d"] = storage.price_change(con, r["mint"], 7, now_iso)
        r["days_on"], r["total_days"] = days_on, total_days
        r["rank"], r["why_not"] = check(r, days_on, total_days, r["trend"], r["change_7d"])
        (rejected if r["why_not"] else picks).append(r)
    picks.sort(key=lambda r: -r["rank"])
    rejected.sort(key=lambda r: -r["rank"])
    return {"scan_id": scan_id, "picks": picks, "rejected": rejected, "total_days": total_days}


def _line(r):
    m = r.get("metrics") or {}
    bits = [f"score {r['score']:.0f}", f"on shortlist {r['days_on']}/{r['total_days']} days",
            f"score {r['trend']:+.0f}" if r.get("trend") is not None else None,
            f"7d {r['change_7d']:+.0f}%" if r.get("change_7d") is not None else "7d: not enough history",
            f"organic {m['organic_score']:.0f}" if m.get("organic_score") is not None else None,
            f"holders {m['holder_change_24h']:+.1f}%/24h" if m.get("holder_change_24h") is not None else None]
    return " · ".join(b for b in bits if b)


def message(result, budget_left, month_label):
    """Telegram text for the monthly decision."""
    lines = [f"<b>🎯 Monthly picks · {month_label}</b>", f"Budget left this month: ${budget_left:.0f}", ""]
    alloc = allocate(result["picks"], budget_left)
    if result["total_days"] < config.PICKS["min_days"]:
        lines.append(f"⚠️ Only {result['total_days']} day(s) of shortlist history so far, so picks are weak. "
                     "They get reliable after ~2 weeks of daily scans.\n")
    if budget_left <= 0:
        lines.append("Budget for this month is used up. No buy this month.")
    elif not alloc:
        lines.append("No coin passed every check, so <b>skip buying this month</b>. That's the rules working.")
    for i, (r, usd) in enumerate(alloc, 1):
        m = r.get("metrics") or {}
        links = [f'<a href="{m.get("dex_url") or "https://dexscreener.com/solana/" + r["mint"]}">chart</a>',
                 f'<a href="https://rugcheck.xyz/tokens/{r["mint"]}">RugCheck</a>']
        x = f'<a href="{m["twitter_url"]}">X account</a>' if m.get("twitter_url") else "no X account linked (be careful)"
        lines += [f"{i}. <b>{escape(r['symbol'])}</b> → buy <b>${usd:.0f}</b>",
                  "   " + _line(r),
                  f"   👉 Your check: {x}. Real people posting? If not, skip it.",
                  "   " + " · ".join(links),
                  f"   After buying, send: <code>/buy {r['mint']} {usd:.0f}</code>", ""]
    others = [r for r in result["picks"] if r not in [a[0] for a in alloc]][:2]
    if others:
        lines.append("Runners-up: " + ", ".join(f"{escape(r['symbol'])} ({r['score']:.0f})" for r in others))
    if result["rejected"]:
        lines.append("Not picked: " + "; ".join(f"{escape(r['symbol'])} ({r['why_not'][0]})"
                                                for r in result["rejected"][:6]))
    lines.append("\nRules after buying: at 2x take your stake out (/initial), at −50% with a dying community let it go.")
    return "\n".join(lines)


def is_last_day_of_month(now=None):
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(config.TIMEZONE)
    except Exception:
        tz = timezone(timedelta(hours=2))
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    return (now + timedelta(days=1)).month != now.month


def month_label(now=None):
    return (now or datetime.now(timezone.utc)).strftime("%B %Y")
