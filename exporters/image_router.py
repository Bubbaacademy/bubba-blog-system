"""
image_router.py — Topic-to-image-category routing.

Maps article keyword + topic_cluster → ordered list of image categories.

HOW ROUTING WORKS
-----------------
1. Build search text: f"{keyword} {topic_cluster}".lower()
2. Scan TOPIC_ROUTING keys from longest to shortest (prevents early short-key match).
3. Return first match's category list.
4. Return [] if no match → section images skipped, article still publishes.

DESIGN RULES
------------
- Longer, more specific keys take priority over shorter generic keys.
  "amazon ppc" matches before "amazon" matches before "ppc".
- [] as value means "no images for this topic" — explicit, safe, never falls back.
- Categories map directly to IMAGE_CATALOG entries.
  If a category has zero approved images, it silently skips (handled by selector).
- Add new topic entries here; add matching catalog entries in image_catalog.py.

REUSE IN OTHER PROJECTS
-----------------------
Replace TOPIC_ROUTING dict with project-specific topic→category mappings.
The route() function logic is project-agnostic.
"""
from __future__ import annotations

import re
import logging

from exporters.image_policy import (
    CAT_FBA_LOGISTICS, CAT_AMAZON_ADS,
    CAT_PRODUCT_RESEARCH, CAT_LISTING_OPTIMIZATION,
    CAT_AMAZON_COMPLIANCE, CAT_PRIVATE_LABEL, CAT_AMAZON_FOUNDATION,
)

log = logging.getLogger("image_router")

# ─────────────────────────────────────────────────────────────────────────────
# TOPIC → CATEGORY ROUTING MAP
# ─────────────────────────────────────────────────────────────────────────────
# Keys: matched as substrings of (keyword + topic_cluster), case-insensitive.
# Values: ordered list of category strings. Selector tries categories in order.
#   [] = no images for this topic (no fallback — article publishes without section images).
# ─────────────────────────────────────────────────────────────────────────────

