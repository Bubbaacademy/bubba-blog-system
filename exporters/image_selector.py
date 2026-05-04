"""
ImageSelector — strict 3-stage validation pipeline for Bubba Academy blog images.

PIPELINE OVERVIEW
-----------------
Every image candidate passes through three sequential gates.
Fail any gate → REJECTED. No fallback. No exceptions.

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  STAGE 1  HARD NEGATIVE FILTER                                          │
  │  • Any tag in BLOCKED_TAGS               → REJECT immediately           │
  │  • Any description word in               → REJECT immediately           │
  │    BLOCKED_DESCRIPTION_WORDS                                             │
  │  Catches: food, people, lifestyle, nature, abstract, fitness, medical.  │
  │  Runs before any scoring. Zero exceptions.                              │
  ├─────────────────────────────────────────────────────────────────────────┤
  │  STAGE 2  CATEGORY CONSISTENCY CHECK                                    │
  │  • image_category NOT in allowed_categories → REJECT                    │
  │  Example: warehouse image for PPC article → REJECTED even if tags match │
  │  This is enforced before validation is called (via _score_pool caller)  │
  │  AND explicitly checked inside _validate_candidate for belt-and-        │
  │  suspenders safety.                                                      │
  ├─────────────────────────────────────────────────────────────────────────┤
  │  STAGE 3  SEMANTIC RELEVANCE (SCORING + THRESHOLD)                      │
  │                                                                         │
  │  Formula: RECALL-BASED (context as denominator)                         │
  │    ctx_meaningful = context_words − STOPWORDS                           │
  │    tag_recall  = tag_matches  / len(ctx_meaningful)                     │
  │    desc_recall = desc_matches / len(ctx_meaningful)                     │
  │    composite   = tag_recall × 0.70 + desc_recall × 0.30                │
  │                                                                         │
  │  WHY RECALL-BASED: With ~10 tags and typical FBA contexts (4-7 key     │
  │  words), precision (matches/total_tags) tops out at 0.30 — structurally │
  │  impossible to reach 0.35. Recall (matches/context_words) correctly     │
  │  measures "how much of the article's topic does this image cover?"       │
  │  and reaches 0.50+ for well-matched images.                             │
  │                                                                         │
  │  THREE requirements — ALL must pass:                                    │
  │    1. tag_recall  ≥ TAG_RECALL_FLOOR  (0.25)                           │
  │    2. desc_recall ≥ DESC_RECALL_FLOOR (0.20)                           │
  │    3. composite   ≥ RELEVANCE_THRESHOLD (0.35) after noise penalty      │
  │                                                                         │
  │  Anti-corruption penalty: −NOISE_PENALTY per NOISE_TAG present          │
  │  (tags indicating generic/lifestyle content, not outright blocked).     │
  │  Max penalty: MAX_NOISE_PENALTY. Applied before threshold check.        │
  └─────────────────────────────────────────────────────────────────────────┘

  CTA images bypass Stages 2 & 3. They are decorative, topic-independent
  accents. Stage 1 (hard negative filter) still applies to them.

CATEGORY ARCHITECTURE
---------------------
  warehouse_logistics  — fulfillment centers, racking, workers, inventory
  shipping_freight     — delivery vans, containers, loading docks, couriers
  packaging_prep       — cardboard boxes, taping, labeling, FBA prep
  amazon_ads_digital   — [PLACEHOLDER] dashboards, analytics screenshots
                         Currently EMPTY. PPC articles map here → None.
                         Add verified Pexels IDs to unlock PPC section images.

ADDING NEW IMAGES — MANDATORY PROCESS
--------------------------------------
  1. Open: https://images.pexels.com/photos/{ID}/pexels-photo-{ID}.jpeg
  2. Confirm: professional, business-context, zero food/people/lifestyle content
  3. Write a BUSINESS-CONTEXT description (not just a visual description).
     Example of WRONG desc: "person standing near boxes"
     Example of RIGHT desc:  "amazon fba prep worker packing boxes for inbound shipment"
     Right descriptions include actual business keywords — they drive desc_recall.
  4. Write tags: combine visual accuracy + business-context keywords.
     Include "amazon" and "fba" (appear in most article contexts), plus
     category-specific terms that appear in section titles for this topic.
  5. Run smoke-test to confirm the new ID is in APPROVED_PEXELS_IDS.

FAIL-SAFE
---------
  No valid image after all stages → return None.
  Caller (hubspot._build_post_body) skips the image slot.
  Missing image is ACCEPTABLE. Wrong image is a SYSTEM FAILURE.
"""

