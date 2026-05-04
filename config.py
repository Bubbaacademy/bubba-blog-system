import os

# ── Google Sheets ──────────────────────────────────────────────────────────────
# GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_JSON must be set in .env / Render env.
SHEET_ID         = os.environ.get("GOOGLE_SHEET_ID",   "1cYzI2qy2uEdI0T5xHPNNKXBP3czCuQAkiAeWeF2JwCM")
SHEET_NAME       = os.environ.get("GOOGLE_SHEET_NAME", "Sheet1")
CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json")

# Column index mapping (1-based, matches Google Sheets columns A=1, B=2...)
COLUMNS = {
    "content_id":     1,   # A
    "topic_cluster":  2,   # B
    "main_keyword":   3,   # C
    "content_title":  4,   # D
    "audience_level": 5,   # E
    "content_type":   6,   # F
    "status":         7,   # G
    "publish_date":   8,   # H
    "blog_draft":     9,   # I
    "social_caption": 10,  # J
    "video_script":   11,  # K
    "email_copy":     12,  # L
    "approval":       13,  # M
    "published_url":  14,  # N
    "seo_title":      15,  # O
    "meta_desc":      16,  # P
    "notes":          17,  # Q
}

# main.py statuses
STATUS_TRIGGER      = "Idea"
STATUS_DRAFT_READY  = "Draft Ready"
STATUS_OUTPUT       = STATUS_DRAFT_READY  # kept for backwards compat

# publisher.py statuses
STATUS_APPROVED     = "Approved"
STATUS_EXPORTED     = "Exported"
STATUS_PUBLISHED    = "Published"

APPROVAL_TRIGGER    = "Yes"

EXPORTS_DIR         = "exports"
GDOCS_FOLDER_ID     = "1PbWpzvifh1E5oU3Cs7_8TtfPE1NzclV8"

# ── HubSpot API Configuration ──────────────────────────────────────────────────
# HUBSPOT_TOKEN must be set in .env / Render env — never hardcoded here.
# Numeric IDs are non-secret but env-configurable for multi-tenant use.

HUBSPOT_PORTAL_ID            = int(os.environ.get("HUBSPOT_PORTAL_ID",   "243737166"))
HUBSPOT_BLOG_ID              = int(os.environ.get("HUBSPOT_BLOG_ID",     "243260089053"))
HUBSPOT_BLOG_LISTING_PAGE_ID = int(os.environ.get("HUBSPOT_BLOG_LISTING_PAGE_ID", "243260089055"))
HUBSPOT_AUTHOR_ID            = int(os.environ.get("HUBSPOT_AUTHOR_ID",   "340728710883"))
HUBSPOT_BASE_URL             = os.environ.get("HUBSPOT_BASE_URL",
                               "https://crm.bubbaacademy.com/bubba-academy-blog")
HUBSPOT_API_URL              = "https://api.hubapi.com/cms/v3/blogs/posts"

# HUBSPOT_MOCK_MODE=true → log payload, skip real POST (safe for staging)
HUBSPOT_MOCK_MODE = os.environ.get("HUBSPOT_MOCK_MODE", "false").lower() == "true"

# ── HubSpot Embedded Form ──────────────────────────────────────────────────────
# Placed after the conclusion section, before the conversion CTA.
HUBSPOT_FORM_ID     = os.environ.get("HUBSPOT_FORM_ID",     "5d3a1165-5203-4ebd-a7de-8b344de6d961")
HUBSPOT_FORM_REGION = os.environ.get("HUBSPOT_FORM_REGION", "na2")

BRAND = {
    "name":    "Bubba Academy",
    "focus":   "Amazon, e-commerce, online business, and practical business skills",
    "tone":    "practical, clear, beginner-friendly, professional but easy to understand",
    "style":   "no fluff, actionable, SEO optimized, AEO optimized",
    "language": "English",
}

# ── Approved URL Registry ──────────────────────────────────────────────────────
# ONLY these URLs may appear in CTAs and internal links.
# All entries are HTTP-verified (200 or 301→200) before inclusion.
# Add new published article slugs here as the content library grows.

BLOG_BASE = "https://243737166.hs-sites-na2.com/bubba-holding-blog"

APPROVED_URLS = {
    # ── Main site ──────────────────────────────────────────────────────────────
    "bubba_home":   "https://bubbaacademy.com",

    # ── Published blog posts (verified HTTP 200) ──────────────────────────────
    "fba_fees":     f"{BLOG_BASE}/amazon-fba-fees-explained-for-beginners",
    "fba_shipping": f"{BLOG_BASE}/amazon-fba-shipping-costs-explained",
    "fba_inbound":  f"{BLOG_BASE}/amazon-fba-inbound-shipping-guide-for-beginners",
    "fba_storage":  f"{BLOG_BASE}/amazon-fba-warehouse-storage-fees-explained",
}

