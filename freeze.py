"""Render the dashboard to static HTML files (for GitHub Pages). No positions are included.

Usage: python freeze.py [output folder, default "site"]
"""
import os
import re
import sys

import app as dashboard

PAGES = {"index.html": "/?view=shortlist", "all.html": "/?view=all", "early.html": "/early"}
LINKS = [('href="/?view=shortlist"', 'href="index.html"'), ('href="/?view=all"', 'href="all.html"'),
         ('href="/early"', 'href="early.html"'), ('href="/"', 'href="index.html"')]
TOKEN_LINK = re.compile(r'href="/token/([A-Za-z0-9]+)"')


def static_links(html):
    for a, b in LINKS:
        html = html.replace(a, b)
    return TOKEN_LINK.sub(r'href="https://dexscreener.com/solana/\1" target="_blank" rel="noopener"', html)


def build(out="site"):
    os.makedirs(out, exist_ok=True)
    dashboard.app.config["STATIC"] = True
    client = dashboard.app.test_client()
    for name, route in PAGES.items():
        resp = client.get(route)
        if resp.status_code != 200:
            raise SystemExit(f"{route} returned {resp.status_code}")
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(static_links(resp.data.decode()))
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"Built {len(PAGES)} pages into {out}/")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "site")
