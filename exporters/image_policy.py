"""
image_policy.py — All selection thresholds, gates, topic categories, and search queries.

Single source of truth for image selection rules.
Change here to change behavior system-wide.

REUSE IN OTHER PROJECTS
-----------------------
Copy this file and adjust thresholds, blocked sets, category constants, and
TOPIC_SEARCH_QUERIES for your specific vertical.
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
# Primary topic categories — used for routing and Pexels query selection.
CAT_FBA_LOGISTICS        = "fba_logistics"
CAT_AMAZON_ADS           = "amazon_ads_digital"
CAT_AI_TOOLS             = "ai_tools_automation"
CAT_PRODUCT_RESEARCH     = "product_research"
CAT_SOURCING             = "sourcing_supply_chain"
CAT_LISTING_OPTIMIZATION = "listing_optimization"
CAT_ECOM_STRATEGY        = "ecommerce_strategy"
CAT_BRAND_BUILDING       = "brand_building"
CAT_AMAZON_COMPLIANCE    = "amazon_compliance"
CAT_PRIVATE_LABEL        = "private_label_branding"
CAT_AMAZON_FOUNDATION    = "amazon_business_foundation"
CAT_GENERAL_BUSINESS     = "general_business"

# ── Static-catalog scoring thresholds (CTA images) ───────────────────────────
QUALITY_THRESHOLD: float    = 0.70   # minimum quality_score in catalog entry
RELEVANCE_THRESHOLD: float  = 0.35   # minimum composite score after penalties
TAG_RECALL_FLOOR: float     = 0.20   # min fraction of context words matched by tags
DESC_RECALL_FLOOR: float    = 0.15   # min fraction of context words matched by description

# ── Penalty constants (CTA static scoring) ───────────────────────────────────
NOISE_PENALTY: float          = 0.10
MAX_NOISE_PENALTY: float      = 0.20
VISUAL_CLUSTER_PENALTY: float = 0.15
CROSS_POST_CLUSTER_PENALTY_PER_USE: float = 0.05
MAX_CROSS_POST_CLUSTER_PENALTY: float     = 0.20

# ── Article image budget ──────────────────────────────────────────────────────
MAX_SECTION_IMAGES: int = 2   # maximum section images per article

# ── Pexels API fetch settings ─────────────────────────────────────────────────
PEXELS_RESULTS_PER_QUERY: int   = 15   # images fetched per Pexels query
PEXELS_QUERIES_PER_SLOT:  int   = 3    # max queries run per image slot
PEXELS_MIN_SCORE:         float = 0.10  # minimum score for a fetched image to be selected

# ── Stopwords — filtered from context before recall scoring ──────────────────
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

# ── Hard-blocked tags — any match → immediate rejection ──────────────────────
# Applied to static catalog entry tags AND fetched image alt text.
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
BLOCKED_DESCRIPTION_WORDS: frozenset = frozenset({
    "onion", "onions", "food", "vegetable", "vegetables", "meal",
    "cooking", "kitchen", "restaurant", "fruit", "grocery",
    "portrait", "friends", "couple", "casual", "lifestyle",
    "fashion", "vacation", "beach", "nature", "landscape",
    "animal", "flower", "pet", "plant", "abstract", "gym", "workout",
})

# ── Noise tags — penalize but don't block (CTA static scoring) ───────────────
NOISE_TAGS: frozenset = frozenset({
    "isolated", "studio", "white background", "generic", "stock",
    "modern", "contemporary", "beautiful", "handsome", "cheerful",
    "happy", "male", "female", "man", "woman", "young", "old",
    "person", "individual", "indoors", "outdoors",
})

# ── Per-topic negative alt-text terms ────────────────────────────────────────
# Applied to Pexels API results for each topic category.
# Words found in Pexels image alt text → image rejected for this topic.
# Supplements the global BLOCKED_TAGS above.
TOPIC_NEGATIVE_TERMS: dict = {
    CAT_AMAZON_ADS: frozenset({
        "warehouse", "forklift", "pallet", "boxes", "shipping",
        "factory", "manufacturing", "construction",
        "food", "cooking", "nature", "landscape", "beach",
    }),
    CAT_AI_TOOLS: frozenset({
        "warehouse", "forklift", "pallet", "boxes", "shipping",
        "factory", "manufacturing", "construction",
        "restaurant", "food", "cooking",
        "nature", "landscape", "forest", "beach",
    }),
    CAT_FBA_LOGISTICS: frozenset({
        "restaurant", "food", "cooking", "nature", "landscape",
        "coffee", "lifestyle", "fashion", "beach",
        "digital marketing", "dashboard screen", "software interface",
    }),
    CAT_PRODUCT_RESEARCH: frozenset({
        "warehouse", "shipping", "forklift", "food", "cooking",
        "nature", "lifestyle", "beach",
    }),
    CAT_SOURCING: frozenset({
        "food", "cooking", "lifestyle", "fashion", "nature",
        "restaurant", "beach",
    }),
    CAT_LISTING_OPTIMIZATION: frozenset({
        "warehouse", "shipping", "forklift", "food", "cooking",
        "nature", "lifestyle", "beach",
    }),
    CAT_ECOM_STRATEGY: frozenset({
        "warehouse", "factory", "food", "cooking", "nature",
        "lifestyle", "beach",
    }),
    CAT_BRAND_BUILDING: frozenset({
        "warehouse", "factory", "food", "cooking", "nature",
        "lifestyle", "beach",
    }),
    CAT_AMAZON_COMPLIANCE: frozenset({
        "food", "cooking", "nature", "lifestyle", "fashion", "beach",
    }),
    CAT_PRIVATE_LABEL: frozenset({
        "food", "cooking", "nature", "lifestyle", "beach",
    }),
    CAT_AMAZON_FOUNDATION: frozenset({
        "warehouse", "factory", "food", "cooking", "nature",
        "lifestyle", "beach",
    }),
    CAT_GENERAL_BUSINESS: frozenset({
        "food", "cooking", "nature", "lifestyle", "fashion", "beach",
    }),
}

# ── Topic search queries — Pexels API search strings per category ─────────────
# Ordered best-to-worst (most specific / business-relevant first).
# image_router.get_search_queries() picks from this list and may prepend
# a section-heading-derived query for even more specificity.
TOPIC_SEARCH_QUERIES: dict = {
    CAT_AMAZON_ADS: [
        "amazon advertising campaign analytics dashboard",
        "ppc digital marketing performance metrics",
        "ecommerce advertising optimization analytics",
        "pay per click campaign strategy business",
        "online advertising budget management",
        "digital marketing ROI analytics dashboard",
    ],
    CAT_AI_TOOLS: [
        "artificial intelligence business software dashboard",
        "AI ecommerce automation analytics interface",
        "business intelligence data analytics dashboard",
        "machine learning business analytics platform",
        "ecommerce technology automation software",
        "AI tools online seller business dashboard",
    ],
    CAT_FBA_LOGISTICS: [
        "amazon warehouse fulfillment center operations",
        "ecommerce fulfillment shipping boxes logistics",
        "inventory management warehouse operations",
        "package shipping courier delivery logistics",
        "supply chain warehouse fulfillment",
        "fulfillment center inventory management",
    ],
    CAT_PRODUCT_RESEARCH: [
        "ecommerce product research analytics data",
        "market research business data analytics",
        "product analysis business growth strategy",
        "competitive research ecommerce data",
        "product discovery business analytics dashboard",
        "market analysis research strategy",
    ],
    CAT_SOURCING: [
        "manufacturing factory production operations",
        "supply chain logistics management operations",
        "product sourcing supplier quality control",
        "factory production quality manufacturing",
        "wholesale supplier business operations",
        "supply chain management global sourcing",
    ],
    CAT_LISTING_OPTIMIZATION: [
        "ecommerce product listing optimization",
        "product photography professional ecommerce",
        "online product page conversion optimization",
        "ecommerce SEO content strategy",
        "product listing professional business",
        "online seller optimization analytics",
    ],
    CAT_ECOM_STRATEGY: [
        "ecommerce business strategy growth analytics",
        "online business revenue growth dashboard",
        "entrepreneur business planning strategy",
        "ecommerce growth strategy analytics",
        "business profit analytics performance",
        "online retail business strategy success",
    ],
    CAT_BRAND_BUILDING: [
        "brand building business marketing strategy",
        "professional branding design business",
        "brand identity business development marketing",
        "business brand marketing growth strategy",
        "brand development professional business",
        "brand strategy marketing professional",
    ],
    CAT_AMAZON_COMPLIANCE: [
        "business compliance strategy professional",
        "legal business documentation compliance",
        "corporate compliance management business",
        "business risk management strategy professional",
        "policy compliance business management",
    ],
    CAT_PRIVATE_LABEL: [
        "private label product packaging design",
        "product branding custom packaging design",
        "brand product development packaging",
        "custom product packaging business brand",
        "private label brand business product",
    ],
    CAT_AMAZON_FOUNDATION: [
        "ecommerce business startup strategy growth",
        "online business entrepreneur success",
        "ecommerce seller getting started strategy",
        "online business growth startup planning",
        "business foundation strategy success",
    ],
    CAT_GENERAL_BUSINESS: [
        "professional business analytics dashboard",
        "ecommerce business growth strategy",
        "business performance metrics analytics",
        "professional business planning strategy",
        "business success entrepreneur analytics",
    ],
}
