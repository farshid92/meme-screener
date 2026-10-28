"""Offline tests using data shaped like real API responses (GATO and Remus examples).
Run: python -m unittest discover tests
"""
import copy
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scan  # noqa: E402
from screener import scoring, storage  # noqa: E402

NOW_MS = 1793117400000              # ~27 Oct 2026
DAY = 86_400_000
SUPPLY = 986_922_714_149_447        # raw units, 6 decimals

GATO_PAIRS = [{
    "chainId": "solana", "dexId": "pumpswap",
    "url": "https://dexscreener.com/solana/5e3dxl",
    "baseToken": {"address": "GATOmint", "name": "GTA6CAT", "symbol": "GATO"},
    "priceUsd": "0.004325",
    "txns": {"h24": {"buys": 8487, "sells": 6093}},
    "volume": {"h24": 945496.67},
    "priceChange": {"h24": 8.08},
    "liquidity": {"usd": 204572.66},
    "marketCap": 4268303,
    "pairCreatedAt": NOW_MS - 63 * DAY,
    "info": {"websites": [{"label": "Website", "url": "https://example.com"}],
             "socials": [{"type": "twitter", "url": "https://x.com/example"}]},
}]

GATO_REPORT = {
    "mintAuthority": None, "freezeAuthority": None, "rugged": False,
    "token": {"supply": SUPPLY, "decimals": 6},
    "creator": "5MVneCcreator", "creatorBalance": 0, "totalHolders": 11059,
    "score_normalised": 1, "risks": [],
    "knownAccounts": {"5E3dXLpool": {"type": "AMM", "name": "Pump Fun AMM"},
                      "5MVneCcreator": {"type": "CREATOR", "name": "Creator"}},
    "topHolders": [
        {"owner": "5MVneCcreator", "address": "a1", "pct": 3.32, "insider": False},
        {"owner": "w2", "address": "a2", "pct": 2.58, "insider": False},
        {"owner": "w3", "address": "a3", "pct": 2.53, "insider": False},
        {"owner": "5E3dXLpool", "address": "a4", "pct": 2.48, "insider": False},
        {"owner": "w5", "address": "a5", "pct": 1.89, "insider": False},
        {"owner": "w6", "address": "a6", "pct": 1.42, "insider": False},
        {"owner": "w7", "address": "a7", "pct": 1.33, "insider": False},
        {"owner": "w8", "address": "a8", "pct": 1.06, "insider": False},
        {"owner": "w9", "address": "a9", "pct": 1.01, "insider": False},
        {"owner": "w10", "address": "a10", "pct": 1.01, "insider": False},
        {"owner": "w11", "address": "a11", "pct": 0.91, "insider": False},
    ],
    "insiderNetworks": [
        {"type": "transfer", "size": 6, "tokenAmount": 16_500_000_000_000, "currentHolding": 6_500_000_000_000},
        {"type": "transfer", "size": 4, "tokenAmount": 14_331_060_924_324, "currentHolding": 14_331_060_924_324},
    ],
    "markets": [
        {"marketType": "pump_fun_amm", "lp": {"baseUSD": 99745, "quoteUSD": 97828, "lpLockedPct": 100}},
        {"marketType": "meteoraDlmm", "lp": {"baseUSD": 11, "quoteUSD": 139, "lpLockedPct": 0}},
    ],
}


def remus_report():
    """Remus-like: clean contract, but ~16% held by linked insider wallets."""
    r = copy.deepcopy(GATO_REPORT)
    r["insiderNetworks"] = [{"type": "transfer", "size": 38,
                             "tokenAmount": 155_000_000_000_000, "currentHolding": 155_000_000_000_000}]
    r["topHolders"] = [{"owner": f"w{i}", "address": f"a{i}", "pct": p, "insider": False}
                       for i, p in enumerate([1.43, 1.39, 1.35, 1.29, 1.23, 1.1, 1.04, 0.95, 0.9, 0.89])]
    return r


def run(pairs=GATO_PAIRS, report=GATO_REPORT):
    return scan.evaluate(pairs, lambda mint: report, NOW_MS)


