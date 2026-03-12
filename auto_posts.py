"""
auto_posts.py — Fully Automatic WordPress Post Creator (v17)
============================================================
Changes from v16:
  ✅ Removed Google Indexing API completely — sitemap handles indexing
  ✅ Removed service_account.json dependency
  ✅ Cleaner and simpler code

File structure:
  auto_posts.py              ← this script
  keywords.txt               ← your seed keywords (one per line)
  intros.txt                 ← intro templates (blocks split by ---)
  meta_descriptions.txt      ← meta desc templates (blocks split by ---)
  title_templates.txt        ← title templates (one per line)
  subheading_fallbacks.txt   ← fallback sets (one set per line, comma separated)
"""

import requests
import random
import re
import time
import argparse
import os
from datetime import datetime, timedelta


# ============================================================
# CONFIGURATION
# ============================================================

WP_URL             = "https://pixlino.com/wp-json/wp/v2"
USERNAME           = os.environ.get("WP_USERNAME", "your_wp_username")
APP_PASSWORD       = os.environ.get("WP_APP_PASSWORD", "your_app_password")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "your_token")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "your_chat_id")

# --- Post settings ---
POSTS_PER_RUN      = 2            # change to 10 for production
IMAGES_PER_HEADING = 10           # images per heading
POST_STATUS        = "publish"    # publish instantly

# --- Gap between posts ---
POST_GAP_SECONDS   = 3600           # change to 7200 for 2 hour gap in production

# --- Slug variation words (tried in order if base slug already exists) ---
SLUG_VARIATIONS = ["hd", "4k", "new", "latest", "best", "images", "3d"]

# --- Words to remove from slug ---
SLUG_REMOVE_WORDS = {
    "free", "download"
}

# --- Fallback category ---
FALLBACK_CATEGORY = "Trending"

# --- All content files ---
KEYWORDS_FILE            = "keywords.txt"
INTROS_FILE              = "intros.txt"
META_DESCRIPTIONS_FILE   = "meta_descriptions.txt"
TITLE_TEMPLATES_FILE     = "title_templates.txt"
SUBHEADING_FALLBACK_FILE = "subheading_fallbacks.txt"

# --- Tracking files ---
USED_KEYWORDS_FILE = "used_keywords.txt"
LOG_FILE           = "logs/auto_posts.log"

# --- Low keywords warning threshold ---
LOW_KEYWORDS_THRESHOLD = 10

AUTH = (USERNAME, APP_PASSWORD)


# ============================================================
# RUN STATS
# ============================================================

