#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram channel table of contents · TOC topic assistant
========================================================
Reads docs/posts.json and proposes a draft docs/topics.json:
turns the most frequent post hashtags into topics.

Brand tags are cut off automatically: if a tag sits on more than 40% of the
posts, it is useless as a topic (it is the channel name) and does not get
into the draft.

This is an ASSISTANT, not magic: the draft needs review — fix the titles
("books" → "Books & Reading") and pick the icons. Edit the resulting file
directly; it gets overwritten only on a re-run with the --force flag.

Usage:
    python3 pipeline/suggest_topics.py                       # show the draft in the console
    python3 pipeline/suggest_topics.py --write               # write docs/topics.json
    python3 pipeline/suggest_topics.py --write --force       # overwrite the existing file
    python3 pipeline/suggest_topics.py --limit 6 --min 3     # fewer topics, higher threshold

Flags:
    --limit N   maximum topics in the draft (default 8)
    --min N     minimum posts with a tag for it to become a topic (default 2)
"""
import argparse
import json
import os
import sys
from collections import Counter

# Icon picked by the meaning of the tag (word starts); otherwise 🏷
# Bilingual ON PURPOSE: Cyrillic roots match RU hashtags from channel
# exports, Latin keys match the EN topic tags of the pipeline.
ICONS = {
    "книг": "📚", "читен": "📚", "чтени": "📚",
    "библи": "📖", "писан": "📖", "псалом": "📖", "евангел": "📖",
    "размышл": "🧠", "дневник": "🧠", "психолог": "🧠",
    "цитат": "💬", "афоризм": "💬",
    "истори": "🏛", "церков": "⛪", "вера": "⛪", "вероучен": "⛪", "богослов": "⛪",
    "анонс": "📣", "новост": "📣", "событи": "📣",
    "юмор": "😄", "шутк": "😄", "анекдот": "😄",
    "видео": "🎬", "подкаст": "🎙", "аудио": "🎧", "музык": "🎵",
    "опрос": "📊", "итог": "📈", "статистик": "📈",
    "семь": "👨‍👩‍👧", "дет": "👨‍👩‍👧", "воспитан": "👨‍👩‍👧",
    "обществ": "🏙", "город": "🏙", "люд": "🏙",
    "жизн": "🌿", "здоров": "🌿", "спорт": "⚽",
    "техник": "💻", "наук": "🔬", "финанс": "💼", "деньг": "💼",
    "едa": "🍳", "еда": "🍳", "рецепт": "🍳", "путешеств": "✈️",
    "культур": "🎭", "кино": "🍿", "искусств": "🎭",
    # EN tags (pipeline whitelist + common ones)
    "book": "📚", "read": "📚",
    "bible": "📖", "scripture": "📖", "gospel": "📖",
    "reflect": "🧠", "diary": "🧠", "psycholog": "🧠",
    "quote": "💬", "aphorism": "💬",
    "histor": "🏛", "church": "⛪", "faith": "⛪", "theolog": "⛪",
    "announce": "📣", "news": "📣", "event": "📣",
    "humor": "😄", "joke": "😄", "anecdote": "😄",
    "video": "🎬", "podcast": "🎙", "audio": "🎧", "music": "🎵",
    "poll": "📊", "statistic": "📈", "result": "📈",
    "famil": "👨‍👩‍👧", "child": "👨‍👩‍👧", "parent": "👨‍👩‍👧",
    "society": "🏙", "city": "🏙", "people": "🏙",
    "life": "🌿", "health": "🌿",
    "technolog": "💻", "science": "🔬", "financ": "💼", "econom": "💼", "money": "💼",
    "food": "🍳", "recipe": "🍳", "travel": "✈️",
    "culture": "🎭", "cinema": "🍿", "movie": "🍿", "art": "🎭",
    "conflict": "⚔️", "politic": "🏛", "incident": "🚨", "energy": "⚡",
    "transport": "🚆", "sport": "⚽",
}


def log(m): print(m, flush=True)

# Tags that must not become topics. Bilingual ON PURPOSE: RU service series
# markers are kept, EN stopwords are added (both RU and EN hashtags occur).
STOP_TAGS = {
    # RU service series markers
    "продолжение", "окончание", "завершение", "продолжение_следует",
    # EN stopwords
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "as", "is", "are", "was", "were", "be", "been",
    "will", "would", "could", "should", "this", "that", "these", "those",
    "it", "its", "he", "she", "they", "them", "his", "her", "their", "we",
    "our", "you", "your", "i", "me", "my", "have", "has", "had", "do",
    "does", "did", "not", "no", "but", "if", "then", "than", "so", "such",
    "more", "most", "over", "after", "before", "about", "into", "out",
    "up", "down", "new", "said", "says", "report", "reports", "according",
}


def norm(t):
    return str(t or "").lstrip("#").lower()


def icon_for(tag):
    for k, v in ICONS.items():
        if k in tag:
            return v
    return "🏷"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--posts", default="docs/posts.json")
    ap.add_argument("--write", action="store_true", help="write the draft to topics.json")
    ap.add_argument("--force", action="store_true", help="overwrite the existing file")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--min", type=int, default=2)
    args = ap.parse_args()

    if not os.path.exists(args.posts):
        log(f"!! file not found: {args.posts}")
        return 1
    with open(args.posts, "r", encoding="utf-8") as f:
        doc = json.load(f)
    posts = doc.get("posts") or []
    if not posts:
        log("There are no posts in posts.json yet. Run the pipeline or the import first.")
        return 1
    total = len(posts)

    counts = Counter()
    for p in posts:
        for t in p.get("tags") or []:
            k = norm(t)
            if k:
                counts[k] += 1

    brand = {t for t, n in counts.items() if n > total * 0.4}
    if brand:
        log("Looks like brand tags (on the majority of posts — not usable as topics): "
            + ", ".join("#" + t for t in sorted(brand)))

    candidates = [(t, n) for t, n in counts.most_common()
                  if n >= args.min and t not in brand and t not in STOP_TAGS]
    if not candidates:
        log("No suitable tags found. If the channel has no hashtags — set the topics manually "
            "in docs/topics.json (by keywords) or ask the seller to configure the rubrics.")
        return 1

    topics = []
    for t, n in candidates[: args.limit]:
        topics.append({
            "id": t,
            "title": t[:1].upper() + t[1:],
            "icon": icon_for(t),
            "tags": [t],
        })

    draft = {
        "version": 1,
        "_note": "Draft from suggest_topics.py: fix the titles and icons, delete what you don't need.",
        "topics": topics,
    }

    log(f"\nTopic draft ({len(topics)} items, {total} posts in total):")
    for t in topics:
        n = counts[t["id"]]
        log(f"  {t['icon']} {t['title']} — {n} post{'s' if n != 1 else ''}")
    misc = sum(1 for p in posts if not any(norm(x) in {tt["id"] for tt in topics} for x in (p.get("tags") or [])))
    log(f"  🗂 Misc (does not fall into any topic): {misc} post{'s' if misc != 1 else ''}")
    log("\nNext: review the draft — topic titles, icons, the set of a topic's tags "
        "(several synonyms can be listed in tags).")

    if args.write:
        out = "docs/topics.json"
        if os.path.exists(out) and not args.force:
            log(f"\n!! {out} already exists — add --force to overwrite.")
            return 1
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
            f.write("\n")
        log(f"Draft written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
