"""
image_catalog.py — Structured image catalog. Single source of truth for every image.

MANDATORY PROCESS TO ADD AN IMAGE
----------------------------------
1. Open: https://images.pexels.com/photos/{ID}/pexels-photo-{ID}.jpeg
2. Confirm visually: professional, business-relevant, zero food/people/lifestyle content.
3. Write a BUSINESS-CONTEXT description — not a visual description.
   WRONG: "two people standing near boxes in a building"
   RIGHT: "amazon fba workers managing inbound shipment boxes at fulfillment warehouse"
   Business keywords in the description drive desc_recall scoring.
4. Write tags: mix visual accuracy with business-context keywords.
   Include "amazon" and "fba" if relevant (appear in most article contexts).
   Add category-specific terms that appear in article headings for this topic.
5. Assign visual_cluster: group images that look similar (same scene/angle/subject).
   Diversity enforcement uses this — do not over-cluster.
6. Set quality_score 0.70–1.00:
   1.00 = perfect professional photo, crisp, highly specific to business context.
   0.90 = strong professional photo, clearly business-relevant.
   0.80 = good photo, some minor visual ambiguity but clearly business.
   0.70 = acceptable, minor quality issues or slightly generic.
   < 0.70 = set status=needs_review or status=disabled.
7. Set status=approved. Image will only be selected if status == "approved".
8. Run: python3 tests/test_image_system.py

REUSE IN OTHER PROJECTS
------------------------
Replace IMAGE_CATALOG entries with project-specific images.
Keep ImageEntry dataclass and all helper functions — they are project-agnostic.
Only catalog data and PEXELS_BASE need to change for a different CDN.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from exporters.image_policy import (
    STATUS_APPROVED, STATUS_DISABLED, STATUS_NEEDS_REVIEW,
    ROLE_HERO, ROLE_SECTION, ROLE_CTA,
    CAT_FBA_LOGISTICS, CAT_AMAZON_ADS,
    CAT_PRODUCT_RESEARCH, CAT_LISTING_OPTIMIZATION,
    CAT_AMAZON_COMPLIANCE, CAT_PRIVATE_LABEL, CAT_AMAZON_FOUNDATION,
    QUALITY_THRESHOLD,
)

PEXELS_BASE = "https://images.pexels.com/photos"


def _url(photo_id: str, width: int = 800) -> str:
    return (
        f"{PEXELS_BASE}/{photo_id}/pexels-photo-{photo_id}.jpeg"
        f"?auto=compress&cs=tinysrgb&w={width}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# ImageEntry dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ImageEntry:
    """
    Immutable metadata record for a single image in the catalog.

    Fields
    ------
    image_id               Pexels photo ID (string). Primary key.
    url                    Full CDN URL with size parameters.
    category               Which image category this belongs to (e.g. fba_logistics).
    allowed_topic_clusters Tuple of topic fragment strings this image is appropriate for.
                           Empty tuple = allowed for all topics (used for CTA images).
    blocked_topic_clusters Tuple of topic fragment strings this image must NOT appear in.
                           Checked as substring match against (keyword + topic_cluster).
    tags                   Tuple of keyword tags — drives tag_recall scoring.
    description            Business-context description — drives desc_recall scoring.
    visual_cluster         Group label for visually similar images (e.g. "warehouse_workers").
                           Diversity logic prevents same cluster appearing twice in one post.
    quality_score          Float 0.0–1.0. Images below QUALITY_THRESHOLD are auto-rejected.
    relevance_keywords     Tuple of high-signal keywords for this image's topic.
                           Drives kw_recall — the strongest scoring signal.
    reusable_cta           True = CTA image that may repeat across posts.
                           False = section/hero image, globally deduped.
    status                 "approved" | "disabled" | "needs_review"
    roles                  Tuple of roles: "hero", "section", "cta".
    """
    image_id: str
    url: str
    category: str
    allowed_topic_clusters: Tuple[str, ...]
    blocked_topic_clusters: Tuple[str, ...]
    tags: Tuple[str, ...]
    description: str
    visual_cluster: str
    quality_score: float
    relevance_keywords: Tuple[str, ...]
    reusable_cta: bool
    status: str
    roles: Tuple[str, ...]

    # ── Convenience predicates ────────────────────────────────────────────────

    def is_approved(self) -> bool:
        return self.status == STATUS_APPROVED

    def meets_quality_threshold(self) -> bool:
        return self.quality_score >= QUALITY_THRESHOLD

    def allows_role(self, role: str) -> bool:
        return role in self.roles

    def is_blocked_for_topic(self, keyword: str, topic_cluster: str) -> bool:
        """True if this image is explicitly blocked for the given article topic."""
        if not self.blocked_topic_clusters:
            return False
        combined = f"{keyword} {topic_cluster}".lower()
        return any(blocked.lower() in combined for blocked in self.blocked_topic_clusters)


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE CATALOG
# ─────────────────────────────────────────────────────────────────────────────
#
# Organised by role and category.
# Verify each ID at: https://images.pexels.com/photos/{ID}/pexels-photo-{ID}.jpeg

IMAGE_CATALOG: list = [

    # =========================================================================
    # SECTION + HERO IMAGES — fba_logistics
    # Blocked for: ppc, advertising, campaign, acos (wrong topic)
    # =========================================================================

    ImageEntry(
        image_id="4481323",
        url=_url("4481323"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("fba", "warehouse", "storage", "inventory", "fulfillment"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("warehouse", "fba", "amazon", "inventory", "storage", "fulfillment",
              "workers", "logistics", "distribution", "ecommerce"),
        description="amazon fba warehouse workers managing inventory storage fulfillment center",
        visual_cluster="warehouse_workers",
        quality_score=0.90,
        relevance_keywords=("warehouse", "inventory", "storage", "fulfillment", "fba", "amazon",
                             "logistics", "distribution"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION, ROLE_HERO),
    ),

    ImageEntry(
        image_id="6169668",
        url=_url("6169668"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("fba", "warehouse", "inventory", "fulfillment", "management"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("warehouse", "fba", "amazon", "inventory", "fulfillment", "team",
              "management", "workers", "logistics", "distribution"),
        description="amazon fba warehouse team reviewing inventory management fulfillment operations",
        visual_cluster="warehouse_workers",
        quality_score=0.85,
        relevance_keywords=("warehouse", "inventory", "management", "fulfillment", "fba",
                             "team", "workers", "amazon"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION,),
    ),

    ImageEntry(
        image_id="3584942",
        url=_url("3584942"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("shipping", "inbound", "delivery", "logistics", "fba",
                                "freight", "courier"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("shipping", "fba", "amazon", "inbound", "delivery", "boxes",
              "courier", "logistics", "shipment", "distribution"),
        description="amazon fba inbound shipping couriers loading delivery boxes logistics",
        visual_cluster="shipping_couriers",
        quality_score=0.88,
        relevance_keywords=("shipping", "inbound", "delivery", "courier", "fba",
                             "logistics", "boxes", "shipment"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION,),
    ),

    ImageEntry(
        image_id="6169661",
        url=_url("6169661"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("shipping", "inbound", "loading", "delivery", "fba",
                                "logistics"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("shipping", "fba", "amazon", "inbound", "loading", "boxes",
              "delivery", "logistics", "shipment", "van"),
        description="amazon fba inbound shipping workers loading boxes for delivery fulfillment",
        visual_cluster="shipping_loading",
        quality_score=0.85,
        relevance_keywords=("shipping", "inbound", "loading", "boxes", "delivery",
                             "fba", "logistics"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION,),
    ),

    ImageEntry(
        image_id="906494",
        url=_url("906494"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("freight", "shipping", "import", "international",
                                "logistics", "supply chain"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("freight", "shipping", "amazon", "import", "containers", "port",
              "logistics", "export", "international", "supply"),
        description="international freight shipping containers port amazon import supply chain logistics",
        visual_cluster="freight_port",
        quality_score=0.82,
        relevance_keywords=("freight", "shipping", "containers", "port", "import",
                             "logistics", "international", "supply"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION,),
    ),

    ImageEntry(
        image_id="2226458",
        url=_url("2226458"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("freight", "shipping", "import", "international",
                                "logistics", "supply chain"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("freight", "shipping", "amazon", "import", "containers", "aerial",
              "logistics", "export", "international", "cargo"),
        description="aerial freight shipping containers cargo amazon import export logistics",
        visual_cluster="freight_aerial",
        quality_score=0.80,
        relevance_keywords=("freight", "shipping", "containers", "cargo", "import",
                             "logistics", "international", "aerial"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION,),
    ),

    ImageEntry(
        image_id="4246120",
        url=_url("4246120"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("packaging", "prep", "fba", "inbound", "shipment",
                                "boxes", "sealing"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("packaging", "fba", "amazon", "prep", "sealing", "boxes",
              "shipment", "packing", "fulfillment", "inbound"),
        description="amazon fba packaging prep sealing cardboard box for inbound shipment",
        visual_cluster="packaging_sealing",
        quality_score=0.87,
        relevance_keywords=("packaging", "prep", "sealing", "boxes", "inbound",
                             "fba", "shipment", "packing"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION,),
    ),

    ImageEntry(
        image_id="4246123",
        url=_url("4246123"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("packaging", "prep", "fba", "packing", "taping",
                                "fulfillment", "inbound"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("packaging", "fba", "amazon", "prep", "packing", "boxes",
              "taping", "fulfillment", "shipment", "inbound"),
        description="amazon fba packaging prep packing taping cardboard boxes fulfillment inbound",
        visual_cluster="packaging_packing",
        quality_score=0.86,
        relevance_keywords=("packaging", "packing", "taping", "boxes", "fulfillment",
                             "fba", "inbound", "prep"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION,),
    ),

    ImageEntry(
        image_id="4246119",
        url=_url("4246119"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=("packaging", "inventory", "fba", "shipping", "delivery",
                                "labeled", "boxes", "inbound"),
        blocked_topic_clusters=("ppc", "advertising", "campaign", "acos", "sponsored"),
        tags=("packaging", "fba", "amazon", "inventory", "boxes", "labeled",
              "shipping", "delivery", "preparation", "inbound"),
        description="amazon fba inventory stacked labeled boxes ready shipping delivery preparation",
        visual_cluster="packaging_stacked",
        quality_score=0.85,
        relevance_keywords=("inventory", "boxes", "labeled", "shipping", "delivery",
                             "fba", "inbound", "preparation"),
        reusable_cta=False,
        status=STATUS_DISABLED,   # section/hero: AI-generated images only
        roles=(ROLE_SECTION,),
    ),

    # =========================================================================
    # CTA IMAGES — fba_logistics — reusable across posts
    # allowed_topic_clusters = () means topic-independent (CTA is decorative)
    # blocked_topic_clusters  = () means never blocked (CTAs are always safe)
    # =========================================================================

    ImageEntry(
        image_id="4483610",
        url=_url("4483610"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=(),
        blocked_topic_clusters=(),
        tags=("warehouse", "shelves", "inventory", "fulfillment", "fba", "amazon",
              "storage", "professional", "ecommerce", "distribution"),
        description="amazon fba fulfillment center wide warehouse interior stocked shelves professional",
        visual_cluster="warehouse_interior",
        quality_score=0.92,
        relevance_keywords=("warehouse", "fulfillment", "inventory", "storage", "fba", "amazon"),
        reusable_cta=True,
        status=STATUS_APPROVED,
        roles=(ROLE_CTA,),
    ),

    ImageEntry(
        image_id="4481326",
        url=_url("4481326"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=(),
        blocked_topic_clusters=(),
        tags=("warehouse", "shelves", "pallets", "organized", "professional",
              "fba", "amazon", "inventory", "fulfillment", "logistics"),
        description="amazon fba warehouse organized racking pallets inventory storage professional",
        visual_cluster="warehouse_racking",
        quality_score=0.90,
        relevance_keywords=("warehouse", "pallets", "organized", "inventory", "fba", "amazon"),
        reusable_cta=True,
        status=STATUS_APPROVED,
        roles=(ROLE_CTA,),
    ),

    ImageEntry(
        image_id="4481259",
        url=_url("4481259"),
        category=CAT_FBA_LOGISTICS,
        allowed_topic_clusters=(),
        blocked_topic_clusters=(),
        tags=("warehouse", "pallets", "inventory", "team", "fulfillment",
              "professional", "fba", "amazon", "distribution", "logistics"),
        description="amazon fba warehouse team organizing inventory pallets fulfillment professional",
        visual_cluster="warehouse_team",
        quality_score=0.88,
        relevance_keywords=("warehouse", "pallets", "team", "fulfillment", "fba", "amazon"),
        reusable_cta=True,
        status=STATUS_APPROVED,
        roles=(ROLE_CTA,),
    ),

    # =========================================================================
    # PLACEHOLDER CATEGORIES — no approved images yet
    # =========================================================================
    # amazon_ads_digital (PPC / campaign / ACOS):
    #   Add verified Pexels IDs of Amazon Seller Central dashboard screenshots,
    #   ad campaign analytics screens, or professional digital marketing imagery.
    #   Example entry:
    #
    # ImageEntry(
    #     image_id="VERIFIED_ID",
    #     url=_url("VERIFIED_ID"),
    #     category=CAT_AMAZON_ADS,
    #     allowed_topic_clusters=("ppc", "advertising", "campaign", "acos"),
    #     blocked_topic_clusters=("warehouse", "shipping", "packaging"),
    #     tags=("analytics", "dashboard", "ppc", "advertising", "amazon", "campaign", ...),
    #     description="amazon seller central ppc campaign dashboard analytics acos roas",
    #     visual_cluster="ads_dashboard",
    #     quality_score=0.90,
    #     relevance_keywords=("ppc", "campaign", "acos", "advertising", "dashboard"),
    #     reusable_cta=False,
    #     status=STATUS_APPROVED,
    #     roles=(ROLE_SECTION,),
    # ),
    #
    # product_research, listing_optimization, amazon_compliance,
    # private_label_branding, amazon_business_foundation:
    #   Same pattern. Add verified IDs, set status=approved, run tests.
]


# ─────────────────────────────────────────────────────────────────────────────
# Catalog lookup helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_by_id(image_id: str) -> "ImageEntry | None":
    for entry in IMAGE_CATALOG:
        if entry.image_id == image_id:
            return entry
    return None


def get_approved(
    category: str | None = None,
    role: str | None = None,
    reusable_cta: bool | None = None,
) -> list:
    """
    Return approved catalog entries, optionally filtered.

    Parameters
    ----------
    category     : filter to a specific category string
    role         : filter to entries that include this role
    reusable_cta : if True/False, filter on the reusable_cta flag
    """
    result = [e for e in IMAGE_CATALOG if e.status == STATUS_APPROVED]
    if category is not None:
        result = [e for e in result if e.category == category]
    if role is not None:
        result = [e for e in result if role in e.roles]
    if reusable_cta is not None:
        result = [e for e in result if e.reusable_cta == reusable_cta]
    return result


# Set of all approved image IDs — used by hubspot_api.py pre-publish validator.
APPROVED_IDS: frozenset = frozenset(
    e.image_id for e in IMAGE_CATALOG if e.status == STATUS_APPROVED
)

# Backward-compat alias (hubspot_api.py imported this name previously)
APPROVED_PEXELS_IDS = APPROVED_IDS
