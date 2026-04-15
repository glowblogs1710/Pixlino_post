"""
auto_posts.py — Fully Automatic WordPress Post Creator (v20)
============================================================
Changes from v19:
  ✅ POSTS_PER_KEYWORD = 5  — each keyword gets exactly 5 posts (different title templates)
  ✅ POSTS_PER_RUN = 2      — only 2 posts published per day/run
  ✅ Keyword marked as used ONLY after all 5 posts for it are published
  ✅ Partial progress tracked in used_keywords.txt as "keyword::3of5" style
  ✅ On next run, resumes from where it left off for that keyword

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

WP_URL             = "https://pixlino.com//wp-json/wp/v2"
USERNAME           = os.environ.get("WP_USERNAME", "your_wp_username")
APP_PASSWORD       = os.environ.get("WP_APP_PASSWORD", "your_app_password")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "your_token")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "your_chat_id")

# --- Post settings ---
POSTS_PER_RUN      = 2            # how many posts to publish per run (per day)
POSTS_PER_KEYWORD  = 5            # how many posts to create for each keyword (uses different title templates)
IMAGES_PER_HEADING = 10           # images per heading
POST_STATUS        = "publish"      # ← TEST MODE: saving as draft (change back to "publish" for production)

# --- Random gap options (in seconds) ---
POST_GAP_OPTIONS_SECONDS = [
    30 * 60,    # 30 minutes
    45 * 60,    # 45 minutes
    60 * 60,    # 1 hour
    75 * 60,    # 1 hour 15 minutes
    90 * 60,    # 1 hour 30 minutes
    105 * 60,   # 1 hour 45 minutes
    120 * 60,   # 2 hours
]

# --- Random startup sleep range (seconds) ---
STARTUP_SLEEP_MIN = 0
STARTUP_SLEEP_MAX = 30 * 60

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
        self.start_time     = datetime.now()
        self.posts_created  = []
        self.posts_failed   = []
        self.posts_skipped  = []
        self.keywords_used  = []
        self.dry_run        = False
        self.gap_seconds    = 0
        self.startup_sleep  = 0

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
# HELPERS
# ============================================================

def seconds_to_human(seconds):
    hours = int(seconds // 3600)
    mins  = int((seconds % 3600) // 60)
    if hours > 0 and mins > 0:
        return f"{hours}h {mins}m"
    elif hours > 0:
        return f"{hours}h"
    else:
        return f"{mins}m"


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
    run_date  = stats.start_time.strftime("%d %b %Y, %I:%M %p IST")
    mode      = "🔍 DRY RUN" if stats.dry_run else "🚀 LIVE RUN"
    gap_human = seconds_to_human(stats.gap_seconds) if stats.gap_seconds > 0 else "None (test mode)"

    lines = [
        "<b>🤖 Auto Posts Report</b>",
        f"<b>Date:</b> {run_date}",
        f"<b>Mode:</b> {mode}",
        f"<b>Gap Used:</b> {gap_human}",
        f"<b>Time Taken:</b> {stats.elapsed()}",
        "",
        "<b>📊 Summary</b>",
        f"✅ Posts Created  : <b>{len(stats.posts_created)}</b>",
        f"❌ Posts Failed   : <b>{len(stats.posts_failed)}</b>",
        f"⏭️ Posts Skipped  : <b>{len(stats.posts_skipped)}</b>",
        "",
    ]

    if stats.posts_created:
        lines.append("<b>📝 Posts Created:</b>")
        for i, p in enumerate(stats.posts_created, 1):
            lines.append(
                f"{i}. <b>{p['title']}</b>\n"
                f"   📂 {p['category']} | 🔑 {p['keyword']} ({p['post_num']})\n"
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
        lines.append("<b>⏭️ Skipped:</b>")
        for s in stats.posts_skipped:
            lines.append(f"  • {s['keyword']} — {s['reason']}")
        lines.append("")

    lines.append("─────────────────────")
    lines.append("<i>pixlino.com | Auto Posts v20</i>")

    return "\n".join(lines)


# ============================================================
# USED KEYWORDS — with partial progress tracking
#
# Format in used_keywords.txt:
#   keyword text            ← fully done (all POSTS_PER_KEYWORD posts published)
#   keyword text::3         ← partially done (3 out of POSTS_PER_KEYWORD published so far)
# ============================================================

def load_used_keywords():
    """
    Returns a dict: { keyword_lower: posts_done_count }
    A keyword with posts_done_count >= POSTS_PER_KEYWORD is fully used.
    """
    if not os.path.exists(USED_KEYWORDS_FILE):
        return {}

    result = {}
    with open(USED_KEYWORDS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if "::" in line:
                kw, count_str = line.rsplit("::", 1)
                try:
                    result[kw.strip().lower()] = int(count_str.strip())
                except ValueError:
                    result[kw.strip().lower()] = POSTS_PER_KEYWORD
            else:
                result[line.lower()] = POSTS_PER_KEYWORD  # fully done
    return result


def save_keyword_progress(kw, posts_done):
    """
    Write or update the progress for a keyword in used_keywords.txt.
    posts_done = total posts published so far for this keyword across all runs.
    If posts_done >= POSTS_PER_KEYWORD, saves without the ::count suffix (fully done).
    """
    kw_lower = kw.strip().lower()

    # Read all existing lines
    lines = []
    if os.path.exists(USED_KEYWORDS_FILE):
        with open(USED_KEYWORDS_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

    # Remove old entry for this keyword if it exists
    new_lines = []
    for line in lines:
        entry = line.strip()
        if not entry:
            new_lines.append(line)
            continue
        entry_kw = entry.split("::")[0].strip().lower()
        if entry_kw != kw_lower:
            new_lines.append(line)

    # Write updated entry
    if posts_done >= POSTS_PER_KEYWORD:
        new_lines.append(kw.strip().lower() + "\n")
        log(f"  ✓ Keyword fully done ({posts_done}/{POSTS_PER_KEYWORD}): '{kw}'")
    else:
        new_lines.append(f"{kw.strip().lower()}::{posts_done}\n")
        log(f"  ✓ Keyword progress saved ({posts_done}/{POSTS_PER_KEYWORD}): '{kw}'")

    with open(USED_KEYWORDS_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


# ============================================================
# LOW KEYWORDS ALERT
# ============================================================

def check_keywords_low(fresh_count):
    if fresh_count == 0:
        send_telegram(
            "🚨 <b>Keywords Exhausted!</b>\n\n"
            "All keywords in <code>keywords.txt</code> have been fully used.\n"
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
            f"Only <b>{fresh_count}</b> keywords still have posts remaining.\n\n"
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


def collect_keywords(used_keywords_dict):
    """
    Returns a list of (keyword, post_number) tuples to process this run.

    For each seed in keywords.txt:
      - Check how many posts already published (from used_keywords.txt)
      - Queue the remaining posts up to POSTS_PER_KEYWORD
      - Each entry is (keyword, post_number) e.g. ("girl image...", 3)

    Keywords fully done (posts_done >= POSTS_PER_KEYWORD) are skipped.
    """
    seeds = load_keywords_from_file()

    if not seeds:
        log("  No seed keywords found in keywords.txt")
        return []

    queue = []
    fully_done_count = 0

    for seed in seeds:
        posts_done = used_keywords_dict.get(seed.lower(), 0)

        if posts_done >= POSTS_PER_KEYWORD:
            fully_done_count += 1
            log(f"  Seed '{seed}' fully done ({posts_done}/{POSTS_PER_KEYWORD}) — skipping")
            continue

        remaining = POSTS_PER_KEYWORD - posts_done
        log(f"  Seed '{seed}' → {posts_done}/{POSTS_PER_KEYWORD} done, {remaining} remaining")

        for n in range(posts_done + 1, POSTS_PER_KEYWORD + 1):
            queue.append((seed, n))

    fresh_keyword_count = len(seeds) - fully_done_count
    log(f"  Queue built: {len(queue)} post slots across {fresh_keyword_count} active keywords")
    check_keywords_low(fresh_keyword_count)

    return queue


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
    text = re.sub(r'[^\x00-\x7F]+', '', kw.lower())
    text = re.sub(r'[^\w\s]', '', text)
    text = re.sub(r'\b\d+\b', '', text)
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

def generate_title(kw, used_templates=None):
    """
    Pick a random title template, avoiding already-used ones for this keyword
    so all 5 posts get different templates.
    """
    templates = load_text_list(TITLE_TEMPLATES_FILE, split_by=None)
    if not templates:
        log("  ⚠ title_templates.txt empty or missing — using fallback")
        templates = ["Best {kw} HD Images Free Download"]

    if used_templates:
        available = [t for t in templates if t not in used_templates]
        if not available:
            available = templates  # all used — just pick any
    else:
        available = templates

    template = random.choice(available)
    return template.replace("{kw}", title_case_keyword(kw)), template


def get_unique_title(kw, existing_titles, used_templates=None):
    title, template_used = generate_title(kw, used_templates)
    if title.strip().lower() in existing_titles:
        log(f"  ✗ Title already exists — skipping: '{title}'")
        return title, template_used, False
    log(f"  ✓ Title is unique: '{title}'")
    return title, template_used, True


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

    pretty_kw = title_case_keyword(keyword)

    # Subheading 1 is always the exact keyword — no autocomplete
    result = [pretty_kw]
    log(f"  Subheading 1 (exact keyword): '{pretty_kw}'")

    # Remaining subheadings (2 onwards) come from Google Autocomplete
    suggestions = fetch_autocomplete(keyword)

    for s in suggestions:
        candidate = title_case_keyword(s)
        if candidate != pretty_kw and candidate not in result:
            result.append(candidate)
        if len(result) >= count:
            break

    log(f"  Google returned {len(result) - 1} additional subheading suggestions")

    # Fill any remaining slots with fallbacks
    if len(result) < count:
        fallback_sets = load_subheading_fallbacks()
        if not fallback_sets:
            fallback_sets = [["Stylish", "Cute", "Aesthetic", "Attitude", "Sad"]]

        modifier_set = random.choice(fallback_sets)

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
# INTERNAL LINKS
# ============================================================

# Cache so we only hit the WP API once per run
_recent_posts_cache = None

def fetch_recent_posts_for_links(count=5):
    """
    Fetch the most recent published posts from WordPress.
    Returns a list of {"title": "...", "link": "..."} dicts.
    Results are cached for the entire run so we don't hammer the API.
    """
    global _recent_posts_cache
    if _recent_posts_cache is not None:
        return _recent_posts_cache

    log("  Fetching recent posts for internal linking...")
    try:
        r = requests.get(
            f"{WP_URL}/posts",
            params={
                "per_page": count,
                "status":   "publish",
                "_fields":  "title,link",
                "orderby":  "date",
                "order":    "desc",
            },
            auth=AUTH,
            timeout=15
        )
        if r.status_code == 200:
            posts = r.json()
            result = []
            for p in posts:
                raw   = p.get("title", {})
                title = raw.get("rendered", "") if isinstance(raw, dict) else str(raw)
                link  = p.get("link", "")
                if title and link:
                    result.append({"title": title, "link": link})
            log(f"  Fetched {len(result)} recent posts for internal links")
            _recent_posts_cache = result
            return result
    except Exception as e:
        log(f"  ⚠ Could not fetch recent posts for internal links: {e}")

    _recent_posts_cache = []
    return []


def build_internal_links_html(recent_posts, current_title):
    """
    Build the 'You might also enjoy our related collections' paragraph.
    Excludes the current post's title. Shows up to 3 links.
    Returns an HTML string, or "" if there are no posts to link to.
    """
    candidates = [
        p for p in recent_posts
        if p["title"].strip().lower() != current_title.strip().lower()
    ][:3]

    if not candidates:
        return ""

    links_html = ", ".join(
        f'<a href="{p["link"]}">{p["title"]}</a>'
        for p in candidates
    )

    return (
        f'<p style="font-size:20px;line-height:1.8;margin-bottom:28px;color:#333;">'
        f'You might also enjoy our related collections: {links_html}.'
        f'</p>'
    )


# ============================================================
# HTML GALLERY BUILDER
# ============================================================

def build_html_gallery(subheadings, all_media, images_per_heading, keyword, intro_text,
                       recent_posts=None, current_title=""):
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

    # Internal linking block — shown right after the intro paragraph
    if recent_posts:
        internal_links_html = build_internal_links_html(recent_posts, current_title)
        if internal_links_html:
            html_parts.append(internal_links_html)

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

def run(posts_to_create=POSTS_PER_RUN, dry_run=False, skip_sleep=False):
    STATS.dry_run = dry_run

    gap_seconds       =  random.choice(POST_GAP_OPTIONS_SECONDS)   # ← TEST MODE: no delay (restore random.choice(POST_GAP_OPTIONS_SECONDS) for production)
    STATS.gap_seconds = gap_seconds
    gap_human         = seconds_to_human(gap_seconds) if gap_seconds > 0 else "None (test mode)"

    log("=" * 60)
    log(f"Auto Posts v20 | target={posts_to_create} posts | posts_per_keyword={POSTS_PER_KEYWORD} | dry_run={dry_run}")
    log(f"Gap between posts this run: {gap_human}")
    log("=" * 60)

    # ── Startup sleep ─────────────────────────────────────────
    if skip_sleep:
        startup_sleep = 0
        log("  Startup sleep: SKIPPED (--skip-sleep flag)")
    else:
        startup_sleep       = random.randint(STARTUP_SLEEP_MIN, STARTUP_SLEEP_MAX)
        STATS.startup_sleep = startup_sleep
        sleep_human         = seconds_to_human(startup_sleep)
        wake_time           = datetime.now() + timedelta(seconds=startup_sleep)
        log(f"  Startup sleep: {sleep_human} — waking at {wake_time.strftime('%I:%M %p')}")

    send_telegram(
        f"🚀 <b>Auto Posts Started</b>\n"
        f"Mode: {'DRY RUN' if dry_run else 'LIVE'}\n"
        f"Target: {posts_to_create} post(s) | {POSTS_PER_KEYWORD} per keyword\n"
        f"Gap: {gap_human}\n"
        f"Startup Sleep: {'None (skipped)' if startup_sleep == 0 else seconds_to_human(startup_sleep)}\n"
        f"Time: {STATS.start_time.strftime('%d %b %Y, %I:%M %p')}"
    )

    if startup_sleep > 0:
        time.sleep(startup_sleep)
        log(f"  Startup sleep done. Actual start: {datetime.now().strftime('%I:%M %p')}")

    # ── Load state ────────────────────────────────────────────
    used_keywords_dict = load_used_keywords()
    log(f"Loaded progress for {len(used_keywords_dict)} keywords from {USED_KEYWORDS_FILE}")

    # ── Categories ────────────────────────────────────────────
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

    # ── Build queue ───────────────────────────────────────────
    log("Building post queue from keywords...")
    queue = collect_keywords(used_keywords_dict)

    if not queue:
        msg = (
            "🚨 <b>No posts remaining!</b>\n\n"
            "All keywords in <code>keywords.txt</code> have been fully processed.\n"
            "Please add new keywords and push to GitHub."
        )
        log("Queue empty. Exiting.")
        send_telegram(msg)
        return

    # Select posts_to_create items from the queue (in order — don't shuffle,
    # so we always complete lower post numbers first)
    selected = queue[:posts_to_create]
    STATS.keywords_used = [kw for kw, _ in selected]
    log(f"Selected {len(selected)} post slots for this run")
    for kw, n in selected:
        log(f"  → '{kw}' (post {n}/{POSTS_PER_KEYWORD})")

    # ── Media ─────────────────────────────────────────────────
    if not dry_run:
        all_media = fetch_all_wp_media()
        if not all_media:
            msg = "❌ ERROR: Could not fetch WP media. Check your credentials."
            log(msg)
            send_telegram(msg)
            return
    else:
        all_media = [
            {"id": i, "source_url": f"https://pixlino.com/wp-content/img{i}.jpg", "alt_text": "girl dp"}
            for i in range(1, 500)
        ]

    existing_titles = fetch_existing_titles() if not dry_run else set()

    # ── Fetch recent posts for internal linking (once per run) ───
    recent_posts_for_links = fetch_recent_posts_for_links(count=5) if not dry_run else [
        {"title": "Hidden Face Girl Pic", "link": "https://pixlino.com/hidden-face-girl-pic/"},
        {"title": "Sad Girl DP", "link": "https://pixlino.com/sad-girl-dp/"},
        {"title": "Attitude Girl DP", "link": "https://pixlino.com/attitude-girl-dp/"},
    ]

    # ── Track in-run progress per keyword ────────────────────
    # keyword_lower → count of posts successfully published this run
    in_run_published = {}

    # ── Main loop ─────────────────────────────────────────────
    for i, (kw, post_num) in enumerate(selected):
        log(f"\n--- Post {i+1}/{len(selected)} | Keyword: '{kw}' | Post {post_num}/{POSTS_PER_KEYWORD} ---")

        # Step 1: Title (avoid templates already used for this keyword)
        used_templates_for_kw = []  # could extend to load from file if needed
        title, template_used, title_ok = get_unique_title(kw, existing_titles, used_templates_for_kw)
        if not title_ok:
            send_telegram(
                f"⏭️ <b>Post Skipped — Duplicate Title</b>\n\n"
                f"🔑 Keyword: <b>{kw}</b> (post {post_num}/{POSTS_PER_KEYWORD})\n"
                f"📝 Title: {title}\n\n"
                f"This title already exists. Slot skipped."
            )
            STATS.posts_skipped.append({"keyword": kw, "reason": f"duplicate title (post {post_num})"})
            continue

        # Step 2: Slug
        slug, slug_ok = get_unique_slug(kw)
        if not slug_ok:
            send_telegram(
                f"⏭️ <b>Post Skipped — All Slugs Exist</b>\n\n"
                f"🔑 Keyword: <b>{kw}</b> (post {post_num}/{POSTS_PER_KEYWORD})\n"
                f"🔗 Base Slug: {slug}\n\n"
                f"All slug variations exist. Slot skipped."
            )
            STATS.posts_skipped.append({"keyword": kw, "reason": f"all slugs exist (post {post_num})"})
            continue

        # Step 3: Build content
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
            subheadings, all_media, IMAGES_PER_HEADING, kw, intro,
            recent_posts=recent_posts_for_links,
            current_title=title
        )

        if dry_run:
            log(f"  [DRY RUN] Would publish: '{title}' (post {post_num}/{POSTS_PER_KEYWORD})")
            log(f"  [DRY RUN] Slug         : {slug}")
            log(f"  [DRY RUN] Category     : {cat_name} (ID={category_id})")
            log(f"  [DRY RUN] HTML size    : {len(html_content)} chars")

            STATS.posts_created.append({
                "title":        title,
                "link":         f"https://pixlino.com/{slug}/",
                "category":     cat_name,
                "keyword":      kw,
                "post_num":     f"post {post_num}/{POSTS_PER_KEYWORD}",
                "published_at": datetime.now().strftime("%d %b %Y %I:%M %p"),
            })

            kw_lower = kw.lower()
            in_run_published[kw_lower] = in_run_published.get(kw_lower, 0) + 1
            total_done = used_keywords_dict.get(kw_lower, 0) + in_run_published[kw_lower]
            save_keyword_progress(kw, total_done)
            existing_titles.add(title.strip().lower())

        else:
            post_id, post_link = create_wp_post(
                title, slug, html_content, category_id, focus_kw, meta_desc
            )

            if post_id:
                published_at = datetime.now().strftime("%d %b %Y %I:%M %p")
                log(f"  ✓ Published! ID={post_id} | Slug={slug} | {published_at}")
                log(f"  ✓ URL: {post_link}")

                kw_lower = kw.lower()
                in_run_published[kw_lower] = in_run_published.get(kw_lower, 0) + 1
                total_done = used_keywords_dict.get(kw_lower, 0) + in_run_published[kw_lower]
                save_keyword_progress(kw, total_done)
                existing_titles.add(title.strip().lower())

                STATS.posts_created.append({
                    "title":        title,
                    "link":         post_link,
                    "category":     cat_name,
                    "keyword":      kw,
                    "post_num":     f"post {post_num}/{POSTS_PER_KEYWORD}",
                    "published_at": published_at,
                })

            else:
                log(f"  ✗ Failed to create post for '{kw}' (post {post_num}/{POSTS_PER_KEYWORD})")
                STATS.posts_failed.append(f"{kw} (post {post_num})")

        # ── Gap between posts ─────────────────────────────────
        if i < len(selected) - 1 and gap_seconds > 0:
            next_post_time = datetime.now() + timedelta(seconds=gap_seconds)
            log(f"  ⏳ Waiting {gap_human} before next post...")
            log(f"  ⏳ Next post at: {next_post_time.strftime('%d %b %Y %I:%M %p')}")
            time.sleep(gap_seconds)

    # ── Final Summary ─────────────────────────────────────────
    log(f"\n{'='*60}")
    log(f"Done | Created: {len(STATS.posts_created)} | Failed: {len(STATS.posts_failed)} | Skipped: {len(STATS.posts_skipped)}")
    log(f"Total time: {STATS.elapsed()}")
    log(f"{'='*60}\n")

    summary = build_telegram_summary(STATS)
    send_telegram(summary)


# ============================================================
# CLI ENTRY POINT
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto WordPress Post Creator v20")
    parser.add_argument("--posts",      type=int,            default=POSTS_PER_RUN, help="Number of posts to create this run")
    parser.add_argument("--dry-run",    action="store_true",                        help="Preview without posting to WordPress")
    parser.add_argument("--skip-sleep", action="store_true",                        help="Skip random startup sleep only")
    args = parser.parse_args()

    run(posts_to_create=args.posts, dry_run=args.dry_run, skip_sleep=args.skip_sleep)
