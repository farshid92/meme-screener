"""Early tracker: pick young graduated coins, record their signals, measure outcomes later.

Pure functions only (no network/DB) so the logic is easy to test."""
from datetime import datetime

import config


def _ts_ms(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000
    except (AttributeError, ValueError):
        return None


def candidates(tokens, now_ms, known=frozenset()):
    """Filter Jupiter list tokens down to early, graduated, tradable coins we haven't seen yet."""
    e = config.EARLY
    out, seen = [], set()
    for t in tokens:
        mint = t.get("id")
        if not mint or mint in known or mint in seen:
            continue
        created = _ts_ms((t.get("firstPool") or {}).get("createdAt"))
        if created is None or (now_ms - created) / 3_600_000 > e["max_age_h"]:
            continue
        if not (e["min_mcap"] <= (t.get("mcap") or 0) <= e["max_mcap"]):
            continue
        if (t.get("liquidity") or 0) < e["min_liquidity"]:
            continue
        if e["require_graduated"] and t.get("launchpad") and not t.get("graduatedPool"):
            continue
        if set(t.get("tags") or []) & config.JUPITER_EXCLUDE_TAGS:
            continue
        seen.add(mint)
        out.append(t)
    return out


def signals(t, now_ms, rug=None):
    """Snapshot of everything we might later find predictive, at the moment we first see the coin."""
    s1h = t.get("stats1h") or {}
    audit = t.get("audit") or {}
    mcap, liq = t.get("mcap") or 0, t.get("liquidity") or 0
    created = _ts_ms((t.get("firstPool") or {}).get("createdAt"))
    sig = {
        "age_h": round((now_ms - created) / 3_600_000, 2) if created else None,
        "mcap": mcap,
        "liquidity": liq,
        "liq_to_mcap": liq / mcap if mcap else 0,
        "holders": t.get("holderCount"),
        "organic_score": t.get("organicScore"),
        "holder_change_1h": s1h.get("holderChange"),
        "price_change_1h": s1h.get("priceChange"),
        "organic_buyers_1h": s1h.get("numOrganicBuyers"),
        "net_buyers_1h": s1h.get("numNetBuyers"),
        "traders_1h": s1h.get("numTraders"),
        "top_holders_pct": audit.get("topHoldersPercentage"),
        "dev_mints": audit.get("devMints"),
        "dev_migrations": audit.get("devMigrations"),
        "mint_disabled": audit.get("mintAuthorityDisabled"),
        "freeze_disabled": audit.get("freezeAuthorityDisabled"),
        "has_twitter": 1 if t.get("twitter") else 0,
        "has_website": 1 if t.get("website") else 0,
        "launchpad": t.get("launchpad"),
    }
    if rug:
        sig.update({k: rug.get(k) for k in ("insider_pct", "creator_pct", "top10_pct", "lp_locked_pct",
                                               "holder_correlation", "rugged")})
    return sig


def outcome(first_price, first_ms, snapshots, days, now_ms):
    """Outcome of one coin `days` after first sight, from (ts_ms, price, liquidity) snapshots.

    Returns None if the window hasn't passed yet. Peak is the best hourly snapshot (intra-hour
    spikes are missed, so real peaks were somewhat higher)."""
    end = first_ms + days * 86_400_000
    if now_ms < end or not first_price:
        return None
    window = [s for s in snapshots if first_ms <= s[0] <= end]
    if not window:
        return None
    peak = max(p for _, p, _ in window) / first_price
    _, last_price, last_liq = max(window, key=lambda s: s[0])
    final = last_price / first_price
    dead = (last_liq or 0) < config.EARLY["dead_liquidity"] or final < 0.1
    return {"peak_x": peak, "final_x": final, "dead": dead,
            "hit2": peak >= 2, "hit5": peak >= 5, "hit10": peak >= 10}


def matured(coins, days, now_ms):
    """[(signals, outcome)] for tracked coins whose `days` window has passed."""
    rows = []
    for c in coins:
        o = outcome(c["first_price"], c["first_ms"], c["snapshots"], days, now_ms)
        if o:
            rows.append((c["signals"], o))
    return rows


def _bucket_label(edges, i):
    lo, hi = edges[i], edges[i + 1]
    fmt = lambda v: ("-∞" if v <= -1e8 else "∞" if v >= 1e8 else
                     f"{v/1e6:g}M" if abs(v) >= 1e6 else f"{v/1e3:g}K" if abs(v) >= 1e3 else f"{v:g}")
    return f"{fmt(lo)}–{fmt(hi)}"


def summarize(rows):
    """rows: [(signals, outcome)] -> stats dict for a group."""
    n = len(rows)
    if not n:
        return None
    finals = sorted(o["final_x"] for _, o in rows)
    return {"n": n,
            "hit2": sum(o["hit2"] for _, o in rows) / n,
            "hit5": sum(o["hit5"] for _, o in rows) / n,
            "hit10": sum(o["hit10"] for _, o in rows) / n,
            "dead": sum(o["dead"] for _, o in rows) / n,
            "median_final": finals[n // 2]}


def bucket_table(rows, buckets=None):
    """[(signal, [(label, stats)])] grouping matured coins by each signal's value."""
    out = []
    for name, edges in buckets or config.EARLY_BUCKETS:
        groups = []
        for i in range(len(edges) - 1):
            grp = [(s, o) for s, o in rows
                   if s.get(name) is not None and edges[i] <= float(s[name]) < edges[i + 1]]
            stats = summarize(grp)
            if stats:
                groups.append((_bucket_label(edges, i), stats))
        unknown = summarize([(s, o) for s, o in rows if s.get(name) is None])
        if unknown:
            groups.append(("unknown", unknown))
        out.append((name, groups))
    return out