class TestScreener(unittest.TestCase):
    def test_gato_passes_with_creator_warning(self):
        r = run()
        self.assertTrue(r["passed"], r.get("gate_reasons"))
        m = r["metrics"]
        self.assertAlmostEqual(m["creator_pct"], 3.32, places=2)        # caught despite creatorBalance=0
        self.assertAlmostEqual(m["top10_pct"], 17.06, places=2)         # pool wallet excluded
        self.assertAlmostEqual(m["insider_pct"], 2.11, places=1)        # uses currentHolding
        self.assertTrue(any("Creator wallet holds 3.3%" in w for w in r["warnings"]))
        self.assertTrue(60 <= r["score"] <= 90, r["score"])

    def test_remus_like_insiders_fail_gate(self):
        remus = run(report=remus_report())
        self.assertFalse(remus["passed"])
        self.assertIn("Insider networks hold 15.7%", remus["gate_reasons"])

    def test_moderate_insiders_lower_score_and_warn(self):
        rep = remus_report()
        rep["insiderNetworks"][0]["currentHolding"] = 70_000_000_000_000   # ~7.1%
        gato, r = run(), run(report=rep)
        self.assertTrue(r["passed"])
        self.assertLess(r["breakdown"]["insiders"], 6)
        self.assertTrue(any("Insider networks hold 7.1%" in w for w in r["warnings"]))
        self.assertLess(r["breakdown"]["insiders"], gato["breakdown"]["insiders"])

    def test_active_mint_authority_fails_gate(self):
        rep = copy.deepcopy(GATO_REPORT)
        rep["mintAuthority"] = "SomeAuthority"
        r = run(report=rep)
        self.assertFalse(r["passed"])
        self.assertIn("Mint authority active", r["gate_reasons"])

    def test_unlocked_lp_fails_gate(self):
        rep = copy.deepcopy(GATO_REPORT)
        rep["markets"][0]["lp"]["lpLockedPct"] = 10
        self.assertIn("Main pool LP locked 10%", run(report=rep)["gate_reasons"])

    def test_new_coin_fails_before_rugcheck_call(self):
        pairs = copy.deepcopy(GATO_PAIRS)
        pairs[0]["pairCreatedAt"] = NOW_MS - 5 * 3_600_000

        def no_call(_):
            raise AssertionError("RugCheck should not be called for gated coins")
        r = scan.evaluate(pairs, no_call, NOW_MS)
        self.assertFalse(r["passed"])
        self.assertEqual(r["gate_reasons"], ["Too new (5h)"])

    def test_insider_pct_capped_at_100(self):
        rep = copy.deepcopy(GATO_REPORT)
        rep["insiderNetworks"] = [{"type": "transfer", "size": 2034,
                                   "currentHolding": 2_071_021_432_233_771}]  # > supply, like SI276
        r = run(report=rep)
        self.assertEqual(r["metrics"]["insider_pct"], 100.0)
        self.assertIn("Insider networks hold over 100%", r["gate_reasons"])

    def test_bot_volume_caps_score(self):
        pairs = copy.deepcopy(GATO_PAIRS)
        pairs[0]["txns"]["h24"] = {"buys": 250_000, "sells": 200_000}   # ~$2 avg trade, like HELL
        r = run(pairs=pairs)
        self.assertTrue(r["passed"])
        self.assertEqual(r["score"], 60)
        self.assertTrue(r["warnings"][0].startswith("Score capped at 60"))

    def test_holder_correlation_halves_holder_points(self):
        rep = copy.deepcopy(GATO_REPORT)
        rep["risks"] = [{"name": "High holder correlation", "level": "warn"}]
        normal, corr = run(), run(report=rep)
        self.assertAlmostEqual(corr["breakdown"]["holders"], normal["breakdown"]["holders"] / 2, delta=0.1)
        self.assertTrue(any("look connected" in w for w in corr["warnings"]))

    def test_small_mcap_fails_gate(self):
        pairs = copy.deepcopy(GATO_PAIRS)
        pairs[0]["marketCap"] = 230_000   # like Groklet
        r = scan.evaluate(pairs, lambda _: self.fail("RugCheck should not be called"), NOW_MS)
        self.assertEqual(r["gate_reasons"], ["Market cap $230K < $500K"])

    def test_interp_clamps_and_interpolates(self):
        pts = [(2, 1.0), (5, 0.5), (10, 0.0)]
        self.assertEqual(scoring.interp(0, pts), 1.0)
        self.assertEqual(scoring.interp(50, pts), 0.0)
        self.assertAlmostEqual(scoring.interp(7.5, pts), 0.25)

    def test_storage_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            con = storage.connect(os.path.join(d, "t.db"))
            try:
                row = run()
                storage.save(con, "2026-10-27T15:00:00+00:00", row)
                scan_id, rows = storage.latest_scan(con)
                self.assertEqual(rows[0]["symbol"], "GATO")
                self.assertEqual(rows[0]["warnings"], row["warnings"])
                self.assertEqual(storage.tracked_mints(con, "2026-10-01"), ["GATOmint"])
            finally:
                # Windows can't delete the temp dir while the db file is open
                con.close()


if __name__ == "__main__":
    unittest.main()


class TestCoverage(unittest.TestCase):
    """GeckoTerminal source, RugCheck cache, watchlist/score change, dashboard filters."""

    def test_gecko_tokens_parses_and_stops_on_short_page(self):
        from screener import sources
        import config
        calls = []

        def fake_get(url, retries=3):
            calls.append(url)
            n = 20 if "page=1&" in url else 3          # page 2 is the last (short) page
            return {"data": [{"relationships": {"base_token": {"data": {"id": f"solana_M{len(calls)}_{i}"}}}}
                             for i in range(n)]}
        orig_get, orig_delay = sources._get, config.GECKO_DELAY_S
        sources._get, config.GECKO_DELAY_S = fake_get, 0
        try:
            mints = sources.gecko_tokens(lists=[("pools", "sort=x", 10)])
        finally:
            sources._get, config.GECKO_DELAY_S = orig_get, orig_delay
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(mints), 23)
        self.assertTrue(all(not m.startswith("solana_") for m in mints))

    def test_rug_cache_hit_and_expiry(self):
        with tempfile.TemporaryDirectory() as d:
            con = storage.connect(os.path.join(d, "t.db"))
            try:
                storage.cache_rug(con, "M", {"x": 1}, now_s=1000)
                self.assertEqual(storage.cached_rug(con, "M", 3600, now_s=2000), {"x": 1})
                self.assertIsNone(storage.cached_rug(con, "M", 3600, now_s=10_000))
                self.assertIsNone(storage.cached_rug(con, "other", 3600, now_s=2000))
            finally:
                con.close()

    def test_evaluate_with_rug_fn_uses_metrics_directly(self):
        from screener.metrics import rug_metrics
        cached = rug_metrics(GATO_REPORT)
        r = scan.evaluate(GATO_PAIRS, None, NOW_MS, rug_fn=lambda _: dict(cached))
        self.assertEqual(r["score"], run()["score"])

    def test_previous_scores_and_dashboard_filters(self):
        import app as dashboard
        import config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            con = storage.connect(path)
            try:
                old = run()
                old["score"] = 70.0
                storage.save(con, "2026-10-26T10:00:00+00:00", old)
                storage.save(con, "2026-10-27T10:00:00+00:00", run())
                self.assertEqual(storage.previous_scores(con, "2026-10-27T10:00:00+00:00"), {"GATOmint": 70.0})
            finally:
                con.close()
            orig = config.DB_PATH
            config.DB_PATH = path
            try:
                c = dashboard.app.test_client()
                html = c.get("/?view=all").data.decode()
                self.assertIn("GATO", html)
                self.assertIn("▲ 13", html)                        # 82.8 - 70 = 12.8
                self.assertNotIn("/token/GATOmint", c.get("/?view=all&min_score=90").data.decode())
                self.assertNotIn("/token/GATOmint", c.get("/?view=all&max_mcap=1").data.decode())
                self.assertIn("/token/GATOmint", c.get("/?view=all&min_mcap=1&sort=change").data.decode())
                self.assertEqual(c.get("/?sort=bogus&min_score=abc").status_code, 200)
            finally:
                config.DB_PATH = orig


