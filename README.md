# "Table of Contents for Your Telegram Channel" — a ready-made post catalog

**A ready-made file kit**: your channel gets a TABLE OF CONTENTS of all its posts —
a mini-app with topics, tags, search and dates that opens right inside Telegram
via a button, plus a news pipeline with AI-written cards. Hosting is free forever
(GitHub Pages), news publishing is automatic (GitHub Actions). Not a single server
to rent.

- Kit price: **one-time payment** (as shown by the sales bot — no subscription)
- Cost of ownership: **$0/month**
- Setup time following the guide: **30–60 minutes**

---

## How it works

```
                     CHANNEL OWNER (once, ~40 minutes)
     ┌──────────────────────────────────────────────────────────┐
     │  1. GitHub repository + kit files (drag & drop)          │
     │  2. Bot via BotFather (token → GitHub secrets)           │
     │  3. Channel → bot added as admin (posting messages)      │
     │  4. GitHub Pages enabled (/docs folder)                  │
     │  5. config.js filled in (channel + license key)          │
     └──────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┴────────────────────┐
        ▼                                          ▼
  NEWS PIPELINE v2.3-en                      "TABLE OF CONTENTS" MINI-APP
  (GitHub Actions, every 2 hours)            (GitHub Pages, free)
  9 RSS sources → AI card in English         4 sections: months · alphabet
  (photos/videos) → post to the              · post types · topics and tags
  channel → posts.json refreshed             + search and a feed — every
  → Pages republished automatically          post gets a "Table of Contents"
                                             button pinned in the channel header
```

A reader taps the "Table of Contents" button in the channel and sees a catalog of
all the posts. The owner does nothing else — everything runs on its own.

## What's inside

| Path | What it is | Who touches it |
|---|---|---|
| `docs/index.html` | "Table of Contents" mini-app v54-en (one page, zero dependencies) | nobody — works as is |
| `docs/config.js` | **Config**: channel name, link, license | the buyer (2 lines) |
| `docs/posts.json` | Contents data (filled in automatically) | nobody — automatic |
| `docs/topics.json` | **Contents topics** (large cards inside "By topics") | the buyer, optional |
| `.github/workflows/news.yml` | News pipeline: RSS → AI → channel → contents | nobody |
| `.github/workflows/import.yml` | Channel history import from a Telegram export | nobody |
| `.github/workflows/button.yml` | Publishing and pinning the "Table of Contents" button | nobody |
| `pipeline/news_pipeline.py` | News pipeline script (v2.3-en) | nobody |
| `pipeline/export_to_posts.py` | Export → contents converter | nobody |
| `pipeline/ai_tags.py` | Prepares posts for tag labeling by any AI | the buyer, optional |
| `pipeline/suggest_topics.py` | Topic draft from post tags | the buyer, optional |
| `pipeline/toc_button.py` | Table-of-contents button script | nobody |
| `pipeline/sources.json` | **RSS news feed list** (9 feeds, easy to change) | the buyer, optional |
| `pipeline/seen.json` | Pipeline memory: dedup + post texts for delete reconciliation | nobody — automatic |

## Mini-app: how the contents page is built (v54-en)

The root of the contents is **always four equal cards**, nothing ever disappears:

1. **📅 By month** — every month is a separate card; inside, posts grouped by day.
2. **🔤 By alphabet** — cards by the first letters of the headlines.
3. **🎭 By post type** — texts, photos, videos, audio, polls, files — types are
   detected automatically.
4. **🏷 By topics** — large topics from `topics.json` (when their tags match
   posts) and **all post tags with counters**. Tap a topic or a tag — get a
   filtered list.

Plus "🕘 Feed" (posts by time), search across titles and texts, the Telegram dark
theme, haptic feedback and transitions to posts via native Telegram means on every
platform — iPhone included.

## News pipeline v2.3-en (capabilities)

- **9 sources**: 6 Russian (RIA Novosti, TASS, Interfax, Kommersant, Lenta.ru,
  Vedomosti) + 3 world (BBC, Al Jazeera, The Guardian). The set is changed by
  editing `pipeline/sources.json` — the pipeline picks it up on its own.
- **Source rotation**: every 2-hour run starts polling from a new source —
  Russian and world feeds share the daily limit fairly.
- **AI card in English**: headline, lede, bullets, emoji and tags (1–3 from the
  12-topic dictionary). Russian-language feeds are translated into natural news
  English by the AI. Provider: OpenRouter with a chain of free models (if one is
  busy — the next one automatically).
- **Photos and videos**: photos from RSS or from the article page; short video
  clips (mp4 up to ~45 MB) are published as a video post with the same AI caption.
- **Dedup protection**: three levels — guid, source, normalized headline.
- **Daily limit**: no more than 10 posts per day (changeable via a flag).
- **Self-healing contents**: a post deleted in Telegram — the pipeline notices it
  on the next run (or manually via Actions → "sync only") and cleans it out of
  the contents. Dead links never happen.

## Two usage scenarios

1. **A content channel (posts already exist).** Export the channel via
   Telegram Desktop (`result.json`), put the file into the `import/` folder, run
   the "History import" action — the contents is filled with the whole archive.
   The news pipeline can stay disabled.
2. **A news channel (auto-publishing).** Fill in `sources.json`, add the AI key
   to the secrets — and the pipeline itself publishes the cards to the channel
   and refreshes the contents. This is how the live demo channel
   **Daily News Digest** works — the product's showcase (world and Russian news,
   delivered in English).

The two scenarios combine well: the archive from the export + fresh news from RSS.

## Quick start (for the impatient)

In short:

1. Upload all the kit files to your new GitHub repository.
2. BotFather → `/newbot` → the bot token.
3. Channel → add the bot as an admin (the "Post messages" permission).
4. Repository secrets: `BOT_TOKEN`, `CHANNEL_USERNAME`, and for the news
   pipeline — `GROQ_API_KEY` (paste your OpenRouter key here; the name is
   historical, nothing needs changing).
5. Settings → Pages → Branch `main`, folder `/docs`.
6. In `docs/config.js` enter the channel and the license key.
7. Actions → "TOC button" → Run → the post with the button is pinned in the channel.
8. Actions → "News pipeline" → Run workflow → `dry_run=true` — preview the future
   cards in the log without publishing. Then a normal run.

## Manual pipeline runs

| Action | How |
|---|---|
| Preview cards without publishing | Actions → "News pipeline" → `dry_run=true` |
| Reconcile deletions only (no news) | Actions → "News pipeline" → `sync_only=true` |
| More posts per single run | `max_posts` (default 1; the daily cap still holds) |

## Requirements

- A GitHub account (free) and a Telegram account.
- The channel must have a public @username (for post links).
- For the news pipeline — a free openrouter.ai key.
- That's it. No servers, databases or hosting bills needed.
