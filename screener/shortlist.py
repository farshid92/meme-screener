"""Shortlist: safe coins (score) that also look alive right now (organic trading, growing holders, steady trend)."""
from datetime import datetime

import config

MIN_TREND_SPAN_DAYS = 3  # need at least this much history before the trend rule applies


def trend_for(row, oldest, scan_id):
    """(points change, days of history) vs the oldest passing score in the window, or (None, 0)."""
    if not oldest:
        return None, 0
    old_score, old_scan = oldest
    span = (datetime.fromisoformat(scan_id) - datetime.fromisoformat(old_scan)).total_seconds() / 86400
    return round(row["score"] - old_score, 1), span


def evaluate(row, trend, span_days):
    """Return (on_shortlist, [reasons it misses]) for a passing coin."""
    s = config.SHORTLIST
    m = row.get("metrics") or {}
    misses = []
    if row["score"] < s["min_score"]:
        misses.append(f"score {row['score']:.0f} < {s['min_score']}")
    org = m.get("organic_score")
    if org is None:
        misses.append("no organic score")
    elif org < s["min_organic"]:
        misses.append(f"organic {org:.0f} < {s['min_organic']}")
    hc = m.get("holder_change_24h")
    if hc is None:
        misses.append("no holder data")
    elif hc < s["min_holder_change"]:
        misses.append(f"holders {hc:+.1f}%")
    if trend is not None and span_days >= MIN_TREND_SPAN_DAYS and trend < -s["max_trend_drop"]:
        misses.append(f"score falling ({trend:+.0f} in {span_days:.0f}d)")
    return not misses, misses


def annotate(rows, oldest_scores, scan_id):
    """Add trend/trend_days/shortlisted/shortlist_misses to each passing row (in place); return the shortlist."""
    short = []
    for r in rows:
        if not r.get("passed"):
            continue
        r["trend"], r["trend_days"] = trend_for(r, oldest_scores.get(r["mint"]), scan_id)
        r["shortlisted"], r["shortlist_misses"] = evaluate(r, r["trend"], r["trend_days"])
        if r["shortlisted"]:
            short.append(r)
    # Rank: safety score first, then organic trading, then holder growth
    short.sort(key=lambda r: (-r["score"], -(r["metrics"].get("organic_score") or 0),
                              -(r["metrics"].get("holder_change_24h") or 0)))
    return short
