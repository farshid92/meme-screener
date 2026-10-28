"""API clients for DexScreener and RugCheck (both free, no key needed)."""
import logging
import time
from datetime import datetime

import requests

import config
from screener.metrics import is_meme

log = logging.getLogger(__name__)

DEX = "https://api.dexscreener.com"
RUG = "https://api.rugcheck.xyz/v1"

_session = requests.Session()
_session.headers["User-Agent"] = config.USER_AGENT


def _get(url, retries=3):
    """GET JSON with backoff on network errors, 429s and 5xx."""
    for attempt in range(retries + 1):
        try:
            r = _session.get(url, timeout=config.HTTP_TIMEOUT)
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                r.raise_for_status()
            try:
                wait = float(r.headers.get("Retry-After", ""))
            except ValueError:
                wait = 2 ** (attempt + 1)
            log.info("HTTP %s from %s, waiting %.0fs", r.status_code, url, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()


def candidate_tokens(chain=config.CHAIN):
    """Token addresses currently getting attention on DexScreener (boosted + new profiles)."""
    seen = {}
    for path in ("/token-boosts/top/v1", "/token-boosts/latest/v1", "/token-profiles/latest/v1"):
        try:
            items = _get(DEX + path) or []
        except Exception as e:  # one feed failing shouldn't kill the scan
            log.warning("Could not load %s: %s", path, e)
            continue
        for it in items:
            if it.get("chainId") == chain and it.get("tokenAddress"):
                seen.setdefault(it["tokenAddress"], path)
    return list(seen)


GECKO = "https://api.geckoterminal.com/api/v2"


def gecko_tokens(chain=config.CHAIN, lists=None):
    """Base-token mints from GeckoTerminal pool lists (volume, pumpswap, trending)."""
    lists = config.GECKO_LISTS if lists is None else lists
    found = []
    for path, query, pages in lists:
        for page in range(1, pages + 1):
            url = f"{GECKO}/networks/{chain}/{path}?page={page}&{query}"
            try:
                data = (_get(url) or {}).get("data") or []
            except Exception as e:
                log.warning("GeckoTerminal %s page %d failed: %s", path, page, e)
                break
            finally:
                time.sleep(config.GECKO_DELAY_S)
            for pool in data:
                tid = ((pool.get("relationships") or {}).get("base_token") or {}).get("data", {}).get("id", "")
                if tid.startswith(chain + "_"):
                    found.append(tid[len(chain) + 1:])
            if len(data) < 20:  # last page
                break
    return list(dict.fromkeys(found))


JUPITER = "https://lite-api.jup.ag/tokens/v2"


def jupiter_verified():
    """All Jupiter-verified tokens (one call; each item includes mcap, liquidity, firstPool, stats24h, tags)."""
    data = _get(f"{JUPITER}/tag?query=verified")
    return data if isinstance(data, list) else []


def jupiter_top(category, interval, limit=100):
    """Jupiter top list, e.g. ('toptrending', '1h'). Each item has full token info."""
    data = _get(f"{JUPITER}/{category}/{interval}?limit={limit}")
    return data if isinstance(data, list) else []


def jupiter_info(mints, batch=50):
    """{mint: Jupiter token info} for any tokens (verified or not), via the search endpoint."""
    out = {}
    for i in range(0, len(mints), batch):
        chunk = mints[i:i + batch]
        try:
            data = _get(f"{JUPITER}/search?query={','.join(chunk)}") or []
        except Exception as e:
            log.warning("Jupiter search failed for a batch: %s", e)
            continue
        wanted = set(chunk)
        for t in data if isinstance(data, list) else []:
            if t.get("id") in wanted:
                out[t["id"]] = t
    return out


def jupiter_candidates(tokens, now_ms):
    """Mints from the Jupiter list that already pass the DexScreener-stage gates and look like memes."""
    g = config.GATES
    out = []
    for t in tokens:
        tags = set(t.get("tags") or [])
        if tags & config.JUPITER_EXCLUDE_TAGS:
            continue
        if config.REQUIRE_MEME and not is_meme(t.get("id", ""), t):
            continue
        if (t.get("symbol") or "").upper() in config.EXCLUDE_SYMBOLS:
            continue
        if (t.get("mcap") or 0) < g["min_mcap_usd"] or (t.get("liquidity") or 0) < g["min_liquidity_usd"]:
            continue
        created = (t.get("firstPool") or {}).get("createdAt")
        try:
            created_ms = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp() * 1000
        except (AttributeError, ValueError):
            continue
        if (now_ms - created_ms) / 3_600_000 < g["min_age_hours"]:
            continue
        s = t.get("stats24h") or {}
        if (s.get("buyVolume") or 0) + (s.get("sellVolume") or 0) < config.JUPITER_MIN_VOLUME_24H:
            continue
        if t.get("id"):
            out.append(t["id"])
    return out


def dex_pairs(addresses, chain=config.CHAIN):
    """{token_address: [pair, ...]} for pairs where the token is the base token."""
    out = {}
    wanted = set(addresses)
    for i in range(0, len(addresses), 30):  # endpoint takes up to 30 addresses
        batch = addresses[i:i + 30]
        pairs = _get(f"{DEX}/tokens/v1/{chain}/{','.join(batch)}") or []
        for p in pairs:
            addr = (p.get("baseToken") or {}).get("address")
            if addr in wanted:
                out.setdefault(addr, []).append(p)
    return out


def rugcheck_report(mint):
    """Full RugCheck report for a Solana token mint."""
    report = _get(f"{RUG}/tokens/{mint}/report")
    time.sleep(config.RUGCHECK_DELAY_S)
    return report
