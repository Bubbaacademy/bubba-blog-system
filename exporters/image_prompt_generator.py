"""
image_prompt_generator.py — Generate precise DALL-E 3 image prompts from article context.

WHY THIS EXISTS
---------------
Stock photo APIs (Pexels, Unsplash) return "ecommerce" or "Amazon" images that are
dominated by warehouse/fulfillment center photography — even for articles about PPC,
AI tools, or product research. The visual category "Amazon seller" on stock sites =
warehouse, which is wrong for 80% of our content.

AI generation solves this by letting us specify exactly what to show AND what to exclude.
DALL-E 3 follows prompt instructions reliably when exclusions are embedded in the prompt.

PROMPT FORMULA (per slot)
--------------------------
  [quality_prefix] + [specific_visual_subject] + [topic_context] + [style_guide] + [exclusions]

Every prompt is:
  - topic-category specific (different base for PPC vs AI vs FBA vs product research)
  - section-heading specific (the heading drives the specific subject)
  - article-keyword specific (for additional context)
  - complete: no implicit fallbacks, exclusions baked in

REUSE
-----
Change TOPIC_VISUAL_BASES to match any other vertical.
UNIVERSAL_EXCLUSIONS and STYLE_SUFFIX are project-agnostic.
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

# ── Prompt constants ───────────────────────────────────────────────────────────

STYLE_SUFFIX = (
    "Photography style: professional editorial, clean composition, sharp focus, "
    "modern corporate aesthetic, warm neutral tones or cool blue-tones depending on topic, "
    "no people unless clearly contextually relevant and professional, "
    "no text overlays, no watermarks, no stock-photo clichés. "
    "Ultra-realistic, 16:9 landscape format, high resolution."
)

UNIVERSAL_EXCLUSIONS = (
    "EXCLUDE: no text, no words, no letters, no numbers overlay, no logos, "
    "no brand names, no Amazon logo, no Amazon branding, "
    "no food, no cooking, no kitchen, no restaurant, no meals, "
    "no animals, no pets, no children, no casual lifestyle, "
    "no beach, no vacation, no nature landscapes, "
    "no abstract art, no illustration, no cartoon, no graphic design, "
    "no gym, no fitness, no medical, no healthcare, "
    "no distorted faces, no AI-artifact hands, "
    "no generic stock photo clichés (no handshakes, no fake smiling)."
)

# ── Per-topic visual base descriptions ────────────────────────────────────────
# These describe what kind of imagery is APPROPRIATE for each topic category.
# Section heading + keyword are layered on top of these bases.

TOPIC_VISUAL_BASES: dict = {
    CAT_AMAZON_ADS: (
        "A professional business analytics workspace showing digital advertising "
        "campaign performance. Clean monitor displays with charts, graphs, "
        "performance metrics. Modern office environment with warm desk lighting. "
        "Focus: digital marketing analytics, campaign optimization dashboard. "
        "NOT a warehouse. NOT shipping boxes. NOT products on shelves. "
        "This is a digital marketing topic."
    ),
    CAT_AI_TOOLS: (
        "A futuristic but grounded technology workspace showing AI-powered business software. "
        "Clean laptop or monitor displaying data analytics interface with clean UI. "
        "Subtle blue-white ambient lighting suggesting technology. "
        "Modern professional workspace or server room concept. "
        "Focus: artificial intelligence, automation, business technology tools. "
        "NOT a warehouse. NOT shipping. NOT physical products on shelves. "
        "This is a technology and software topic."
    ),
    CAT_FBA_LOGISTICS: (
        "A modern Amazon fulfillment center or professional logistics warehouse. "
        "Organized shelving racks with packages, clean operations floor, "
        "professional workers managing inventory. "
        "Focus: ecommerce fulfillment, warehouse operations, shipping logistics. "
        "Appropriate: warehouse, shelves, boxes, packages, logistics."
    ),
    CAT_PRODUCT_RESEARCH: (
        "A professional business analyst or entrepreneur at a clean modern desk, "
        "reviewing product research data on a laptop or multiple monitors. "
        "Screen shows charts, market data, product comparisons. "
        "Focus: product analysis, data-driven research, business strategy. "
        "NOT a warehouse. NOT shipping boxes. NOT warehouse shelving. "
        "This is a data analysis and research topic."
    ),
    CAT_SOURCING: (
        "A modern manufacturing facility or professional supply chain operation. "
        "Clean factory floor, quality control inspection, or supplier meeting. "
        "Professional industrial environment. "
        "Focus: manufacturing, sourcing, quality control, supply chain. "
        "NOT warehouse shelving. NOT Amazon packaging. "
        "This is a manufacturing and supplier topic."
    ),
    CAT_LISTING_OPTIMIZATION: (
        "A clean professional product photography studio setup or ecommerce listing "
        "displayed on a laptop screen. Product spotlight lighting, clean white background, "
        "or ecommerce interface on screen. "
        "Focus: product presentation, listing quality, visual optimization. "
        "NOT a warehouse. NOT shipping. "
        "This is a product listing and photography topic."
    ),
    CAT_ECOM_STRATEGY: (
        "A professional entrepreneur or business strategist at a modern workspace, "
        "reviewing growth charts or business plans. Clean desk, laptop, "
        "business charts on screen or whiteboard. "
        "Focus: business strategy, revenue growth, planning, entrepreneurship. "
        "NOT a warehouse. NOT shipping boxes. "
        "This is a business strategy topic."
    ),
    CAT_BRAND_BUILDING: (
        "A professional brand design or marketing strategy session. "
        "Brand mood board, color palette swatches, packaging design samples, "
        "or marketing materials laid out professionally. "
        "Focus: brand identity, design, marketing, business branding. "
        "NOT a warehouse. NOT logistics. "
        "This is a brand and marketing topic."
    ),
    CAT_AMAZON_COMPLIANCE: (
        "A professional business compliance or legal review setting. "
        "Clean desk with documents, laptop showing policy dashboard, "
        "professional business environment suggesting review and compliance. "
        "Focus: business policy, account management, professional review. "
        "NOT a warehouse. NOT food. "
        "This is a business compliance topic."
    ),
    CAT_PRIVATE_LABEL: (
        "Custom product packaging and branding materials in a professional studio. "
        "Clean product boxes with modern branding, label design samples, "
        "private label product close-up with clean background. "
        "Focus: custom branding, packaging design, product identity. "
        "NOT a warehouse. NOT generic stock. "
        "This is a private label branding topic."
    ),
    CAT_AMAZON_FOUNDATION: (
        "A motivated entrepreneur or small business owner at a clean modern home office, "
        "working on a laptop with ecommerce seller interface visible on screen. "
        "Professional but accessible setting suggesting starting an online business. "
        "Focus: ecommerce entrepreneurship, starting a business, online selling basics. "
        "NOT a warehouse unless the article specifically discusses logistics. "
        "NOT food, NOT random lifestyle. "
        "This is a beginner Amazon seller education topic."
    ),
    CAT_GENERAL_BUSINESS: (
        "A clean professional business workspace or meeting environment. "
        "Modern office, laptop with business dashboard, or professional team collaboration. "
        "Focus: business strategy, professional work environment, growth mindset. "
        "NOT food, NOT warehouse, NOT lifestyle. "
        "This is a general professional business topic."
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# ImagePrompt dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImagePrompt:
    """
    A fully constructed image generation prompt.

    Fields
    ------
    text          Full prompt text sent to DALL-E 3 (or used as Pexels query)
    topic_category Topic category this prompt was built for
    slot          "hero" | "section_0" | "section_1" | "cta_0" etc.
    basis         Human-readable explanation of what drove the prompt (for logging)
    prompt_hash   SHA-256 of text[:8] prefix — used as registry image_id for AI images
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
        """
        Condensed version of the prompt suitable as a Pexels search query.
        Takes the first meaningful noun phrase from the prompt.
        """
        # Extract meaningful words from the first sentence of the topic base
        first_sentence = self.text.split(".")[0]
        words = re.sub(r"[^a-z0-9\s]", " ", first_sentence.lower()).split()
        meaningful = [w for w in words if w not in STOPWORDS and len(w) > 3]
        return " ".join(meaningful[:6]) if meaningful else "professional business ecommerce"