# ── Topic Cluster Internal Link Map ───────────────────────────────────────────
# Maps keyword fragment → ordered list of (anchor_phrase, url_key) candidates.
# The linker searches the rendered article HTML for each anchor_phrase and,
# if found naturally inside a <p> tag, wraps it with the approved URL.
# Rules:
#   • url_key must exist in APPROVED_URLS (enforced at link time)
#   • Each URL is used at most once per article
#   • Current article's own URL is never linked to itself
#   • If anchor_phrase not found in body → silently skipped (no forced links)
#   • Max 3 cluster links per article

TOPIC_CLUSTER_LINKS = {
    "fee": [
        ("shipping costs",    "fba_shipping"),
        ("inbound shipping",  "fba_inbound"),
        ("storage fees",      "fba_storage"),
        ("long-term storage", "fba_storage"),
    ],
    "cost": [
        ("FBA fees",          "fba_fees"),
        ("fulfillment fees",  "fba_fees"),
        ("storage fees",      "fba_storage"),
        ("inbound shipping",  "fba_inbound"),
    ],
    "shipping": [
        ("FBA fees",          "fba_fees"),
        ("fulfillment fees",  "fba_fees"),
        ("storage fees",      "fba_storage"),
        ("long-term storage", "fba_storage"),
        ("inbound shipping",  "fba_inbound"),
    ],
    "inbound": [
        ("shipping costs",    "fba_shipping"),
        ("storage fees",      "fba_storage"),
        ("long-term storage", "fba_storage"),
        ("FBA fees",          "fba_fees"),
        ("fulfillment fees",  "fba_fees"),
    ],
    "storage": [
        ("fulfillment fees",  "fba_fees"),
        ("referral fees",     "fba_fees"),
        ("FBA fees",          "fba_fees"),
        ("inbound shipping",  "fba_inbound"),
        ("shipping costs",    "fba_shipping"),
    ],
    "warehouse": [
        ("fulfillment fees",  "fba_fees"),
        ("referral fees",     "fba_fees"),
        ("inbound shipping",  "fba_inbound"),
        ("shipping costs",    "fba_shipping"),
    ],
    "product": [
        ("FBA fees",          "fba_fees"),
        ("fulfillment fees",  "fba_fees"),
        ("shipping costs",    "fba_shipping"),
        ("storage fees",      "fba_storage"),
    ],
}

# ── CTA Configuration ──────────────────────────────────────────────────────────
# TOP CTA    → lead_magnet (captures new readers after intro)
# MID CTA    → contextual  (dynamic per topic — see MID_CTA_CONFIGS below)
# BOTTOM CTA → conversion  (strong transformation close)
#
# Includes: icon, headline, body, bullets (max 3), button_text, href
# All href values are approved URLs — zero FILL_IN placeholders.

CTA_CONFIG = {
    "lead_magnet": {
        "icon":        "📦",
        "headline":    "Free Download: The Amazon FBA Starter Checklist",
        "body":        (
            "Before you source your first product, make sure you have the "
            "fundamentals locked in. This free checklist is used by hundreds "
            "of Bubba Academy students to launch faster and avoid costly rookie mistakes."
        ),
        "bullets": [
            "Step-by-step product selection framework",
            "Supplier sourcing and negotiation checklist",
            "First FBA shipment prep guide",
        ],
        "button_text": "Download Free Checklist",
        "href":        "https://bubbaacademy.com",
        "offer":       "Free Amazon FBA Starter Checklist",
    },
    "conversion": {
        "icon":        "🚀",
        "headline":    "Join Bubba Academy — Build Your Amazon Business the Right Way",
        "body":        (
            "You've learned the concepts. Now build the business. "
            "Bubba Academy gives you a complete step-by-step roadmap — "
            "from finding your first product to your first profitable sale on Amazon."
        ),
        "bullets": [
            "Complete FBA launch roadmap from zero to first sale",
            "Private community of active Amazon sellers",
            "Live coaching and expert support",
        ],
        "button_text": "Join Bubba Academy Today",
        "href":        "https://bubbaacademy.com",
    },
    # Legacy keys — kept for backward compat with older exports
    "course_offer": {
        "icon":        "🎓",
        "headline":    "Start Your Amazon Business with Bubba Academy",
        "body":        "Join hundreds of students learning to build real Amazon FBA businesses.",
        "bullets":     [],
        "button_text": "Join Bubba Academy",
        "href":        "https://bubbaacademy.com",
    },
    "newsletter": {
        "icon":        "📧",
        "headline":    "Get Weekly Amazon Tips — Free",
        "body":        "Practical e-commerce strategies delivered every week. No fluff, no spam.",
        "bullets":     [],
        "button_text": "Subscribe Free",
        "href":        "https://bubbaacademy.com",
    },
}

# ── Contextual Mid-Article CTA ─────────────────────────────────────────────────
# Matched by article keyword fragment (first match wins, then "default").
# Each entry includes: icon, headline, body, bullets (max 3), button_text, href.

