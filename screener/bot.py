"""Telegram commands, so positions can be managed from your phone (processed on each hourly run).

  /buy <token address> <amount $> [buy price]   record a buy (price defaults to the current price)
  /initial <id>                                  you took your initial stake out
  /sold <id>                                     you sold everything
  /positions                                     list open positions
  /picks                                         this month's buying picks
  /help

Only messages from your own chat (TELEGRAM_CHAT_ID) are accepted.
"""
import logging
from datetime import datetime, timezone
from html import escape

import config
from screener import storage, telegram

log = logging.getLogger(__name__)

HELP = ("<b>Commands</b> (handled within the hour)\n"
        "/buy &lt;token address&gt; &lt;amount $&gt; [price]: record a buy\n"
        "/initial &lt;id&gt;: you took your initial stake out\n"
        "/sold &lt;id&gt;: you sold everything\n"
        "/positions: list open positions\n"
        "/picks: this month's buying picks (sent automatically on the last day of the month)")


def _fmt_pos(p, price=None):
    sym = escape(p.get("symbol") or p["mint"][:6])
    line = f"#{p['id']} <b>{sym}</b> ${p['amount_usd']:.2f} @ {p['buy_price'] or '?'}"
    if price and p.get("buy_price"):
        x = price / p["buy_price"]
        line += f" → now {x:.2f}x (${p['amount_usd'] * x:.2f})"
    if p.get("initial_taken"):
        line += " · initial out"
    return line


def handle(con, text, price_fn, today=None):
    """Run one command; return the reply text. price_fn(mint) -> (price, liquidity, symbol) or None."""
    parts = (text or "").strip().split()
    if not parts:
        return None
    cmd = parts[0].split("@")[0].lower()
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if cmd in ("/help", "/start"):
        return HELP

    if cmd == "/buy":
        if len(parts) < 3:
            return "Usage: /buy &lt;token address&gt; &lt;amount $&gt; [price]"
        mint = parts[1]
        try:
            amount = float(parts[2].lstrip("$"))
            price = float(parts[3]) if len(parts) > 3 else None
        except ValueError:
            return "Amount and price must be numbers, e.g. /buy &lt;address&gt; 20"
        if len(mint) < 32 or amount <= 0:
            return "Paste the full token address and an amount above 0."
        live = price_fn(mint) or (None, None, None)
        pid = storage.add_position(con, mint, amount, price or live[0], today,
                                   symbol=live[2] or storage.latest_symbol(con, mint), liquidity=live[1])
        p = next(x for x in storage.positions(con) if x["id"] == pid)
        note = "" if p["buy_price"] else "\n(price unknown for now, it'll be filled in on the next scan)"
        return f"✅ Recorded {_fmt_pos(p)}{note}"

    if cmd in ("/initial", "/sold"):
        if len(parts) < 2 or not parts[1].lstrip("#").isdigit():
            return f"Usage: {cmd} &lt;id&gt;  (see /positions for ids)"
        pid = int(parts[1].lstrip("#"))
        p = next((x for x in storage.positions(con, "open") if x["id"] == pid), None)
        if not p:
            return f"No open position #{pid}. See /positions."
        if cmd == "/initial":
            storage.update_position(con, pid, initial_taken=1)
            return f"✅ #{pid} marked: initial stake taken out. The rest is a free ride."
        live = price_fn(p["mint"]) or (None, None, None)
        storage.update_position(con, pid, status="closed", close_price=live[0], closed_at=today)
        return f"✅ #{pid} closed" + (f" at {live[0]:.8g}" if live[0] else "")

    if cmd == "/picks":
        from screener import picks
        left = config.MONTHLY_BUDGET_USD - storage.spent_in_month(con, today[:7])
        return picks.message(picks.compute(con), max(0, left), picks.month_label())

    if cmd == "/positions":
        open_pos = storage.positions(con, "open")
        if not open_pos:
            return "No open positions. Use /buy &lt;address&gt; &lt;amount&gt; after buying."
        month = today[:7]
        lines = [_fmt_pos(p, (price_fn(p["mint"]) or (None,))[0]) for p in open_pos]
        return ("<b>Open positions</b>\n" + "\n".join(lines) +
                f"\n\nSpent in {month}: ${storage.spent_in_month(con, month):.0f}")

    return "Unknown command. " + HELP


def poll(con, settings, price_fn):
    """Fetch new messages, run commands from the owner's chat, reply. Returns number handled."""
    offset = int(storage.get_state(con, "tg_offset", "0"))
    updates = telegram.call(settings["token"], "getUpdates", offset=offset, timeout=0)
    handled = 0
    for upd in updates:
        storage.set_state(con, "tg_offset", upd["update_id"] + 1)  # never re-run a command
        msg = upd.get("message") or {}
        if msg.get("chat", {}).get("id") != settings["chat_id"]:
            continue
        try:
            reply = handle(con, msg.get("text"), price_fn)
        except Exception as e:
            log.warning("Command failed: %s", e)
            reply = f"⚠️ That command failed: {escape(str(e))}"
        if reply:
            telegram.send(reply, settings)
            handled += 1
    return handled