TOPIC_ROUTING: dict = {

    # ── Amazon Ads / PPC (must appear before "amazon" and "campaign") ─────────
    "amazon ppc":           [CAT_AMAZON_ADS],
    "amazon ads":           [CAT_AMAZON_ADS],
    "sponsored product":    [CAT_AMAZON_ADS],
    "sponsored brands":     [CAT_AMAZON_ADS],
    "sponsored display":    [CAT_AMAZON_ADS],
    "top of the search":    [CAT_AMAZON_ADS],
    "top of search":        [CAT_AMAZON_ADS],
    "placement adjustment": [CAT_AMAZON_ADS],
    "bid adjustment":       [CAT_AMAZON_ADS],
    "placement bid":        [CAT_AMAZON_ADS],
    "search term report":   [CAT_AMAZON_ADS],
    "keyword bid":          [CAT_AMAZON_ADS],
    "acos":                 [CAT_AMAZON_ADS],
    "roas":                 [CAT_AMAZON_ADS],
    "ppc":                  [CAT_AMAZON_ADS],
    "advertising":          [CAT_AMAZON_ADS],
    "campaign":             [CAT_AMAZON_ADS],
    "sponsored":            [CAT_AMAZON_ADS],

    # ── FBA Logistics / Warehouse / Shipping ──────────────────────────────────
    "inbound shipping":     [CAT_FBA_LOGISTICS],
    "fba inbound":          [CAT_FBA_LOGISTICS],
    "fba shipping":         [CAT_FBA_LOGISTICS],
    "fba storage":          [CAT_FBA_LOGISTICS],
    "storage fees":         [CAT_FBA_LOGISTICS],
    "warehouse storage":    [CAT_FBA_LOGISTICS],
    "inventory management": [CAT_FBA_LOGISTICS],
    "long-term storage":    [CAT_FBA_LOGISTICS],
    "fba fees":             [CAT_FBA_LOGISTICS],
    "fba fee":              [CAT_FBA_LOGISTICS],
    "fba cost":             [CAT_FBA_LOGISTICS],
    "amazon fees":          [CAT_FBA_LOGISTICS],
    "shipping cost":        [CAT_FBA_LOGISTICS],
    "shipping plan":        [CAT_FBA_LOGISTICS],
    "ltl":                  [CAT_FBA_LOGISTICS],
    "freight":              [CAT_FBA_LOGISTICS],
    "carrier":              [CAT_FBA_LOGISTICS],
    "fulfillment":          [CAT_FBA_LOGISTICS],
    "inventory":            [CAT_FBA_LOGISTICS],
    "storage":              [CAT_FBA_LOGISTICS],
    "warehouse":            [CAT_FBA_LOGISTICS],
    "shipping":             [CAT_FBA_LOGISTICS],
    "inbound":              [CAT_FBA_LOGISTICS],
    "fba":                  [CAT_FBA_LOGISTICS],

    # ── Product Research ──────────────────────────────────────────────────────
    "product research":     [CAT_PRODUCT_RESEARCH],
    "helium 10":            [CAT_PRODUCT_RESEARCH],
    "jungle scout":         [CAT_PRODUCT_RESEARCH],
    "product validation":   [CAT_PRODUCT_RESEARCH],
    "niche research":       [CAT_PRODUCT_RESEARCH],
    "market research":      [CAT_PRODUCT_RESEARCH],
    "keyword research":     [CAT_PRODUCT_RESEARCH, CAT_AMAZON_ADS],

    # ── Listing Optimization ──────────────────────────────────────────────────
    "listing optimization": [CAT_LISTING_OPTIMIZATION],
    "a+ content":           [CAT_LISTING_OPTIMIZATION],
    "product images":       [CAT_LISTING_OPTIMIZATION],
    "product listing":      [CAT_LISTING_OPTIMIZATION],
    "listing seo":          [CAT_LISTING_OPTIMIZATION],
    "conversion rate":      [CAT_LISTING_OPTIMIZATION],

    # ── Account Health / Compliance ───────────────────────────────────────────
    "account health":       [CAT_AMAZON_COMPLIANCE],
    "policy compliance":    [CAT_AMAZON_COMPLIANCE],
    "suspension":           [CAT_AMAZON_COMPLIANCE],
    "reinstatement":        [CAT_AMAZON_COMPLIANCE],

    # ── Private Label / Branding ──────────────────────────────────────────────
    "private label":        [CAT_PRIVATE_LABEL],
    "brand registry":       [CAT_PRIVATE_LABEL],
    "packaging design":     [CAT_PRIVATE_LABEL],
    "branding":             [CAT_PRIVATE_LABEL],
    "sourcing":             [CAT_PRIVATE_LABEL, CAT_FBA_LOGISTICS],
    "supplier":             [CAT_PRIVATE_LABEL, CAT_FBA_LOGISTICS],
    "manufacturer":         [CAT_PRIVATE_LABEL],

    # ── General Amazon / Beginner (must come after more specific keys) ─────────
    "amazon fba":           [CAT_FBA_LOGISTICS],
    "amazon seller":        [CAT_AMAZON_FOUNDATION],
    "amazon beginner":      [CAT_AMAZON_FOUNDATION],
    "start amazon":         [CAT_AMAZON_FOUNDATION],
    "sell on amazon":       [CAT_AMAZON_FOUNDATION],
    "amazon business":      [CAT_AMAZON_FOUNDATION],
    "amazon":               [CAT_FBA_LOGISTICS, CAT_AMAZON_FOUNDATION],

    # ── No visual category — explicitly safe (never falls back) ───────────────
    "profit margin":        [],
    "profit calculator":    [],
    "pricing strategy":     [],
    "review":               [],
    "rank":                 [],
}

# Sorted once at module load — longest key first for deterministic matching.
_SORTED_KEYS: list = sorted(TOPIC_ROUTING.keys(), key=len, reverse=True)


def route(keyword: str, topic_cluster: str) -> list:
    """
    Return ordered list of image categories for this article.

    Returns [] if no match — caller must handle gracefully (skip section images,
    still publish article). Never raises.

    Parameters
    ----------
    keyword       : "Main Keyword" column value from the sheet
    topic_cluster : "Topic Cluster" column value from the sheet
    """
    search_text = re.sub(r"\s+", " ", f"{keyword} {topic_cluster}".lower().strip())

    for fragment in _SORTED_KEYS:
        if fragment in search_text:
            categories = TOPIC_ROUTING[fragment]
            log.info(
                f"[IMAGE_ROUTE] keyword='{keyword}'  cluster='{topic_cluster}'  "
                f"matched_fragment='{fragment}'  "
                f"categories={categories or 'NONE (section images skipped for this topic)'}"
            )
            return list(categories)

    log.warning(
        f"[IMAGE_ROUTE] NO_MATCH  keyword='{keyword}'  cluster='{topic_cluster}'  "
        f"search='{search_text}'  → section images skipped"
    )
    return []