MID_CTA_CONFIGS = {
    "fee": {
        "icon":        "💰",
        "headline":    "Stop Losing Money to Hidden FBA Fees",
        "body":        (
            "Most new sellers don't fail because of bad products — they fail "
            "because they never calculated their fees correctly. Bubba Academy "
            "shows you exactly which numbers to run before you place your first "
            "order, so you protect your margins from day one."
        ),
        "bullets": [
            "Calculate your true profit before you source anything",
            "Understand every fee Amazon charges — no surprises",
            "Build a pricing strategy that holds margin at scale",
        ],
        "button_text": "Show Me the Real Numbers",
        "href":        "https://bubbaacademy.com",
    },
    "cost": {
        "icon":        "📊",
        "headline":    "Cut Your Amazon Costs Before They Cut Your Profits",
        "body":        (
            "Successful FBA sellers don't guess at their costs — they calculate "
            "them precisely before sourcing anything. Bubba Academy teaches you "
            "how to build a profitable cost structure and protect your margins "
            "on every product you sell."
        ),
        "bullets": [
            "True landed cost calculation (product + shipping + fees)",
            "Margin targets that actually work for FBA",
            "How to price competitively without losing money",
        ],
        "button_text": "Learn Cost Control",
        "href":        "https://bubbaacademy.com",
    },
    "shipping": {
        "icon":        "🚚",
        "headline":    "Never Get Your FBA Shipment Rejected Again",
        "body":        (
            "Inbound shipping mistakes cost new sellers hundreds of dollars in "
            "rejected inventory and re-prep fees. Bubba Academy walks you through "
            "every step of the FBA shipping process so your inventory arrives "
            "on time, correctly labeled, and accepted."
        ),
        "bullets": [
            "Create shipping plans that Amazon accepts first time",
            "Choose the right carrier for SPD vs LTL shipments",
            "Avoid labeling and packaging errors that cause rejections",
        ],
        "button_text": "Master FBA Shipping",
        "href":        "https://bubbaacademy.com",
    },
    "inbound": {
        "icon":        "📬",
        "headline":    "Send Your First FBA Shipment Without Costly Mistakes",
        "body":        (
            "From creating a shipping plan to choosing carriers and prepping "
            "your products — the FBA inbound process has many moving parts. "
            "Bubba Academy breaks it all down so your first shipment goes "
            "through without rejection or delay."
        ),
        "bullets": [
            "Step-by-step shipping plan creation in Seller Central",
            "SPD vs LTL — which to choose and when",
            "Product prep and labeling requirements explained simply",
        ],
        "button_text": "Learn the FBA Shipping Process",
        "href":        "https://bubbaacademy.com",
    },
    "storage": {
        "icon":        "💸",
        "headline":    "Stop Letting Storage Fees Quietly Drain Your Profits",
        "body":        (
            "Storage fees are one of the most overlooked profit killers in "
            "Amazon FBA. Bubba Academy teaches you how to forecast demand, "
            "move slow-moving stock before surcharges hit, and build an "
            "inventory strategy that keeps fees to an absolute minimum."
        ),
        "bullets": [
            "Forecast reorder points to avoid overstocking",
            "Move slow stock with removal orders and promotions",
            "Avoid aged inventory surcharges with a clearance system",
        ],
        "button_text": "Manage My Inventory Smarter",
        "href":        "https://bubbaacademy.com",
    },
    "warehouse": {
        "icon":        "💸",
        "headline":    "Stop Letting Storage Fees Quietly Drain Your Profits",
        "body":        (
            "Storage fees are one of the most overlooked profit killers in "
            "Amazon FBA. Bubba Academy teaches you how to forecast demand, "
            "move slow-moving stock before surcharges hit, and build an "
            "inventory strategy that keeps fees to an absolute minimum."
        ),
        "bullets": [
            "Forecast reorder points to avoid overstocking",
            "Move slow stock with removal orders and promotions",
            "Avoid aged inventory surcharges with a clearance system",
        ],
        "button_text": "Manage My Inventory Smarter",
        "href":        "https://bubbaacademy.com",
    },
    "product": {
        "icon":        "🔍",
        "headline":    "Find Your First Winning Amazon Product — Without Guessing",
        "body":        (
            "Product research is where most beginners make expensive mistakes. "
            "Bubba Academy gives you a proven, data-driven framework so you can "
            "identify profitable, low-competition products with real confidence."
        ),
        "bullets": [
            "Spot winning products before your competitors do",
            "Validate demand and competition using real data",
            "Avoid saturated markets and thin-margin traps",
        ],
        "button_text": "Find a Winning Product",
        "href":        "https://bubbaacademy.com",
    },
    "default": {
        "icon":        "📈",
        "headline":    "Ready to Build a Profitable Amazon FBA Business?",
        "body":        (
            "You now know the theory. It's time to build something real. "
            "Bubba Academy's step-by-step program takes you from zero to your "
            "first sale on Amazon — even if you've never sold anything online before."
        ),
        "bullets": [
            "Complete roadmap from product research to first sale",
            "Avoid the mistakes that waste time and money for beginners",
            "Learn from sellers who have actually built FBA businesses",
        ],
        "button_text": "Start with Bubba Academy",
        "href":        "https://bubbaacademy.com",
    },
}