class TestJupiter(unittest.TestCase):
    def _tok(self, **kw):
        t = {"id": "MEMEpump", "symbol": "MEME", "tags": ["verified", "community"],
             "mcap": 2_000_000, "liquidity": 120_000,
             "firstPool": {"createdAt": "2026-08-01T00:00:00Z"},
             "stats24h": {"buyVolume": 40_000, "sellVolume": 35_000}}
        t.update(kw)
        return t

    def test_prefilter(self):
        from screener import sources
        toks = [
            self._tok(),                                                    # keep
            self._tok(id="STABLE", tags=["verified", "stable"]),            # non-meme tag
            self._tok(id="SOLx", symbol="SOL"),                             # excluded symbol
            self._tok(id="SMALL", mcap=200_000),                            # mcap gate
            self._tok(id="THIN", liquidity=10_000),                         # liquidity gate
            self._tok(id="NEW", firstPool={"createdAt": "2026-10-27T00:00:00Z"}),  # < 48h
            self._tok(id="DEAD", stats24h={"buyVolume": 100, "sellVolume": 50}),   # no volume
            self._tok(id="NOPOOL", firstPool=None),                         # unknown age
        ]
        self.assertEqual(sources.jupiter_candidates(toks, NOW_MS), ["MEMEpump"])


class TestPositions(unittest.TestCase):
    """Alert rules, digest, and the positions dashboard."""

    def setUp(self):
        from screener import positions
        self.p = positions
        self.pos = {"id": 1, "mint": "GATOmint", "symbol": "GATO", "amount_usd": 20, "buy_price": 0.002,
                    "liquidity_at_buy": 200_000, "initial_taken": 0}

    def _cur(self, **kw):
        r = run()
        r.update(kw)
        return r

    def kinds(self, cur, prev=None, has_pairs=True, **pos):
        return [k for k, _ in self.p.check_position({**self.pos, **pos}, cur, prev, has_pairs)]

    def test_take_profit_once_initial_not_taken(self):
        self.assertIn("take_profit", self.kinds(self._cur(price_usd=0.0045)))
        self.assertNotIn("take_profit", self.kinds(self._cur(price_usd=0.0045), initial_taken=1))

    def test_drawdowns(self):
        self.assertEqual(self.kinds(self._cur(price_usd=0.0009, liquidity=200_000)), ["down50"])
        self.assertEqual(self.kinds(self._cur(price_usd=0.0005, liquidity=200_000)), ["down50", "down70"])

    def test_dead_liquidity_and_gate(self):
        self.assertEqual(self.kinds(None, has_pairs=False), ["dead"])
        self.assertIn("liquidity_drop", self.kinds(self._cur(price_usd=0.002, liquidity=50_000)))
        cur = self._cur(price_usd=0.002, liquidity=200_000, passed=False,
                        gate_reasons=["Insider networks hold 12.0%", "Market cap $400K < $500K"])
        self.assertEqual(self.kinds(cur), ["gate:Insider networks"])  # mcap gate isn't a safety alert

    def test_creator_selling_and_insider_rise(self):
        prev = self._cur()
        cur = self._cur(price_usd=0.002, liquidity=200_000)
        cur["metrics"] = dict(cur["metrics"], creator_pct=1.0, insider_pct=6.0)
        self.assertEqual(sorted(self.kinds(cur, prev)), ["creator_sell", "insider_rise"])

    def test_digest_text(self):
        gato = run()
        txt = self.p.build_digest("2026-10-27T08:00:00+00:00", [gato], {"GATOmint": 70.0}, {"GATOmint"},
                                  [self.pos], {"GATOmint": 0.003}, 20, 20)
        self.assertIn("1 passed / 1 checked", txt)
        self.assertIn("GATO</a> 83 (▲13)", txt)
        self.assertIn("GATO 70→83", txt)                    # big move
        self.assertNotIn("New today", txt)                  # passed before
        self.assertIn("GATO: $20 → $30 (+50%)", txt)
        self.assertIn("Budget this month: $20 / $20", txt)

    def test_telegram_chunks_keep_lines_whole(self):
        from screener import telegram
        parts = list(telegram._chunks("\n".join(["x" * 100] * 100)))
        self.assertTrue(all(len(p) <= telegram.MAX_LEN for p in parts))
        self.assertEqual(sum(p.count("x" * 100) for p in parts), 100)

    def test_alerts_dedup_and_position_fill_in_scan(self):
        with tempfile.TemporaryDirectory() as d:
            con = storage.connect(os.path.join(d, "t.db"))
            try:
                pid = storage.add_position(con, "GATOmint", 20, None, "2026-10-20")   # price unknown at entry
                row = self._cur(price_usd=0.004)
                storage.save(con, "2026-10-27T08:00:00+00:00", row)
                first = scan.position_alerts(con, "2026-10-27T08:00:00+00:00", [row], {"GATOmint": GATO_PAIRS})
                p = storage.positions(con, "open")[0]
                self.assertEqual((p["symbol"], p["buy_price"]), ("GATO", 0.004))   # filled from scan
                self.assertEqual(first, [])
                storage.update_position(con, pid, buy_price=0.002)
                a1 = scan.position_alerts(con, "2026-10-28T08:00:00+00:00", [row], {"GATOmint": GATO_PAIRS})
                a2 = scan.position_alerts(con, "2026-10-29T08:00:00+00:00", [row], {"GATOmint": GATO_PAIRS})
                self.assertEqual(len(a1), 1)
                self.assertIn("2.0x", a1[0])
                self.assertEqual(a2, [])                    # not sent twice
            finally:
                con.close()

    def test_positions_dashboard_flow(self):
        import app as dashboard
        import config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            con = storage.connect(path)
            try:
                row = run()
                storage.save(con, "2026-10-27T08:00:00+00:00", row)
                storage.save_price(con, "GATOmint", "2026-10-27T08:00:00+00:00", 0.004325, 204572, 4.2e6)
            finally:
                con.close()
            orig = config.DB_PATH
            config.DB_PATH = path
            try:
                c = dashboard.app.test_client()
                mint = "HsprHxBRgiVaQdRf3TnKNuZZtaX9JUSPgMFdAkk6pump"
                self.assertIn("full token address", c.post("/positions/add", data={"mint": "x", "amount_usd": "5"},
                                                             follow_redirects=True).data.decode())
                c.post("/positions/add", data={"mint": "GATOmint" + "x" * 30, "amount_usd": "20",
                                               "buy_price": "0.002"})
                html = c.get("/positions").data.decode()
                self.assertIn("$20.00", html)
                self.assertIn("Took initial out", html)
                c.post("/positions/1/initial")
                c.post("/positions/1/close")
                html = c.get("/positions").data.decode()
                self.assertIn("closed", html)
                self.assertNotIn("Took initial out", html)
                self.assertEqual(c.get(f"/positions?mint={mint}").status_code, 200)
                self.assertEqual(c.post("/positions/1/bogus").status_code, 404)
            finally:
                config.DB_PATH = orig



