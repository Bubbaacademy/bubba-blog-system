"""
image_prompt_generator.py — Generate Replicate Flux image prompts from article context.

WHY THIS EXISTS
---------------
Stock photo APIs return "ecommerce" / "Amazon" images dominated by warehouse
photography — even for articles about PPC, AI tools, or product research.

AI generation solves this: we specify exactly what to show AND what to exclude.
Replicate's Flux models follow concise, descriptive prompts reliably.

PROMPT FORMULA (per slot)
--------------------------
  [quality_prefix] + [specific_visual_subject] + [topic_base] + [exclusions]

Flux prompt guidelines:
  - 200–500 chars is ideal (shorter than DALL-E)
  - Clear subject first, style/exclusions last
  - Negative terms embedded inline ("NOT a warehouse")
  - No DALL-E-specific style flags needed (natural/vivid, etc.)

Every prompt is:
  - topic-category specific (12 topic categories)
  - section-heading specific for section images
  - article-title specific for hero images
  - complete: exclusions baked in, no implicit fallbacks
"""
from __future__ import annotations

import hashlib
import re
import logging
from dataclasses import dataclass

from exporters.image_policy import (
    CAT_FBA_LOGISTICS, CAT_AMAZON_ADS, CAT_AI_TOOLS,
    CAT_PRODUCT_RESEARCH, CAT_SOURCING, CAT_LISTING_OPTIMIZATION,
    CAT_ECOM_STRATEGY, CAT_BRAND_BUILDING,
    CAT_AMAZON_COMPLIANCE, CAT_PRIVATE_LABEL, CAT_AMAZON_FOUNDATION,
    CAT_GENERAL_BUSINESS,
    STOPWORDS,
)

log = logging.getLogger("image_prompt_generator")

# ── Style suffix — appended to every prompt ───────────────────────────────────

STYLE_SUFFIX = (
    "Professional editorial photo, sharp focus, clean modern composition, "
    "16:9 landscape, high resolution, no text, no watermarks, no logos."
)

# ── Universal exclusions — baked into every prompt ────────────────────────────

UNIVERSAL_EXCLUSIONS = (
    "No text overlays, no brand logos, no food, no animals, no children, "
    "no beach, no gym, no cartoon, no illustration, no distorted faces."
)

# ── Per-topic visual base descriptions ────────────────────────────────────────
# Short, direct descriptions of what imagery is APPROPRIATE for each topic.
# Section heading + keyword are layered on top of these bases.

