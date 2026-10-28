"""Minimal Telegram Bot API client (sendMessage + getUpdates)."""
import json
import logging
import os

import requests

import config

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram's limit is 4096 characters


def load_settings(path=None):
    """Bot token + chat id: from TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (cloud secrets) or telegram.json."""
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        return {"token": token, "chat_id": int(chat)}
    path = path or config.TELEGRAM_SETTINGS
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    return s if s.get("token") and s.get("chat_id") else None


def save_settings(token, chat_id, path=None):
    with open(path or config.TELEGRAM_SETTINGS, "w", encoding="utf-8") as f:
        json.dump({"token": token, "chat_id": chat_id}, f, indent=2)


def call(token, method, **params):
    r = requests.post(API.format(token=token, method=method), json=params, timeout=config.HTTP_TIMEOUT)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {data.get('description')}")
    return data["result"]


def _chunks(text):
    """Split on line breaks so HTML tags aren't cut in half."""
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > MAX_LEN and chunk:
            yield chunk
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        yield chunk


def send(text, settings=None):
    """Send an HTML-formatted message. Returns False (and logs) if Telegram isn't set up or fails."""
    settings = settings or load_settings()
    if not settings:
        return False
    try:
        for part in _chunks(text):
            call(settings["token"], "sendMessage", chat_id=settings["chat_id"], text=part,
                 parse_mode="HTML", disable_web_page_preview=True)
        return True
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        print(f"Telegram send failed: {e}")
        return False
