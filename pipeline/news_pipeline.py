#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram channel "Table of Contents" · news pipeline v2.3-en (AI cards)
=======================================================================
GitHub Actions runs this script every 2 hours:

    RSS sources (world wires + Russian news agencies) → photo from the article
    → AI summary (OpenRouter / Gemini / Groq)
    → a polished card in the Telegram channel (photo + caption, or text)
    → written into docs/posts.json → commit → Pages refreshes the mini-app.

Selection: sources are walked round-robin (the offset depends on the time of
    day), otherwise the round-the-clock Russian agencies (they publish several
    times more often than the world wires) would squeeze BBC/Al Jazeera/Guardian
    out of the feed. Feeds arrive in whatever language the agency publishes in
    (English or Russian) — the AI translates everything into English.

Card format (the AI always writes in English; the source language may be any):
    🌍 Headline
    Lede: 2–3 sentences of the gist.
    • fact 1
    • fact 2
    Source: BBC            ← a quiet link, no preview

Modes:
    --mode dry      show what WOULD be published (no sending, no writes)
    --mode publish  full cycle: publish to the channel + update posts.json

Secrets (GitHub → Settings → Secrets and variables → Actions):
    BOT_TOKEN        bot token from @BotFather
    CHANNEL_USERNAME channel username like @my_channel (the channel must be public!)
    GROQ_API_KEY     AI key (the name is historical): OpenRouter (openrouter.ai,
                     "sk-or-…", free models available) / Google Gemini
                     ("AIza…") / Groq ("gsk_…"). WITHOUT IT PUBLISHING STALLS:
                     the pipeline never posts raw announcements without an AI
                     summary. The provider is detected by the key prefix.

Limits: --max news per run, --daily-cap posts per day (counted over posts.json).
Photos: from the RSS (media:content/enclosure/thumbnail) or the article's
    og:image; no photo → post as text; Telegram rejects the photo → text as well.
Video: if the RSS entry has a video enclosure (media/enclosure) or the article
    page has og:video with a direct mp4 — publish a VIDEO post (kind=video):
    Telegram fetches the file itself by URL (≤20 MB), or we download and upload
    it as a file (≤45 MB); if that fails → photo, then text. Agencies serve
    short 1–2 min clips in modest resolution (~4–8 MB) — good enough for the channel.
Dedup: seen.json (hash) + links in posts.json + normalized titles.

TOC sync (publish runs): the last ~120 posts are probed in the channel with
    editMessageText/Caption carrying THE SAME text ("message is not modified"
    = alive, and nothing changes on screen; older posts without a stored
    text — via the t.me embed). A post deleted in Telegram is purged from
    posts.json — the TOC no longer points to "Post not found".
    Manual sync without publishing: Run workflow → sync_only=true.
