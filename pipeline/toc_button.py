#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram channel table of contents · TOC button post in the channel
===================================================================
Publishes a post with a "📖 Table of Contents" button in the channel and
pins it. Runs from GitHub Actions (workflow button.yml) or manually locally.

How it works (Bot API 10.x, verified by a live test):
  web_app buttons in channel posts are FORBIDDEN ("Available in private
  chats only"), so the official "main mini app" pattern is used:
    1. the script binds the Pages URL to the bot as the main mini app
       (setChatMenuButton) — needed once, repeating it is harmless;
    2. it publishes a post with a REGULAR url button pointing to the direct
       link https://t.me/<bot>?startapp — it opens the mini app in Telegram;
    3. it pins the post to the top of the channel.

Arguments:
    --url   https://<login>.github.io/<repo>/   mini app address (Pages)
    --app-link  https://t.me/<bot>/<name>  direct Direct-Link Mini App URL
                (created once in BotFather via /newapp; BEST UX: the button
                opens the catalog right away, without a bot chat and /start)
    --text  button label (default "📖 Table of Contents")
    --caption  text above the button
    --no-pin   do not pin the post

Secrets: BOT_TOKEN, CHANNEL_USERNAME (same as for the news pipeline).
"""
import argparse
import json
import os
import sys
import urllib.request

def http_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="mini app address on GitHub Pages")
    ap.add_argument("--app-link", default="",
                    help="direct Direct-Link Mini App URL (t.me/bot/name) — takes priority")
    ap.add_argument("--text", default="📖 Table of Contents")
    ap.add_argument("--caption", default="All the channel's posts in one catalog.\nSearch by topics, dates and words 👇")
    ap.add_argument("--no-pin", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("BOT_TOKEN", "")
    chat = (os.environ.get("CHANNEL_USERNAME", "") or "").strip().lstrip("@").replace("https://t.me/", "")
    if not token or not chat:
        print("!! BOT_TOKEN and CHANNEL_USERNAME are required (environment or GitHub Secrets)")
        return 1
    if not args.url.startswith("https://"):
        print("!! --url must start with https:// (the GitHub Pages address)")
        return 1

    api = f"https://api.telegram.org/bot{token}"

    # 0. Bot username — for the direct mini app link
    me = http_json(api + "/getMe", {})
    if not me.get("ok"):
        print(f"!! getMe: {me.get('description')}")
        return 1
    bot = me["result"]["username"]

    # Choose the button link: direct (Direct-Link) > ?startapp fallback
    if args.app_link:
        app_link = args.app_link.strip()
        if not app_link.startswith("https://t.me/"):
            print("!! --app-link must start with https://t.me/")
            return 1
        print("✓ the button points to the Direct-Link Mini App:", app_link)
    else:
        app_link = f"https://t.me/{bot}?startapp"
        print("! no direct link set — falling back to t.me/{}?startapp".format(bot))
        print("  (best UX is a Direct-Link: BotFather → /newapp, see the guide, section 7)")

    # 1. Bind the mini app to the bot (main mini app) — makes the
    #    t.me/<bot>?startapp link work. Repeating the call is harmless.
    menu = http_json(api + "/setChatMenuButton", {
        "menu_button": {"type": "web_app", "text": args.text,
                        "web_app": {"url": args.url}},
    })
    print("✓ mini app bound to the bot (main mini app)" if menu.get("ok")
          else f"!! setChatMenuButton: {menu.get('description')} — the in-post button may not open")

    # 2. Post with a regular url button (web_app buttons are forbidden in channels)
    resp = http_json(api + "/sendMessage", {
        "chat_id": "@" + chat,
        "text": args.caption,
        "reply_markup": {"inline_keyboard": [[
            {"text": args.text, "url": app_link}
        ]]},
    })
    if not resp.get("ok"):
        print(f"!! Telegram: {resp.get('description')}")
        return 1
    msg_id = resp["result"]["message_id"]
    print(f"✓ button post published: t.me/{chat}/{msg_id}")

    # 3. Pinning
    if not args.no_pin:
        pin = http_json(api + "/pinChatMessage", {
            "chat_id": "@" + chat, "message_id": msg_id, "disable_notification": True,
        })
        print("✓ pinned in the channel" if pin.get("ok") else f"!! pin failed: {pin.get('description')} — pin it manually")
    print(f"\nDone: for the channel readers the \"{args.text}\" button opens the table of contents.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