# Jupiter info shaped like the real /tokens/v2/search response for GATO
GATO_JUP = {"id": "GATOmint", "symbol": "GATO", "tags": ["unknown", "token-2022"],
            "organicScore": 70.17, "organicScoreLabel": "medium",
            "stats24h": {"holderChange": 6.48}}


class TestShortlist(unittest.TestCase):
    def test_is_meme_rules(self):
        from screener.metrics import is_meme
        self.assertTrue(is_meme("XYZpump", {"tags": ["unknown"]}))
        self.assertTrue(is_meme("Bonk", {"tags": ["verified", "meme"]}))
        self.assertTrue(is_meme("ZCAT", {"tags": ["verified"], "symbol": "ZCAT"}))   # untagged meme stays
        self.assertFalse(is_meme("ZEC", {"tags": ["verified"], "symbol": "ZEC"}))    # known project
        self.assertFalse(is_meme("GRASS", {"tags": ["verified", "infra"]}))          # non-meme tag
        self.assertTrue(is_meme("Unknown", None))

    def test_non_meme_fails_gate_before_rugcheck(self):
        r = scan.evaluate(GATO_PAIRS, lambda _: self.fail("no RugCheck"), NOW_MS,
                          jup={"id": "GATOmint", "tags": ["verified", "defi"], "isVerified": True})
        self.assertFalse(r["passed"])
        self.assertIn("Not a meme coin (verified, defi)", r["gate_reasons"])

    def test_organic_score_used_and_caps(self):
        good = scan.evaluate(GATO_PAIRS, lambda _: GATO_REPORT, NOW_MS, jup=GATO_JUP)
        self.assertTrue(good["passed"])
        self.assertAlmostEqual(good["metrics"]["organic_score"], 70.17)
        self.assertAlmostEqual(good["metrics"]["holder_change_24h"], 6.48)
        bot = scan.evaluate(GATO_PAIRS, lambda _: GATO_REPORT, NOW_MS, jup=dict(GATO_JUP, organicScore=0))
        self.assertEqual(bot["score"], 60)
        self.assertIn("organic score 0", bot["warnings"][0])
        self.assertLess(bot["breakdown"]["trading"], good["breakdown"]["trading"])

    def _row(self, **jup):
        return scan.evaluate(GATO_PAIRS, lambda _: GATO_REPORT, NOW_MS, jup=dict(GATO_JUP, **jup))

    def test_shortlist_rules(self):
        from screener import shortlist
        ok, misses = shortlist.evaluate(self._row(), None, 0)
        self.assertTrue(ok, misses)
        self.assertEqual(shortlist.evaluate(self._row(organicScore=40), None, 0)[1], ["organic 40 < 60"])
        shrinking = self._row(stats24h={"holderChange": -1.5})
        self.assertEqual(shortlist.evaluate(shrinking, None, 0)[1], ["holders -1.5%"])
        # Falling score only counts once there are 3+ days of history
        self.assertTrue(shortlist.evaluate(self._row(), -10, 1)[0])
        self.assertEqual(shortlist.evaluate(self._row(), -10, 4)[1], ["score falling (-10 in 4d)"])
        self.assertEqual(shortlist.evaluate(scan.evaluate(GATO_PAIRS, lambda _: GATO_REPORT, NOW_MS), None, 0)[1],
                         ["no organic score", "no holder data"])

    def test_annotate_trend_and_digest(self):
        from screener import positions, shortlist
        row = self._row()
        short = shortlist.annotate([row], {"GATOmint": (75.0, "2026-10-22T08:00:00+00:00")},
                                   "2026-10-27T08:00:00+00:00")
        self.assertEqual(len(short), 1)
        self.assertAlmostEqual(row["trend_days"], 5)
        txt = positions.build_digest("2026-10-27T08:00:00+00:00", [row], {}, set(), [], {}, 0, 20, shortlist=short)
        self.assertIn("⭐ Shortlist (1)", txt)
        self.assertIn(f"organic 70 · holders +6.5% · 5d ▲{row['score'] - 75:.0f}", txt)
        empty = positions.build_digest("2026-10-27T08:00:00+00:00", [row], {}, set(), [], {}, 0, 20, shortlist=[])
        self.assertIn("none today", empty)

    def test_trends_from_storage_and_dashboard_view(self):
        import app as dashboard
        import config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            con = storage.connect(path)
            try:
                old = self._row()
                old["score"] = 80.0
                storage.save(con, "2026-10-19T08:00:00+00:00", old)   # outside 7-day window
                old["score"] = 78.0
                storage.save(con, "2026-10-22T08:00:00+00:00", old)   # oldest inside window
                storage.save(con, "2026-10-27T08:00:00+00:00", self._row())
                self.assertEqual(storage.score_trends(con, "2026-10-27T08:00:00+00:00", 7),
                                 {"GATOmint": (78.0, "2026-10-22T08:00:00+00:00")})
            finally:
                con.close()
            orig = config.DB_PATH
            config.DB_PATH = path
            try:
                html = dashboard.app.test_client().get("/").data.decode()
                self.assertIn("⭐ Shortlist (1)", html)
                self.assertIn("organic 70", html)
                self.assertIn(f"5d trend {self._row()['score'] - 78:+.0f}", html)
            finally:
                config.DB_PATH = orig

    def test_jupiter_info_batches(self):
        from screener import sources
        seen = []

        def fake_get(url, retries=3):
            q = url.split("query=")[1].split(",")
            seen.append(len(q))
            return [{"id": m, "organicScore": 50} for m in q] + [{"id": "unrelated"}]
        orig = sources._get
        sources._get = fake_get
        try:
            info = sources.jupiter_info([f"m{i}" for i in range(120)])
        finally:
            sources._get = orig
        self.assertEqual(seen, [50, 50, 20])
        self.assertEqual(len(info), 120)
        self.assertNotIn("unrelated", info)