# ─────────────────────────────────────────────────────────────────────────────
# Prompt generation
# ─────────────────────────────────────────────────────────────────────────────

def _extract_heading_concepts(heading: str) -> str:
    """Extract 4–6 key concept words from a section heading."""
    clean = re.sub(r"[^a-z0-9\s]", " ", heading.lower())
    words = [w for w in clean.split() if w not in STOPWORDS and len(w) > 3]
    return " ".join(words[:6]) if words else ""


def generate_prompt(
    role: str,
    keyword: str,
    topic_category: str,
    section_heading: str = "",
    paragraph_snippet: str = "",
    article_title: str = "",
) -> ImagePrompt:
    """
    Generate a complete DALL-E 3 image prompt for a single image slot.

    Parameters
    ----------
    role             : "hero" | "section" | "cta"
    keyword          : article main keyword
    topic_category   : routing category (e.g. "amazon_ads_digital")
    section_heading  : section heading text (for section images)
    paragraph_snippet: first ~200 chars of the section paragraph (for context)
    article_title    : article title (for hero images)

    Returns
    -------
    ImagePrompt with fully constructed text ready for DALL-E 3
    """
    topic_base = TOPIC_VISUAL_BASES.get(
        topic_category,
        TOPIC_VISUAL_BASES[CAT_GENERAL_BUSINESS],
    )

    if role == "hero":
        # Hero: article-level visual, based on title + keyword
        heading_concepts = _extract_heading_concepts(article_title or keyword)
        slot = "hero"
        basis = f"article_title='{(article_title or keyword)[:60]}'"
        specific = (
            f"The primary hero image for a business blog article titled: '{article_title or keyword}'. "
            f"Key concepts to visualize: {heading_concepts}. "
        ) if heading_concepts else ""

    elif role == "section":
        # Section: heading-specific visual
        heading_concepts = _extract_heading_concepts(section_heading)
        slot = f"section_{section_heading[:30]}"
        basis = f"heading='{section_heading[:60]}'"

        # Extract additional context from paragraph snippet if provided
        para_hint = ""
        if paragraph_snippet:
            para_words = re.sub(r"[^a-z0-9\s]", " ", paragraph_snippet.lower()).split()
            para_concepts = [w for w in para_words if w not in STOPWORDS and len(w) > 4]
            if para_concepts:
                para_hint = f"Additional context from article section: {' '.join(para_concepts[:8])}. "

        specific = (
            f"An image illustrating the concept: '{section_heading}'. "
            f"Key visual concepts: {heading_concepts}. "
            f"{para_hint}"
        ) if heading_concepts else f"An image illustrating: '{section_heading}'. "

    else:
        # CTA or unknown: generic topic-level image
        slot = f"cta_{role}"
        basis = f"topic={topic_category}"
        specific = ""

    # Compose final prompt
    quality_prefix = (
        "Professional editorial business photography, ultra-realistic, "
        "16:9 landscape, high-quality stock photo alternative. "
    )

    full_prompt = (
        f"{quality_prefix}"
        f"{specific}"
        f"Topic category: {topic_category.replace('_', ' ')}. "
        f"{topic_base} "
        f"{STYLE_SUFFIX} "
        f"{UNIVERSAL_EXCLUSIONS}"
    )

    log.info(
        f"[IMAGE_PROMPT] slot={slot}  "
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