"""
import argparse
import hashlib
import html as html_mod
import json
import os
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL_DEFAULT = "openai/gpt-oss-120b"

# Groq blocks datacenter IPs, so the primary providers are
# OpenRouter (an aggregator; does not block cloud IPs) and Google Gemini.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_MODEL_DEFAULT = "gemini-2.5-flash"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Free models (:free suffix), tried one by one until one of them answers:
# 1) GLM — the strongest of the free tier + structured_outputs (reliable JSON);
# 2) MiniMax M3 — 1M context, response_format;
# 3) Nemotron Super — compact, structured_outputs;
# 4) Nemotron Ultra — the largest reserve.
# Override via the AI_MODEL secret/variable (a comma-separated list is allowed).
OPENROUTER_MODELS = [
    "z-ai/glm-5.2:free",
    "minimax/minimax-m3:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
]

# Emojis the AI may put on a card (anything outside the list → the source's emoji)
EMOJI_WHITELIST = [
    "🌍", "🔥", "⚡", "💰", "🏛️", "⚖️", "🚀", "🔬", "💊", "🎓", "⚠️", "🌐",
    "📱", "🛰️", "🏭", "🎭", "🏆", "🌊", "✈️", "🚗", "📊", "🤝", "🗳️", "🕊️",
    "⛽", "📈", "📉", "🧑‍⚖️", "🏗️", "🛡️",
]

# Words that "contain" the keyword roots below — they are cut out before
# matching: газета (newspaper) must not match газ (gas), невролог (neurologist)
# — евро (euro), Европа (Europe) — the euro currency, чипсы (chips) — a chip,
# судьба (fate) — a court, рейсинг (racing) — рейс (a flight). Latin look-alikes
# are cut as well: "airlin" guards the "ai" key, "newspap" guards the gas-like keys.
FALSE_STEMS = ["европ", "газет", "неврол", "чипс", "судьб", "рейсинг", "airlin", "newspap"]

# Topic by keywords (for the second label in the TOC). Bilingual on purpose:
# the RSS input is often Russian, so the Russian roots are kept alongside the
# English keys of the same topics. Latin keys match WHOLE words at word
# boundaries (otherwise "ai" would catch "airline"); Cyrillic ones match
# by root («нефт» catches «нефть/нефти»).
KEYWORD_TAGS = [
    ("conflict",    ["войн", "удар", "обстрел", "наступлен", "боев", "перемир", "ракет", "дрон", "атак",
                     "conflict", "attack", "strike", "war", "missile", "drone"]),
    ("politics",    ["выбор", "президен", "парламент", "министр", "выборы", "саммит", "переговор", "выставил", "депутат",
                     "politics", "president", "parliament", "government", "election"]),
    ("economy",     ["инфляц", "ставк", "банк", "рынк", "доллар", "евро", "нефт", "газ", "санкц", "бюджет", "тариф", "экспорт", "импорт", "дивиденд",
                     "economy", "inflation", "oil", "market", "bank", "dividends"]),
    ("science",     ["учен", "наук", "исследован", "открыт", "космос", "nasa", "ракет-носител", "климат",
                     "science"]),
    ("technology",  ["ai", "искусственн", "технолог", "приложен", "чек", "cyber", "хакер", "чип", "apple", "google", "tesla",
                     "technology", "chip"]),
    ("health",      ["медиц", "врач", "болезн", "вирус", "вакцин", "пациент", "здоровь", "эпидеми",
                     "health", "vaccine", "virus", "hospital"]),
    ("incidents",   ["землетрясен", "наводнен", "пожар", "авиакатастроф", "крушен", "вспышк", "авар",
                     "взрыв", "взорвал", "жертв", "обрушен", "затоплен",
                     "incident", "crash", "fire", "flood", "earthquake", "explosion", "killed", "casualt"]),
    ("culture",     ["фильм", "преми", "фестивал", "альбом", "сериал", "книг", "выставк", "оскар",
                     "culture", "film", "festival"]),
    ("sport",       ["чемпион", "матч", "кубок", "олимпиад", "турнир", "футбол", "хоккей",
                     "sport", "cup", "match", "olympic"]),
    ("society",     ["забастовк", "протест", "мигрант", "суд", "приговор", "закон", "школ", "больниц"]),
    ("energy",      ["энергет", "электроэнерг", "аэс", "атэс", "гэс", "тэс", "нефтепровод", "газопровод", "энергоблок",
                     "energy", "gas", "power grid"]),
    ("transport",   ["аэропорт", "метро", "железнодорож", "ж/д", "рейс", "паром", "трамвай", "трасс", "пробк", "перелет", "перелёт",
                     "transport", "flight", "airport", "railway"]),
]

# Managed topic vocabulary: the AI picks its tags from this list, and the
# keyword fallback draws from the same list — the labels in the TOC never diverge.
TAG_WHITELIST = [t for t, _ in KEYWORD_TAGS]

AI_SYSTEM = (
    "You are the news editor of an English-language Telegram news channel "
    "(world and Russian events).\n"
    "The input is a news headline and description from an RSS feed: it MAY BE "
    "IN RUSSIAN — Russian agencies publish in Russian, the world wires in English.\n"
    "Return STRICTLY one JSON object with no markdown wrappers:\n"
    '{"emoji": "…", "headline": "…", "lede": "…", "bullets": ["…", "…"], "tags": ["…", "…"]}\n'
    "Rules (strict):\n"
    "— Write everything in ENGLISH. If the input is in Russian, translate it: "
    "render the card in natural, idiomatic news English (not word-for-word), "
    "preserving every number, name and fact.\n"
    "— tags: 1–3 labels STRICTLY from the list, the first one being the MAIN topic. Topic rubric:\n"
    "  " + ", ".join(TAG_WHITELIST) + "\n"
    "  Topic boundaries: conflict — hostilities, strikes, shelling, drones; "
    "incidents — explosions, fires, floods, crashes, accidents, attacks with damage or casualties "
    "(including at hydro plants, factories, pipelines and power lines); "
    "energy — the industry as business and projects (oil & gas pipelines, power-plant construction, electricity markets); "
    "technology — ONLY gadgets, internet, AI, cybersecurity; "
    "economy — money, markets, oil/gas as commodities, companies, budgets; "
    "politics — elections, summits, laws, appointments; "
    "science — research, space, discoveries; health — diseases and medicine; "
    "sport — competitions; culture — films, music, books; society — courts, everyday life, education; "
    "transport — airports, metro, railways as infrastructure.\n"
    "  Hard rule: an EMERGENCY (explosion, fire, flood, crash, collapse, attack with damage) is "
    "ALWAYS the first label «incidents», even if the site is a hydro plant, factory or power grid. "
    "«technology» for accidents and emergencies is FORBIDDEN.\n"
    "  Examples: \"rescuers blast a slope at a hydro plant after floods, casualties reported\" → incidents, worldnews; "
    "\"gas pipeline construction enters the final stage\" → energy, economy; "
    "\"skiers cleared for international starts\" → sport; "
    "\"court protected a pensioner's only home\" → society.\n"
    "  No confident topic — []. "
    "Any other labels (including source labels like \"russia\" or \"worldnews\") are "
    "forbidden — the country/region is already visible from the source.\n"
    "— headline: up to 100 characters, a concise headline carrying the point, "
    "no trailing period, no surrounding quotes.\n"
    "— lede: 2–3 sentences (up to 340 characters) — the essence of the event: "
    "who, what, where, when, numbers.\n"
    "— bullets: 0–3 short facts (each up to 100 characters) ONLY if they are really present in the input.\n"
    "  No specifics — an empty list []. Do not invent anything and do not add outside context.\n"
    "— emoji: ONE from the list that best matches the topic:\n"
    "  " + " ".join(EMOJI_WHITELIST) + "\n"
    "— Carry numbers, dates, amounts and names exactly as they are in the input. Do not invent causes or consequences.\n"
    "— If the input is an opinion, an announcement or too small for a news story, still make the card strictly from the input.\n"
)


# ───────────────────────────── basic utilities ─────────────────────────────

def log(msg):
    print(msg, flush=True)

def http_json(url, payload=None, headers=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    base = {"Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; Soderzhanie/2.0)"}
    if headers:
        base.update(headers)
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers=base)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def http_get_bytes(url, timeout=12, max_len=400_000):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; Soderzhanie/2.0)",
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_len)

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def clean_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html_mod.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

def trim(s, limit, dots="…"):
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    cut = s[:limit - 1]
    cut = cut[:cut.rfind(" ")] if " " in cut[-15:] else cut
    return cut.rstrip(",;:- ") + dots

def plain_len(html_text):
    """Length of the post text WITHOUT html tags (that is exactly what Telegram counts)."""
    return len(re.sub(r"<[^>]+>", "", html_text))

def esc(s):
    return html_mod.escape(s or "", quote=True)

def keyword_tags(text, default="#worldnews"):
    low = " " + (text or "").lower() + " "
    for stem in FALSE_STEMS:
        low = low.replace(stem, "§")
    for tag, keys in KEYWORD_TAGS:
        for k in keys:
            pat = (r"\b" + re.escape(k) + r"\b") if k.isascii() else (r"\b" + re.escape(k))
            if re.search(pat, low):
                return "#" + tag
    return default

def norm_title(t):
    """Title normalization for cross-source dedup."""
    t = (t or "").lower()
    t = re.sub(r"[^\wа-яё ]+", " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


# ───────────────────────────── RSS: reading and media ─────────────────────────────

def fetch_feed(source):
    """Fetches and parses a single RSS/Atom feed with Python's standard library
    only (no external dependencies — no pip install needed in Actions).
    A failure of one source does not bring the run down."""
    import xml.etree.ElementTree as ET
    try:
        req = urllib.request.Request(source["url"], headers={
            "User-Agent": "Mozilla/5.0 (compatible; Soderzhanie/2.0)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        })
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        # RSS 2.0: <rss><channel><item>… | Atom: <feed><entry>…
        items = root.findall(".//item") or root.findall(".//{*}entry")
        entries = [normalize_entry(it) for it in items]
        entries = [e for e in entries if e]
        log(f"  · {source.get('name','?')}: {len(entries)} entries")
        return entries
    except Exception as e:
        log(f"  · {source.get('name','?')}: ERROR ({e}) — skipping")
        return []


def _txt(el):
    if el is None:
        return ""
    return clean_html("".join(el.itertext()))


def _when(el):
    """pubDate/updated → struct_time; supports RSS (RFC822) and Atom (ISO)."""
    from email.utils import parsedate_to_datetime
    if el is None or not (el.text or "").strip():
        return None
    s = el.text.strip()
    try:
        return parsedate_to_datetime(s).utctimetuple()          # RFC822 (RSS)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).utctimetuple()  # Atom/ISO
    except Exception:
        return None


def _image_url(el):
    """Image URL from a media:*/enclosure element + a width hint (to pick the best)."""
    if el is None:
        return "", 0
    url = (el.get("url") or el.get("href") or "").strip()
    if not url:
        return "", 0
    mime = (el.get("type") or el.get("medium") or "").lower()
    if mime and not (mime.startswith("image") or mime == "photo"):
        return "", 0
    if not re.match(r"^https?://", url):
        return "", 0
    try:
        w = int(el.get("width") or 0)
    except ValueError:
        w = 0
    return url, w


def _pick_biggest(cands):
    """From one entry's media:content variants pick the biggest one:
    the width attribute first; when it is zero — a heuristic on the digits in the URL."""
    best, best_w = "", -1
    for u, w in cands:
        if w == 0:
            m = re.search(r"[/_.-](\d{2,4})[x×][/.-]", u)  # 640x480 in the URL path
            if m:
                w = int(m.group(1))
        if w > best_w:
            best, best_w = u, w
    return best


def _video_url(el):
    """Video URL from a media:*/enclosure element (a video/* type or .mp4/.m4v/.mov
    in the address), or ''. Redirects (e.g. RIA's file.aspx) are resolved later,
    right before sending (resolve_video)."""
    if el is None:
        return ""
    url = (el.get("url") or el.get("href") or "").strip()
    if not re.match(r"^https?://", url):
        return ""
    mime = (el.get("type") or el.get("medium") or "").lower()
    if not (mime.startswith("video") or mime == "movie"
            or re.search(r"\.(mp4|m4v|mov)([?#]|$)", url, re.I)):
        return ""
    return url


def normalize_entry(item):
    """RSS <item> or Atom <entry> → a single entry dict (+ a photo, if any)."""
    def _local(p):
        return p.split("}")[-1].split(":")[-1]   # "media:content" → "content"

    def first(*paths):
        for p in paths:
            found = item.find(p)
            if found is None:
                found = item.find("{*}" + _local(p))   # namespace-agnostic
            if found is not None:
                return found
        return None

    def all_els(*paths):
        out = []
        for p in paths:
            out.extend(item.findall(p))
            out.extend(item.findall("{*}" + _local(p)))
        return out

    title_el = first("title")
    if title_el is None or _txt(title_el) == "":
        return None

    # link: RSS <link>text</link> | Atom <link href="…" />
    link = ""
    link_el = first("link")
    if link_el is not None:
        link = (link_el.text or "").strip() or (link_el.get("href") or "").strip()
    if not link:
        return None

    # summary: the first NON-EMPTY of the candidate tags (many feeds keep an empty
    # description, while the real text lives in content:encoded / summary)
    summary = ""
    for tag_path in ("description", "summary", "content", "encoded"):
        el = first(tag_path)
        if el is not None:
            cand = _txt(el)
            if cand:
                summary = cand
                break

    # photo: media:content (there may be several sizes) → enclosure → media:thumbnail
    img = ""
    cands = [_image_url(e) for e in all_els("media:content")]
    cands = [c for c in cands if c[0]]
    if cands:
        img = _pick_biggest(cands)
    if not img:
        for e in all_els("enclosure"):
            u, _w = _image_url(e)
            if u:
                img = u
                break
    if not img:
        for e in all_els("media:thumbnail"):
            u, _w = _image_url(e)
            if u:
                img = u
                break

    # video: the first video enclosure found (enclosure/media:content)
    video = ""
    for e in all_els("enclosure") + all_els("media:content"):
        video = _video_url(e)
        if video:
            break

    when = _when(first("pubDate", "published", "updated", "date"))
    guid_el = first("guid", "id")
    return {
        "title": _txt(title_el),
        "link": link,
        "summary": summary,
        "image": img,
        "video": video,
        "id": ((guid_el.text if guid_el is not None else "") or link),
        "published_parsed": when,
    }


OG_RE = re.compile(
    r'<meta[^>]+(?:property=["\']og:image(?::secure_url)?["\']'
    r'|name=["\']twitter:image(?::src)?["\'])[^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE)
OG_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property=["\']og:image(?::secure_url)?["\']'
    r'|name=["\']twitter:image(?::src)?["\'])',
    re.IGNORECASE)


OG_VID_RE = re.compile(
    r'<meta[^>]+(?:property=["\']og:video(?::secure_url|:url)?["\]'
    r'|name=["\']twitter:player:stream["\'])[^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE)
OG_VID_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property=["\']og:video(?::secure_url|:url)?["\]'
    r'|name=["\']twitter:player:stream["\'])',
    re.IGNORECASE)


def og_media(article_url):
    """Fallback media from the article page: (og:image, og:video). Best-effort.
    Only direct video files (.mp4/.m4v/.mov) are taken for the video — og:video
    often points to an iframe player that Telegram cannot ingest."""
    if not article_url:
        return "", ""
    try:
        raw = http_get_bytes(article_url, timeout=10, max_len=300_000).decode("utf-8", "ignore")
    except Exception:
        return "", ""
    m = OG_RE.search(raw) or OG_RE2.search(raw)
    img = html_mod.unescape(m.group(1)).strip() if m else ""
    if img and not re.match(r"^https?://", img):
        img = ""
    vid = ""
    mv = OG_VID_RE.search(raw) or OG_VID_RE2.search(raw)
    if mv:
        vid = html_mod.unescape(mv.group(1)).strip()
        if not re.search(r"\.(mp4|m4v|mov)([?#]|$)", vid, re.I):
            vid = ""
    return img, vid


# ───────────────────────── AI summary (OpenRouter/Gemini/Groq) ─────────────────────────

def _parse_ai_json(raw):
    """A tolerant parser of the model's reply: strips ``` fences and junk around the JSON."""
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b <= a:
        return None
    try:
        d = json.loads(s[a:b + 1])
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    headline = clean_html(str(d.get("headline") or ""))
    lede = clean_html(str(d.get("lede") or ""))
    if not headline:
        return None
    bullets_raw = d.get("bullets")
    bullets = []
    if isinstance(bullets_raw, list):
        for b_ in bullets_raw:
            if isinstance(b_, str) and clean_html(b_):
                bullets.append(trim(clean_html(b_), 100))
            if len(bullets) >= 3:
                break
    emoji = str(d.get("emoji") or "").strip()
    tags_raw = d.get("tags")
    ai_tags = []
    if isinstance(tags_raw, list):
        for t_ in tags_raw:
            if isinstance(t_, str):
                t_ = clean_html(t_).strip().lower().lstrip("#")
                if t_ in TAG_WHITELIST and ("#" + t_) not in ai_tags:
                    ai_tags.append("#" + t_)
            if len(ai_tags) >= 3:
                break
    return {
        "headline": trim(headline, 110),
        "lede": trim(lede, 340),
        "bullets": bullets,
        "emoji": emoji if emoji in EMOJI_WHITELIST else "",
        "tags": ai_tags,
    }


def ai_card(title, summary, source_name):
    """AI summary of one story. None — the AI is unavailable / the reply is not a valid card.
    The provider is detected by the key prefix: "sk-or-…" → OpenRouter, "AIza…" → Gemini,
    "gsk_…" → Groq. The AI_URL / AI_MODEL variables override it manually
    (AI_MODEL may list several models comma-separated — they will be tried one by
    one). OpenRouter's free models have rate limits, so the model list is a
    fallback chain: 429/failure → next model."""
    key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY") or "").strip()
    if not key:
        return None
    url = (os.environ.get("AI_URL") or "").strip()
    model_env = (os.environ.get("AI_MODEL") or os.environ.get("GROQ_MODEL") or "").strip().strip("\"'")
    if not url:
        if key.startswith("AIza"):
            url = GEMINI_URL
        elif key.startswith("sk-or-"):
            url = OPENROUTER_URL
        else:
            url = GROQ_URL
    if model_env:
        models = [m.strip() for m in model_env.split(",") if m.strip()]
    elif "openrouter" in url:
        models = OPENROUTER_MODELS
    elif "generativelanguage" in url:
        models = [GEMINI_MODEL_DEFAULT]
    else:
        models = [GROQ_MODEL_DEFAULT]
    headers = {"Authorization": f"Bearer {key}"}
    if "openrouter" in url:
        headers["HTTP-Referer"] = "https://eremeevmaks41-sys.github.io/soderzhanie-demo/"
        headers["X-Title"] = "soderzhanie-demo"
    user_msg = (f"Source: {source_name}\n"
                f"Headline: {title}\n"
                f"Description: {summary or '(empty)'}")
    for i, model in enumerate(models):
        payload = {
            "model": model,
            "temperature": 0.2,
            "max_tokens": 2500,
            "messages": [
                {"role": "system", "content": AI_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
        }
        if "openrouter" in url:
            # "thinking" models must not spend their token budget on reasoning
            payload["reasoning"] = {"enabled": False}
        if i:
            log(f"    · trying model {model}…")
        try:
            resp = http_json(url, payload, headers=headers, timeout=60)
            card = _parse_ai_json((resp["choices"][0]["message"].get("content") or ""))
            if card:
                return card
            log(f"    · AI {model}: no valid JSON in the response")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(300).decode("utf-8", "ignore")
            except Exception:
                pass
            log(f"    · AI {model}: HTTP {e.code} {e.reason} :: {detail or '(empty response body)'}")
            if e.code in (401, 402, 403):
                return None          # key/access problem — switching models will not help
        except Exception as e:
            log(f"    · AI {model}: {e}")
    return None


def fallback_card(item):
    """A card without AI (for dry mode without a key): the raw RSS text."""
    return {
        "headline": trim(item["title"], 110),
        "lede": trim(item["summary"], 340),
        "bullets": [],
        "emoji": "",
    }


# ───────────────────────────── post card ─────────────────────────────

def compose(item, card):
    """HTML card of a post. Returns (html, kind). Tags go only into posts.json —
    the post text itself carries none, so the feed looks clean.

    With a photo the caption must fit into 1024 characters BY TEXT (tags excluded):
    first we sacrifice the bullets, then shorten the lede.
    The source link is a single quiet line at the bottom; the preview is disabled.
    """
    s = item["source"]
    emoji = card.get("emoji") or s.get("emoji", "🌍")
    src_name = s.get("name", "Source")
    source_line = f"Source: <a href=\"{esc(item['src'])}\">{esc(src_name)}</a>"

    headline = esc(card["headline"])
    lede = esc(card.get("lede") or "")
    bullets = [esc(b) for b in card.get("bullets") or []]

    def build(with_bullets=True):
        lines = [f"{emoji} <b>{headline}</b>"]
        if lede:
            lines += ["", lede]
        if with_bullets and bullets:
            lines += ["", "• " + "\n• ".join(bullets)]
        lines += ["", source_line]
        return "\n".join(lines)

    text = build(True)
    if item.get("image") or item.get("video"):
        kind_media = "video" if item.get("video") else "photo"
        # compress down to the caption limit (1024 by text, 60 headroom for Telegram's quirks)
        while plain_len(text) > 960:
            if bullets:
                bullets = bullets[:-1]
            elif len(lede) > 120:
                lede = esc(trim(html_mod.unescape(lede), int(len(lede) * 0.75)))
            else:
                break
            text = build(True)
        if plain_len(text) <= 960:
            return text, kind_media
    # text post: the 4096 limit — almost never needs any trimming here
    text = build(True)
    if plain_len(text) > 4090:
        text = build(False)
    return text, "text"


# ───────────────────────────── Telegram ─────────────────────────────

def tg_send(token, chat, text):
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    return http_json(api, {
        "chat_id": chat, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def tg_send_photo(token, chat, photo_url, caption):
    api = f"https://api.telegram.org/bot{token}/sendPhoto"
    return http_json(api, {
        "chat_id": chat, "photo": photo_url, "caption": caption,
        "parse_mode": "HTML",
    })


def tg_send_photo_upload(token, chat, photo_url, caption, timeout=60):
    """Fallback: Telegram could not fetch the photo by URL (block, format,
    size) — we download it ourselves and upload it as a file (multipart;
    the photo limit is 10 MB, we fetch at most 9 MB)."""
    import uuid
    raw = http_get_bytes(photo_url, timeout=30, max_len=9_000_000)
    if len(raw) < 1024:
        raise ValueError("image too small — not a picture")
    bnd = "----Soderzhanie" + uuid.uuid4().hex
    parts = []
    for name, val in (("chat_id", chat), ("caption", caption), ("parse_mode", "HTML")):
        parts.append((f"--{bnd}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n").encode("utf-8"))
    parts.append((f"--{bnd}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"news.jpg\"\r\n"
                  f"Content-Type: image/jpeg\r\n\r\n").encode("utf-8"))
    parts.append(raw)
    parts.append(f"\r\n--{bnd}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(parts),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def tg_send_video(token, chat, video_url, caption):
    api = f"https://api.telegram.org/bot{token}/sendVideo"
    return http_json(api, {
        "chat_id": chat, "video": video_url, "caption": caption,
        "parse_mode": "HTML", "supports_streaming": True,
    })


def tg_send_video_upload(token, chat, video_url, caption, timeout=240):
    """Fallback: Telegram could not fetch the video by URL — we download it
    ourselves and upload it as a file (multipart; the bot limit is 50 MB,
    we fetch at most 45 MB)."""
    import uuid
    raw = http_get_bytes(video_url, timeout=180, max_len=48_000_000)
    bnd = "----Soderzhanie" + uuid.uuid4().hex
    parts = []
    for name, val in (("chat_id", chat), ("caption", caption),
                      ("parse_mode", "HTML"), ("supports_streaming", "true")):
        parts.append((f"--{bnd}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n").encode("utf-8"))
    parts.append((f"--{bnd}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"news.mp4\"\r\n"
                  f"Content-Type: video/mp4\r\n\r\n").encode("utf-8"))
    parts.append(raw)
    parts.append(f"\r\n--{bnd}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendVideo", data=b"".join(parts),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_video(url):
    """HEAD-probe of the video: (final URL, content-type, content-length).
    Many feeds serve redirects (RIA: file.aspx → *.mp4) — we follow them."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (compatible; Soderzhanie/2.0)"})
        with urllib.request.urlopen(req, timeout=15) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            try:
                clen = int(r.headers.get("Content-Length") or 0)
            except ValueError:
                clen = 0
            return r.geturl(), ctype, clen
    except Exception:
        return url, "", 0


def publish_item(token, chat, item, text, kind):
    """Video → sendVideo (by URL or as a file); photo → sendPhoto;
    if that fails — the fallbacks below (video → photo → text).
    Returns (ok, actual_kind, message_id)."""
    if kind == "video" and item.get("video"):
        url, ctype, clen = resolve_video(item["video"])
        ok_video = (ctype.startswith("video") or not ctype) \
            and "flv" not in (ctype + " " + url).lower()   # the HEAD probe may have failed — try as is; FLV is unplayable in Telegram
        if ok_video and clen > 45_000_000:
            log("    · the video is over 45 MB — sending as photo/text")
            ok_video = False
        if ok_video:
            # by URL Telegram fetches files ≤20 MB itself; larger — only as a file upload
            if clen == 0 or clen <= 20_000_000:
                try:
                    resp = tg_send_video(token, chat, url, text)
                    if resp.get("ok"):
                        return True, "video", resp["result"]["message_id"]
                    log(f"    · video by URL rejected ({resp.get('description')}) — trying a file upload")
                except Exception as e:
                    log(f"    · video by URL failed to send ({e}) — trying a file upload")
            else:
                log(f"    · the video is ~{clen // 1_000_000} MB — uploading as a file")
            try:
                resp = tg_send_video_upload(token, chat, url, text)
                if resp.get("ok"):
                    return True, "video", resp["result"]["message_id"]
                log(f"    · video file upload rejected ({resp.get('description')}) — sending as photo/text")
            except Exception as e:
                log(f"    · video file upload failed ({e}) — sending as photo/text")
    if kind in ("video", "photo") and item.get("image"):
        try:
            resp = tg_send_photo(token, chat, item["image"], text)
            if resp.get("ok"):
                return True, "photo", resp["result"]["message_id"]
            log(f"    · photo rejected ({resp.get('description')}) — trying a file upload")
        except Exception as e:
            log(f"    · photo failed to send ({e}) — trying a file upload")
        try:
            resp = tg_send_photo_upload(token, chat, item["image"], text)
            if resp.get("ok"):
                return True, "photo", resp["result"]["message_id"]
            log(f"    · photo file upload rejected ({resp.get('description')}) — sending as text")
        except Exception as e:
            log(f"    · photo file upload failed ({e}) — sending as text")
    try:
        resp = tg_send(token, chat, text)
    except Exception as e:
        log(f"    × Telegram rejected (\"{e}\")")
        return False, "text", None
    if not resp.get("ok"):
        log(f"    × Telegram returned an error: {resp.get('description')}")
        return False, "text", None
    return True, "text", resp["result"]["message_id"]


# ───────────────────── TOC ↔ channel sync ─────────────────────

SYNC_PROBE_LIMIT = 120          # how many recent posts we probe per run


def classify_probe_error(desc):
    """Telegram's reply to an edit-probe with the same text → the post's state:
    alive (the post is alive, nothing changed on screen), dead (deleted),
    chat (the channel is not accessible — the sync must stop), unknown (unclear)."""
    low = (desc or "").lower()
    if "chat not found" in low or "chat_id is invalid" in low:
        return "chat"
    if "message is not modified" in low:
        return "alive"
    if "message to edit not found" in low or ("not found" in low and "message" in low):
        return "dead"
    if "no text in the message" in low or "no caption" in low:
        return "alive"            # it is there, but of another kind — we will re-probe with the embed
    return "unknown"


def probe_alive_api(token, chat, msg_id, meta):
    """editMessageText/editMessageCaption with THE SAME text: the post is alive →
    Telegram answers "message is not modified" (the content does not change);
    deleted → "message to edit not found"."""
    kind = meta.get("kind") or "text"
    if kind == "text":
        api, field = "editMessageText", "text"
    else:
        api, field = "editMessageCaption", "caption"
    payload = {"chat_id": "@" + chat, "message_id": msg_id, field: meta["text"],
               "parse_mode": "HTML"}
    if kind == "text":
        payload["disable_web_page_preview"] = True
    try:
        resp = http_json(f"https://api.telegram.org/bot{token}/{api}", payload, timeout=20)
        return "alive" if resp.get("ok") else "unknown"
    except urllib.error.HTTPError as e:
        desc = ""
        try:
            desc = e.read(300).decode("utf-8", "ignore")
        except Exception:
            pass
        return classify_probe_error(desc)
    except Exception:
        return "unknown"


def probe_alive_embed(chat, msg_id):
    """Fallback for posts with no stored text: the t.me embed of a deleted post
    contains tgme_widget_message_error ("Post not found"), a live one does not."""
    try:
        raw = http_get_bytes(f"https://t.me/{chat}/{msg_id}?embed=1&mode=tme",
                             timeout=10, max_len=150_000).decode("utf-8", "ignore")
    except Exception:
        return "unknown"
    low = raw.lower()
    if "tgme_widget_message_error" in low:
        return "dead"
    if "tgme_widget_message_date" in low or "tgme_widget_message_text" in low:
        return "alive"
    return "unknown"


def sync_deleted(token, chat, posts, texts):
    """Syncs the TOC with the channel: the last SYNC_PROBE_LIMIT posts are probed
    with the edit method (when a stored text exists) or via the t.me embed.
    Posts deleted in Telegram are purged from posts.json (and from texts).
    Returns the number of purged posts (0 — none, -1 — the channel is not accessible)."""
    with_id = [p for p in posts if p.get("id")]
    dead = []
    for p in with_id[-SYNC_PROBE_LIMIT:]:
        meta = texts.get(str(p["id"]))
        if meta and meta.get("text"):
            state = probe_alive_api(token, chat, p["id"], meta)
        else:
            state = probe_alive_embed(chat, p["id"])
        if state == "chat":
            log("!! the channel is not accessible to the bot — sync stopped, nothing removed")
            return -1
        if state == "dead":
            dead.append(p["id"])
            log(f"  × post deleted in the channel → purging from the TOC: id={p['id']} \"{trim(p.get('title',''), 50)}\"")
        elif state == "unknown":
            log(f"  · sync: the state of id={p['id']} is unclear — leaving it alone")
    if dead:
        dead_set = set(dead)
        posts[:] = [p for p in posts if p.get("id") not in dead_set]
        for mid in dead:
            texts.pop(str(mid), None)
    return len(dead)


# ───────────────────────────── candidate selection ─────────────────────────────

def pick(entries, source, seen, existing_srcs, existing_titles, max_items):
    """Picks the freshest not-yet-published entries of one source.
    Dedup: GUID hash, the link in the TOC, the normalized title."""
    out = []
    local_titles = set()
    for e in entries:
        link = e.get("link") or ""
        guid = e.get("id") or link
        if not link:
            continue
        h = hashlib.sha1(guid.encode("utf-8")).hexdigest()
        if h in seen or link in existing_srcs:
            continue
        title = clean_html(e.get("title") or "")
        summary = clean_html(e.get("summary", ""))
        if not title:
            continue
        nt = norm_title(title)
        if len(nt) < 15:
            continue                                  # service/empty titles
        if nt in existing_titles or nt in local_titles:
            continue                                  # the same story carried by another source
        local_titles.add(nt)
        when = e.get("published_parsed") or e.get("updated_parsed")
        dt = (datetime(*when[:6], tzinfo=timezone.utc).astimezone(MSK)
              if when else datetime.now(MSK))
        out.append({"src": link, "hash": h, "title": title, "summary": summary,
                    "image": e.get("image", ""), "dt": dt, "source": source})
        if len(out) >= max_items:
            break
    return out


def merge_tags(base, card, text):
    """The post's final labels: the source label (first) + AI topics (from
    TAG_WHITELIST), at most 3 in total. No AI topics → the keyword fallback;
    the fallback does not duplicate the source label."""
    base = list(base or ["#worldnews"])
    picked = [t for t in (card.get("tags") or []) if t not in base]
    # rubric guardrail: the input is an emergency (explosion, flood, crash…)
    # but the AI forgot «incidents» — put the label first, deterministically.
    low = (text or "").lower()
    if "incidents" not in [str(t).lstrip("#").lower() for t in picked] \
            and any(k in low for k in KEYWORD_TAGS["incidents"]):
        picked = ["#incidents"] + picked
    tags = (base + picked)[:3]
    if len(tags) == len(base):
        extra = keyword_tags(text, default=base[0])
        if extra not in tags:
            tags.append(extra)
    return tags


def interleave_by_source(candidates, offset=0):
    """Fair source rotation: within a source — by freshness, across sources —
    round-robin with the offset shift (the shift changes from run to run,
    since it is derived from the hour of day). Without the rotation the source
    with the busiest stream would take the whole daily cap."""
    groups = {}
    for c in candidates:
        groups.setdefault(c["source"].get("name", "?"), []).append(c)
    for g in groups.values():
        g.sort(key=lambda x: x["dt"], reverse=True)
    names = sorted(groups)
    if not names:
        return []
    offset %= len(names)
    names = names[offset:] + names[:offset]
    out = []
    for idx in range(max(len(g) for g in groups.values())):
        for n in names:
            g = groups[n]
            if idx < len(g):
                out.append(g[idx])
    return out


def today_count(posts):
    """How many posts were published today (MSK) — for the daily cap."""
    today = datetime.now(MSK).strftime("%Y-%m-%d")
    return sum(1 for p in posts if p.get("date") == today)


def save_state(posts_doc, seen_doc, posts, chat, args):
    """A single write of the run's results: the TOC + seen.json
    (it also stores the post texts for the future edit-probes of the sync)."""
    posts.sort(key=lambda p: (p.get("date", ""), p.get("time", "")), reverse=True)
    posts_doc["posts"] = posts[:1500]
    posts_doc["updated_at"] = datetime.now(MSK).isoformat(timespec="seconds")
    if isinstance(posts_doc.get("channel"), dict) and chat:
        posts_doc["channel"]["url"] = f"https://t.me/{chat}"
    save_json(args.posts, posts_doc)
    seen_doc["seen"] = dict(sorted((seen_doc.get("seen") or {}).items(),
                                   key=lambda kv: kv[1], reverse=True)[:5000])
    texts = seen_doc.get("texts") or {}
    if len(texts) > 400:
        seen_doc["texts"] = dict(list(texts.items())[-300:])
    save_json(args.seen, seen_doc)


# ───────────────────────────── main ─────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dry", "publish"], default="dry")
    ap.add_argument("--max", type=int, default=1, help="max news items per run (across ALL sources)")
    ap.add_argument("--max-per-source", type=int, default=1)
    ap.add_argument("--daily-cap", type=int, default=10, help="max posts per day (MSK)")
    ap.add_argument("--no-og-image", action="store_true",
                    help="do not fetch og:image when the RSS gave no photo")
    ap.add_argument("--sources", default="pipeline/sources.json")
    ap.add_argument("--posts", default="docs/posts.json")
    ap.add_argument("--seen", default="pipeline/seen.json")
    args = ap.parse_args()

    cfg = load_json(args.sources, {"sources": []})
    sources = cfg.get("sources") or []
    if not sources:
        log("!! sources.json lists no sources — nothing to do")
        return 1

    posts_doc = load_json(args.posts, {"version": 1, "updated_at": "", "channel": {}, "posts": []})
    posts = posts_doc.get("posts") or []
    existing_srcs = {p.get("src") for p in posts if p.get("src")}
    existing_titles = {norm_title(p.get("title") or "") for p in posts[-300:]}

    seen_doc = load_json(args.seen, {"seen": {}})
    seen = seen_doc.get("seen") or {}
    texts = seen_doc.setdefault("texts", {})   # stored post texts for the edit-probe of the sync

    token = os.environ.get("BOT_TOKEN", "")
    chat = (os.environ.get("CHANNEL_USERNAME", "") or "").strip()
    if args.mode == "publish" and (not token or not chat):
        log("!! publish requires BOT_TOKEN and CHANNEL_USERNAME (GitHub Secrets)")
        return 1
    chat = chat.lstrip("@").replace("https://t.me/", "")

    # TOC ↔ channel sync: posts deleted in Telegram are purged from posts.json
    # (also works with --max 0 — "sync only").
    removed = 0
    if args.mode == "publish":
        removed = max(0, sync_deleted(token, chat, posts, texts))

    published_today = today_count(posts)
    if published_today >= args.daily_cap:
        log(f"Daily cap reached ({published_today}/{args.daily_cap}) — "
            "not publishing new posts"
            + (f"; deleted posts purged: {removed}" if removed else ""))

    log(f"Sources: {len(sources)}; the TOC holds {len(posts)} posts; "
        f"today {published_today}/{args.daily_cap} already published")
    candidates = []
    for src in sources:
        candidates += pick(fetch_feed(src), src, seen, existing_srcs,
                           existing_titles, args.max_per_source)
    # Source rotation: the shift is the number of the 2-hour slot of the day, so
    # every run starts the queue with a different source (see interleave_by_source).
    rotation = (datetime.now(MSK).hour // 2) % max(1, len(sources))
    candidates = interleave_by_source(candidates, rotation)
    log(f"Source rotation: the queue starts at #{rotation + 1} of {len(sources)}")
    room = args.daily_cap - published_today
    candidates = candidates[: max(0, min(args.max, room))]
    log(f"Selected for publishing: {len(candidates)}")
    if not candidates:
        log("No new stories"
            + (f"; deleted posts purged: {removed}" if removed else ""))
        if args.mode == "publish" and removed:
            save_state(posts_doc, seen_doc, posts, chat, args)
            log(f"TOC updated: {len(posts_doc['posts'])} posts")
        return 0

    ai_ready = bool((os.environ.get("GEMINI_API_KEY") or os.environ.get("GROQ_API_KEY") or "").strip())
    if args.mode == "publish" and not ai_ready:
        log("!! no AI key set — raw announcements without a summary will not be "
            "published. Add an OpenRouter key (openrouter.ai/keys, free) "
            "to the GROQ_API_KEY secret.")

    added = 0
    for item in candidates:
        date_s, time_s = item["dt"].strftime("%Y-%m-%d"), item["dt"].strftime("%H:%M")

        # media: RSS enclosures → the article's og:image/og:video (best-effort)
        if not item.get("image") and not item.get("video") and not args.no_og_image:
            item["image"], item["video"] = og_media(item["src"])

        card = ai_card(item["title"], item["summary"], item["source"].get("name", ""))
        if card is None:
            if args.mode == "publish":
                log(f"  × no AI summary — not publishing: {trim(item['title'], 60)}")
                continue                      # the hash is NOT stored → retried on the next run
            card = fallback_card(item)

        tags = merge_tags(item["source"].get("tags"), card,
                          item["title"] + " " + item["summary"] + " " + card.get("lede", ""))

        text, kind = compose(item, card)

        if args.mode == "dry":
            media = " (with photo)" if kind == "photo" else (" (video)" if kind == "video" else "")
            log(f"\n--- DRY ({date_s} {time_s}){media} ---\n{text}\n")
            log(f"    tags: {' '.join(tags)}")
            added += 1
            continue

        ok, kind, msg_id = publish_item(token, "@" + chat, item, text, kind)
        if not ok:
            continue                          # Telegram rejected it outright — skip

        posts.append({
            "id": msg_id, "date": date_s, "time": time_s,
            "title": trim(card["headline"], 110), "preview": trim(card.get("lede") or item["summary"], 180),
            "tags": tags, "kind": kind,
            "url": f"https://t.me/{chat}/{msg_id}", "src": item["src"],
        })
        seen[item["hash"]] = date_s
        texts[str(msg_id)] = {"kind": kind, "text": text}   # for the edit-probe of future syncs
        added += 1
        log(f"  ✓ {kind}: {trim(card['headline'], 60)} → t.me/{chat}/{msg_id}")

    if args.mode == "publish" and (added or removed):
        save_state(posts_doc, seen_doc, posts, chat, args)
        log(f"\nDone: {added} new, {removed} purged; the TOC holds {len(posts_doc['posts'])}")
    elif args.mode == "publish":
        log("\nTOC unchanged")
    elif args.mode == "dry":
        log(f"\nDRY mode: would have published {added}; no files changed")
    return 0

if __name__ == "__main__":
    sys.exit(main())