H = 3_600_000
# Shaped like a real Jupiter /tokens/v2/toporganicscore/1h item
EARLY_TOKEN = {
    "id": "FRONGpump", "symbol": "FRONG", "launchpad": "pump.fun", "graduatedPool": "Deb7pool",
    "firstPool": {"createdAt": "2026-10-27T06:30:00Z"},  # 2h before NOW_MS (~08:30Z)
    "mcap": 1_095_770, "liquidity": 285_964, "usdPrice": 0.0011, "holderCount": 7486,
    "organicScore": 79, "twitter": "https://x.com/frong", "tags": [],
    "audit": {"devMigrations": 198, "devMints": 4218, "topHoldersPercentage": 13.56,
              "mintAuthorityDisabled": True, "freezeAuthorityDisabled": True},
    "stats1h": {"holderChange": 147.7, "priceChange": 96.8, "numOrganicBuyers": 171,
                "numNetBuyers": 4646, "numTraders": 9559},
}


class TestEarlyTracker(unittest.TestCase):
    def setUp(self):
        from datetime import datetime
        self.now = datetime.fromisoformat("2026-10-27T08:30:00+00:00").timestamp() * 1000
        from screener import early
        self.e = early

    def tok(self, **kw):
        t = copy.deepcopy(EARLY_TOKEN)
        t.update(kw)
        return t

    def test_candidates_filter(self):
        toks = [
            self.tok(),                                                   # keep
            self.tok(),                                                   # duplicate in same run
            self.tok(id="OLD", firstPool={"createdAt": "2026-10-20T00:00:00Z"}),   # > 72h
            self.tok(id="DUST", mcap=10_000),                             # below min mcap
            self.tok(id="BIG", mcap=50_000_000),                          # no longer early
            self.tok(id="THIN", liquidity=1_000),
            self.tok(id="CURVE", graduatedPool=None),                     # still on bonding curve
            self.tok(id="RAYD", launchpad=None, graduatedPool=None),      # plain pool, no launchpad: ok
            self.tok(id="SEEN"),                                          # already tracked
        ]
        got = [t["id"] for t in self.e.candidates(toks, self.now, known={"SEEN"})]
        self.assertEqual(got, ["FRONGpump", "RAYD"])

    def test_signals(self):
        s = self.e.signals(self.tok(), self.now, rug={"insider_pct": 3.2, "creator_pct": 0.0, "top10_pct": 14.0,
                                                       "lp_locked_pct": 100, "holder_correlation": False,
                                                       "rugged": False, "ignored": 1})
        self.assertAlmostEqual(s["age_h"], 2.0)
        self.assertEqual((s["dev_mints"], s["organic_score"], s["has_twitter"]), (4218, 79, 1))
        self.assertAlmostEqual(s["liq_to_mcap"], 285_964 / 1_095_770)
        self.assertEqual(s["insider_pct"], 3.2)
        self.assertNotIn("ignored", s)
        self.assertNotIn("insider_pct", self.e.signals(self.tok(), self.now))   # no RugCheck data

    def test_outcome(self):
        t0 = self.now
        snaps = [(t0, 1.0, 50_000), (t0 + 10 * H, 12.0, 300_000), (t0 + 100 * H, 3.0, 80_000),
                 (t0 + 200 * H, 99.0, 1)]   # after the 7-day window: ignored
        o = self.e.outcome(1.0, t0, snaps, 7, t0 + 8 * 24 * H)
        self.assertEqual((o["peak_x"], o["final_x"], o["dead"]), (12.0, 3.0, False))
        self.assertTrue(o["hit10"])
        self.assertIsNone(self.e.outcome(1.0, t0, snaps, 7, t0 + 5 * 24 * H))   # not matured yet
        rug = self.e.outcome(1.0, t0, [(t0, 1.0, 50_000), (t0 + 5 * H, 0.0, 0.0)], 1, t0 + 2 * 24 * H)
        self.assertTrue(rug["dead"])
        self.assertFalse(rug["hit2"])

    def test_bucket_table(self):
        mk = lambda org, peak: ({"organic_score": org}, {"peak_x": peak, "final_x": peak / 2, "dead": peak < 1,
                                                          "hit2": peak >= 2, "hit5": peak >= 5, "hit10": peak >= 10})
        rows = [mk(80, 12), mk(75, 3), mk(20, 0.5), mk(None, 1)]
        table = dict(self.e.bucket_table(rows, [("organic_score", [0, 30, 50, 70, 101])]))
        groups = dict(table["organic_score"])
        self.assertEqual(groups["70–101"]["n"], 2)
        self.assertEqual(groups["70–101"]["hit10"], 0.5)
        self.assertEqual(groups["0–30"]["dead"], 1.0)
        self.assertEqual(groups["unknown"]["n"], 1)
        self.assertEqual(self.e.summarize(rows)["n"], 4)

    def test_tracker_end_to_end_and_page(self):
        import app as dashboard
        import config
        import early_track
        from screener import sources
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            orig = (sources.jupiter_top, sources.rugcheck_report, sources.dex_pairs, config.DB_PATH)
            calls = {"rug": 0}

            def rug(_):
                calls["rug"] += 1
                return GATO_REPORT
            sources.jupiter_top = lambda cat, interval, limit=100: [self.tok(), self.tok(id="OLD", firstPool={"createdAt": "2026-01-01T00:00:00Z"})]
            sources.rugcheck_report = rug
            later = copy.deepcopy(GATO_PAIRS)
            later[0]["baseToken"]["address"] = "FRONGpump"
            later[0]["priceUsd"] = "0.0033"
            sources.dex_pairs = lambda mints, chain="solana": {"FRONGpump": later}
            try:
                for _ in range(2):        # 2nd run: coin already known -> only re-priced
                    sys.argv = ["early_track.py", "--db", path]
                    early_track.main()
                con = storage.connect(path)
                try:
                    coins = storage.early_coins(con)
                finally:
                    con.close()
                self.assertEqual(len(coins), 1)
                self.assertEqual(calls["rug"], 1)
                self.assertEqual([p for _, p, _ in coins[0]["snapshots"]], [0.0011, 0.0033])
                self.assertAlmostEqual(coins[0]["signals"]["insider_pct"], 2.11, places=1)
                config.DB_PATH = path
                html = dashboard.app.test_client().get("/early").data.decode()
                self.assertIn("FRONG", html)
                self.assertIn("3.0x", html)               # 0.0033 / 0.0011
                self.assertIn("Not enough data yet", html)
            finally:
                sources.jupiter_top, sources.rugcheck_report, sources.dex_pairs, config.DB_PATH = orig