from __future__ import annotations

import os
import re
import logging
from collections import Counter

log = logging.getLogger("image_selector")

PEXELS_BASE = "https://images.pexels.com/photos"


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 1 — HARD NEGATIVE FILTER
# ══════════════════════════════════════════════════════════════════════════════
#
# Any match in an image's tags or description → REJECTED. No exceptions.

BLOCKED_TAGS: frozenset = frozenset({
    # ── Food / kitchen ────────────────────────────────────────────────────────
    "food", "meal", "cooking", "kitchen", "vegetable", "vegetables",
    "onion", "onions", "fruit", "fruits", "grocery", "groceries",
    "ingredient", "ingredients", "recipe", "restaurant", "plate",
    "bowl", "eat", "eating", "chef", "spice", "spices", "salad",
    "bread", "meat", "sauce", "soup", "snack", "drink",
    # ── Lifestyle / people ────────────────────────────────────────────────────
    "portrait", "selfie", "friends", "couple", "family", "children",
    "kids", "baby", "casual", "hanging", "sitting", "laughing",
    "smiling", "coffee", "cafe", "bar", "party", "vacation",
    "beach", "leisure", "lifestyle", "fashion", "model", "dating",
    "romance", "love", "entertainment", "social",
    # ── Nature / non-business ─────────────────────────────────────────────────
    "nature", "landscape", "forest", "mountain", "river", "lake",
    "sky", "sunset", "sunrise", "animal", "pet", "dog", "cat",
    "bird", "plant", "flower", "garden", "tree", "grass",
    # ── Abstract / artistic ───────────────────────────────────────────────────
    "abstract", "artistic", "art", "creative", "colorful",
    "wallpaper", "background", "texture", "pattern", "illustration",
    "drawing", "painting",
    # ── Fitness / medical ─────────────────────────────────────────────────────
    "gym", "fitness", "workout", "exercise", "sport", "sports",
    "medical", "healthcare", "doctor", "hospital", "clinic",
    # ── Catch-all ─────────────────────────────────────────────────────────────
    "random", "unrelated", "miscellaneous",
})

BLOCKED_DESCRIPTION_WORDS: frozenset = frozenset({
    "onion", "onions", "food", "vegetable", "vegetables", "meal",
    "cooking", "kitchen", "restaurant", "fruit", "grocery",
    "portrait", "friends", "couple", "casual", "lifestyle",
    "fashion", "vacation", "beach", "nature", "landscape",
    "animal", "flower", "pet", "plant", "abstract", "gym", "workout",
})

# ── Anti-corruption penalty tags ──────────────────────────────────────────────
# Not outright blocked, but signal generic/consumer/stock content.
# Each match reduces composite score by NOISE_PENALTY (max MAX_NOISE_PENALTY).
NOISE_TAGS: frozenset = frozenset({
    "isolated",         # object on white/transparent background
    "studio",           # artificial studio setup (not real-world)
    "white background", # product photography setup
    "generic",          # acknowledged vague content
    "stock",            # generic stock imagery
    "modern",           # purely aesthetic label, no topic signal
    "contemporary",     # same
    "beautiful",        # lifestyle aesthetic, no business signal
    "handsome",         # lifestyle
    "cheerful",         # lifestyle emotion
    "happy",            # lifestyle emotion (blocked separately? No — just penalize)
    "male", "female",   # demographic segmentation (off-brand for business)
    "man", "woman",     # same
    "young", "old",     # demographic
    "person",           # single-person tag without work context
    "individual",       # same
    "indoors",          # non-specific setting
    "outdoors",         # non-specific setting
})

NOISE_PENALTY: float     = 0.10   # per matched noise tag
MAX_NOISE_PENALTY: float = 0.20   # total penalty cap


# ══════════════════════════════════════════════════════════════════════════════
# STAGE 3 — SCORING THRESHOLDS
# ══════════════════════════════════════════════════════════════════════════════

