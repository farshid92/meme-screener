"""One-time Telegram setup.

1. In Telegram, open @BotFather, send /newbot, follow the steps, copy the token it gives you.
2. Run: python telegram_setup.py   and paste the token.
3. Open your new bot in Telegram and send it any message (e.g. "hi").
The script finds your chat, saves telegram.json and sends a test message.
"""
import time

from screener import telegram


def main():
    token = input("Paste your bot token from @BotFather: ").strip()
    me = telegram.call(token, "getMe")
    print(f"Bot found: @{me['username']}")
    print(f"Now open https://t.me/{me['username']} in Telegram and send it any message. Waiting up to 2 minutes...")

    chat_id = None
    deadline = time.time() + 120
    while time.time() < deadline and chat_id is None:
        for upd in telegram.call(token, "getUpdates", timeout=10):
            msg = upd.get("message") or {}
            if msg.get("chat", {}).get("type") == "private":
                chat_id = msg["chat"]["id"]
                break
    if chat_id is None:
        print("No message received. Send your bot a message and run this script again.")
        return

    telegram.save_settings(token, chat_id)
    telegram.send("✅ Meme screener connected. You'll get the daily digest and position alerts here.",
                  {"token": token, "chat_id": chat_id})
    print("Saved telegram.json and sent a test message. Keep telegram.json private (it contains your bot token).")


if __name__ == "__main__":
    main()
