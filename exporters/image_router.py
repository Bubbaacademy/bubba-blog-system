"""
image_router.py — Topic routing and Pexels search query generation.

HOW ROUTING WORKS
-----------------
1. Build search text: f"{keyword} {topic_cluster}".lower()
2. Scan TOPIC_ROUTING keys from longest to shortest.
3. Return the matched topic_category string.
4. Falls back to CAT_GENERAL_BUSINESS if no match.

HOW QUERY GENERATION WORKS
---------------------------
get_search_queries() generates an ordered list of Pexels search queries for a
given image slot. The list is ordered most-specific first:
  1. Section heading + topic context (most specific)
  2. Article keyword + topic context
  3. Base topic queries from TOPIC_SEARCH_QUERIES

DESIGN RULES
------------
- Longer, more specific keys take priority over shorter generic keys.
- topic_category is a single string (not a list).
- Never returns empty list from get_search_queries() — always ≥1 query.
- Even with zero relevant images, the article still publishes (selector returns None).
"""
from __future__ import annotations

import re
import logging

from exporters.image_policy import (
    CAT_FBA_LOGISTICS, CAT_AMAZON_ADS, CAT_AI_TOOLS,
    CAT_PRODUCT_RESEARCH, CAT_SOURCING, CAT_LISTING_OPTIMIZATION,
    CAT_ECOM_STRATEGY, CAT_BRAND_BUILDING,
    CAT_AMAZON_COMPLIANCE, CAT_PRIVATE_LABEL, CAT_AMAZON_FOUNDATION,
    CAT_GENERAL_BUSINESS,
    TOPIC_SEARCH_QUERIES, STOPWORDS,
    PEXELS_QUERIES_PER_SLOT,
)

log = logging.getLogger("image_router")

# ─────────────────────────────────────────────────────────────────────────────
# TOPIC ROUTING MAP
# Keys matched as substrings of (keyword + topic_cluster), case-insensitive.
# Longest keys first (enforced by _SORTED_KEYS) — prevents early short-key match.
# ─────────────────────────────────────────────────────────────────────────────

