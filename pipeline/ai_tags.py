#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram channel table of contents · tagging via any AI chatbot
===============================================================
The product intentionally ships without a built-in AI (zero server costs).
Instead the script does two simple things:

  1) --prepare  builds a compact file of posts from result.json together with
     a READY-MADE prompt: paste its contents into any chatbot (ChatGPT,
     Claude, Gemini, GigaChat, ...) and save the answer to the file
     ai_tags_answer.json.

  2) --apply    collects the AI answer back: it builds docs/posts.json where
     every post gets its AI tags (posts without text — media/polls — receive
     the same fallback captions as a plain import).

After that run suggest_topics.py — it turns the tags into TOC topics.

Usage:
    python3 pipeline/ai_tags.py --prepare --input import/result.json
    #  → ai_tags_prompt.txt appears: copy it to the bot, save the answer
    #    as ai_tags_answer.json next to result.json

    python3 pipeline/ai_tags.py --apply --input import/result.json \
        --tags ai_tags_answer.json --posts docs/posts.json \
        --channel-username @my_channel

If the AI mislabels a couple of posts — no big deal: edit the tags right in
docs/posts.json, the record format is obvious.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from export_to_posts import build_entry, detect_kind, log  # noqa: E402

# The fixed topic vocabulary (TAG_WHITELIST) the AI must pick its tags from.
PROMPT_RULES = """TASK: sort the posts of a Telegram channel into topics.

RULES:
1. Use EXACTLY these topic tags, lowercase, no hash sign:
   conflict, politics, economy, science, technology, health, incidents,
   culture, sport, society, energy, transport
2. Assign each post 1–2 topics FROM THIS LIST (the same fixed set for all
   posts; do not invent new tags per post).
3. For small/service posts pick the closest topic from the list; if nothing
   fits at all, return an empty tags array.
4. Answer with a STRICTLY valid JSON array, no explanations, no markdown:
[{"id": 42, "tags": ["technology"]}, {"id": 43, "tags": ["conflict", "politics"]}]

POSTS (format: id | date | start of the text):
"""


def clean_one_line(s, limit=110):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true", help="build the prompt for the AI")
    ap.add_argument("--apply", action="store_true", help="merge the AI answer into posts.json")
    ap.add_argument("--input", default="import/result.json", help="Telegram export result.json")
    ap.add_argument("--tags", default="ai_tags_answer.json", help="AI answer (JSON array)")
    ap.add_argument("--out", default="ai_tags_prompt.txt", help="where to write the prompt")
    ap.add_argument("--posts", default="docs/posts.json")
    ap.add_argument("--channel-username", default="", help="channel @username (for --apply)")
    ap.add_argument("--limit", type=int, default=120, help="characters of post text in the prompt")
    args = ap.parse_args()

    if not args.prepare and not args.apply:
        log("Pick an action: --prepare (build the prompt) or --apply (collect the answer)")
        return 1

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            export = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        log(f"!! cannot read {args.input}: {e}")
        return 1

    messages = [m for m in (export.get("messages") or []) if m.get("type") == "message"]
    username = (args.channel_username or export.get("name") or "channel").strip()

    # ── mode 1: prompt ──
    if args.prepare:
        rows = []
        for m in messages:
            raw = m.get("text")
            if isinstance(raw, list):
                raw = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
            body = clean_one_line(raw, args.limit)
            if not body:
                continue  # media/polls without text: captions are generic, no AI needed
            rows.append(f"{m.get('id')} | {m.get('date','')[:10]} | {body}")
        if not rows:
            log("There are no text posts in the export — AI tagging is not needed.")
            return 1
        if len(rows) > 600:
            log(f"!! {len(rows)} posts — the prompt file will be large. "
                f"If the bot won't take it whole, split it: first half of the lines, then the second.")
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(PROMPT_RULES)
            f.write("\n".join(rows))
            f.write("\n")
        log(f"Done: {args.out} ({len(rows)} text posts).")
        log("Copy the file contents into any chatbot, save the answer as "
            f"{args.tags} and run: python3 pipeline/ai_tags.py --apply "
            f"--input {args.input} --tags {args.tags} --channel-username @my_channel")
        return 0

    # ── mode 2: apply the AI answer ──
    if not args.channel_username:
        log("!! --apply requires --channel-username @my_channel")
        return 1
    try:
        with open(args.tags, "r", encoding="utf-8") as f:
            raw_answer = f.read()
    except FileNotFoundError:
        log(f"!! no answer file: {args.tags}")
        return 1
    raw_answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_answer.strip(),
                        flags=re.M | re.S)  # the bot may have wrapped the answer in a markdown block
    try:
        start, end = raw_answer.find("["), raw_answer.rfind("]")
        answer = json.loads(raw_answer[start:end + 1])
    except (ValueError, json.JSONDecodeError) as e:
        log(f"!! cannot parse the AI answer: {e}. Ask the bot to \"return only a JSON array\" and retry.")
        return 1

    ai_tags = {}
    for item in answer if isinstance(answer, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        tags = []
        for t in (item.get("tags") or [])[:3]:
            t = str(t).strip().lstrip("#").lower()[:30]
            if t and t not in tags:
                tags.append("#" + t)
        if tags:
            ai_tags[pid] = tags
    log(f"AI answer: tags for {len(ai_tags)} posts.")

    fresh, with_ai, skipped = [], 0, 0
    for m in messages:
        if m.get("type") != "message":
            skipped += 1
            continue
        e = build_entry(m, username.lstrip("@").replace("https://t.me/", ""))
        if not e:
            skipped += 1
            continue
        e["kind"] = detect_kind(m)
        if e["id"] in ai_tags:
            e["tags"] = ai_tags[e["id"]]
            with_ai += 1
        fresh.append(e)

    username_clean = username.lstrip("@").replace("https://t.me/", "")
    old = []
    if os.path.exists(args.posts):
        try:
            with open(args.posts, "r", encoding="utf-8") as f:
                old = (json.load(f).get("posts")) or []
        except (json.JSONDecodeError, OSError):
            old = []
    by_url = {p.get("url"): p for p in old}
    for p in fresh:
        by_url[p["url"]] = p
    merged = list(by_url.values())
    merged.sort(key=lambda p: (p.get("date", ""), p.get("time", ""), str(p.get("id", ""))), reverse=True)

    doc = {
        "version": 1,
        "updated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone(__import__("datetime").timedelta(hours=3))
        ).isoformat(timespec="seconds"),
        "channel": {
            "name": export.get("name") or username_clean,
            "url": f"https://t.me/{username_clean}",
        },
        "posts": merged,
    }
    os.makedirs(os.path.dirname(args.posts) or ".", exist_ok=True)
    with open(args.posts, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    log(f"Done: {len(merged)} posts in the table of contents; with AI tags: {with_ai}; "
        f"media/polls with fallback captions: {len(fresh) - with_ai}; skipped: {skipped}")
    log("Next: python3 pipeline/suggest_topics.py --write — it turns the tags into TOC topics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
