"""
image_provider.py — Provider abstraction for section/hero image sourcing.

PROVIDER CHAIN (tried in order)
--------------------------------
  1. OpenAIImageProvider  — DALL-E 3 (requires OPENAI_API_KEY)
     Generates topic-specific images from exact prompts.
     Images uploaded to HubSpot Files for permanent hosting.

  2. PexelsImageProvider  — Pexels API (requires PEXELS_API_KEY)
     Searches stock photos using the prompt as a search query.
     Stronger negative filters applied before accepting an image.

  3. NullImageProvider    — Always returns None.
     Article publishes without this section image.
     Never uses static warehouse catalog as fallback.

DEDUPLICATION
-------------
Both providers check registry before accepting an image:
- Global: image URL or provider_id already used in a prior post → reject
- Local: image URL already used in this post → reject

IMAGE ASSETS
------------
ImageAsset carries:
- url           : permanent URL usable in <img src="...">
- provider      : "openai" | "pexels" | "none"
- provider_id   : DALL-E revised_prompt hash or Pexels photo ID
- prompt_hash   : SHA-256 prefix of the prompt text
- search_query  : Pexels query used (or "" for AI)
- visual_cluster: for diversity tracking

ENVIRONMENT VARIABLES
---------------------
OPENAI_API_KEY   → enables DALL-E 3 generation
PEXELS_API_KEY   → enables Pexels search (fallback)
IMAGE_PROVIDER   → force override: "openai" | "pexels" | "none"
HUBSPOT_TOKEN    → required for uploading AI images to HubSpot Files
"""
from __future__ import annotations

import os
import re
import time
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("image_provider")


# ─────────────────────────────────────────────────────────────────────────────
# ImageAsset — result of a successful image provision
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImageAsset:
    """A sourced image ready to embed in a blog post."""
    url:            str      # permanent URL for <img src="">
    provider:       str      # "openai" | "pexels" | "static_catalog"
    provider_id:    str      # DALL-E prompt_hash or Pexels photo ID
    prompt_hash:    str      # sha256 prefix of prompt text (always set)
    search_query:   str      # Pexels query (or "" for AI)
    visual_cluster: str      # for cross-post diversity tracking
    alt_text:       str      # descriptive alt text for accessibility

    @property
    def image_id(self) -> str:
        """Canonical dedup key: provider_id when available, else prompt_hash."""
        return self.provider_id or self.prompt_hash


# ─────────────────────────────────────────────────────────────────────────────
# Base provider class
# ─────────────────────────────────────────────────────────────────────────────

class ImageProvider:
    """Abstract base. Subclasses implement get_image()."""

    @property
    def name(self) -> str:
        return "base"

    @property
    def available(self) -> bool:
        return False

    def get_image(
        self,
        prompt,              # ImagePrompt
        article_slug: str,
        slot_name: str,
        registry,            # ImageRegistry — for global dedup
        used_urls: set,      # within-post dedup
    ) -> "ImageAsset | None":
        return None


# ─────────────────────────────────────────────────────────────────────────────
# OpenAI DALL-E 3 provider
# ─────────────────────────────────────────────────────────────────────────────

