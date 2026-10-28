"""Hard gates + 0-100 score + human-readable warnings."""
import config


def interp(x, points):
    """Piecewise-linear lookup on (x, y) points sorted by x; clamps at both ends."""
    if x is None:
        return 0.0
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def dex_gates(m):
    """Gates that only need DexScreener data (checked first to save RugCheck calls)."""
    g = config.GATES
    fails = []
    if m["liquidity_usd"] < g["min_liquidity_usd"]:
        fails.append(f"Liquidity ${m['liquidity_usd']/1000:.0f}K < ${g['min_liquidity_usd']/1000:.0f}K")
    if m["age_hours"] is None or m["age_hours"] < g["min_age_hours"]:
        age = "unknown" if m["age_hours"] is None else f"{m['age_hours']:.0f}h"
        fails.append(f"Too new ({age})")
    if m["mcap"] < g["min_mcap_usd"]:
        fails.append(f"Market cap {_money(m['mcap'])} < {_money(g['min_mcap_usd'])}")
    if config.REQUIRE_MEME and m.get("is_meme") is False:
        tags = ", ".join(m.get("jup_tags") or []) or "no meme tag"
        fails.append(f"Not a meme coin ({tags})")
    return fails


def _money(v):
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.0f}K"


def _pct(v):
    return "over 100%" if v >= 100 else f"{v:.1f}%"


def rug_gates(m):
    fails = []
    if not m["mint_revoked"]:
        fails.append("Mint authority active")
    if not m["freeze_revoked"]:
        fails.append("Freeze authority active")
    if m["rugged"]:
        fails.append("Flagged as rugged")
    if m["lp_locked_pct"] is None or m["lp_locked_pct"] < config.GATES["min_lp_locked_pct"]:
        lp = "?" if m["lp_locked_pct"] is None else f"{m['lp_locked_pct']:.0f}%"
        fails.append(f"Main pool LP locked {lp}")
    if m["insider_pct"] > config.GATES["max_insider_pct"]:
        fails.append(f"Insider networks hold {_pct(m['insider_pct'])}")
    fails += [f"RugCheck danger: {r}" for r in m["danger_risks"]]
    return fails


def derived(m):
    txns = m["buys_24h"] + m["sells_24h"]
    return {
        "age_days": (m["age_hours"] or 0) / 24,
        "liq_to_mcap": m["liquidity_usd"] / m["mcap"] if m["mcap"] else 0.0,
        "vol_to_liq": m["volume_24h"] / m["liquidity_usd"] if m["liquidity_usd"] else 0.0,
        "avg_trade_usd": m["volume_24h"] / txns if txns else 0.0,
        "buy_sell_ratio": m["buys_24h"] / m["sells_24h"] if m["sells_24h"] else 0.0,
    }


def score(m):
    """Return (total 0-100, {category: points}, [warnings]) for a coin that passed the gates."""
    c, w = config.CURVES, config.WEIGHTS
    d = derived(m)

    parts = {
        "holders": 0.7 * interp(m["top10_pct"], c["top10_pct"])
                   + 0.3 * interp(m["max_single_pct"], c["max_single_pct"]),
        "insiders": interp(m["insider_pct"], c["insider_pct"]),
        "creator": interp(m["creator_pct"], c["creator_pct"]),
        "liquidity": interp(d["liq_to_mcap"], c["liq_to_mcap"]),
        "age": interp(d["age_days"], c["age_days"]),
        # Organic score (real vs bot trading) is the better signal; avg trade size is the fallback.
        "trading": 0.5 * interp(d["vol_to_liq"], c["vol_to_liq"])
                   + 0.3 * (interp(m["organic_score"], c["organic_score"]) if m.get("organic_score") is not None
                            else interp(d["avg_trade_usd"], c["avg_trade_usd"]))
                   + 0.2 * interp(d["buy_sell_ratio"], c["buy_sell_ratio"]),
        # Community is a proxy until an X integration exists: links + holder count.
        "community": 0.35 * m["has_twitter"] + 0.15 * m["has_website"] + 0.10 * m["has_telegram"]
                     + 0.40 * interp(m["total_holders"], c["total_holders"]),
    }
    if m.get("holder_correlation"):
        parts["holders"] *= config.CORRELATION_HOLDER_FACTOR

    breakdown = {k: round(v * w[k], 1) for k, v in parts.items()}
    total = round(sum(breakdown.values()), 1)
    warns = warnings(m, d)
    org = m.get("organic_score")
    bots = (org < config.ORGANIC_BOT_CAP) if org is not None else (0 < d["avg_trade_usd"] < config.BOT_AVG_TRADE_USD)
    if bots and total > config.BOT_SCORE_CAP:
        total = float(config.BOT_SCORE_CAP)
        why = f"organic score {org:.0f}" if org is not None else f"avg trade ${d['avg_trade_usd']:.0f}"
        warns.insert(0, f"Score capped at {config.BOT_SCORE_CAP}: volume looks mostly bots ({why})")
    return total, breakdown, warns


def warnings(m, d):
    t = config.WARN
    out = []
    if m["creator_pct"] > t["creator_pct"]:
        out.append(f"Creator wallet holds {m['creator_pct']:.1f}%")
    if m["insider_pct"] > t["insider_pct"]:
        out.append(f"Insider networks hold {_pct(m['insider_pct'])}")
    if m.get("holder_correlation"):
        out.append("Top holders look connected (holder points halved)")
    if m["top10_pct"] > t["top10_pct"]:
        out.append(f"Top 10 hold {m['top10_pct']:.1f}%")
    if m["max_single_pct"] > t["max_single_pct"]:
        out.append(f"One wallet holds {m['max_single_pct']:.1f}%")
    if d["vol_to_liq"] > t["vol_to_liq"]:
        out.append(f"Volume {d['vol_to_liq']:.0f}x liquidity (possible wash trading)")
    org = m.get("organic_score")
    if org is not None and org < 50:
        out.append(f"Low organic score {org:.0f} (much of the trading is bots)")
    elif org is None and 0 < d["avg_trade_usd"] < t["avg_trade_usd"]:
        out.append(f"Avg trade ${d['avg_trade_usd']:.0f} (likely bots)")
    hc = m.get("holder_change_24h")
    if hc is not None and hc < -2:
        out.append(f"Holders down {abs(hc):.1f}% in 24h")
    if d["age_days"] < t["age_days"]:
        out.append(f"Only {d['age_days']:.1f} days old")
    if not m["has_twitter"]:
        out.append("No X account linked")
    if m["lp_locked_pct"] is not None and m["lp_locked_pct"] < 100:
        out.append(f"LP {m['lp_locked_pct']:.0f}% locked")
    out += [f"RugCheck: {r}" for r in m["warn_risks"]]
    return out