# Common English words that add no topic signal — filtered from context before scoring.
# Conservative set: business terms like "reduce", "calculate", "manage" are NOT removed.
STOPWORDS: frozenset = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "into", "onto", "your", "their", "its", "this",
    "that", "these", "those", "up", "out", "as", "if", "it", "not", "no",
    "per", "also", "each", "all", "any", "both", "can",
})

TAG_RECALL_FLOOR: float  = 0.25   # fraction of meaningful context words covered by tags
DESC_RECALL_FLOOR: float = 0.20   # fraction of meaningful context words covered by description
RELEVANCE_THRESHOLD: float = 0.35  # minimum composite score (after noise penalty)


# ══════════════════════════════════════════════════════════════════════════════
# VERIFIED IMAGE LIBRARY
# ══════════════════════════════════════════════════════════════════════════════
#
# Format: (pexels_id, business_context_description, [relevance_tags])
#
# DESCRIPTION: Write a BUSINESS-CONTEXT description, not a visual one.
#   Include the actual business keywords that appear in article contexts.
#   This drives desc_recall — the second required signal in Stage 3 scoring.
#
# TAGS: Combine visual accuracy with business-context keywords.
#   Always include "amazon" and "fba" (appear in most contexts).
#   Add category-specific terms that appear in section titles for this topic.
#   Tags drive tag_recall — the primary signal in Stage 3 scoring.

_SECTION_LIBRARY: dict = {

    "warehouse_logistics": [

        ("4481323",
         "amazon fba warehouse workers managing inventory storage fulfillment",
         ["warehouse", "fba", "amazon", "inventory", "storage", "fulfillment",
          "workers", "logistics", "distribution", "ecommerce"]),

        ("6169668",
         "amazon fba warehouse team reviewing inventory management fulfillment",
         ["warehouse", "fba", "amazon", "inventory", "fulfillment", "team",
          "management", "workers", "logistics", "distribution"]),
    ],

    "shipping_freight": [

        ("3584942",
         "amazon fba inbound shipping couriers loading delivery boxes logistics",
         ["shipping", "fba", "amazon", "inbound", "delivery", "boxes",
          "courier", "logistics", "shipment", "distribution"]),

        ("6169661",
         "amazon fba inbound shipping workers loading boxes for delivery",
         ["shipping", "fba", "amazon", "inbound", "loading", "boxes",
          "delivery", "logistics", "shipment", "van"]),

        ("906494",
         "international freight shipping containers port amazon import logistics",
         ["freight", "shipping", "amazon", "import", "containers", "port",
          "logistics", "export", "international", "supply"]),

        ("2226458",
         "aerial freight shipping containers cargo amazon import export logistics",
         ["freight", "shipping", "amazon", "import", "containers", "aerial",
          "logistics", "export", "international", "cargo"]),
    ],

    "packaging_prep": [

        ("4246120",
         "amazon fba packaging prep sealing cardboard box for inbound shipment",
         ["packaging", "fba", "amazon", "prep", "sealing", "boxes",
          "shipment", "packing", "fulfillment", "inbound"]),

        ("4246123",
         "amazon fba packaging prep packing taping cardboard boxes fulfillment",
         ["packaging", "fba", "amazon", "prep", "packing", "boxes",
          "taping", "fulfillment", "shipment", "inbound"]),

        ("4246119",
         "amazon fba inventory stacked labeled boxes ready shipping delivery",
         ["packaging", "fba", "amazon", "inventory", "boxes", "labeled",
          "shipping", "delivery", "preparation", "inbound"]),
    ],

    # ── Placeholder: verified dashboard/analytics screenshots go here ─────────
    # PPC/ads articles map to this category. Currently empty → returns None.
    # When IDs are added here, PPC articles automatically receive relevant visuals.
    "amazon_ads_digital": [],
}