class RunStats:
    def __init__(self):
        self.start_time    = datetime.now()
        self.posts_created = []   # list of dicts
        self.posts_failed  = []   # list of keywords
        self.posts_skipped = []   # list of dicts {keyword, reason}
        self.keywords_used = []
        self.dry_run       = False

    def elapsed(self):
        delta = datetime.now() - self.start_time
        hours = int(delta.total_seconds() // 3600)
        mins  = int((delta.total_seconds() % 3600) // 60)
        secs  = int(delta.total_seconds() % 60)
        if hours > 0:
            return f"{hours}h {mins}m {secs}s"
        return f"{mins}m {secs}s"


STATS = RunStats()


# ============================================================
# LOGGING
# ============================================================

def log(msg):
    os.makedirs("logs", exist_ok=True)
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ============================================================
# FILE LOADERS
# ============================================================

def load_text_list(filepath, split_by="---"):
    if not os.path.exists(filepath):
        log(f"  ⚠ File not found: {filepath}")
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    if split_by:
        entries = [e.strip() for e in content.split(split_by) if e.strip()]
    else:
        entries = [
            line.strip() for line in content.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    log(f"  Loaded {len(entries)} entries from {filepath}")
    return entries


def load_subheading_fallbacks():
    lines  = load_text_list(SUBHEADING_FALLBACK_FILE, split_by=None)
    result = []
    for line in lines:
        parts = [p.strip() for p in line.split(",") if p.strip()]
        if parts:
            result.append(parts)
    log(f"  Loaded {len(result)} subheading fallback sets")
    return result


def load_keywords_from_file():
    seeds = load_text_list(KEYWORDS_FILE, split_by=None)
    log(f"  Loaded {len(seeds)} seed keywords from {KEYWORDS_FILE}")
    return seeds


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("  ⚠ Telegram not configured — skipping notification")
        return

    if len(message) > 4000:
        message = message[:3997] + "..."

    url    = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    params = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
    }

    try:
        r = requests.post(url, data=params, timeout=15)
        if r.status_code == 200:
            log("  ✓ Telegram notification sent")
        else:
            log(f"  ✗ Telegram error {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log(f"  ✗ Telegram send error: {e}")


def build_telegram_summary(stats):
    run_date = stats.start_time.strftime("%d %b %Y, %I:%M %p IST")
    mode     = "🔍 DRY RUN" if stats.dry_run else "🚀 LIVE RUN"

    lines = [
        "<b>🤖 Auto Posts Report</b>",
        f"<b>Date:</b> {run_date}",
        f"<b>Mode:</b> {mode}",
        f"<b>Time Taken:</b> {stats.elapsed()}",
        "",
        "<b>📊 Summary</b>",
        f"✅ Posts Published  : <b>{len(stats.posts_created)}</b>",
        f"❌ Posts Failed     : <b>{len(stats.posts_failed)}</b>",
        f"⏭️ Posts Skipped    : <b>{len(stats.posts_skipped)}</b>",
        "",
    ]

    if stats.posts_created:
        lines.append("<b>📝 Posts Published:</b>")
        for i, p in enumerate(stats.posts_created, 1):
            lines.append(
                f"{i}. <b>{p['title']}</b>\n"
                f"   📂 {p['category']} | 🔑 {p['keyword']}\n"
                f"   🕐 {p['published_at']}\n"
                f"   🔗 <a href=\"{p['link']}\">{p['link']}</a>"
            )
        lines.append("")

    if stats.posts_failed:
        lines.append("<b>❌ Failed Keywords:</b>")
        for kw in stats.posts_failed:
            lines.append(f"  • {kw}")
        lines.append("")

    if stats.posts_skipped:
        lines.append("<b>⏭️ Skipped Keywords:</b>")
        for s in stats.posts_skipped:
            lines.append(f"  • {s['keyword']} — {s['reason']}")
        lines.append("")

    lines.append("─────────────────────")
    lines.append("<i>unityimage.com | Auto Posts v17</i>")

    return "\n".join(lines)


# ============================================================
# USED KEYWORDS
# ============================================================

def load_used_keywords():
    if not os.path.exists(USED_KEYWORDS_FILE):
        return set()
    with open(USED_KEYWORDS_FILE, "r", encoding="utf-8") as f:
        return set(line.strip().lower() for line in f if line.strip())


def save_used_keyword(kw):
    with open(USED_KEYWORDS_FILE, "a", encoding="utf-8") as f:
        f.write(kw.strip().lower() + "\n")


# ============================================================
# LOW KEYWORDS ALERT
# ============================================================

def check_keywords_low(fresh_count):
    if fresh_count == 0:
        send_telegram(
            "🚨 <b>Keywords Exhausted!</b>\n\n"
            "All keywords in <code>keywords.txt</code> have been used up.\n"
            "No new posts can be created until you add more.\n\n"
            "👉 <b>What to do:</b>\n"
            "1. Open <code>keywords.txt</code> in your project\n"
            "2. Add new keywords (one per line)\n"
            "3. Save → commit → push to GitHub\n\n"
            "Script will resume automatically on next run. ✅"
        )
    elif fresh_count <= LOW_KEYWORDS_THRESHOLD:
        send_telegram(
            f"⚠️ <b>Keywords Running Low!</b>\n\n"
            f"Only <b>{fresh_count}</b> fresh keywords remaining.\n\n"
            f"👉 Please add more keywords to <code>keywords.txt</code> "
            f"and push to GitHub soon to avoid interruption."
        )


# ============================================================
# GOOGLE AUTOCOMPLETE
# ============================================================

def fetch_autocomplete(seed):
    url     = "https://suggestqueries.google.com/complete/search"
    params  = {"client": "firefox", "q": seed, "hl": "en"}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 200:
            data        = r.json()
            suggestions = data[1] if len(data) > 1 else []
            return [s.strip().lower() for s in suggestions if s.strip()]
    except Exception as e:
        log(f"  Autocomplete error for '{seed}': {e}")
    return []


def collect_keywords(used_keywords):
    seeds = load_keywords_from_file()

    if not seeds:
        log("  No seed keywords found in keywords.txt")
        return []

    all_kws = []
    for seed in seeds:
        suggestions = fetch_autocomplete(seed)
        log(f"  Seed '{seed}' → {len(suggestions)} suggestions")
        all_kws.extend(suggestions)
        time.sleep(0.5)

    all_kws.extend(seeds)

    seen, unique = set(), []
    for kw in all_kws:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)

    fresh = [kw for kw in unique if kw not in used_keywords and len(kw.split()) >= 3]
    log(f"  Total fresh keywords available: {len(fresh)}")

    check_keywords_low(len(fresh))

    return fresh


# ============================================================
# TITLE CASE HELPER
# ============================================================

def title_case_keyword(kw):
    always_upper = {"dp", "hd", "4k"}
    stop_words   = {"a", "an", "the", "and", "or", "for", "of", "in", "on", "at", "to"}
    words  = kw.split()
    result = []
    for i, w in enumerate(words):
        if w in always_upper:
            result.append(w.upper())
        elif i == 0 or w not in stop_words:
            result.append(w.capitalize())
        else:
            result.append(w)
    return " ".join(result)


# ============================================================
# SLUG GENERATOR
# ============================================================

def build_clean_slug(kw):
    # Remove emojis and non-ASCII
    text = re.sub(r'[^\x00-\x7F]+', '', kw.lower())
    # Remove special characters
    text = re.sub(r'[^\w\s]', '', text)
    # Remove standalone numbers
    text = re.sub(r'\b\d+\b', '', text)
    # Filter remove words
    words = [w for w in text.split() if w and w not in SLUG_REMOVE_WORDS]
    slug  = "-".join(words).strip("-")
    return slug


def check_slug_exists(slug):
    try:
        r = requests.get(
            f"{WP_URL}/posts",
            params={"slug": slug, "_fields": "slug", "status": "publish,future,draft"},
            auth=AUTH,
            timeout=10
        )
        if r.status_code == 200:
            return len(r.json()) > 0
    except Exception as e:
        log(f"  ⚠ Slug check error: {e}")
    return False


def get_unique_slug(kw):
    base_slug = build_clean_slug(kw)
    log(f"  Base slug: '{base_slug}'")

    if not check_slug_exists(base_slug):
        log(f"  ✓ Slug is unique: '{base_slug}'")
        return base_slug, True

    log(f"  ✗ Base slug exists — trying variations...")

    for variation in SLUG_VARIATIONS:
        candidate = f"{base_slug}-{variation}"
        if not check_slug_exists(candidate):
            log(f"  ✓ Variation slug found: '{candidate}'")
            return candidate, True
        else:
            log(f"  ✗ '{candidate}' also exists")

    log(f"  ⏭ All slug variations exist — skipping: '{kw}'")
    return base_slug, False


# ============================================================
# TITLE GENERATOR
# ============================================================

def generate_title(kw):
    templates = load_text_list(TITLE_TEMPLATES_FILE, split_by=None)
    if not templates:
        log("  ⚠ title_templates.txt empty or missing — using fallback")
        templates = ["Best {kw} HD Images Free Download"]
    template = random.choice(templates)
    return template.replace("{kw}", title_case_keyword(kw))


def get_unique_title(kw, existing_titles):
    title = generate_title(kw)
    if title.strip().lower() in existing_titles:
        log(f"  ✗ Title already exists — skipping: '{title}'")
        return title, False
    log(f"  ✓ Title is unique: '{title}'")
    return title, True


# ============================================================
# FOCUS KEYWORD
# ============================================================

def generate_focus_keyword(kw):
    return title_case_keyword(kw)


# ============================================================
# INTRO GENERATOR
# ============================================================

def generate_intro(keyword):
    intros = load_text_list(INTROS_FILE, split_by="---")
    if not intros:
        log("  ⚠ intros.txt empty or missing — using fallback")
        intros = ["Welcome to the best {topic} collection free in HD quality."]

    pretty   = title_case_keyword(keyword)
    template = random.choice(intros)
    intro    = template.replace("{topic}", pretty)
    log(f"  ✓ Intro generated ({len(intro)} chars)")
    return intro


# ============================================================
# META DESCRIPTION
# ============================================================

def generate_meta_description(keyword):
    descriptions = load_text_list(META_DESCRIPTIONS_FILE, split_by="---")
    if not descriptions:
        log("  ⚠ meta_descriptions.txt empty or missing — using fallback")
        descriptions = ["Download the best {topic} HD images free for Instagram and WhatsApp."]

    pretty   = title_case_keyword(keyword)
    template = random.choice(descriptions)
    meta     = template.replace("{topic}", pretty).strip()

    if len(meta) > 155:
        meta = meta[:152] + "..."

    log(f"  ✓ Meta description ({len(meta)} chars): {meta[:60]}...")
    return meta


# ============================================================
# SUBHEADINGS
# ============================================================

def fetch_subheadings_from_google(keyword, count=5):
    log(f"  Fetching subheadings from Google for: '{keyword}'")
    suggestions = fetch_autocomplete(keyword)

    result = []
    for s in suggestions:
        result.append(title_case_keyword(s))
        if len(result) >= count:
            break

    log(f"  Google returned {len(result)} subheading suggestions")

    if len(result) < count:
        fallback_sets = load_subheading_fallbacks()
        if not fallback_sets:
            fallback_sets = [["Stylish", "Cute", "Aesthetic", "Attitude", "Sad"]]

        modifier_set = random.choice(fallback_sets)
        pretty_kw    = title_case_keyword(keyword)

        for mod in modifier_set:
            if len(result) >= count:
                break
            candidate = f"{mod} {pretty_kw}"
            if candidate not in result:
                result.append(candidate)

        log(f"  Fallback added — total subheadings: {len(result)}")

    return result[:count]


# ============================================================
# WP EXISTING TITLES
# ============================================================

def fetch_existing_titles():
    log("  Fetching existing post titles from WordPress...")
    all_titles = set()
    page = 1

    while True:
        try:
            r = requests.get(
                f"{WP_URL}/posts",
                params={
                    "per_page": 100,
                    "page":     page,
                    "status":   "publish,future,draft",
                    "_fields":  "title",
                },
                auth=AUTH,
                timeout=30
            )
            if r.status_code != 200:
                break
            data = r.json()
            if not data:
                break
            for post in data:
                raw = post.get("title", {})
                t   = raw.get("rendered", "") if isinstance(raw, dict) else str(raw)
                all_titles.add(t.strip().lower())
            page += 1
            time.sleep(0.3)
        except Exception as e:
            log(f"  ⚠ Error fetching titles page {page}: {e}")
            break

    log(f"  Fetched {len(all_titles)} existing post titles")
    return all_titles


# ============================================================
# WP CATEGORIES
# ============================================================

def fetch_wp_categories():
    try:
        r = requests.get(
            f"{WP_URL}/categories",
            params={"per_page": 100},
            auth=AUTH,
            timeout=10
        )
        cats = r.json()
        log(f"  Fetched {len(cats)} categories from WordPress:")
        for cat in cats:
            log(f"    ID={cat['id']}  Name='{cat['name']}'")
        return cats
    except Exception as e:
        log(f"  Category fetch error: {e}")
        return []


def match_category(title, categories):
    title_lower = title.lower()

    for cat in categories:
        cat_name = cat["name"].lower()
        if cat_name == FALLBACK_CATEGORY.lower():
            continue
        cat_words   = cat_name.split()
        match_words = cat_words[:3]
        if all(word in title_lower for word in match_words):
            log(f"  ✓ Category matched: '{cat['name']}' (ID={cat['id']})")
            return cat["id"]

    for cat in categories:
        if cat["name"].lower() == FALLBACK_CATEGORY.lower():
            log(f"  No match — fallback to '{FALLBACK_CATEGORY}' (ID={cat['id']})")
            return cat["id"]

    if categories:
        log(f"  '{FALLBACK_CATEGORY}' not found — using first category")
        return categories[0]["id"]

    log("  WARNING: No categories found — using ID=1")
    return 1


# ============================================================
# WP MEDIA
# ============================================================

_media_cache = None

def fetch_all_wp_media():
    global _media_cache
    if _media_cache is not None:
        return _media_cache

    log("  Fetching WP Media Library (runs once per session)...")
    all_items = []
    page = 1

    while True:
        r = requests.get(
            f"{WP_URL}/media",
            params={"media_type": "image", "per_page": 100, "page": page},
            auth=AUTH,
            timeout=30
        )
        if r.status_code != 200:
            break
        data = r.json()
        if not data:
            break
        all_items.extend(data)
        log(f"    Page {page} → {len(data)} images (total: {len(all_items)})")
        page += 1
        time.sleep(0.3)

    log(f"  Total images in library: {len(all_items)}")
    _media_cache = all_items
    return all_items


# ============================================================
# HTML GALLERY BUILDER
# ============================================================

def build_html_gallery(subheadings, all_media, images_per_heading, keyword, intro_text):
    pretty_kw  = title_case_keyword(keyword)
    html_parts = []

    if intro_text:
        pretty_kw_bold  = f"<strong>{pretty_kw}</strong>"
        intro_formatted = intro_text.replace(pretty_kw, pretty_kw_bold)
        html_parts.append(
            f'<p style="font-size:20px;line-height:1.8;margin-bottom:28px;color:#333;">'
            f'{intro_formatted}'
            f'</p>'
        )

    pool = list(all_media)
    random.shuffle(pool)

    needed = images_per_heading * len(subheadings)
    while len(pool) < needed:
        extra = list(all_media)
        random.shuffle(extra)
        pool.extend(extra)

    cursor = 0
    for sub in subheadings:
        chunk   = pool[cursor: cursor + images_per_heading]
        cursor += images_per_heading

        html_parts.append(f'<h2>{sub}</h2>')

        for item in chunk:
            url = item.get("source_url", "")
            alt = item.get("alt_text") or pretty_kw

            html_parts.append(
                f'<figure style="margin-bottom:20px;text-align:center;">'
                f'<img src="{url}" alt="{alt}" style="width:100%;border-radius:8px;" />'
                f'<div style="margin-top:6px;color:#555;font-size:13px;">{alt}</div>'
                f'</figure>'
            )

    return "\n".join(html_parts)


# ============================================================
# CREATE WORDPRESS POST
# ============================================================

def create_wp_post(title, slug, content, category_id, focus_kw, meta_desc):
    data = {
        "title":      title,
        "slug":       slug,
        "content":    content,
        "status":     POST_STATUS,
        "categories": [category_id],
        "meta": {
            "_yoast_wpseo_focuskw":  focus_kw,
            "_yoast_wpseo_metadesc": meta_desc,
        }
    }
    try:
        r      = requests.post(f"{WP_URL}/posts", json=data, auth=AUTH, timeout=30)
        result = r.json()

        if r.status_code not in (200, 201):
            log(f"  ✗ WP API error {r.status_code}")
            log(f"  ✗ Code   : {result.get('code', 'unknown')}")
            log(f"  ✗ Message: {result.get('message', 'unknown')}")
            send_telegram(
                f"❌ <b>Post Creation Failed</b>\n\n"
                f"<b>Status:</b> {r.status_code}\n"
                f"<b>Code:</b> {result.get('code', 'unknown')}\n"
                f"<b>Message:</b> {result.get('message', 'unknown')}\n"
                f"<b>Title:</b> {title}"
            )
            return None, ""

        return result.get("id"), result.get("link", "")

    except Exception as e:
        log(f"  ✗ WP post creation error: {e}")
        return None, ""


# ============================================================
# MAIN PIPELINE
# ============================================================

def run(posts_to_create=POSTS_PER_RUN, dry_run=False):
    STATS.dry_run = dry_run

    log("=" * 60)
    log(f"Auto Posts v17 | target={posts_to_create} posts | dry_run={dry_run}")
    log(f"Gap between posts: {POST_GAP_SECONDS}s")
    log("=" * 60)

    send_telegram(
        f"🚀 <b>Auto Posts Started</b>\n"
        f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n"
        f"Target: {posts_to_create} post(s)\n"
        f"Gap: {POST_GAP_SECONDS} seconds between each post\n"
        f"Time: {STATS.start_time.strftime('%d %b %Y, %I:%M %p')}"
    )

    used_keywords = load_used_keywords()
    log(f"Loaded {len(used_keywords)} already-used keywords")

    log("Fetching WordPress categories...")
    categories = fetch_wp_categories() if not dry_run else [
        {"id": 1, "name": "Hidden Face Girl Pic"},
        {"id": 2, "name": "Sad Girl DP"},
        {"id": 3, "name": "Attitude Girl DP"},
        {"id": 4, "name": "Aesthetic Girl DP"},
        {"id": 5, "name": "Trending"},
    ]

    if not categories:
        msg = "❌ ERROR: Could not fetch WordPress categories. Check your credentials."
        log(msg)
        send_telegram(msg)
        return

    log("Fetching keyword suggestions from Google...")
    keywords = collect_keywords(used_keywords)

    if not keywords:
        msg = (
            "🚨 <b>No fresh keywords found!</b>\n\n"
            "All keywords in <code>keywords.txt</code> are either used up or empty.\n"
            "Please add new keywords and push to GitHub."
        )
        log("No fresh keywords found. Exiting.")
        send_telegram(msg)
        return

    random.shuffle(keywords)
    selected = keywords[:posts_to_create]
    STATS.keywords_used = selected
    log(f"Selected {len(selected)} keywords for this run")

    if not dry_run:
        all_media = fetch_all_wp_media()
        if not all_media:
            msg = "❌ ERROR: Could not fetch WP media. Check your credentials."
            log(msg)
            send_telegram(msg)
            return
    else:
        all_media = [
            {"id": i, "source_url": f"https://unityimage.com/wp-content/img{i}.jpg", "alt_text": "girl dp"}
            for i in range(1, 500)
        ]

    existing_titles = fetch_existing_titles() if not dry_run else set()

    # ── Main loop ─────────────────────────────────────────────
    for i, kw in enumerate(selected):
        log(f"\n--- Post {i+1}/{len(selected)} | Keyword: '{kw}' ---")

        # Step 1: Title duplicate check
        title, title_ok = get_unique_title(kw, existing_titles)
        if not title_ok:
            send_telegram(
                f"⏭️ <b>Post Skipped — Duplicate Title</b>\n\n"
                f"🔑 Keyword: <b>{kw}</b>\n"
                f"📝 Title: {title}\n\n"
                f"This title already exists. Keyword skipped."
            )
            STATS.posts_skipped.append({"keyword": kw, "reason": "duplicate title"})
            continue

        # Step 2: Slug check
        slug, slug_ok = get_unique_slug(kw)
        if not slug_ok:
            send_telegram(
                f"⏭️ <b>Post Skipped — All Slugs Exist</b>\n\n"
                f"🔑 Keyword: <b>{kw}</b>\n"
                f"🔗 Base Slug: {slug}\n\n"
                f"Base slug + all variations already exist. Keyword skipped."
            )
            STATS.posts_skipped.append({"keyword": kw, "reason": "all slugs exist"})
            continue

        # Step 3: Build post content
        focus_kw    = generate_focus_keyword(kw)
        intro       = generate_intro(kw)
        meta_desc   = generate_meta_description(kw)
        subheadings = fetch_subheadings_from_google(kw, count=5)
        category_id = match_category(title, categories)
        cat_name    = next((c["name"] for c in categories if c["id"] == category_id), "Unknown")

        log(f"  Title      : {title}")
        log(f"  Slug       : {slug}")
        log(f"  Focus KW   : {focus_kw}")
        log(f"  Category   : {cat_name}")
        log(f"  Subheadings: {' | '.join(subheadings)}")
        log(f"  Meta Desc  : {meta_desc[:80]}...")

        html_content = build_html_gallery(
            subheadings, all_media, IMAGES_PER_HEADING, kw, intro
        )

        if dry_run:
            log(f"  [DRY RUN] Would publish : '{title}'")
            log(f"  [DRY RUN] Slug          : {slug}")
            log(f"  [DRY RUN] Category      : {cat_name} (ID={category_id})")
            log(f"  [DRY RUN] HTML size     : {len(html_content)} chars")

            STATS.posts_created.append({
                "title":        title,
                "link":         f"https://unityimage.com/{slug}/",
                "category":     cat_name,
                "keyword":      kw,
                "published_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
            })

        else:
            post_id, post_link = create_wp_post(
                title, slug, html_content, category_id, focus_kw, meta_desc
            )

            if post_id:
                published_at = datetime.now().strftime("%d %b %Y %I:%M %p")
                log(f"  ✓ Published! ID={post_id} | Slug={slug} | {published_at}")
                log(f"  ✓ URL: {post_link}")
                save_used_keyword(kw)
                existing_titles.add(title.strip().lower())

                STATS.posts_created.append({
                    "title":        title,
                    "link":         post_link,
                    "category":     cat_name,
                    "keyword":      kw,
                    "published_at": published_at,
                })

            else:
                log(f"  ✗ Failed to create post for '{kw}'")
                STATS.posts_failed.append(kw)

        # Wait between posts
        if i < len(selected) - 1:
            if dry_run:
                log(f"  [DRY RUN] Would wait {POST_GAP_SECONDS} seconds before next post")
            else:
                next_post_time = datetime.now() + timedelta(seconds=POST_GAP_SECONDS)
                log(f"  ⏳ Waiting {POST_GAP_SECONDS} seconds before next post...")
                log(f"  ⏳ Next post at: {next_post_time.strftime('%d %b %Y %I:%M %p')}")
                time.sleep(POST_GAP_SECONDS)

    # ── Final Summary ─────────────────────────────────────────
    log(f"\n{'='*60}")
    log(f"Done | Published: {len(STATS.posts_created)} | Failed: {len(STATS.posts_failed)} | Skipped: {len(STATS.posts_skipped)}")
    log(f"Total time: {STATS.elapsed()}")
    log(f"{'='*60}\n")

    summary = build_telegram_summary(STATS)
    send_telegram(summary)


# ============================================================
# CLI ENTRY POINT
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto WordPress Post Creator v17")
    parser.add_argument("--posts",   type=int,           default=POSTS_PER_RUN, help="Number of posts to create")
    parser.add_argument("--dry-run", action="store_true",                        help="Preview without posting to WordPress")
    args = parser.parse_args()


    run(posts_to_create=args.posts, dry_run=args.dry_run)