TOPIC_ROUTING: dict = {

    # ── AI / Automation (must come before "online seller" and "amazon") ────────
    # Keys are sorted longest-first — longer keys must be > 13 chars ("online seller")
    # to win over ecommerce-strategy routing when both match.
    "artificial intelligence":    CAT_AI_TOOLS,   # 23 chars
    "ai tools automation":        CAT_AI_TOOLS,   # 18 chars — matches "AI Tools Automation" cluster
    "ai for online sellers":      CAT_AI_TOOLS,   # 21 chars
    "ai tools for online":        CAT_AI_TOOLS,   # 19 chars
    "ai tools for sellers":       CAT_AI_TOOLS,   # 20 chars
    "ai tools for":               CAT_AI_TOOLS,   # 12 chars (lower priority — may lose to 13-char keys)
    "ai tools":                   CAT_AI_TOOLS,   # 8 chars
    "ai for sellers":             CAT_AI_TOOLS,   # 14 chars
    "ai for amazon":              CAT_AI_TOOLS,   # 13 chars
    "ai seller":                  CAT_AI_TOOLS,   # 9 chars
    "machine learning":           CAT_AI_TOOLS,   # 15 chars
    "chatgpt for":                CAT_AI_TOOLS,   # 11 chars
    "chatgpt":                    CAT_AI_TOOLS,   # 7 chars
    "generative ai":              CAT_AI_TOOLS,   # 12 chars
    "automation tools":           CAT_AI_TOOLS,   # 16 chars
    "ai automation":              CAT_AI_TOOLS,   # 13 chars
    "ai 2025":                    CAT_AI_TOOLS,   # 7 chars
    "ai 2026":                    CAT_AI_TOOLS,   # 7 chars
    "ai tool":                    CAT_AI_TOOLS,   # 7 chars

    # ── Amazon Ads / PPC ──────────────────────────────────────────────────────
    "amazon ppc":                 CAT_AMAZON_ADS,
    "amazon ads":                 CAT_AMAZON_ADS,
    "amazon advertising":         CAT_AMAZON_ADS,
    "sponsored product":          CAT_AMAZON_ADS,
    "sponsored brands":           CAT_AMAZON_ADS,
    "sponsored display":          CAT_AMAZON_ADS,
    "top of the search":          CAT_AMAZON_ADS,
    "top of search":              CAT_AMAZON_ADS,
    "placement adjustment":       CAT_AMAZON_ADS,
    "bid adjustment":             CAT_AMAZON_ADS,
    "placement bid":              CAT_AMAZON_ADS,
    "search term report":         CAT_AMAZON_ADS,
    "keyword bid":                CAT_AMAZON_ADS,
    "acos":                       CAT_AMAZON_ADS,
    "tacos":                      CAT_AMAZON_ADS,
    "roas":                       CAT_AMAZON_ADS,
    "ppc":                        CAT_AMAZON_ADS,
    "advertising":                CAT_AMAZON_ADS,
    "campaign budget":            CAT_AMAZON_ADS,
    "campaign":                   CAT_AMAZON_ADS,
    "sponsored":                  CAT_AMAZON_ADS,

    # ── FBA Logistics / Warehouse / Shipping ──────────────────────────────────
    "inbound shipping":           CAT_FBA_LOGISTICS,
    "fba inbound":                CAT_FBA_LOGISTICS,
    "fba shipping":               CAT_FBA_LOGISTICS,
    "fba storage":                CAT_FBA_LOGISTICS,
    "storage fees":               CAT_FBA_LOGISTICS,
    "warehouse storage":          CAT_FBA_LOGISTICS,
    "inventory management":       CAT_FBA_LOGISTICS,
    "long-term storage":          CAT_FBA_LOGISTICS,
    "fba fees":                   CAT_FBA_LOGISTICS,
    "fba fee":                    CAT_FBA_LOGISTICS,
    "fba cost":                   CAT_FBA_LOGISTICS,
    "amazon fees":                CAT_FBA_LOGISTICS,
    "shipping cost":              CAT_FBA_LOGISTICS,
    "shipping plan":              CAT_FBA_LOGISTICS,
    "ltl":                        CAT_FBA_LOGISTICS,
    "freight":                    CAT_FBA_LOGISTICS,
    "carrier":                    CAT_FBA_LOGISTICS,
    "fulfillment":                CAT_FBA_LOGISTICS,
    "inventory":                  CAT_FBA_LOGISTICS,
    "storage":                    CAT_FBA_LOGISTICS,
    "warehouse":                  CAT_FBA_LOGISTICS,
    "shipping":                   CAT_FBA_LOGISTICS,
    "inbound":                    CAT_FBA_LOGISTICS,
    "fba":                        CAT_FBA_LOGISTICS,

    # ── Product Research ──────────────────────────────────────────────────────
    "product research":           CAT_PRODUCT_RESEARCH,
    "helium 10":                  CAT_PRODUCT_RESEARCH,
    "jungle scout":               CAT_PRODUCT_RESEARCH,
    "product validation":         CAT_PRODUCT_RESEARCH,
    "niche research":             CAT_PRODUCT_RESEARCH,
    "market research":            CAT_PRODUCT_RESEARCH,
    "keyword research":           CAT_PRODUCT_RESEARCH,

    # ── Sourcing / Supply Chain ───────────────────────────────────────────────
    "supply chain":               CAT_SOURCING,
    "manufacturer":               CAT_SOURCING,
    "sourcing":                   CAT_SOURCING,
    "supplier":                   CAT_SOURCING,
    "alibaba":                    CAT_SOURCING,
    "manufacturing":              CAT_SOURCING,
    "wholesale":                  CAT_SOURCING,
    "import":                     CAT_SOURCING,
    "mold":                       CAT_SOURCING,
    "prototype":                  CAT_SOURCING,

    # ── Listing Optimization ──────────────────────────────────────────────────
    "listing optimization":       CAT_LISTING_OPTIMIZATION,
    "a+ content":                 CAT_LISTING_OPTIMIZATION,
    "product images":             CAT_LISTING_OPTIMIZATION,
    "product listing":            CAT_LISTING_OPTIMIZATION,
    "listing seo":                CAT_LISTING_OPTIMIZATION,
    "conversion rate":            CAT_LISTING_OPTIMIZATION,
    "click-through":              CAT_LISTING_OPTIMIZATION,

    # ── Account Health / Compliance ───────────────────────────────────────────
    "account health":             CAT_AMAZON_COMPLIANCE,
    "policy compliance":          CAT_AMAZON_COMPLIANCE,
    "suspension":                 CAT_AMAZON_COMPLIANCE,
    "reinstatement":              CAT_AMAZON_COMPLIANCE,

    # ── Private Label / Branding ──────────────────────────────────────────────
    "private label":              CAT_PRIVATE_LABEL,
    "brand registry":             CAT_PRIVATE_LABEL,
    "packaging design":           CAT_PRIVATE_LABEL,
    "branding":                   CAT_BRAND_BUILDING,
    "brand building":             CAT_BRAND_BUILDING,
    "brand awareness":            CAT_BRAND_BUILDING,

    # ── Ecommerce Strategy ────────────────────────────────────────────────────
    "ecommerce strategy":         CAT_ECOM_STRATEGY,
    "selling strategy":           CAT_ECOM_STRATEGY,
    "profit margin":              CAT_ECOM_STRATEGY,
    "revenue growth":             CAT_ECOM_STRATEGY,
    "profit calculator":          CAT_ECOM_STRATEGY,
    "pricing strategy":           CAT_ECOM_STRATEGY,

    # ── General Amazon / Beginner ─────────────────────────────────────────────
    "amazon fba":                 CAT_FBA_LOGISTICS,
    "amazon seller":              CAT_AMAZON_FOUNDATION,
    "amazon beginner":            CAT_AMAZON_FOUNDATION,
    "start amazon":               CAT_AMAZON_FOUNDATION,
    "sell on amazon":             CAT_AMAZON_FOUNDATION,
    "amazon business":            CAT_AMAZON_FOUNDATION,
    "amazon":                     CAT_AMAZON_FOUNDATION,

    # ── Ecommerce general ────────────────────────────────────────────────────
    "ecommerce":                  CAT_ECOM_STRATEGY,
    "online seller":              CAT_ECOM_STRATEGY,
    "online selling":             CAT_ECOM_STRATEGY,
}

