/* ════════════════════════════════════════════════════════════════
   "Table of Contents" · MINI-APP CONFIG (EN edition)
   This is the ONLY file you need to edit. Fill it in — done.
   ════════════════════════════════════════════════════════════════ */

window.SZ_CONFIG = {

  /* ── 1. CHANNEL (filled in by the buyer) ────────────────────── */

  // Channel name as it will appear in the contents header:
  channel_name: "Daily News Digest",

  // Channel link (must start with https://):
  channel_url: "https://t.me/daily_news_digest_en",

  // Header button (e.g. "Subscribe"):
  channel_button: "Channel",

  // Emoji logo in the header (any emoji of your choice):
  header_emoji: "📖",

  // Username of the bot this mini-app is attached to (no @; "" is OK):
  // needed for the rescue banner. If the app was opened via a direct
  // link (the Telegram bridge is unavailable), the banner button leads
  // to t.me/<bot>?startapp — the proper entrance to the mini-app.
  // Leave "" and the button will simply open the channel's pinned
  // "Table of Contents" post.

  bot_username: "",

  // ── 2. LICENSE (filled in by the SELLER when handing over the kit) ──

  // Buyer's name as printed in the license (watermark in the footer):
  licensee: "DEMO",

  // License key issued by the seller (format SZ-XXXXXXXXXXXXXXXX):
  license_key: "SZ-976B6BE64F5DF589",

  // Short license number for bookkeeping (any label, safe to keep):
  license_id: "demo-en-0001"

  /* ── 3. Nothing else needs changing ───────────────────────────
     Theme colors can be changed in index.html (the :root block) —
     optional; by default the warm "paper" theme is used, which
     automatically switches to dark together with Telegram.
     ────────────────────────────────────────────────────────────── */
};
