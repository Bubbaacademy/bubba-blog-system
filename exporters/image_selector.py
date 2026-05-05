"""
image_selector.py — ImageSelectionService: per-post image selection engine.

ARCHITECTURE (v3 — AI-first)
------------------------------
TWO image sources, strict separation:

  A. Section / Hero images — AI GENERATED or PEXELS FALLBACK (never static)
     ─────────────────────────────────────────────────────────────────────────
     1. Route keyword+cluster → topic_category
     2. Generate topic+heading-specific prompt (image_prompt_generator)
     3. Get image from provider chain (image_provider):
        a. DALL-E 3 (if OPENAI_API_KEY) → upload to HubSpot Files → permanent URL
        b. Pexels API (if PEXELS_API_KEY) → Pexels CDN URL
        c. None → section image skipped; article still publishes
     4. Apply global dedup (registry) and within-post dedup
     5. Return URL or None

  B. CTA images — STATIC CATALOG only (image_catalog.py)
     ──────────────────────────────────────────────────────
     The 3 warehouse CTA images are brand-consistent decorative blocks.
     They are never used for section/hero roles.
     CTA images with reusable_cta=True freely repeat across posts.

HARD RULES
----------
• Static catalog images NEVER appear in section or hero slots.
• If no image provider is configured: section/hero = None (no fallback).
• If provider fails: section/hero = None (no fallback to warehouse).
• CTA pool exhaustion: cycles through available CTAs (never None for CTA).

PUBLIC API
----------
    service = ImageSelectionService(keyword, cluster, title, slug)
    hero_url    = service.hero(article_intro_text)
    section_url = service.section(heading_text, index, paragraph_snippet)
    cta_url     = service.cta(slot)
    # After validation + API success:
    service.commit(post_slug, post_title, keyword, cluster)
    report = service.validation_report()
"""
from __future__ import annotations

import re
import datetime
import logging
from dataclasses import dataclass
from collections import Counter
from typing import Optional

from exporters.image_policy import (
    BLOCKED_TAGS, BLOCKED_DESCRIPTION_WORDS, NOISE_TAGS, STOPWORDS,
    QUALITY_THRESHOLD, STATUS_APPROVED, ROLE_HERO, ROLE_SECTION, ROLE_CTA,
    MAX_SECTION_IMAGES,
)
from exporters.image_catalog import (
    ImageEntry, IMAGE_CATALOG, get_approved,
    APPROVED_IDS, APPROVED_PEXELS_IDS,
)
from exporters.image_router import route
from exporters.image_registry import get_registry, RegistryEntry, ImageRegistry
from exporters.image_prompt_generator import generate_prompt, ImagePrompt
from exporters.image_provider import get_provider, ImageAsset
import exporters.image_logging as ilog

log = logging.getLogger("image_selector")


# ─────────────────────────────────────────────────────────────────────────────
# ImageSelection — result record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImageSelection:
    """Immutable record of a single image selection decision."""
    image_id:        str
    url:             str
    role:            str         # hero / section / cta
    category:        str
    visual_cluster:  str
    relevance_score: float
    context:         str         # section heading or "" for hero/cta
    source:          str         # "openai" | "pexels" | "static_catalog"
    search_query:    str         # Pexels query or "" for AI/static
    prompt_hash:     str         # sha256 prefix of prompt text
    provider_id:     str         # provider's own ID (HS file ID, Pexels photo ID)


# ─────────────────────────────────────────────────────────────────────────────
# Static catalog CTA gate evaluator
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_cta_gates(entry: ImageEntry) -> tuple:
    """
    Gate check for static catalog CTA images.
    Returns (passed: bool, reason: str, score: float).
    Score is always 1.0 for CTAs (decorative, not relevance-scored).
    """
    if entry.status != STATUS_APPROVED:
        return False, f"REJECTED_DISABLED:status={entry.status}", 0.0

    if entry.quality_score < QUALITY_THRESHOLD:
        return False, f"REJECTED_LOW_QUALITY:{entry.quality_score:.2f}", 0.0

    tags_lower = {t.lower() for t in entry.tags}
    hit = tags_lower & BLOCKED_TAGS
    if hit:
        return False, f"REJECTED_BLOCKED_TAG:{sorted(hit)}", 0.0

    desc_words = set(entry.description.lower().split())
    hit2 = desc_words & BLOCKED_DESCRIPTION_WORDS
    if hit2:
        return False, f"REJECTED_BLOCKED_DESCRIPTION:{sorted(hit2)}", 0.0

    return True, "APPROVED_CTA", 1.0


# ─────────────────────────────────────────────────────────────────────────────
# ImageSelectionService
# ─────────────────────────────────────────────────────────────────────────────