class OpenAIImageProvider(ImageProvider):
    """
    Generates images using DALL-E 3 and hosts them on HubSpot Files API.

    Each generated image is:
    - Topic-specific (prompt controls the subject precisely)
    - Uploaded to HubSpot Files (permanent URL)
    - Registered in the Image Registry after successful publication

    Cost: ~$0.04–$0.08 per image (standard/HD quality at 1792×1024).
    Rate limit: varies by tier; we add 0.5s delay between calls.
    """

    _DALL_E_MODEL = "dall-e-3"
    _SIZE         = "1792x1024"   # best landscape size for DALL-E 3
    _QUALITY      = "standard"    # "standard" (~$0.04) or "hd" (~$0.08)
    _STYLE        = "natural"     # "natural" (photographic) or "vivid" (artistic)
    _LAST_CALL    = 0.0

    def __init__(self):
        self._api_key      = os.environ.get("OPENAI_API_KEY", "").strip()
        self._pkg_available = self._check_package()

        if not self._pkg_available:
            log.error(
                "[AI_IMAGE_FAILED] openai Python package is not installed. "
                "Add 'openai>=1.0.0' to requirements.txt and redeploy. "
                "No AI images will be generated until this is fixed."
            )
        elif self._api_key:
            log.info("[IMAGE_PROVIDER] OpenAIImageProvider ready (DALL-E 3)")
        else:
            log.warning(
                "[IMAGE_PROVIDER] OPENAI_API_KEY not set — "
                "OpenAI provider disabled. Set OPENAI_API_KEY in Render env vars."
            )

    @staticmethod
    def _check_package() -> bool:
        """Return True if the openai package is importable."""
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def name(self) -> str:
        return "openai"

    @property
    def available(self) -> bool:
        return bool(self._api_key) and self._pkg_available

    def get_image(self, prompt, article_slug, slot_name, registry, used_urls) -> "ImageAsset | None":
        if not self._pkg_available:
            log.error(
                f"[AI_IMAGE_FAILED] slot={slot_name}  "
                f"reason=OPENAI_PACKAGE_NOT_INSTALLED  "
                f"fix='add openai>=1.0.0 to requirements.txt and redeploy'"
            )
            return None

        if not self._api_key:
            log.error(
                f"[AI_IMAGE_FAILED] slot={slot_name}  "
                f"reason=OPENAI_API_KEY_NOT_SET  "
                f"fix='set OPENAI_API_KEY in Render environment variables'"
            )
            return None

        # Rate limit: 0.5s between calls
        elapsed = time.time() - OpenAIImageProvider._LAST_CALL
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)

        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key)

            log.info(
                f"[IMAGE_PROVIDER] Generating DALL-E 3 image  "
                f"slot={slot_name}  "
                f"prompt_hash={prompt.prompt_hash}  "
                f"prompt_len={len(prompt.text)}"
            )

            response = client.images.generate(
                model   = self._DALL_E_MODEL,
                prompt  = prompt.text[:4000],
                n       = 1,
                size    = self._SIZE,
                quality = self._QUALITY,
                style   = self._STYLE,
            )

            OpenAIImageProvider._LAST_CALL = time.time()

            temp_url       = response.data[0].url
            revised_prompt = getattr(response.data[0], "revised_prompt", "") or prompt.text
            provider_id    = hashlib.sha256(revised_prompt.encode()).hexdigest()[:16]

            log.info(
                f"[AI_IMAGE_GENERATED] provider=dall-e-3  "
                f"slot={slot_name}  "
                f"provider_id={provider_id}  "
                f"prompt_hash={prompt.prompt_hash}  "
                f"temp_url={temp_url[:80]}"
            )

            # Global dedup check before uploading
            if registry.is_globally_used(provider_id):
                log.info(
                    f"[IMAGE_PROVIDER] provider_id={provider_id} "
                    f"already in registry — skipping upload"
                )
                return None

            # Upload to HubSpot Files for permanent URL
            from exporters.hubspot_files import upload_image_to_hubspot
            permanent_url = upload_image_to_hubspot(
                source_url   = temp_url,
                article_slug = article_slug,
                slot_name    = slot_name,
            )

            if not permanent_url:
                log.error(
                    f"[AI_IMAGE_FAILED] slot={slot_name}  "
                    f"reason=HUBSPOT_FILES_UPLOAD_FAILED  "
                    f"provider_id={provider_id}  "
                    f"check=HUBSPOT_TOKEN env var and /bubba-blog-images folder access"
                )
                return None

            # Within-post dedup check
            if permanent_url in used_urls:
                log.info(
                    f"[IMAGE_PROVIDER] url already used in this post — skipping  "
                    f"url={permanent_url[:60]}"
                )
                return None

            log.info(
                f"[HUBSPOT_FILE_UPLOADED] slot={slot_name}  "
                f"provider_id={provider_id}  "
                f"permanent_url={permanent_url[:80]}"
            )

            visual_cluster = f"ai_{prompt.topic_category}_{slot_name.split('_')[0]}"

            return ImageAsset(
                url            = permanent_url,
                provider       = "openai",
                provider_id    = provider_id,
                prompt_hash    = prompt.prompt_hash,
                search_query   = "",
                visual_cluster = visual_cluster,
                alt_text       = f"Professional image for {prompt.topic_category.replace('_', ' ')} article",
            )

        except ImportError:
            # Should not reach here since _check_package() ran at init,
            # but guard defensively.
            log.error(
                f"[AI_IMAGE_FAILED] slot={slot_name}  "
                f"reason=OPENAI_IMPORT_ERROR  "
                f"fix='add openai>=1.0.0 to requirements.txt and redeploy'"
            )
            return None

        except Exception as exc:
            # Classify common OpenAI SDK errors for actionable log messages
            exc_type = type(exc).__name__
            exc_str  = str(exc)

            if "AuthenticationError" in exc_type or "authentication" in exc_str.lower():
                reason = "OPENAI_API_KEY_INVALID"
                fix    = "verify OPENAI_API_KEY in Render env vars"
            elif "RateLimitError" in exc_type or "rate_limit" in exc_str.lower():
                reason = "OPENAI_RATE_LIMIT"
                fix    = "reduce generation frequency or upgrade OpenAI tier"
            elif "ContentPolicyViolation" in exc_type or "content_policy" in exc_str.lower():
                reason = "DALL_E_CONTENT_POLICY_VIOLATION"
                fix    = "review prompt for policy-violating terms"
            elif "InvalidRequestError" in exc_type or "invalid_request" in exc_str.lower():
                reason = "OPENAI_INVALID_REQUEST"
                fix    = f"check prompt length and API params — {exc_str[:100]}"
            else:
                reason = f"OPENAI_API_ERROR:{exc_type}"
                fix    = str(exc_str)[:120]

            log.error(
                f"[AI_IMAGE_FAILED] slot={slot_name}  "
                f"reason={reason}  "
                f"fix='{fix}'"
            )
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Pexels provider (fallback)
# ─────────────────────────────────────────────────────────────────────────────

