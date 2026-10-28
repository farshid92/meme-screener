"""Turn raw API responses into the flat numbers the scorer uses."""
import config

# Known-account types that are infrastructure, not real holders.
INFRA_TYPES = {"AMM", "LOCKER", "CLOB"}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def dex_metrics(pairs, now_ms):
    """Metrics from DexScreener pairs of one token (all pairs are combined)."""
    main = max(pairs, key=lambda p: _num((p.get("liquidity") or {}).get("usd")))
    base = main.get("baseToken") or {}

    buys = sells = 0
    for p in pairs:
        h24 = (p.get("txns") or {}).get("h24") or {}
        buys += int(h24.get("buys") or 0)
        sells += int(h24.get("sells") or 0)

    created = [p["pairCreatedAt"] for p in pairs if p.get("pairCreatedAt")]
    socials, websites = set(), False
    twitter_url = None
    for p in pairs:
        info = p.get("info") or {}
        websites = websites or bool(info.get("websites"))
        for s in info.get("socials") or []:
            socials.add(s.get("type"))
            if s.get("type") == "twitter" and not twitter_url:
                twitter_url = s.get("url")

    return {
        "mint": base.get("address"),
        "symbol": base.get("symbol") or "?",
        "name": base.get("name") or "?",
        "price_usd": _num(main.get("priceUsd")),
        "mcap": _num(main.get("marketCap") or main.get("fdv")),
        "liquidity_usd": sum(_num((p.get("liquidity") or {}).get("usd")) for p in pairs),
        "volume_24h": sum(_num((p.get("volume") or {}).get("h24")) for p in pairs),
        "buys_24h": buys,
        "sells_24h": sells,
        "price_change_24h": _num((main.get("priceChange") or {}).get("h24")),
        "age_hours": (now_ms - min(created)) / 3_600_000 if created else None,
        "has_twitter": "twitter" in socials,
        "has_telegram": "telegram" in socials,
        "has_website": websites,
        "twitter_url": twitter_url,
        "dex_url": main.get("url"),
    }


def is_meme(mint, jup):
    """Treat a coin as a meme unless Jupiter tags it as something else (DeFi, infra, stocks, ...)
    or its symbol is a known non-meme project. Jupiter's "meme" tag is too sparse to require."""
    tags = set((jup or {}).get("tags") or [])
    if "meme" in tags:
        return True
    if tags & config.JUPITER_EXCLUDE_TAGS:
        return False
    return ((jup or {}).get("symbol") or "").upper() not in config.NON_MEME_SYMBOLS


def jup_metrics(mint, jup):
    """Organic trading and holder growth from Jupiter (None when Jupiter has no data)."""
    stats = (jup or {}).get("stats24h") or {}
    org = (jup or {}).get("organicScore")
    hc = stats.get("holderChange")
    return {
        "organic_score": float(org) if org is not None and jup else None,
        "organic_label": (jup or {}).get("organicScoreLabel"),
        "holder_change_24h": float(hc) if hc is not None else None,
        "jup_tags": [t for t in (jup or {}).get("tags") or [] if t not in ("unknown", "token-2022")],
        "is_meme": is_meme(mint, jup),
    }


def rug_metrics(report):
    """Metrics from a RugCheck report."""
    supply = _num((report.get("token") or {}).get("supply"))
    known = report.get("knownAccounts") or {}
    creator = report.get("creator")

    def is_infra(h):
        acct = known.get(h.get("owner")) or known.get(h.get("address")) or {}
        return acct.get("type") in INFRA_TYPES

    all_holders = report.get("topHolders") or []
    real = sorted((h for h in all_holders if not is_infra(h)),
                  key=lambda h: _num(h.get("pct")), reverse=True)

    # RugCheck's creatorBalance can read 0 while the creator wallet still shows up
    # in the holder list, so take whichever is larger.
    creator_pct = sum(_num(h.get("pct")) for h in all_holders if creator and h.get("owner") == creator)
    if supply:
        creator_pct = max(creator_pct, _num(report.get("creatorBalance")) / supply * 100)

    network_raw = sum(_num(n.get("currentHolding", n.get("tokenAmount")))
                      for n in report.get("insiderNetworks") or [])
    insider_pct = network_raw / supply * 100 if supply else 0.0
    flagged_pct = sum(_num(h.get("pct")) for h in all_holders if h.get("insider"))
    # RugCheck can count tokens moving between linked wallets more than once,
    # so network totals sometimes exceed the supply. Cap at 100%.
    insider_pct = min(max(insider_pct, flagged_pct), 100.0)

    def market_liq(m):
        lp = m.get("lp") or {}
        return _num(lp.get("baseUSD")) + _num(lp.get("quoteUSD"))

    markets = report.get("markets") or []
    lp_locked = None
    if markets:
        main = max(markets, key=market_liq)
        if main.get("marketType") == "pump_fun":  # still on the bonding curve
            lp_locked = 100.0
        else:
            lp_locked = _num((main.get("lp") or {}).get("lpLockedPct"))

    risks = report.get("risks") or []
    return {
        "mint_revoked": report.get("mintAuthority") is None,
        "freeze_revoked": report.get("freezeAuthority") is None,
        "rugged": bool(report.get("rugged")),
        "lp_locked_pct": lp_locked,
        "top10_pct": sum(_num(h.get("pct")) for h in real[:10]),
        "max_single_pct": _num(real[0].get("pct")) if real else 0.0,
        "creator_pct": creator_pct,
        "insider_pct": insider_pct,
        "total_holders": int(report.get("totalHolders") or 0),
        "danger_risks": [r.get("name") for r in risks if r.get("level") == "danger"],
        "warn_risks": [r.get("name") for r in risks if r.get("level") == "warn"],
        "holder_correlation": any("correlation" in (r.get("name") or "").lower() for r in risks),
        "rugcheck_score": report.get("score_normalised"),
    }