# CTA images — topic-independent professional warehouse images for CTA banners.
# MUST have ≥ 3 entries (one per CTA slot) to avoid duplicate URLs in postBody.
# Stage 1 validation still applies. Stages 2 & 3 are bypassed (decorative only).
_CTA_POOL: list = [
    ("4483610",
     "amazon fba fulfillment center wide warehouse interior stocked shelves",
     ["warehouse", "shelves", "inventory", "fulfillment", "fba", "amazon",
      "storage", "professional", "ecommerce", "distribution"]),
    ("4481326",
     "amazon fba warehouse organized racking pallets inventory storage",
     ["warehouse", "shelves", "pallets", "organized", "professional",
      "fba", "amazon", "inventory", "fulfillment", "logistics"]),
    ("4481259",
     "amazon fba warehouse team organizing inventory pallets fulfillment",
     ["warehouse", "pallets", "inventory", "team", "fulfillment",
      "professional", "fba", "amazon", "distribution", "logistics"]),
]

# Remove CTA IDs from section pools (belt-and-suspenders)
_CTA_IDS: set = {entry[0] for entry in _CTA_POOL}
for _cat in list(_SECTION_LIBRARY.keys()):
    _SECTION_LIBRARY[_cat] = [
        e for e in _SECTION_LIBRARY[_cat] if e[0] not in _CTA_IDS
    ]

# Full approved set — used by hubspot_api validator
APPROVED_PEXELS_IDS: set = (
    {e[0] for pool in _SECTION_LIBRARY.values() for e in pool}
    | {e[0] for e in _CTA_POOL}
)


# ══════════════════════════════════════════════════════════════════════════════
# TOPIC → CATEGORY MAP
# ══════════════════════════════════════════════════════════════════════════════
# Longest-key-first matching: "amazon fba storage fees" matches "storage fees"
# before shorter "fba" can fire.
# [] = no section images (no matching visual category exists yet).
# Empty category pool (like amazon_ads_digital) also produces None — correct.

TOPIC_CATEGORY_MAP: dict = {
    # ── FBA storage / inventory ───────────────────────────────────────────────
    "storage fees":           ["warehouse_logistics", "packaging_prep"],
    "warehouse storage":      ["warehouse_logistics"],
    "inventory management":   ["warehouse_logistics", "packaging_prep"],
    "long-term storage":      ["warehouse_logistics"],
    "inventory":              ["warehouse_logistics", "packaging_prep"],
    "storage":                ["warehouse_logistics", "packaging_prep"],
    "warehouse":              ["warehouse_logistics"],
    "fulfillment":            ["warehouse_logistics", "shipping_freight"],
    "fba fees":               ["warehouse_logistics", "shipping_freight"],
    "fba fee":                ["warehouse_logistics", "shipping_freight"],
    "fba cost":               ["warehouse_logistics", "shipping_freight"],
    "fba":                    ["warehouse_logistics", "shipping_freight", "packaging_prep"],
    "amazon fees":            ["warehouse_logistics", "shipping_freight"],
    # ── Shipping / inbound ────────────────────────────────────────────────────
    "inbound shipping":       ["shipping_freight", "packaging_prep"],
    "shipping cost":          ["shipping_freight", "packaging_prep"],
    "shipping plan":          ["shipping_freight", "packaging_prep"],
    "inbound":                ["shipping_freight", "packaging_prep"],
    "shipping":               ["shipping_freight", "packaging_prep"],
    "freight":                ["shipping_freight"],
    "ltl":                    ["shipping_freight"],
    "carrier":                ["shipping_freight"],
    # ── Sourcing / product ────────────────────────────────────────────────────
    "product research":       ["warehouse_logistics", "packaging_prep"],
    "product launch":         ["warehouse_logistics", "shipping_freight"],
    "private label":          ["warehouse_logistics", "packaging_prep"],
    "sourcing":               ["warehouse_logistics", "packaging_prep"],
    "supplier":               ["warehouse_logistics", "packaging_prep"],
    "product":                ["warehouse_logistics", "packaging_prep"],
    "toys":                   ["warehouse_logistics", "packaging_prep"],
    "listing":                ["warehouse_logistics"],
    "launch":                 ["warehouse_logistics", "shipping_freight"],
    # ── PPC / ads → ads_digital (empty pool → None until images added) ────────
    "amazon ppc":             ["amazon_ads_digital"],
    "amazon ads":             ["amazon_ads_digital"],
    "sponsored product":      ["amazon_ads_digital"],
    "sponsored brands":       ["amazon_ads_digital"],
    "sponsored display":      ["amazon_ads_digital"],
    "sponsored":              ["amazon_ads_digital"],
    "top of the search":      ["amazon_ads_digital"],
    "top of search":          ["amazon_ads_digital"],
    "placement adjustment":   ["amazon_ads_digital"],
    "bid adjustment":         ["amazon_ads_digital"],
    "placement bid":          ["amazon_ads_digital"],
    "campaign":               ["amazon_ads_digital"],
    "acos":                   ["amazon_ads_digital"],
    "roas":                   ["amazon_ads_digital"],
    "ppc":                    ["amazon_ads_digital"],
    "advertising":            ["amazon_ads_digital"],
    "search term":            ["amazon_ads_digital"],
    "keyword research":       ["amazon_ads_digital"],
    "keyword":                ["amazon_ads_digital"],
    # ── No matching images yet ────────────────────────────────────────────────
    "profit margin":          [],
    "profit calculator":      [],
    "pricing strategy":       [],
    "a+ content":             [],
    "brand registry":         [],
    "review":                 [],
    "rank":                   [],
}


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _url(photo_id: str, width: int = 800) -> str:
    return (
        f"{PEXELS_BASE}/{photo_id}/pexels-photo-{photo_id}.jpeg"
        f"?auto=compress&cs=tinysrgb&w={width}"
    )


