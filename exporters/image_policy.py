"""
image_policy.py — All selection thresholds, hard gates, and quality constants.

Single source of truth for image selection rules.
Change here to change behavior system-wide.

REUSE IN OTHER PROJECTS
-----------------------
Copy this file and adjust thresholds, blocked sets, and category constants.
No business logic lives here — only constants.
"""
from __future__ import annotations

# ── Image status ──────────────────────────────────────────────────────────────
STATUS_APPROVED     = "approved"
STATUS_DISABLED     = "disabled"
STATUS_NEEDS_REVIEW = "needs_review"

# ── Image roles ───────────────────────────────────────────────────────────────
ROLE_HERO    = "hero"
ROLE_SECTION = "section"
ROLE_CTA     = "cta"

# ── Category identifiers ──────────────────────────────────────────────────────
CAT_FBA_LOGISTICS        = "fba_logistics"
CAT_AMAZON_ADS           = "amazon_ads_digital"
CAT_PRODUCT_RESEARCH     = "product_research"
CAT_LISTING_OPTIMIZATION = "listing_optimization"
CAT_AMAZON_COMPLIANCE    = "amazon_compliance"
CAT_PRIVATE_LABEL        = "private_label_branding"
CAT_AMAZON_FOUNDATION    = "amazon_business_foundation"

# ── Scoring thresholds ────────────────────────────────────────────────────────
# All images must pass quality gate first; then relevance gates.
QUALITY_THRESHOLD: float    = 0.70   # minimum quality_score in catalog entry
RELEVANCE_THRESHOLD: float  = 0.35   # minimum composite score after penalties
TAG_RECALL_FLOOR: float     = 0.20   # min fraction of context words matched by tags
DESC_RECALL_FLOOR: float    = 0.15   # min fraction of context words matched by description

# ── Penalty constants ─────────────────────────────────────────────────────────
NOISE_PENALTY: float          = 0.10   # per noise tag (generic/lifestyle)
MAX_NOISE_PENALTY: float      = 0.20   # cap on total noise penalty
VISUAL_CLUSTER_PENALTY: float = 0.15   # penalty for reusing same visual_cluster in same post
CROSS_POST_CLUSTER_PENALTY_PER_USE: float = 0.05   # per prior-post use of cluster
MAX_CROSS_POST_CLUSTER_PENALTY: float     = 0.20   # cap

# ── Article image budget ──────────────────────────────────────────────────────
MAX_SECTION_IMAGES: int = 2   # maximum section images inserted per article
# Hero is 0 or 1 — omit rather than insert a weak image
# CTA slots are fixed at 3 per article

# ── Stopwords — filtered from context before recall scoring ──────────────────
# Conservative: only function words. Business terms ("reduce", "optimize") kept.
STOPWORDS: frozenset = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "into", "onto", "your", "their", "its", "this",
    "that", "these", "those", "up", "out", "as", "if", "it", "not", "no",
    "per", "also", "each", "all", "any", "both", "can", "may", "use",
    "used", "using", "how", "what", "why", "when", "which", "who", "our",
    "we", "you", "they", "them", "then", "than", "about", "after", "before",
})

# ── Hard-blocked tags — any match → immediate REJECTED_BLOCKED_TAG ────────────
# These tags indicate content that is never acceptable in a business blog.
BLOCKED_TAGS: frozenset = frozenset({
    # Food / kitchen
    "food", "meal", "cooking", "kitchen", "vegetable", "vegetables",
    "onion", "onions", "fruit", "fruits", "grocery", "groceries",
    "ingredient", "ingredients", "recipe", "restaurant", "plate",
    "bowl", "eat", "eating", "chef", "spice", "spices", "salad",
    "bread", "meat", "sauce", "soup", "snack", "drink",
    # Lifestyle / non-professional people
    "portrait", "selfie", "friends", "couple", "family", "children",
    "kids", "baby", "casual", "hanging", "laughing", "smiling",
    "coffee", "cafe", "bar", "party", "vacation", "beach", "leisure",
    "lifestyle", "fashion", "model", "dating", "romance", "love",
    "entertainment", "social",
    # Nature / non-business environments
    "nature", "landscape", "forest", "mountain", "river", "lake",
    "sky", "sunset", "sunrise", "animal", "pet", "dog", "cat",
    "bird", "plant", "flower", "garden", "tree", "grass",
    # Abstract / artistic
    "abstract", "artistic", "art", "creative", "colorful",
    "wallpaper", "background", "texture", "pattern", "illustration",
    "drawing", "painting",
    # Fitness / medical
    "gym", "fitness", "workout", "exercise", "sport", "sports",
    "medical", "healthcare", "doctor", "hospital", "clinic",
    # Catch-all
    "random", "unrelated", "miscellaneous",
})

# ── Hard-blocked description words ────────────────────────────────────────────
# Applied to image description text — prevents mislabeled images.
BLOCKED_DESCRIPTION_WORDS: frozenset = frozenset({
    "onion", "onions", "food", "vegetable", "vegetables", "meal",
    "cooking", "kitchen", "restaurant", "fruit", "grocery",
    "portrait", "friends", "couple", "casual", "lifestyle",
    "fashion", "vacation", "beach", "nature", "landscape",
    "animal", "flower", "pet", "plant", "abstract", "gym", "workout",
})

# ── Noise tags — penalize but don't block ────────────────────────────────────
# Indicate generic/stock/consumer content that weakens relevance signal.
NOISE_TAGS: frozenset = frozenset({
    "isolated", "studio", "white background", "generic", "stock",
    "modern", "contemporary", "beautiful", "handsome", "cheerful",
    "happy", "male", "female", "man", "woman", "young", "old",
    "person", "individual", "indoors", "outdoors",
})