TOPIC_VISUAL_BASES: dict = {
    CAT_AMAZON_ADS: (
        "Business analytics workspace, digital advertising dashboard on monitor, "
        "marketing performance charts, modern office, NOT a warehouse, NOT shipping boxes."
    ),
    CAT_AI_TOOLS: (
        "AI software interface on laptop screen, futuristic technology workspace, "
        "data visualization blue ambient lighting, NOT a warehouse, NOT shipping."
    ),
    CAT_FBA_LOGISTICS: (
        "Modern Amazon fulfillment center, organized warehouse shelving with packages, "
        "professional logistics workers, clean operations floor."
    ),
    CAT_PRODUCT_RESEARCH: (
        "Business analyst reviewing product data on multiple monitors, "
        "market research charts on screen, professional desk, NOT a warehouse."
    ),
    CAT_SOURCING: (
        "Clean manufacturing facility, quality control inspection, "
        "supplier meeting in professional industrial environment, NOT Amazon packaging."
    ),
    CAT_LISTING_OPTIMIZATION: (
        "Product photography studio with spotlight lighting, "
        "ecommerce listing on laptop screen, clean white background, NOT a warehouse."
    ),
    CAT_ECOM_STRATEGY: (
        "Entrepreneur at modern desk reviewing growth charts and business plans, "
        "laptop with business dashboard, NOT a warehouse, NOT shipping boxes."
    ),
    CAT_BRAND_BUILDING: (
        "Brand design session, color palette swatches, packaging design samples, "
        "marketing materials on professional desk, NOT a warehouse."
    ),
    CAT_AMAZON_COMPLIANCE: (
        "Professional compliance review setting, clean desk with documents, "
        "laptop showing policy dashboard, business environment, NOT a warehouse."
    ),
    CAT_PRIVATE_LABEL: (
        "Custom product packaging in professional studio, "
        "modern branding on product boxes, label design samples, clean background."
    ),
    CAT_AMAZON_FOUNDATION: (
        "Motivated entrepreneur at home office, laptop with ecommerce seller interface, "
        "professional accessible setting, NOT a warehouse, NOT food."
    ),
    CAT_GENERAL_BUSINESS: (
        "Clean professional business workspace, laptop with analytics dashboard, "
        "modern office environment, NOT food, NOT warehouse, NOT lifestyle."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# ImagePrompt dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImagePrompt:
    """
    A fully constructed Replicate Flux image prompt.

    Fields
    ------
    text           Full prompt text sent to Replicate (concise, ≤1500 chars)
    topic_category Topic category this prompt was built for
    slot           "hero" | "section_0" | "section_1" | "cta_0" etc.
    basis          Human-readable explanation of what drove the prompt (logging)
    """
    text:           str
    topic_category: str
    slot:           str
    basis:          str

    @property
    def prompt_hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:16]

    @property
    def pexels_query(self) -> str:
        """Condensed version of the prompt (kept for backward compatibility)."""
        first_sentence = self.text.split(".")[0]
        words = re.sub(r"[^a-z0-9\s]", " ", first_sentence.lower()).split()
        meaningful = [w for w in words if w not in STOPWORDS and len(w) > 3]
        return " ".join(meaningful[:6]) if meaningful else "professional business ecommerce"


# ─────────────────────────────────────────────────────────────────────────────
# Prompt generation
# ─────────────────────────────────────────────────────────────────────────────

def _extract_heading_concepts(heading: str) -> str:
    """Extract 3–5 key concept words from a section heading."""
    clean = re.sub(r"[^a-z0-9\s]", " ", heading.lower())
    words = [w for w in clean.split() if w not in STOPWORDS and len(w) > 3]
    return " ".join(words[:5]) if words else ""


def generate_prompt(
    role: str,
    keyword: str,
    topic_category: str,
    section_heading: str = "",
    paragraph_snippet: str = "",
    article_title: str = "",
) -> ImagePrompt:
    """
    Generate a complete Replicate Flux image prompt for a single image slot.

    Parameters
    ----------
    role             : "hero" | "section" | "cta"
    keyword          : article main keyword
    topic_category   : routing category (e.g. "amazon_ads_digital")
    section_heading  : section heading text (for section images)
    paragraph_snippet: first ~200 chars of the section paragraph (optional context)
    article_title    : article title (for hero images)

    Returns
    -------
    ImagePrompt with concise text ready for Replicate Flux models
    """
    topic_base = TOPIC_VISUAL_BASES.get(
        topic_category,
        TOPIC_VISUAL_BASES[CAT_GENERAL_BUSINESS],
    )

    if role == "hero":
        heading_concepts = _extract_heading_concepts(article_title or keyword)
        slot  = "hero"
        basis = f"article_title='{(article_title or keyword)[:60]}'"
        specific = (
            f"Hero image for article: '{(article_title or keyword)[:80]}'. "
            f"Key concepts: {heading_concepts}. "
        ) if heading_concepts else ""

    elif role == "section":
        heading_concepts = _extract_heading_concepts(section_heading)
        slot  = f"section_{section_heading[:30]}"
        basis = f"heading='{section_heading[:60]}'"

        para_hint = ""
        if paragraph_snippet:
            para_words   = re.sub(r"[^a-z0-9\s]", " ", paragraph_snippet.lower()).split()
            para_concepts = [w for w in para_words if w not in STOPWORDS and len(w) > 4]
            if para_concepts:
                para_hint = f"Context: {' '.join(para_concepts[:6])}. "

        specific = (
            f"Illustrating concept: '{section_heading[:80]}'. "
            f"Visual focus: {heading_concepts}. "
            f"{para_hint}"
        ) if heading_concepts else f"Illustrating: '{section_heading[:80]}'. "

    else:
        # CTA or unknown: generic topic-level image
        slot     = f"cta_{role}"
        basis    = f"topic={topic_category}"
        specific = ""

    # Compose final prompt — concise for Flux (target 200–450 chars)
    full_prompt = (
        f"{specific}"
        f"{topic_base} "
        f"{STYLE_SUFFIX} "
        f"{UNIVERSAL_EXCLUSIONS}"
    ).strip()

    log.info(
        f"[AI_IMAGE_PROMPT] slot={slot}  "
        f"category={topic_category}  "
        f"basis={basis}  "
        f"prompt_len={len(full_prompt)}"
    )

    return ImagePrompt(
        text           = full_prompt,
        topic_category = topic_category,
        slot           = slot,
        basis          = basis,
    )