def _clean_words(text: str) -> set:
    """Lowercase, strip punctuation, return word set."""
    return set(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def _meaningful_words(text: str) -> set:
    """Clean words minus STOPWORDS — used as context denominator in recall scoring."""
    return _clean_words(text) - STOPWORDS


def _get_allowed_categories(article_keyword: str, topic_cluster: str) -> list:
    """
    Return allowed image categories for this article.
    Longest key wins (prevents short fragment matching before specific one).
    Returns [] on no match → no section images (safe default).
    """
    search_text = f"{article_keyword} {topic_cluster}".lower().strip()
    for fragment in sorted(TOPIC_CATEGORY_MAP.keys(), key=len, reverse=True):
        if fragment in search_text:
            cats = TOPIC_CATEGORY_MAP[fragment]
            log.info(
                f"[ImageSelector] topic='{fragment}' → "
                f"categories={cats if cats else 'NONE (section images skipped)'}"
            )
            return cats
    log.warning(
        f"[ImageSelector] No category match: keyword='{article_keyword}' "
        f"cluster='{topic_cluster}' — section images will be skipped"
    )
    return []


# ══════════════════════════════════════════════════════════════════════════════
# THREE-STAGE CANDIDATE VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

def _validate_candidate(
    entry: tuple,
    article_keyword: str,
    topic_cluster: str,
    section_context: str,
    image_category: str = "",
    allowed_categories: list | None = None,
    cta_mode: bool = False,
) -> tuple:
    """
    Run a candidate through the 3-stage validation pipeline.

    Args:
        entry              — (photo_id, description, tags) tuple
        article_keyword    — Main Keyword column value
        topic_cluster      — Topic Cluster column value
        section_context    — section heading text
        image_category     — which library category this image came from
        allowed_categories — categories allowed for this article
        cta_mode           — if True: only Stage 1 runs (CTAs are topic-independent)

    Returns:
        (approved: bool, reason: str, score: float)
    """
    photo_id, description, tags = entry

    # ── Stage 1: Hard negative filter ─────────────────────────────────────────
    tags_lower   = {t.lower() for t in tags}
    blocked_tag  = tags_lower & BLOCKED_TAGS
    if blocked_tag:
        return False, f"BLOCKED_TAG:{sorted(blocked_tag)}", 0.0

    desc_words_raw = set(description.lower().split())
    blocked_desc   = desc_words_raw & BLOCKED_DESCRIPTION_WORDS
    if blocked_desc:
        return False, f"BLOCKED_DESCRIPTION:{sorted(blocked_desc)}", 0.0

    # CTA mode: only Stage 1 needed — they're decorative, no topic-relevance required
    if cta_mode:
        return True, "APPROVED_CTA:stage1_only", 1.0

    # ── Stage 2: Category consistency ─────────────────────────────────────────
    # Belt-and-suspenders: caller already filters by category, but explicit check
    # ensures no warehouse image ever sneaks into a PPC article validation path.
    if allowed_categories is not None and image_category:
        if image_category not in allowed_categories:
            return (
                False,
                f"CATEGORY_MISMATCH:{image_category} not in {allowed_categories}",
                0.0,
            )

    # ── Stage 3: Semantic relevance scoring ───────────────────────────────────
    # RECALL-BASED formula: denominator = len(meaningful context words).
    # Measures "what fraction of the article's key topic words does this image cover?"
    # This is semantically correct and achieves 0.35+ for well-matched images.
    # See module docstring for why precision-based formula cannot reach 0.35.

    full_context  = f"{section_context} {article_keyword} {topic_cluster}"
    ctx_meaningful = _meaningful_words(full_context)
    ctx_size       = max(len(ctx_meaningful), 1)   # safe denominator

    # Tag recall: how many of the article's meaningful keywords appear in tags?
    tag_words   = _clean_words(" ".join(tags))
    tag_matches = len(tag_words & ctx_meaningful)
    tag_recall  = tag_matches / ctx_size

    # Description recall: same measurement on the business-context description
    desc_words   = _clean_words(description)
    desc_matches = len(desc_words & ctx_meaningful)
    desc_recall  = desc_matches / ctx_size

    # Anti-corruption penalty for noise/generic/lifestyle tags (not fully blocked
    # but indicative of non-business stock imagery)
    noise_count   = len(tags_lower & NOISE_TAGS)
    noise_penalty = min(MAX_NOISE_PENALTY, noise_count * NOISE_PENALTY)

    composite      = tag_recall * 0.70 + desc_recall * 0.30
    final_score    = round(composite - noise_penalty, 4)

    # Require BOTH signals AND composite threshold
    if tag_recall < TAG_RECALL_FLOOR:
        return (
            False,
            f"LOW_TAG_RECALL:tag_recall={tag_recall:.4f}<{TAG_RECALL_FLOOR}  "
            f"tag_matches={tag_matches}  ctx_size={ctx_size}  "
            f"score={final_score:.4f}",
            final_score,
        )
    if desc_recall < DESC_RECALL_FLOOR:
        return (
            False,
            f"LOW_DESC_RECALL:desc_recall={desc_recall:.4f}<{DESC_RECALL_FLOOR}  "
            f"desc_matches={desc_matches}  ctx_size={ctx_size}  "
            f"score={final_score:.4f}",
            final_score,
        )
    if final_score < RELEVANCE_THRESHOLD:
        return (
            False,
            f"LOW_COMPOSITE:score={final_score:.4f}<{RELEVANCE_THRESHOLD}  "
            f"(noise_penalty={noise_penalty:.2f}  raw={composite:.4f})",
            final_score,
        )

    matched_kws = sorted((tag_words | desc_words) & ctx_meaningful)
    return True, f"APPROVED:score={final_score:.4f}:matched={matched_kws}", final_score


# ══════════════════════════════════════════════════════════════════════════════
# GLOBAL IMAGE REGISTRY
# ══════════════════════════════════════════════════════════════════════════════
#
# PRIMARY: Google Sheets tab "Image Registry" — persists across Render deploys.
# FALLBACK: in-memory only — used when Sheets is unreachable (never blocks publish).
#
# This wrapper delegates to SheetsImageRegistry (see exporters/sheets_image_registry.py).
# It exists so the rest of image_selector.py never needs to know which backend is live.


class GlobalImageRegistry:
    """
    Thin facade over SheetsImageRegistry.

    Delegates is_globally_used() and register_post() to the Sheets backend.
    Falls back gracefully if Sheets is unavailable.
    """

    def __init__(self):
        try:
            from exporters.sheets_image_registry import get_sheets_registry
            self._backend = get_sheets_registry()
        except Exception as exc:
            log.warning(
                f"[ImageRegistry] Could not initialise Sheets backend ({exc}) — "
                "running in-memory dedup only for this session"
            )
            self._backend = None
        self._in_memory_used: set = set()

    def is_globally_used(self, photo_id: str) -> bool:
        if self._backend is not None:
            return self._backend.is_globally_used(photo_id)
        return photo_id in self._in_memory_used

    def register_post(self, slug: str, photo_ids: list):
        """
        Register section images used by this post.
        photo_ids — list of Pexels ID strings (section images only, not CTAs).
        """
        entries = [{"id": pid, "url": _url(pid), "type": "section"} for pid in photo_ids]
        self._in_memory_used.update(photo_ids)
        if self._backend is not None:
            self._backend.register_post(slug, entries)
        else:
            log.info(
                f"[ImageRegistry] (in-memory) Registered {len(photo_ids)} "
                f"image(s) for '{slug}': {photo_ids}"
            )

    def available_section_count(self) -> int:
        section_ids = {e[0] for pool in _SECTION_LIBRARY.values() for e in pool}
        used = (
            self._backend._used_ids if self._backend is not None
            else self._in_memory_used
        )
        return len(section_ids - used)

    def status_report(self) -> dict:
        section_ids = {e[0] for pool in _SECTION_LIBRARY.values() for e in pool}
        used = (
            self._backend._used_ids if self._backend is not None
            else self._in_memory_used
        )
        connected = self._backend._connected if self._backend is not None else False
        return {
            "total_section_images": len(section_ids),
            "globally_used":        len(used & section_ids),
            "available":            self.available_section_count(),
            "connected":            connected,
            "backend":              "Google Sheets" if connected else "in-memory",
        }


_global_registry: GlobalImageRegistry | None = None


def get_global_registry() -> GlobalImageRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = GlobalImageRegistry()
        rpt = _global_registry.status_report()
        log.info(
            f"[ImageRegistry] {rpt['globally_used']}/{rpt['total_section_images']} "
            f"section images used globally — {rpt['available']} available  "
            f"backend={rpt['backend']}"
        )
        if rpt["available"] < 4:
            log.warning(
                f"[ImageRegistry] Only {rpt['available']} section images remain. "
                "Add more verified images to image_selector.py."
            )
    return _global_registry


# ══════════════════════════════════════════════════════════════════════════════
# IMAGE TRACKER (per-post)
# ══════════════════════════════════════════════════════════════════════════════

class ImageTracker:
    """
    Per-post image selection pipeline.

    Selection order (priority):
      1. Category match   — only images in article's allowed categories
      2. All 3 validation stages — Stage 1 hard filter, Stage 2 category check,
                                    Stage 3 dual-signal + composite threshold
      3. Best score       — highest composite score across all allowed categories
      4. Local dedup      — no URL reused within this post
      5. Global dedup     — no section image ID reused across posts

    Returns None when no candidate survives. Caller skips the image slot.
    Never falls back to a wrong or low-quality image.
    """

    def __init__(self, article_keyword: str = "", article_topic_cluster: str = ""):
        self._keyword: str             = article_keyword
        self._cluster: str             = article_topic_cluster
        self._used_urls: set           = set()
        self._section_ids: list        = []
        self._registry                 = get_global_registry()
        self._allowed_categories: list = _get_allowed_categories(
            article_keyword, article_topic_cluster
        )

    # ── Internal: score all candidates in a pool ──────────────────────────────

    def _score_pool(
        self,
        pool: list,
        section_context: str,
        check_global: bool,
        image_category: str = "",
        cta_mode: bool = False,
    ) -> list:
        """
        Run every entry in pool through the validation pipeline.
        Returns list of (score, photo_id, url) sorted descending.
        Logs [IMAGE_REJECTED] for every rejected candidate.
        Does NOT modify _used_urls (caller commits the winner).
        """
        passed = []

        for entry in pool:
            photo_id = entry[0]
            url      = _url(photo_id)

            if url in self._used_urls:
                log.debug(f"[IMAGE] {photo_id} — local dedup skip")
                continue
            if check_global and self._registry.is_globally_used(photo_id):
                log.debug(f"[IMAGE] {photo_id} — global dedup skip")
                continue

            approved, reason, score = _validate_candidate(
                entry,
                self._keyword,
                self._cluster,
                section_context,
                image_category=image_category,
                allowed_categories=self._allowed_categories,
                cta_mode=cta_mode,
            )

            if approved:
                passed.append((score, photo_id, url))
            else:
                log.info(
                    f"[IMAGE_REJECTED] id={photo_id}  score={score:.4f}  "
                    f"reason={reason}  context='{section_context[:60]}'"
                )

        passed.sort(key=lambda x: -x[0])
        return passed

    # ── Section image API ──────────────────────────────────────────────────────

    def section(self, context_text: str = "", index: int = 0) -> str | None:
        """
        Return the best section image URL, or None.

        Priority order (enforced):
          1. Category match — only allowed categories considered
          2. Validation  — all 3 stages must pass
          3. Best score  — highest composite wins across all allowed categories
          4. Local + global dedup

        Returns None if nothing passes. Caller skips the slot. No fallback.
        """
        if not self._allowed_categories:
            log.info(
                f"[IMAGE] Section skipped — no category mapped for "
                f"keyword='{self._keyword}' cluster='{self._cluster}'"
            )
            return None

        all_candidates: list = []

        for cat in self._allowed_categories:
            pool = _SECTION_LIBRARY.get(cat, [])
            if not pool:
                log.info(
                    f"[IMAGE] Category '{cat}' is empty — "
                    "add verified Pexels IDs to unlock this category"
                )
                continue

            for score, pid, url in self._score_pool(
                pool, context_text, check_global=True, image_category=cat
            ):
                all_candidates.append((score, pid, url, cat))

        if not all_candidates:
            log.warning(
                f"[IMAGE] No image passed 3-stage validation for "
                f"categories={self._allowed_categories}  "
                f"context='{context_text[:60]}'. Slot skipped."
            )
            return None

        # Best across all allowed categories
        all_candidates.sort(key=lambda x: -x[0])
        best_score, best_id, best_url, best_cat = all_candidates[0]

        # Commit to local dedup and global tracking
        self._used_urls.add(best_url)
        self._section_ids.append(best_id)

        # Compute matched keywords for log
        best_entry = next(
            e for pool in _SECTION_LIBRARY.values() for e in pool if e[0] == best_id
        )
        ctx_words   = _meaningful_words(
            f"{context_text} {self._keyword} {self._cluster}"
        )
        tag_words   = _clean_words(" ".join(best_entry[2]))
        desc_words  = _clean_words(best_entry[1])
        matched_kws = sorted((tag_words | desc_words) & ctx_words)

        log.info(
            f"[IMAGE_APPROVED] id={best_id}  category={best_cat}  "
            f"score={best_score:.4f}  matched_keywords={matched_kws}  "
            f"section='{context_text[:60]}'"
        )
        return best_url

    # ── CTA image API ──────────────────────────────────────────────────────────

    def cta(self, slot: int = 0) -> str:
        """
        Return a CTA image URL. Only Stage 1 validation (no topic-relevance needed).
        Never returns None — caller always needs an image for the CTA block.
        """
        candidates = self._score_pool(
            _CTA_POOL, section_context="", check_global=False, cta_mode=True
        )

        if candidates:
            # Rotate through slots: slot 0 → index 0, slot 1 → index 1, etc.
            pick_idx  = slot % len(candidates)
            _, photo_id, url = candidates[pick_idx]
            self._used_urls.add(url)
            log.info(f"[IMAGE_APPROVED] CTA slot={slot}  id={photo_id}")
            return url

        # All pool entries failed Stage 1 — this should never happen with clean library
        fallback_entry = _CTA_POOL[slot % len(_CTA_POOL)]
        fallback_url   = _url(fallback_entry[0])
        log.warning(
            f"[IMAGE] CTA pool fully blocked/exhausted — force-reusing {fallback_entry[0]}"
        )
        return fallback_url

    # ── Registry / reporting ───────────────────────────────────────────────────

    def commit_to_global_registry(self, slug: str):
        if self._section_ids:
            self._registry.register_post(slug, self._section_ids)
        else:
            log.info(f"[ImageRegistry] No section images to register for '{slug}'")

    def validation_report(self) -> dict:
        used_ids = []
        for url in self._used_urls:
            m = re.search(r"/photos/(\d+)/", url)
            if m:
                used_ids.append(m.group(1))
        counts     = Counter(used_ids)
        duplicates = [i for i, n in counts.items() if n > 1]
        unverified = [i for i in set(used_ids) if i not in APPROVED_PEXELS_IDS]
        return {
            "image_count":        len(used_ids),
            "unique_image_count": len(set(used_ids)),
            "duplicate_ids":      duplicates or "none",
            "unverified_ids":     unverified or "none",
            "allowed_categories": (
                self._allowed_categories or "none (topic has no image category)"
            ),
        }