class PexelsImageProvider(ImageProvider):
    """
    Fallback provider using Pexels API.

    Uses the prompt's pexels_query as the search string.
    Applies additional negative filters specifically for the topic category
    to reduce warehouse/food/lifestyle contamination.

    NOTE: Pexels has known bias — "amazon ecommerce" queries return warehouse
    imagery regardless of article topic. This provider is acceptable only
    for topics where warehouse imagery is either appropriate (FBA) or
    where the query is specific enough to avoid it.
    """

    @property
    def name(self) -> str:
        return "pexels"

    @property
    def available(self) -> bool:
        from exporters.image_fetcher import get_pexels_client
        return get_pexels_client().available

    def get_image(self, prompt, article_slug, slot_name, registry, used_urls) -> "ImageAsset | None":
        from exporters.image_fetcher import get_pexels_client, fetch_and_score
        from exporters.image_policy import TOPIC_NEGATIVE_TERMS, CAT_FBA_LOGISTICS

        client = get_pexels_client()
        if not client.available:
            return None

        # For non-FBA topics, add extra warehouse-blocking terms
        if prompt.topic_category != CAT_FBA_LOGISTICS:
            extra_blocked = frozenset({"warehouse", "fulfillment", "forklift",
                                       "pallet", "shipping boxes", "shelving rack",
                                       "inventory storage"})
        else:
            extra_blocked = frozenset()

        # Use the prompt-derived Pexels query
        query = prompt.pexels_query

        log.info(
            f"[IMAGE_PROVIDER] Pexels search  "
            f"slot={slot_name}  query='{query}'  "
            f"topic={prompt.topic_category}"
        )

        candidates = fetch_and_score(
            queries        = [query],
            context        = slot_name,
            keyword        = query,
            topic_category = prompt.topic_category,
            registry       = registry,
            used_urls      = used_urls,
            used_clusters  = set(),
        )

        if not candidates:
            log.info(
                f"[IMAGE_SKIPPED] slot={slot_name}  "
                f"reason=NO_RELEVANT_PEXELS_IMAGE  "
                f"query='{query}'"
            )
            return None

        score, best = candidates[0]

        # Extra warehouse block for non-FBA topics
        if extra_blocked:
            alt_lower = best.alt.lower()
            if any(term in alt_lower for term in extra_blocked):
                log.info(
                    f"[IMAGE_SKIPPED] slot={slot_name}  "
                    f"reason=PEXELS_WAREHOUSE_CONTAMINATION  "
                    f"alt='{best.alt[:60]}'"
                )
                return None

        log.info(
            f"[IMAGE_SELECTED] role={slot_name}  "
            f"source=pexels  "
            f"id={best.image_id}  "
            f"score={score:.4f}  "
            f"url={best.url[:80]}"
        )

        return ImageAsset(
            url            = best.url,
            provider       = "pexels",
            provider_id    = best.image_id,
            prompt_hash    = prompt.prompt_hash,
            search_query   = query,
            visual_cluster = best.visual_cluster,
            alt_text       = best.alt[:150] if best.alt else "Professional business image",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Null provider — explicit no-image fallback
# ─────────────────────────────────────────────────────────────────────────────

class NullImageProvider(ImageProvider):
    """
    Returns None for every request.

    Used when no API keys are configured.
    Article publishes without section images — never uses warehouse fallback.
    """

    @property
    def name(self) -> str:
        return "none"

    @property
    def available(self) -> bool:
        return True   # always "available" (always returns None cleanly)

    def get_image(self, prompt, article_slug, slot_name, registry, used_urls) -> None:
        log.info(
            f"[IMAGE_SKIPPED] slot={slot_name}  "
            f"reason=NO_IMAGE_PROVIDER_CONFIGURED  "
            f"(set OPENAI_API_KEY or PEXELS_API_KEY)"
        )
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Provider factory
# ─────────────────────────────────────────────────────────────────────────────

_provider: Optional[ImageProvider] = None


def get_provider(force: str = "") -> ImageProvider:
    """
    Return the best available image provider.

    Priority:
      1. OpenAI DALL-E 3 (if OPENAI_API_KEY set)
      2. Pexels (if PEXELS_API_KEY set)
      3. NullImageProvider (always)

    Override:
      Set IMAGE_PROVIDER=openai|pexels|none to force a specific provider.
      Set force= argument to override in code (tests only).
    """
    global _provider

    override = force or os.environ.get("IMAGE_PROVIDER", "").lower()

    if override == "openai":
        p = OpenAIImageProvider()
        if p.available:
            return p
        log.warning("[IMAGE_PROVIDER] IMAGE_PROVIDER=openai but OPENAI_API_KEY not set")
        return NullImageProvider()

    if override == "pexels":
        p = PexelsImageProvider()
        if p.available:
            return p
        log.warning("[IMAGE_PROVIDER] IMAGE_PROVIDER=pexels but PEXELS_API_KEY not set")
        return NullImageProvider()

    if override == "none":
        return NullImageProvider()

    # Auto-select: OpenAI DALL-E 3 only. Pexels is NOT used as an automatic
    # fallback — it returns warehouse/stock images that are indistinguishable
    # from the old static catalog for non-FBA topics.
    # To force Pexels manually: set IMAGE_PROVIDER=pexels (not recommended).
    if _provider is None:
        openai_p = OpenAIImageProvider()
        if openai_p.available:
            _provider = openai_p
            log.info("[IMAGE_PROVIDER] Auto-selected: OpenAI DALL-E 3")
        else:
            _provider = NullImageProvider()
            log.info(
                "[IMAGE_PROVIDER] Auto-selected: None — OPENAI_API_KEY not set. "
                "Articles will publish without images. "
                "Set OPENAI_API_KEY to enable AI-generated images."
            )

    return _provider


def reset_provider() -> None:
    """Force provider re-detection on next call. Tests only."""
    global _provider
    _provider = None
