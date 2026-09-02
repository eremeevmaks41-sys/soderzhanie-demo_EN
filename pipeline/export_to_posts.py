#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram channel table of contents · history import
===================================================
Turns a Telegram Desktop export (result.json) into docs/posts.json.

How to get result.json:
    Telegram Desktop → channel → ⋮ (three dots) → "Export chat history"
    → format "Machine-readable JSON" → download.

Usage:
    python pipeline/export_to_posts.py \
        --input import/result.json \
        --posts docs/posts.json \
        --channel-username @my_channel

Flags:
    --merge     add to the existing table of contents (export takes priority)
                [on by default]
    --replace   fully replace the table of contents with the export

Posts without text (only photo/video/audio) get a fallback title "📷 Photo
post" and so on. Polls are captioned with their question: "📊 <question>".
Service records (member joined, etc.) are skipped.
"""
import argparse
import html as html_mod
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
PHOTO_TITLE = "📷 Photo post"
VIDEO_TITLE = "🎬 Video post"
AUDIO_TITLE = "🎧 Audio"
FILE_TITLE = "📎 File"
POLL_TITLE = "📊 Poll"

def log(m): print(m, flush=True)

def clean(s):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s

def strip_hashtags(s):
    # bilingual ON PURPOSE: \w already matches Latin letters, а-яё keeps
    # Cyrillic hashtags (from RU channel exports) recognized
    return clean(re.sub(r"#[\wа-яё]+", "", s or "", flags=re.I))

def extract_text(raw):
    """text in result.json is either a string or a list of strings and objects."""
    if isinstance(raw, str):
        return raw
    parts = []
    if isinstance(raw, list):
        for p in raw:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                parts.append(str(p.get("text") or ""))
    return "\n".join(x for x in parts if x)

def split_title(line, limit=100):
    """(title, rest of the line): cut ONLY at a word boundary, so the
    preview never starts with a word fragment."""
    if len(line) <= limit:
        return line, ""
    cut = line[:limit]
    sp = cut.rfind(" ")
    if sp > 40:
        return cut[:sp].rstrip(" ,;:-") + "…", line[sp:].strip()
    return cut.rstrip(" ,;:-") + "…", ""

def clean_line(l):
    """Cleans ONE line: squeezes spaces but keeps line breaks."""
    return re.sub(r"[ \t\u00a0]+", " ", l or "").strip()

def first_meaningful_line(body):
    """Index of the first meaningful line (not empty, not made of hashtags
    only and not emoji/symbols only — the title needs a letter or a digit)."""
    lines = [clean_line(l) for l in (body or "").splitlines()]
    for i, line in enumerate(lines):
        if not line:
            continue
        if re.fullmatch(r"#[\wа-яё]+(\s+#[\wа-яё]+)*", line, flags=re.I):
            continue  # a line of hashtags only — take the next one
        if not re.search(r"[0-9a-zа-яё]", line, flags=re.I):
            continue  # an emoji-only line ("🌿") cannot be a title
        return i, lines
    return -1, lines

def detect_kind(msg):
    """Content type for the universal TOC grouping."""
    if (msg.get("poll") or {}).get("question"):
        return "poll"
    if msg.get("photo"):
        return "photo"
    if msg.get("media_type") in ("video_file", "video_message"):
        return "video"
    if msg.get("media_type") == "audio_file":
        return "audio"
    if msg.get("sticker_emoji"):
        return "sticker"
    if msg.get("file"):
        return "file"
    return "text"

def build_entry(msg, username):
    raw_text = extract_text(msg.get("text"))
    hashtags = re.findall(r"#([\wа-яё]+)", raw_text, flags=re.I)  # Latin + Cyrillic hashtags
    i0, lines = first_meaningful_line(raw_text)
    has_media = any(msg.get(k) for k in ("photo", "video_file", "media_type", "file"))
    if i0 < 0:
        # no text title — caption the media/poll
        poll_q = clean((msg.get("poll") or {}).get("question") or "")
        if poll_q:
            title, _ = split_title(POLL_TITLE + ' "' + poll_q + '"', 110)
            rest_line = ""
        elif msg.get("photo"):
            title, rest_line = PHOTO_TITLE, ""
        elif msg.get("media_type") in ("video_file", "video_message"):
            title, rest_line = VIDEO_TITLE, ""
        elif msg.get("media_type") == "audio_file":
            title, rest_line = AUDIO_TITLE, ""
        elif msg.get("file"):
            title, rest_line = FILE_TITLE, ""
        elif hashtags:
            title, rest_line = "#" + hashtags[0], ""
        else:
            return None  # nothing to show
    else:
        title, rest_line = split_title(lines[i0])
    # preview: rest of the title line + the following lines, hashtags stripped
    preview = strip_hashtags(" ".join(x for x in [rest_line] + lines[i0 + 1:] if x)) if i0 >= 0 else ""
    dt = msg.get("date") or ""
    try:
        d = datetime.fromisoformat(dt)
        date_s, time_s = d.strftime("%Y-%m-%d"), d.strftime("%H:%M")
    except ValueError:
        date_s, time_s = dt[:10], dt[11:16] if len(dt) > 15 else ""
    tags = ["#" + t for t in hashtags[:4]]
    return {
        "id": msg.get("id"),
        "date": date_s, "time": time_s,
        "title": title,
        "preview": preview[:180],
        "tags": tags,
        "kind": detect_kind(msg),
        "url": f"https://t.me/{username}/{msg.get('id')}",
        "src": "",  # channel posts have no external source
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="path to result.json (Telegram Desktop export)")
    ap.add_argument("--posts", default="docs/posts.json")
    ap.add_argument("--channel-username", required=True, help="channel @username")
    ap.add_argument("--replace", action="store_true", help="replace the whole table of contents with the export")
    args = ap.parse_args()

    username = args.channel_username.strip().lstrip("@").replace("https://t.me/", "")
    try:
        with open(args.input, "r", encoding="utf-8") as f:
            export = json.load(f)
    except FileNotFoundError:
        log(f"!! file not found: {args.input}")
        return 1
    except json.JSONDecodeError as e:
        log(f"!! this is not JSON: {e}")
        return 1

    messages = export.get("messages") or []
    log(f"Export: \"{export.get('name','?')}\", records: {len(messages)}")

    fresh, skipped = [], 0
    for m in messages:
        if m.get("type") != "message":
            skipped += 1
            continue
        e = build_entry(m, username)
        if e:
            fresh.append(e)
        else:
            skipped += 1

    if args.replace:
        merged = fresh
    else:
        doc = None
        if os.path.exists(args.posts):
            with open(args.posts, "r", encoding="utf-8") as f:
                doc = json.load(f)
        old = (doc or {}).get("posts") or []
        by_url = {p.get("url"): p for p in old}
        for p in fresh:
            by_url[p["url"]] = p   # the export takes priority
        merged = list(by_url.values())

    merged.sort(key=lambda p: (p.get("date", ""), p.get("time", ""), str(p.get("id", ""))), reverse=True)
    doc = {
        "version": 1,
        "updated_at": datetime.now(MSK).isoformat(timespec="seconds"),
        "channel": {
            "name": export.get("name") or username,
            "url": f"https://t.me/{username}",
        },
        "posts": merged,
    }
    os.makedirs(os.path.dirname(args.posts) or ".", exist_ok=True)
    with open(args.posts, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    log(f"Done: {len(merged)} posts in the table of contents (new from the export: {len(fresh)}, skipped: {skipped})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
