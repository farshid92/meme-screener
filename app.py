"""Dashboard for the latest scan. Run: python app.py, then open http://127.0.0.1:5000"""
from datetime import datetime, timezone

from flask import Flask, abort, redirect, render_template, request, url_for

import config
from screener import early, shortlist, storage

app = Flask(__name__)


@app.context_processor
def inject_static():
    """freeze.py sets STATIC to render plain HTML pages without filters or positions."""
    return {"static": app.config.get("STATIC", False)}

SORTS = {
    "score": lambda r: -r["score"],
    "change": lambda r: -(r["delta"] if r["delta"] is not None else -999),
    "mcap": lambda r: -(r["mcap"] or 0),
    "age": lambda r: (r["metrics"].get("age_hours") or 0),
}


def _float(name):
    try:
        v = request.args.get(name, "").strip()
        return float(v) if v else None
    except ValueError:
        return None


@app.route("/")
def index():
    con = storage.connect(config.DB_PATH, config.PRIVATE_DB_PATH)
    try:
        scan_id, rows = storage.latest_scan(con)
        prev = storage.previous_scores(con, scan_id) if scan_id else {}
        oldest = storage.score_trends(con, scan_id, config.SHORTLIST["trend_days"]) if scan_id else {}
    finally:
        con.close()
    short = shortlist.annotate(rows, oldest, scan_id) if scan_id else []

    f = {"min_score": _float("min_score"), "min_mcap": _float("min_mcap"),
         "max_mcap": _float("max_mcap"), "min_age": _float("min_age"),
         "sort": request.args.get("sort", "score"),
         "view": request.args.get("view", "shortlist")}
    if f["sort"] not in SORTS:
        f["sort"] = "score"
    if f["view"] not in ("shortlist", "all"):
        f["view"] = "shortlist"

    passed = []
    for r in rows:
        if not r["passed"]:
            continue
        if f["view"] == "shortlist" and not r.get("shortlisted"):
            continue
        r["delta"] = round(r["score"] - prev[r["mint"]], 1) if r["mint"] in prev else None
        mcap_m = (r["mcap"] or 0) / 1e6
        age_d = (r["metrics"].get("age_hours") or 0) / 24
        if f["min_score"] is not None and r["score"] < f["min_score"]:
            continue
        if f["min_mcap"] is not None and mcap_m < f["min_mcap"]:
            continue
        if f["max_mcap"] is not None and mcap_m > f["max_mcap"]:
            continue
        if f["min_age"] is not None and age_d < f["min_age"]:
            continue
        passed.append(r)
    passed.sort(key=SORTS[f["sort"]])

    failed = [r for r in rows if not r["passed"]]
    total_passed = sum(1 for r in rows if r["passed"])
    return render_template("index.html", scan_id=scan_id, passed=passed, failed=failed,
                           total_passed=total_passed, n_short=len(short), weights=config.WEIGHTS,
                           f=f, rules=config.SHORTLIST)


@app.route("/token/<mint>")
def token(mint):
    con = storage.connect(config.DB_PATH, config.PRIVATE_DB_PATH)
    try:
        history = storage.token_history(con, mint)
    finally:
        con.close()
    if not history:
        abort(404)
    return render_template("token.html", history=history, latest=history[-1], weights=config.WEIGHTS)


@app.route("/positions")
def positions_page():
    con = storage.connect(config.DB_PATH, config.PRIVATE_DB_PATH)
    try:
        rows = storage.positions(con)
        for p in rows:
            lp = storage.latest_price(con, p["mint"])
            p["current_price"] = p["close_price"] if p["status"] == "closed" else (lp or {}).get("price_usd")
            p["x"] = (p["current_price"] / p["buy_price"]) if p["current_price"] and p["buy_price"] else None
            p["value"] = p["amount_usd"] * p["x"] if p["x"] is not None else None
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        spent = storage.spent_in_month(con, month)
        _, latest = storage.latest_scan(con)
    finally:
        con.close()
    suggestions = sorted((r for r in latest if r["passed"]), key=lambda r: -r["score"])
    open_rows = [p for p in rows if p["status"] == "open"]
    invested = sum(p["amount_usd"] for p in open_rows)
    value = sum(p["value"] if p["value"] is not None else p["amount_usd"] for p in open_rows)
    return render_template("positions.html", rows=rows, spent=spent, budget=config.MONTHLY_BUDGET_USD,
                           month=month, invested=invested, value=value, suggestions=suggestions,
                           prefill=request.args.get("mint", ""), error=request.args.get("error"),
                           today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                           take_profit_x=config.ALERTS["take_profit_x"])


@app.post("/positions/add")
def positions_add():
    mint = request.form.get("mint", "").strip()
    try:
        amount = float(request.form.get("amount_usd", ""))
        price_txt = request.form.get("buy_price", "").strip()
        price = float(price_txt) if price_txt else None
    except ValueError:
        return redirect(url_for("positions_page", error="Amount and price must be numbers."))
    if len(mint) < 32 or amount <= 0:
        return redirect(url_for("positions_page", error="Paste the full token address and an amount above 0."))
    opened = request.form.get("opened_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    con = storage.connect(config.DB_PATH, config.PRIVATE_DB_PATH)
    try:
        lp = storage.latest_price(con, mint) or {}
        storage.add_position(con, mint, amount, price or lp.get("price_usd"), opened,
                             symbol=storage.latest_symbol(con, mint), liquidity=lp.get("liquidity"),
                             note=request.form.get("note") or None)
    finally:
        con.close()
    return redirect(url_for("positions_page"))


@app.post("/positions/<int:pid>/<action>")
def positions_action(pid, action):
    con = storage.connect(config.DB_PATH, config.PRIVATE_DB_PATH)
    try:
        if action == "initial":
            storage.update_position(con, pid, initial_taken=1)
        elif action == "close":
            pos = next((p for p in storage.positions(con) if p["id"] == pid), None)
            if pos:
                lp = storage.latest_price(con, pos["mint"]) or {}
                storage.update_position(con, pid, status="closed", close_price=lp.get("price_usd"),
                                        closed_at=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        else:
            abort(404)
    finally:
        con.close()
    return redirect(url_for("positions_page"))


@app.route("/early")
def early_page():
    import time
    days = _float("days") or 7
    con = storage.connect(config.DB_PATH, config.PRIVATE_DB_PATH)
    try:
        coins = storage.early_coins(con)
    finally:
        con.close()
    now_ms = time.time() * 1000
    rows = early.matured(coins, days, now_ms)
    recent = []
    for c in coins[:100]:
        prices = [p for _, p, _ in c["snapshots"] if p is not None]
        fp = c["first_price"] or 0
        recent.append({**c, "age_d": (now_ms - c["first_ms"]) / 86_400_000,
                       "now_x": (prices[-1] / fp) if prices and fp else None,
                       "peak_x": (max(prices) / fp) if prices and fp else None})
    return render_template("early.html", days=days, total=len(coins), n=len(rows),
                           baseline=early.summarize(rows), table=early.bucket_table(rows) if rows else [],
                           recent=recent)


@app.template_filter("money")
def money(v):
    v = v or 0
    if v >= 1e6:
        return f"${v/1e6:.2f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


if __name__ == "__main__":
    app.run(debug=False)