class TestCloud(unittest.TestCase):
    """Private DB, encryption, Telegram commands, network wait, pruning, static site."""

    def test_private_db_separates_and_migrates_positions(self):
        with tempfile.TemporaryDirectory() as d:
            main, priv = os.path.join(d, "s.db"), os.path.join(d, "p.db")
            con = storage.connect(main)                 # local mode: positions in main
            storage.add_position(con, "M" * 40, 20, 0.001, "2026-10-28")
            con.close()
            con = storage.connect(main, priv)           # cloud mode: moved to private file
            try:
                self.assertEqual(len(storage.positions(con)), 1)
                self.assertIsNone(con.execute("SELECT 1 FROM main.sqlite_master WHERE name='positions'").fetchone())
                storage.set_state(con, "tg_offset", 42)
                self.assertEqual(storage.get_state(con, "tg_offset"), "42")
            finally:
                con.close()
            raw = sqlite3.connect(main)
            self.assertEqual(raw.execute("SELECT count(*) FROM sqlite_master WHERE name='positions'").fetchone()[0], 0)
            raw.close()

    def test_vault_roundtrip_and_wrong_key(self):
        from screener import vault
        with tempfile.TemporaryDirectory() as d:
            src, enc, out = (os.path.join(d, n) for n in ("a", "a.enc", "b"))
            open(src, "wb").write(b"secret positions")
            vault.encrypt(src, enc, "pass")
            self.assertNotIn(b"secret", open(enc, "rb").read())
            vault.decrypt(enc, out, "pass")
            self.assertEqual(open(out, "rb").read(), b"secret positions")
            with self.assertRaises(Exception):
                vault.decrypt(enc, out, "wrong")

    def test_bot_commands(self):
        from screener import bot
        with tempfile.TemporaryDirectory() as d:
            con = storage.connect(os.path.join(d, "s.db"), os.path.join(d, "p.db"))
            try:
                mint = "HsprHxBRgiVaQdRf3TnKNuZZtaX9JUSPgMFdAkk6pump"
                price = lambda m: (0.004, 200_000, "GATO")
                self.assertIn("Recorded #1 <b>GATO</b> $20.00 @ 0.004", bot.handle(con, f"/buy {mint} 20", price, "2026-10-28"))
                self.assertIn("@ 0.002", bot.handle(con, f"/buy {mint} $5 0.002", price, "2026-10-28"))
                self.assertIn("full token address", bot.handle(con, "/buy abc 5", price))
                self.assertIn("must be numbers", bot.handle(con, f"/buy {mint} lots", price))
                lst = bot.handle(con, "/positions", lambda m: (0.008, 1, "GATO"), "2026-10-28")
                self.assertIn("#2 <b>GATO</b> $5.00 @ 0.002 → now 4.00x ($20.00)", lst)
                self.assertIn("Spent in 2026-10: $25", lst)
                self.assertIn("initial stake taken out", bot.handle(con, "/initial 2", price))
                self.assertIn("#1 closed at 0.004", bot.handle(con, "/sold #1", price, "2026-10-28"))
                self.assertIn("No open position #1", bot.handle(con, "/sold 1", price))
                self.assertIn("Unknown command", bot.handle(con, "hello", price))
                self.assertIn("/buy", bot.handle(con, "/help@MyBot", price))
                self.assertEqual(len(storage.positions(con, "open")), 1)
            finally:
                con.close()

    def test_bot_poll_only_owner_and_offset(self):
        from screener import bot, telegram
        sent, calls = [], []
        updates = [{"update_id": 10, "message": {"chat": {"id": 999}, "text": "/help"}},       # stranger
                   {"update_id": 11, "message": {"chat": {"id": 1}, "text": "/positions"}}]
        orig = (telegram.call, telegram.send)
        telegram.call = lambda token, method, **kw: (calls.append(kw), updates if kw["offset"] == 0 else [])[1]
        telegram.send = lambda text, s=None: sent.append(text) or True
        try:
            with tempfile.TemporaryDirectory() as d:
                con = storage.connect(os.path.join(d, "s.db"), os.path.join(d, "p.db"))
                try:
                    self.assertEqual(bot.poll(con, {"token": "t", "chat_id": 1}, lambda m: None), 1)
                    self.assertEqual(bot.poll(con, {"token": "t", "chat_id": 1}, lambda m: None), 0)
                    self.assertEqual(calls[1]["offset"], 12)        # confirmed, never re-run
                finally:
                    con.close()
        finally:
            telegram.call, telegram.send = orig
        self.assertEqual(len(sent), 1)
        self.assertIn("No open positions", sent[0])

    def test_telegram_settings_from_env(self):
        from screener import telegram
        os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = "tok", "123"
        try:
            self.assertEqual(telegram.load_settings(), {"token": "tok", "chat_id": 123})
        finally:
            del os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]

    def test_wait_for_network(self):
        from screener import net
        tries = []

        def resolve(h):
            tries.append(h)
            if len(tries) < 5:
                raise OSError("no dns")
        self.assertTrue(net.wait_for_network(timeout_s=60, sleep=lambda s: None, resolve=resolve, hosts=("a", "b")))
        self.assertFalse(net.wait_for_network(timeout_s=0, sleep=lambda s: None,
                                              resolve=lambda h: (_ for _ in ()).throw(OSError())))

    def test_prune_keeps_passing_rows(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as d:
            con = storage.connect(os.path.join(d, "s.db"))
            try:
                ok, bad = run(), run()
                bad["passed"] = False
                storage.save(con, "2026-09-01T00:00:00+00:00", ok)
                storage.save(con, "2026-09-01T00:00:00+00:00", bad)
                storage.save(con, "2026-10-27T00:00:00+00:00", bad)
                storage.cache_rug(con, "old", {}, 0)
                storage.prune(con, now=datetime(2026, 10, 28, tzinfo=timezone.utc))
                rows = con.execute("SELECT scan_id, passed FROM scans ORDER BY id").fetchall()
                self.assertEqual([tuple(r) for r in rows], [("2026-09-01T00:00:00+00:00", 1), ("2026-10-27T00:00:00+00:00", 0)])
                self.assertEqual(con.execute("SELECT count(*) FROM rug_cache").fetchone()[0], 0)
            finally:
                con.close()

    def test_freeze_static_site(self):
        import config
        import freeze
        import app as dashboard
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            con = storage.connect(path)
            try:
                storage.save(con, "2026-10-27T08:00:00+00:00", run())
                storage.add_position(con, "SECRETmint" + "x" * 30, 20, 0.001, "2026-10-27", symbol="MYBAG")
            finally:
                con.close()
            orig = config.DB_PATH
            config.DB_PATH = path
            try:
                out = os.path.join(d, "site")
                freeze.build(out)
            finally:
                config.DB_PATH = orig
                dashboard.app.config["STATIC"] = False
            self.assertEqual(sorted(os.listdir(out)), [".nojekyll", "all.html", "early.html", "index.html", "picks.html"])
            html = open(os.path.join(out, "all.html"), encoding="utf-8").read()
            self.assertIn("GATO", html)
            self.assertIn('href="https://dexscreener.com/solana/GATOmint"', html)
            self.assertIn('href="early.html"', html)
            for name in os.listdir(out):
                page = open(os.path.join(out, name), encoding="utf-8").read()
                self.assertNotIn("/positions", page)
                self.assertNotIn("MYBAG", page)
                self.assertNotIn('<form class="filters"', page)
                self.assertNotIn('href="/', page)              # no links back to the Flask server

    def test_hourly_price_alerts(self):
        import hourly
        from screener import sources
        with tempfile.TemporaryDirectory() as d:
            con = storage.connect(os.path.join(d, "s.db"), os.path.join(d, "p.db"))
            orig = sources.dex_pairs
            sources.dex_pairs = lambda mints, chain="solana": {"GATOmint": GATO_PAIRS}   # price 0.004325
            try:
                storage.add_position(con, "GATOmint", 20, 0.002, "2026-10-27", symbol="GATO", liquidity=200_000)
                storage.add_position(con, "GONEmint", 5, 0.01, "2026-10-27", symbol="GONE")
                alerts = hourly.price_alerts(con, "2026-10-28T10:00:00+00:00")
                self.assertEqual(len(alerts), 2)
                self.assertTrue(any("2.2x" in a for a in alerts))
                self.assertTrue(any("no trading pairs" in a for a in alerts))
                self.assertEqual(hourly.price_alerts(con, "2026-10-28T11:00:00+00:00"), [])   # sent once
            finally:
                sources.dex_pairs = orig
                con.close()


class TestOldRows(unittest.TestCase):
    def test_pages_render_rows_without_newer_fields(self):
        import app as dashboard
        import config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            con = storage.connect(path)
            try:
                old = run()                                    # scanned before organic score existed
                for k in ("organic_score", "holder_change_24h"):
                    old["metrics"].pop(k, None)
                storage.save(con, "2026-10-27T08:00:00+00:00", old)
                storage.add_early(con, "X" * 40, "OLD", "2026-10-27T08:00:00+00:00", 1.0, 0.001,
                                  {"mcap": 1e5, "age_h": 3})   # no RugCheck data, no organic
            finally:
                con.close()
            orig = config.DB_PATH
            config.DB_PATH = path
            try:
                c = dashboard.app.test_client()
                for url in ("/", "/?view=all", "/early"):
                    self.assertEqual(c.get(url).status_code, 200, url)
                self.assertIn("organic ?", c.get("/?view=all").data.decode())
            finally:
                config.DB_PATH = orig



class TestPicks(unittest.TestCase):
    def setUp(self):
        from screener import picks
        self.pk = picks

    def coin(self, mint, symbol, **metrics):
        r = scan.evaluate(GATO_PAIRS, lambda _: GATO_REPORT, NOW_MS, jup=GATO_JUP)
        r.update(mint=mint, symbol=symbol)
        r["metrics"] = {**r["metrics"], "mint": mint, "creator_pct": 0.5, **metrics}
        return r

    def test_check_rules(self):
        r = self.coin("A", "AAA")
        rank, why = self.pk.check(r, 10, 12, 2, 10)
        self.assertEqual(why, [])
        self.assertAlmostEqual(rank, r["score"] + 10 * 10 / 12)
        self.assertEqual(self.pk.check(r, 2, 12, 0, 0)[1], ["on shortlist only 2/12 days"])
        self.assertEqual(self.pk.check(r, 10, 12, -8, 0)[1], ["score falling (-8)"])
        self.assertEqual(self.pk.check(r, 10, 12, 0, 80)[1], ["already up 80% in 7d (don't chase)"])
        self.assertEqual(self.pk.check(r, 10, 12, 0, -60)[1], ["down 60% in 7d"])
        self.assertEqual(self.pk.check(r, 10, 12, None, None)[1], [])           # unknown history is allowed
        bad = self.coin("B", "BBB", insider_pct=8, max_single_pct=6.1, creator_pct=3.3, holder_correlation=True)
        self.assertEqual(self.pk.check(bad, 10, 12, 0, 0)[1],
                         ["insiders 8.0%", "one wallet 6.1%", "creator 3.3%", "top holders look connected"])

    def test_allocate(self):
        a, b, c = {"rank": 100}, {"rank": 97}, {"rank": 80}
        self.assertEqual(self.pk.allocate([a, b], 20), [(a, 10), (b, 10)])
        self.assertEqual(self.pk.allocate([a, c], 20), [(a, 20)])
        self.assertEqual(self.pk.allocate([a], 15), [(a, 15)])
        self.assertEqual(self.pk.allocate([], 20), [])
        self.assertEqual(self.pk.allocate([a], 0), [])

    def test_last_day_of_month(self):
        from datetime import datetime, timezone
        self.assertTrue(self.pk.is_last_day_of_month(datetime(2026, 10, 31, 7, 0, tzinfo=timezone.utc)))
        self.assertFalse(self.pk.is_last_day_of_month(datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc)))
        # 23:30 UTC on Oct 31 is already Nov 1 in Cyprus
        self.assertFalse(self.pk.is_last_day_of_month(datetime(2026, 10, 31, 23, 30, tzinfo=timezone.utc)))

    def _history_db(self, d):
        """12 daily scans: AAA shortlisted every day (+ price up 10%), BBB 3 days, CCC every day but creator 3.3%."""
        path = os.path.join(d, "t.db")
        con = storage.connect(path)
        for day in range(16, 28):
            sid = f"2026-10-{day:02d}T08:00:00+00:00"
            rows = [self.coin("AAA" + "x" * 40, "AAA"), self.coin("CCC" + "x" * 40, "CCC", creator_pct=3.3)]
            if day >= 25:
                rows.append(self.coin("BBB" + "x" * 40, "BBB"))
            for r in rows:
                storage.save(con, sid, r)
                price = 0.001 * (1 + 0.1 * (day - 16) / 11) if r["symbol"] == "AAA" else 0.001
                storage.save_price(con, r["mint"], sid, price, 200_000, 4e6)
        return con

    def test_compute_backfills_and_picks(self):
        with tempfile.TemporaryDirectory() as d:
            con = self._history_db(d)
            try:
                res = self.pk.compute(con)
                self.assertEqual(res["total_days"], 12)              # backfilled from stored scans
                self.assertEqual([r["symbol"] for r in res["picks"]], ["AAA"])
                a = res["picks"][0]
                self.assertEqual((a["days_on"], a["trend"]), (12, 0))
                self.assertAlmostEqual(a["change_7d"], 10 * 7 / 11, delta=1.5)
                why = {r["symbol"]: r["why_not"] for r in res["rejected"]}
                self.assertEqual(why["BBB"], ["on shortlist only 3/12 days"])
                self.assertEqual(why["CCC"], ["creator 3.3%"])
                msg = self.pk.message(res, 20, "October 2026")
                self.assertIn("1. <b>AAA</b> → buy <b>$20</b>", msg)
                self.assertIn("on shortlist 12/12 days", msg)
                self.assertIn("<code>/buy AAA", msg)
                self.assertIn("Your check:", msg)
                self.assertIn("Not picked: CCC (creator 3.3%); BBB (on shortlist only 3/12 days)", msg)
                self.assertIn("Budget for this month is used up", self.pk.message(res, 0, "October 2026"))
                self.assertIn("skip buying this month", self.pk.message(dict(res, picks=[]), 20, "October 2026"))
            finally:
                con.close()

    def test_picks_page_and_bot_command(self):
        import app as dashboard
        import config
        from screener import bot
        with tempfile.TemporaryDirectory() as d:
            con = self._history_db(d)
            try:
                reply = bot.handle(con, "/picks", lambda m: None, "2026-10-28")
                self.assertIn("🎯 Monthly picks", reply)
                self.assertIn("AAA", reply)
            finally:
                con.close()
            orig = config.DB_PATH
            config.DB_PATH = os.path.join(d, "t.db")
            try:
                html = dashboard.app.test_client().get("/picks").data.decode()
                self.assertIn("$20", html)
                self.assertIn("AAA", html)
                self.assertIn("creator 3.3%", html)
            finally:
                config.DB_PATH = orig
