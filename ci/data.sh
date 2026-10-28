#!/usr/bin/env bash
# Keep the database between GitHub Actions runs on an orphan "data" branch.
#   pull: download screener.db (+ decrypt private.db) into ./data
#   push: prune, encrypt private.db, force-push a single fresh commit (no history growth)
set -euo pipefail
mkdir -p data

if [[ "${1:-}" == "pull" ]]; then
  if git ls-remote --exit-code --heads origin data >/dev/null 2>&1; then
    git fetch --depth=1 origin data
    git show FETCH_HEAD:screener.db > data/screener.db
    if git cat-file -e FETCH_HEAD:private.db.enc 2>/dev/null; then
      git show FETCH_HEAD:private.db.enc > data/private.db.enc
      python -m screener.vault decrypt data/private.db.enc data/private.db
    fi
    echo "Loaded data: $(du -h data/screener.db | cut -f1)"
  else
    echo "No data branch yet: starting fresh."
  fi

elif [[ "${1:-}" == "push" ]]; then
  python -c "import config; from screener import storage; c = storage.connect(config.DB_PATH, config.PRIVATE_DB_PATH); storage.prune(c); c.close()"
  tmp=$(mktemp -d)
  cp data/screener.db "$tmp/"
  if [[ -f data/private.db && -n "${POSITIONS_KEY:-}" ]]; then
    python -m screener.vault encrypt data/private.db "$tmp/private.db.enc"
  fi
  printf 'Database for the meme screener, rewritten on every run.\nprivate.db.enc is encrypted (positions, alerts, bot state).\n' > "$tmp/README.md"
  cd "$tmp"
  git init -q -b data
  git -c user.name="meme-screener" -c user.email="actions@users.noreply.github.com" add -A
  git -c user.name="meme-screener" -c user.email="actions@users.noreply.github.com" commit -qm "data $(date -u +%FT%TZ)"
  git push -q --force "${DATA_REMOTE:-https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git}" data
  echo "Saved data: $(du -h screener.db | cut -f1)"
else
  echo "usage: ci/data.sh pull|push" >&2; exit 2
fi