class ImageSelectionService:
    """
    Per-post image selection orchestrator.

    Section/Hero: AI generation (DALL-E 3) or Pexels fallback — NEVER static warehouse.
    CTA: Static catalog only (3 reusable brand images).

    Usage:
        service = ImageSelectionService(keyword, cluster, title, slug)
        service.hero(intro_text)
        service.section(heading, 0, paragraph_snippet)
        service.cta(0); service.cta(1); service.cta(2)
        # After publish succeeds:
        service.commit(slug, title, keyword, cluster)
    """

    def __init__(
        self,
        article_keyword:       str = "",
        article_topic_cluster: str = "",
        article_title:         str = "",
        article_slug:          str = "",
        _provider=None,     # injectable for tests
        _registry=None,     # injectable for tests
    ):
        self._keyword  = article_keyword
        self._cluster  = article_topic_cluster
        self._title    = article_title
        self._slug     = article_slug or re.sub(r"[^a-z0-9\-]", "-", article_title.lower())[:60]

        # Topic category drives prompt generation and provider queries
        self._topic_category: str = route(article_keyword, article_topic_cluster)

        # Registry — singleton (Sheets-backed)
        self._registry: ImageRegistry = _registry if _registry is not None else get_registry()

        # Image provider — injectable for tests
        self._provider = _provider if _provider is not None else get_provider()

        # Per-post state
        self._used_urls:     set  = set()
        self._used_clusters: set  = set()
        self._selections:    list = []   # list[ImageSelection]
        self._section_count: int  = 0

        log.info(
            f"[IMAGE_ROUTE] article='{article_title[:60]}'  "
            f"keyword='{article_keyword}'  cluster='{article_topic_cluster}'  "
            f"topic_category='{self._topic_category}'  "
            f"provider='{self._provider.name}'"
        )

    # ── Internal: record a selection locally ─────────────────────────────────

    def _record(self, sel: ImageSelection) -> None:
        """Register selection to local post state (no Sheets write yet)."""
        self._used_urls.add(sel.url)
        self._used_clusters.add(sel.visual_cluster)
        self._selections.append(sel)

    # ── Internal: get image from provider + check dedup ──────────────────────

    def _fetch_image(
        self,
        role: str,
        context: str,
        paragraph_snippet: str = "",
    ) -> "ImageAsset | None":
        """
        Generate a prompt, call the provider, check dedup, return ImageAsset or None.
        """
        prompt = generate_prompt(
            role             = role,
            keyword          = self._keyword,
            topic_category   = self._topic_category,
            section_heading  = context if role == ROLE_SECTION else "",
            paragraph_snippet= paragraph_snippet,
            article_title    = self._title if role == ROLE_HERO else "",
        )

        asset = self._provider.get_image(
            prompt       = prompt,
            article_slug = self._slug,
            slot_name    = f"{role}_{self._section_count}" if role == ROLE_SECTION else role,
            registry     = self._registry,
            used_urls    = self._used_urls,
        )

        return asset

    # ── Public selection API ──────────────────────────────────────────────────

    def hero(self, context: str = "") -> "str | None":
        """
        Select hero image for the article.

        Uses AI generation or Pexels — NEVER static catalog.
        Returns URL or None (section skipped gracefully).
        """
        asset = self._fetch_image(ROLE_HERO, context or self._keyword)

        if asset is None:
            log.info(
                f"[IMAGE_SKIPPED] role=hero  "
                f"reason=NO_IMAGE_RETURNED_BY_PROVIDER_{self._provider.name.upper()}  "
                f"topic='{self._topic_category}'"
            )
            return None

        sel = ImageSelection(
            image_id        = asset.image_id,
            url             = asset.url,
            role            = ROLE_HERO,
            category        = self._topic_category,
            visual_cluster  = asset.visual_cluster,
            relevance_score = 1.0,
            context         = context,
            source          = asset.provider,
            search_query    = asset.search_query,
            prompt_hash     = asset.prompt_hash,
            provider_id     = asset.provider_id,
        )
        self._record(sel)
        log.info(
            f"[IMAGE_INSERTED] role=hero  "
            f"source={asset.provider}  "
            f"provider_id={asset.provider_id}  "
            f"url={asset.url[:80]}"
        )
        return asset.url

    def section(
        self,
        context: str = "",
        index: int = 0,
        paragraph_snippet: str = "",
    ) -> "str | None":
        """
        Select section image for the given heading/context.

        Uses AI generation or Pexels — NEVER static catalog.
        Returns URL or None. None means: no relevant image found.
        Article still publishes.

        Max MAX_SECTION_IMAGES per article.
        """
        if self._section_count >= MAX_SECTION_IMAGES:
            log.info(
                f"[IMAGE_SKIPPED] role=section  "
                f"reason=MAX_SECTION_IMAGES_REACHED({MAX_SECTION_IMAGES})  "
                f"context='{context[:60]}'"
            )
            return None

        asset = self._fetch_image(ROLE_SECTION, context, paragraph_snippet)

        if asset is None:
            log.info(
                f"[IMAGE_SKIPPED] role=section  "
                f"reason=NO_RELEVANT_IMAGE_FOUND  "
                f"context='{context[:60]}'"
            )
            return None

        sel = ImageSelection(
            image_id        = asset.image_id,
            url             = asset.url,
            role            = ROLE_SECTION,
            category        = self._topic_category,
            visual_cluster  = asset.visual_cluster,
            relevance_score = 1.0,
            context         = context,
            source          = asset.provider,
            search_query    = asset.search_query,
            prompt_hash     = asset.prompt_hash,
            provider_id     = asset.provider_id,
        )
        self._record(sel)
        self._section_count += 1
        log.info(
            f"[IMAGE_INSERTED] role=section  "
            f"source={asset.provider}  "
            f"provider_id={asset.provider_id}  "
            f"url={asset.url[:80]}"
        )
        return asset.url

    def cta(self, slot: int = 0) -> "str | None":
        """
        Select CTA image using AI generation (same provider as section/hero).

        Returns URL or None.
        - NEVER uses the static catalog.
        - NEVER uses Pexels.
        - If AI provider is unavailable or returns None → CTA block publishes
          without an image (caller must handle None gracefully).
        """
        context = f"cta_slot_{slot}_{self._topic_category}"
        asset = self._fetch_image(ROLE_CTA, context)

        if asset is None:
            log.info(
                f"[IMAGE_SKIPPED] role=cta  slot={slot}  "
                f"reason=NO_AI_IMAGE_RETURNED  "
                f"provider={self._provider.name}  "
                f"topic='{self._topic_category}'"
            )
            return None

        sel = ImageSelection(
            image_id        = asset.image_id,
            url             = asset.url,
            role            = ROLE_CTA,
            category        = self._topic_category,
            visual_cluster  = asset.visual_cluster,
            relevance_score = 1.0,
            context         = context,
            source          = asset.provider,
            search_query    = asset.search_query,
            prompt_hash     = asset.prompt_hash,
            provider_id     = asset.provider_id,
        )
        self._record(sel)
        log.info(
            f"[IMAGE_INSERTED] role=cta  slot={slot}  "
            f"source={asset.provider}  "
            f"provider_id={asset.provider_id}  "
            f"url={asset.url[:80]}"
        )
        return asset.url

    # ── Registry commit ───────────────────────────────────────────────────────

    def commit(
        self,
        post_slug: str,
        post_title: str,
        article_keyword: str,
        topic_cluster: str,
    ) -> None:
        """
        Write all selections for this post to the Sheets registry.

        Call ONLY after:
          1. All image selection complete.
          2. Pre-publish validation passed.
          3. HubSpot API POST succeeded (or DRY_RUN).
        """
        if not self._selections:
            log.info(f"[IMAGE_REGISTRY_WRITTEN] No selections to commit for '{post_slug}'")
            return

        ts      = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        entries = []

        for sel in self._selections:
            entries.append(RegistryEntry(
                post_slug            = post_slug,
                post_title           = post_title,
                article_keyword      = article_keyword,
                topic_cluster        = topic_cluster,
                image_id             = sel.image_id,
                image_url            = sel.url,
                image_type           = sel.role,
                category             = sel.category,
                visual_cluster       = sel.visual_cluster,
                selected_for_section = sel.context,
                search_query         = sel.search_query,
                image_source         = sel.source,
                relevance_score      = sel.relevance_score,
                prompt_used          = sel.prompt_hash,
                provider_image_id    = sel.provider_id,
                selected_at          = ts,
            ))

        self._registry.register(entries)
        ilog.log_registry_written(post_slug, len(entries))

    # ── Validation report ─────────────────────────────────────────────────────

    def validation_report(self) -> dict:
        """
        Summary for publisher.py logging and hubspot.json 'images' field.

        'unverified_ids' only flags static catalog images not in APPROVED_IDS.
        AI-generated and Pexels images are always considered verified.
        """
        all_ids  = [s.image_id for s in self._selections]
        counts   = Counter(all_ids)
        dupes    = [i for i, n in counts.items() if n > 1]

        unverif = [
            s.image_id for s in self._selections
            if s.source == "static_catalog" and s.image_id not in APPROVED_IDS
        ]
        sections = [s for s in self._selections if s.role == ROLE_SECTION]

        return {
            "image_count":         len(all_ids),
            "unique_image_count":  len(set(all_ids)),
            "section_images":      len(sections),
            "duplicate_ids":       dupes or "none",
            "unverified_ids":      unverif or "none",
            "topic_category":      self._topic_category,
            "provider":            self._provider.name,
            "selections": [
                {
                    "role":           s.role,
                    "id":             s.image_id[:16],
                    "category":       s.category,
                    "visual_cluster": s.visual_cluster,
                    "score":          round(s.relevance_score, 4),
                    "source":         s.source,
                    "search_query":   s.search_query[:60] if s.search_query else "",
                    "context":        s.context[:60],
                }
                for s in self._selections
            ],
        }