# Sorted once at module load — longest key first for deterministic matching.
_SORTED_KEYS: list = sorted(TOPIC_ROUTING.keys(), key=len, reverse=True)

# Per-category human-readable context suffixes for heading-derived queries
_CATEGORY_CONTEXT_SUFFIX: dict = {
    CAT_AMAZON_ADS:          "business analytics digital marketing",
    CAT_AI_TOOLS:            "technology AI software dashboard",
    CAT_FBA_LOGISTICS:       "warehouse logistics ecommerce fulfillment",
    CAT_PRODUCT_RESEARCH:    "business analytics ecommerce research",
    CAT_SOURCING:            "manufacturing supply chain business",
    CAT_LISTING_OPTIMIZATION: "ecommerce product optimization",
    CAT_ECOM_STRATEGY:       "ecommerce business strategy growth",
    CAT_BRAND_BUILDING:      "brand business marketing strategy",
    CAT_AMAZON_COMPLIANCE:   "business compliance professional",
    CAT_PRIVATE_LABEL:       "brand product packaging business",
    CAT_AMAZON_FOUNDATION:   "ecommerce business online seller",
    CAT_GENERAL_BUSINESS:    "professional business analytics",
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def route(keyword: str, topic_cluster: str) -> str:
    """
    Map article keyword + topic_cluster to a topic_category string.

    Returns the category string (e.g. "amazon_ads_digital").
    Falls back to CAT_GENERAL_BUSINESS if no match — never returns None.

    Parameters
    ----------
    keyword       : "Main Keyword" column value from the sheet
    topic_cluster : "Topic Cluster" column value from the sheet
    """
    search_text = re.sub(r"\s+", " ", f"{keyword} {topic_cluster}".lower().strip())

    for fragment in _SORTED_KEYS:
        if fragment in search_text:
            category = TOPIC_ROUTING[fragment]
            log.info(
                f"[IMAGE_ROUTE] keyword='{keyword}'  cluster='{topic_cluster}'  "
                f"matched_fragment='{fragment}'  category='{category}'"
            )
            return category

    log.info(
        f"[IMAGE_ROUTE] NO_MATCH  keyword='{keyword}'  cluster='{topic_cluster}'  "
        f"→ fallback to general_business"
    )
    return CAT_GENERAL_BUSINESS


def get_search_queries(
    topic_category: str,
    keyword: str,
    section_heading: str = "",
    max_queries: int = PEXELS_QUERIES_PER_SLOT,
) -> list:
    """
    Generate ordered Pexels search query strings for a given image slot.

    Strategy (most specific first):
      1. Section heading words + category context suffix (if heading provided)
      2. Article keyword + category context suffix
      3. Base topic queries from TOPIC_SEARCH_QUERIES

    Parameters
    ----------
    topic_category  : routing category (e.g. "amazon_ads_digital")
    keyword         : article main keyword
    section_heading : section heading text (empty for hero)
    max_queries     : how many queries to return (default PEXELS_QUERIES_PER_SLOT)

    Returns
    -------
    List of query strings, length ≤ max_queries, always at least 1 item.
    """
    base_queries = list(
        TOPIC_SEARCH_QUERIES.get(topic_category, TOPIC_SEARCH_QUERIES[CAT_GENERAL_BUSINESS])
    )
    context_suffix = _CATEGORY_CONTEXT_SUFFIX.get(topic_category, "business")

    queries: list = []

    # 1. Section heading → specific query
    if section_heading:
        heading_clean = re.sub(r"[^a-z0-9\s]", " ", section_heading.lower())
        heading_words = [
            w for w in heading_clean.split()
            if w not in STOPWORDS and len(w) > 3
        ]
        if heading_words:
            heading_fragment = " ".join(heading_words[:5])
            q = f"{heading_fragment} {context_suffix}"
            if q not in queries:
                queries.append(q)

    # 2. Article keyword → topic-contextualized query
    kw_clean = re.sub(r"[^a-z0-9\s]", " ", keyword.lower()).strip()
    if kw_clean:
        q = f"{kw_clean} {context_suffix}"
        if q not in queries:
            queries.append(q)

    # 3. Base topic queries (fill remainder up to max_queries)
    for q in base_queries:
        if len(queries) >= max_queries:
            break
        if q not in queries:
            queries.append(q)

    # Guarantee at least one query
    if not queries:
        queries = [base_queries[0]] if base_queries else ["ecommerce business professional"]

    return queries[:max_queries]
