"""Alert rules for your positions and the daily digest text (pure functions, easy to test)."""
from html import escape

import config

DANGER_GATE_WORDS = ("Mint authority", "Freeze authority", "rugged", "LP locked", "Insider networks", "RugCheck danger")


def check_position(pos, cur, prev, has_pairs):
    """Return [(kind, message)] for one open position.

    pos: positions row; cur: this scan's row for the coin (or None);
    prev: the previous scan row (or None); has_pairs: DexScreener still lists trading pairs.
    Each kind is only sent once per position (deduplicated by the caller)."""
    a = config.ALERTS
    sym = escape(pos.get("symbol") or pos["mint"][:6])
    out = []

    if not has_pairs:
        return [("dead", f"🚨 <b>{sym}</b>: no trading pairs found anymore. It may have been rugged or delisted.")]
    if cur is None:
        return out

    price, buy = cur.get("price_usd") or 0, pos.get("buy_price") or 0
    if buy > 0 and price > 0:
        x = price / buy
        if x >= a["take_profit_x"] and not pos.get("initial_taken"):
            out.append(("take_profit", f"💰 <b>{sym}</b> is at {x:.1f}x. Rule: sell enough to take out your "
                                        f"initial ${pos['amount_usd']:.0f}, then mark it on the dashboard."))
        for level in a["drawdowns"]:
            if x <= 1 - level:
                out.append((f"down{int(level * 100)}",
                            f"📉 <b>{sym}</b> is down {(1 - x) * 100:.0f}% from your buy. "
                            f"Check if the community is still active; if not, let it go."))

    liq_buy, liq_now = pos.get("liquidity_at_buy") or 0, cur.get("liquidity") or 0
    if liq_buy > 0 and liq_now < liq_buy * a["liquidity_drop"]:
        out.append(("liquidity_drop", f"🚨 <b>{sym}</b>: liquidity fell from ${liq_buy/1e3:.0f}K to "
                                      f"${liq_now/1e3:.0f}K since you bought. Exits are getting thin."))

    for reason in cur.get("gate_reasons") or []:
        word = next((w for w in DANGER_GATE_WORDS if w in reason), None)
        if word:  # kind uses the check name, not the number, so it isn't re-sent when the % changes
            out.append((f"gate:{word}", f"🚨 <b>{sym}</b> now fails a safety check: {escape(reason)}"))

    m, pm = cur.get("metrics") or {}, (prev or {}).get("metrics") or {}
    if "creator_pct" in m and "creator_pct" in pm:
        if pm["creator_pct"] - m["creator_pct"] >= a["creator_sell_pts"]:
            out.append(("creator_sell", f"⚠️ <b>{sym}</b>: creator wallet went from {pm['creator_pct']:.1f}% "
                                        f"to {m['creator_pct']:.1f}%. The dev is selling."))
    if "insider_pct" in m and "insider_pct" in pm:
        if m["insider_pct"] - pm["insider_pct"] >= a["insider_rise_pts"]:
            out.append(("insider_rise", f"⚠️ <b>{sym}</b>: insider networks grew from {pm['insider_pct']:.1f}% "
                                        f"to {m['insider_pct']:.1f}%."))
    return out


def _money(v):
    v = v or 0
    return f"${v/1e6:.1f}M" if v >= 1e6 else f"${v/1e3:.0f}K"


def _shortlist_line(i, r):
    m = r.get("metrics") or {}
    url = m.get("dex_url") or f"https://rugcheck.xyz/tokens/{r['mint']}"
    bits = [f"{i}. <a href=\"{url}\">{escape(r['symbol'])}</a> {r['score']:.0f}",
            f"organic {m['organic_score']:.0f}" if m.get("organic_score") is not None else None,
            f"holders {m['holder_change_24h']:+.1f}%" if m.get("holder_change_24h") is not None else None,
            (f"{r['trend_days']:.0f}d {'▲' if r['trend'] >= 0 else '▼'}{abs(r['trend']):.0f}"
             if r.get("trend") is not None and r.get("trend_days", 0) >= 1 else None),
            _money(r["mcap"])]
    return " · ".join(b for b in bits if b)


def build_digest(scan_id, results, prev_scores, passed_before, open_positions, prices, spent, budget,
                 shortlist=None):
    """Daily Telegram summary. `prices` maps mint -> current price for your positions."""
    passed = sorted((r for r in results if r["passed"]), key=lambda r: -r["score"])
    lines = [f"<b>🗞 Meme screener · {scan_id[:10]}</b>",
             f"{len(passed)} passed / {len(results)} checked", ""]

    if shortlist is not None:
        if shortlist:
            lines.append(f"<b>⭐ Shortlist ({len(shortlist)})</b>")
            lines += [_shortlist_line(i, r) for i, r in enumerate(shortlist[:config.SHORTLIST["max"]], 1)]
            if len(shortlist) > config.SHORTLIST["max"]:
                lines.append(f"+{len(shortlist) - config.SHORTLIST['max']} more on the dashboard")
        else:
            lines.append("<b>⭐ Shortlist:</b> none today. No coin met every rule, so nothing to buy.")
        lines.append("")

    if passed:
        lines.append(f"<b>Top {min(config.DIGEST_TOP_N, len(passed))} by safety score</b>")
        for i, r in enumerate(passed[:config.DIGEST_TOP_N], 1):
            prev = prev_scores.get(r["mint"])
            delta = "new" if prev is None else (f"▲{r['score'] - prev:.0f}" if r["score"] > prev + 0.5
                                               else f"▼{prev - r['score']:.0f}" if r["score"] < prev - 0.5 else "–")
            url = (r.get("metrics") or {}).get("dex_url") or f"https://rugcheck.xyz/tokens/{r['mint']}"
            warn = f" · ⚠ {escape(r['warnings'][0])}" if r.get("warnings") else ""
            lines.append(f"{i}. <a href=\"{url}\">{escape(r['symbol'])}</a> {r['score']:.0f} ({delta}) · "
                         f"{_money(r['mcap'])}{warn}")
        lines.append("")

    new = [r for r in passed if r["mint"] not in passed_before]
    if new:
        lines.append("<b>New today:</b> " + ", ".join(f"{escape(r['symbol'])} ({r['score']:.0f})" for r in new[:8])
                     + (f" +{len(new) - 8} more" if len(new) > 8 else ""))
    movers = [(r, r["score"] - prev_scores[r["mint"]]) for r in passed if r["mint"] in prev_scores]
    movers = [(r, d) for r, d in movers if abs(d) >= config.DIGEST_BIG_MOVE]
    if movers:
        lines.append("<b>Big moves:</b> " + ", ".join(
            f"{escape(r['symbol'])} {prev_scores[r['mint']]:.0f}→{r['score']:.0f}" for r, _ in movers[:8]))
    if new or movers:
        lines.append("")

    if open_positions:
        lines.append("<b>Your positions</b>")
        for p in open_positions:
            price, buy = prices.get(p["mint"]), p.get("buy_price")
            sym = escape(p.get("symbol") or p["mint"][:6])
            if price and buy:
                x = price / buy
                lines.append(f"{sym}: ${p['amount_usd']:.0f} → ${p['amount_usd'] * x:.0f} ({(x - 1) * 100:+.0f}%)")
            else:
                lines.append(f"{sym}: ${p['amount_usd']:.0f} (no price yet)")
        lines.append("")
    lines.append(f"Budget this month: ${spent:.0f} / ${budget:.0f}")
    if config.SITE_URL:
        lines.append(f'<a href="{config.SITE_URL}">Open the dashboard</a>')
    return "\n".join(lines)
