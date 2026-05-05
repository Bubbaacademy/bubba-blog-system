"""
image_provider.py — Replicate-powered image provider for blog post images.

PROVIDER CHAIN
--------------
  1. ReplicateImageProvider — black-forest-labs/flux-schnell (default)
     Generates topic-specific images via Replicate API.
     Output URL downloaded and uploaded to HubSpot Files for permanent CDN hosting.

  2. NullImageProvider — Always returns None.
     Article publishes without images.
     NEVER falls back to warehouse stock photos.

COST GUARD
----------
Before every Replicate API call, estimated cost is checked against:
  - MAX_IMAGE_COST_PER_POST_USD  (per-post limit, default $0.05)
  - MAX_IMAGE_COST_PER_DAY_USD   (per-day limit, default $1.00)
If either limit would be exceeded: [COST_GUARD_BLOCKED] logged, returns None.
After each successful call: [IMAGE_COST_GUARD] logged with running totals.

APPROVED MODELS
---------------
APPROVED_REPLICATE_MODELS maps model → estimated cost per image (USD):
  black-forest-labs/flux-schnell → $0.003  (default — fast and cheap)
  black-forest-labs/flux-dev     → $0.025  (higher quality, slower)
Any other model name is blocked with [IMAGE_MODEL_BLOCKED] and returns None.

SECURITY
--------
REPLICATE_API_TOKEN is read ONLY from environment variables.
It is NEVER logged, hardcoded, or written to any file.
Log lines that would expose the token use only the first 4 chars + "***".

ENVIRONMENT VARIABLES
---------------------
REPLICATE_API_TOKEN          → required for Replicate Flux generation
REPLICATE_MODEL              → model override (default: black-forest-labs/flux-schnell)
IMAGE_PROVIDER               → force: "replicate" | "none"
MAX_IMAGES_PER_POST          → max images per article (default: 3)
MAX_IMAGE_COST_PER_POST_USD  → per-post USD spend limit (default: 0.05)
MAX_IMAGE_COST_PER_DAY_USD   → per-day USD spend limit (default: 1.00)
HUBSPOT_TOKEN                → required for uploading to HubSpot Files
"""
from __future__ import annotations

import os
import time
import logging
import hashlib
import datetime
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("image_provider")

# ─────────────────────────────────────────────────────────────────────────────
# Approved Replicate model allowlist
# ─────────────────────────────────────────────────────────────────────────────

APPROVED_REPLICATE_MODELS: dict[str, float] = {
    "black-forest-labs/flux-schnell": 0.003,
    "black-forest-labs/flux-dev":     0.025,
}

_DEFAULT_MODEL = "black-forest-labs/flux-schnell"


# ─────────────────────────────────────────────────────────────────────────────
# ImageAsset — result of a successful image provision
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImageAsset:
    """A sourced image ready to embed in a blog post."""
    url:            str      # permanent URL for <img src="">
    provider:       str      # "replicate" | "none"
    provider_id:    str      # prompt hash or model-specific ID
    prompt_hash:    str      # sha256[:16] of prompt text (always set)
    search_query:   str      # Pexels query (or "" for AI)
    visual_cluster: str      # for cross-post diversity tracking
    alt_text:       str      # descriptive alt text for accessibility
    model:          str = "" # model used to generate (e.g. "black-forest-labs/flux-schnell")

    @property
    def image_id(self) -> str:
        """Canonical dedup key: provider_id when set, else prompt_hash."""
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

    def start_post(self) -> None:
        """Reset per-post cost and image counters. Called by ImageSelectionService."""
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Replicate Flux provider
# ─────────────────────────────────────────────────────────────────────────────

class ReplicateImageProvider(ImageProvider):
    """
    Generates images using Replicate black-forest-labs/flux-schnell (default).

    Each generated image is:
    - Topic-specific (prompt controls the subject precisely)
    - Downloaded from Replicate's temporary URL
    - Uploaded to HubSpot Files API (permanent hubspotusercontent.com URL)
    - Registered in the Image Registry after successful publication

    Cost guard enforces per-post and per-day USD limits before every call.
    """

    # Day-level cost tracking shared across all provider instances.
    # Reset automatically when the calendar date changes.
    _day_cost_usd: float = 0.0
    _day_str:      str   = ""  # "YYYY-MM-DD"

    def __init__(self):
        # SECURITY: token is read from env only, never logged in full
        self._token = os.environ.get("REPLICATE_API_TOKEN", "").strip()
        self._model = self._resolve_model()
        self._estimated_cost = APPROVED_REPLICATE_MODELS.get(self._model, 0.003)
        self._max_post_cost  = float(os.environ.get("MAX_IMAGE_COST_PER_POST_USD", "0.05"))
        self._max_day_cost   = float(os.environ.get("MAX_IMAGE_COST_PER_DAY_USD", "1.00"))
        self._max_images     = int(os.environ.get("MAX_IMAGES_PER_POST", "3"))
        self._pkg_available  = self._check_package()

        # Per-post counters (reset via start_post())
        self._post_cost:   float = 0.0
        self._post_images: int   = 0

        if not self._pkg_available:
            log.error(
                "[IMAGE_GENERATION_FAILED] replicate Python package is not installed. "
                "Add 'replicate>=0.25.0' to requirements.txt and redeploy. "
                "No AI images will be generated until this is fixed."
            )
        elif self._token:
            safe_token = self._token[:4] + "***" if len(self._token) > 4 else "***"
            log.info(
                f"[IMAGE_PROVIDER] ReplicateImageProvider ready  "
                f"model={self._model}  "
                f"token_prefix={safe_token}  "
                f"cost_per_image=${self._estimated_cost:.4f}  "
                f"max_post=${self._max_post_cost:.2f}  "
                f"max_day=${self._max_day_cost:.2f}"
            )
        else:
            log.warning(
                "[IMAGE_PROVIDER] REPLICATE_API_TOKEN not set — "
                "Replicate provider disabled. "
                "Set REPLICATE_API_TOKEN in Render environment variables."
            )

    # ── Model resolution ──────────────────────────────────────────────────────

    def _resolve_model(self) -> str:
        """
        Pick and validate the Replicate model.

        1. Read REPLICATE_MODEL env var (or use default flux-schnell).
        2. Check against APPROVED_REPLICATE_MODELS allowlist.
        3. Log [IMAGE_MODEL_BLOCKED] and fall back to default if not approved.
        """
        requested = os.environ.get("REPLICATE_MODEL", _DEFAULT_MODEL).strip()
        if requested in APPROVED_REPLICATE_MODELS:
            return requested
        log.error(
            f"[IMAGE_MODEL_BLOCKED] model='{requested}'  "
            f"reason=NOT_IN_ALLOWLIST  "
            f"approved={sorted(APPROVED_REPLICATE_MODELS.keys())}  "
            f"falling_back_to={_DEFAULT_MODEL}"
        )
        return _DEFAULT_MODEL

    @staticmethod
    def _check_package() -> bool:
        """Return True if the replicate package is importable."""
        try:
            import replicate  # noqa: F401
            return True
        except ImportError:
            return False

    # ── Cost guard ────────────────────────────────────────────────────────────

    def _check_cost_guard(self) -> bool:
        """
        Return True if the next generation call is ALLOWED under cost limits.
        Return False (and log [COST_GUARD_BLOCKED]) if either limit would be exceeded.
        """
        cost  = self._estimated_cost
        today = datetime.date.today().isoformat()

        # Reset day tracking at midnight
        if ReplicateImageProvider._day_str != today:
            ReplicateImageProvider._day_cost_usd = 0.0
            ReplicateImageProvider._day_str      = today

        if self._post_cost + cost > self._max_post_cost:
            log.warning(
                f"[COST_GUARD_BLOCKED] model={self._model}  "
                f"reason=POST_LIMIT  "
                f"post_cost_so_far=${self._post_cost:.4f}  "
                f"estimated=${cost:.4f}  "
                f"post_limit=${self._max_post_cost:.4f}"
            )
            return False

        if ReplicateImageProvider._day_cost_usd + cost > self._max_day_cost:
            log.warning(
                f"[COST_GUARD_BLOCKED] model={self._model}  "
                f"reason=DAY_LIMIT  "
                f"day_cost_so_far=${ReplicateImageProvider._day_cost_usd:.4f}  "
                f"estimated=${cost:.4f}  "
                f"day_limit=${self._max_day_cost:.4f}"
            )
            return False

        return True

    def _record_cost(self, cost: float) -> None:
        """Accumulate cost after a successful generation and log running totals."""
        self._post_cost                          += cost
        ReplicateImageProvider._day_cost_usd     += cost
        self._post_images                        += 1
        log.info(
            f"[IMAGE_COST_GUARD] model={self._model}  "
            f"image_cost=${cost:.4f}  "
            f"post_total=${self._post_cost:.4f}  "
            f"day_total=${ReplicateImageProvider._day_cost_usd:.4f}  "
            f"post_limit=${self._max_post_cost:.2f}  "
            f"day_limit=${self._max_day_cost:.2f}"
        )

    # ── Per-post reset ────────────────────────────────────────────────────────

    def start_post(self) -> None:
        """Reset per-post cost and image counters. Called by ImageSelectionService."""
        self._post_cost   = 0.0
        self._post_images = 0

    # ── Provider interface ────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "replicate"

    @property
    def available(self) -> bool:
        return bool(self._token) and self._pkg_available

    def get_image(self, prompt, article_slug, slot_name, registry, used_urls) -> "ImageAsset | None":
        # ── Package check ──────────────────────────────────────────────────
        if not self._pkg_available:
            log.error(
                f"[IMAGE_GENERATION_FAILED] slot={slot_name}  "
                f"reason=REPLICATE_PACKAGE_NOT_INSTALLED  "
                f"fix='add replicate>=0.25.0 to requirements.txt and redeploy'"
            )
            return None

        # ── Token check ────────────────────────────────────────────────────
        if not self._token:
            log.error(
                f"[IMAGE_GENERATION_FAILED] slot={slot_name}  "
                f"reason=REPLICATE_API_TOKEN_NOT_SET  "
                f"fix='set REPLICATE_API_TOKEN in Render environment variables'"
            )
            return None

        # ── Model allowlist check ──────────────────────────────────────────
        if self._model not in APPROVED_REPLICATE_MODELS:
            log.error(
                f"[IMAGE_MODEL_BLOCKED] slot={slot_name}  "
                f"model='{self._model}'  "
                f"reason=NOT_IN_ALLOWLIST"
            )
            return None

        # ── Per-post image count guard ─────────────────────────────────────
        if self._post_images >= self._max_images:
            log.info(
                f"[IMAGE_SKIPPED] slot={slot_name}  "
                f"reason=MAX_IMAGES_PER_POST_REACHED({self._max_images})"
            )
            return None

        # ── Cost guard ─────────────────────────────────────────────────────
        if not self._check_cost_guard():
            return None

        # ── Replicate API call ─────────────────────────────────────────────
        try:
            import replicate

            prompt_text = prompt.text[:1500]  # Flux works best with concise prompts

            log.info(
                f"[IMAGE_GENERATION_STARTED] provider=replicate  "
                f"model={self._model}  "
                f"slot={slot_name}  "
                f"prompt_hash={prompt.prompt_hash}  "
                f"prompt_len={len(prompt_text)}"
            )

            client = replicate.Client(api_token=self._token)

            # Build model-specific inputs
            model_input = _build_model_input(self._model, prompt_text)

            output = client.run(self._model, input=model_input)

            # Replicate returns list of FileOutput objects or URL strings
            if not output:
                log.error(
                    f"[IMAGE_GENERATION_FAILED] slot={slot_name}  "
                    f"reason=REPLICATE_EMPTY_RESPONSE  "
                    f"model={self._model}"
                )
                return None

            temp_url   = str(output[0])
            prompt_hash = prompt.prompt_hash
            provider_id = hashlib.sha256(
                f"{self._model}:{prompt_hash}:{time.time()}".encode()
            ).hexdigest()[:16]

            log.info(
                f"[IMAGE_GENERATED] provider=replicate  "
                f"model={self._model}  "
                f"slot={slot_name}  "
                f"provider_id={provider_id}  "
                f"prompt_hash={prompt_hash}  "
                f"temp_url={temp_url[:80]}"
            )

            # Global dedup check before uploading
            if registry.is_globally_used(provider_id):
                log.info(
                    f"[IMAGE_SKIPPED] provider_id={provider_id} "
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
                    f"[IMAGE_GENERATION_FAILED] slot={slot_name}  "
                    f"reason=HUBSPOT_FILES_UPLOAD_FAILED  "
                    f"provider_id={provider_id}  "
                    f"check=HUBSPOT_TOKEN env var and /bubba-blog-images folder access"
                )
                return None

            # Within-post dedup check
            if permanent_url in used_urls:
                log.info(
                    f"[IMAGE_SKIPPED] url already used in this post  "
                    f"url={permanent_url[:60]}"
                )
                return None

            # Record cost after successful upload
            self._record_cost(self._estimated_cost)

            visual_cluster = f"replicate_{prompt.topic_category}_{slot_name.split('_')[0]}"

            return ImageAsset(
                url            = permanent_url,
                provider       = "replicate",
                provider_id    = provider_id,
                prompt_hash    = prompt_hash,
                search_query   = "",
                visual_cluster = visual_cluster,
                alt_text       = (
                    f"Professional image for "
                    f"{prompt.topic_category.replace('_', ' ')} article"
                ),
                model          = self._model,
            )

        except ImportError:
            log.error(
                f"[IMAGE_GENERATION_FAILED] slot={slot_name}  "
                f"reason=REPLICATE_IMPORT_ERROR  "
                f"fix='add replicate>=0.25.0 to requirements.txt and redeploy'"
            )
            return None

        except Exception as exc:
            exc_type = type(exc).__name__
            exc_str  = str(exc)

            if "authentication" in exc_str.lower() or "unauthorized" in exc_str.lower():
                reason = "REPLICATE_API_TOKEN_INVALID"
                fix    = "verify REPLICATE_API_TOKEN in Render env vars"
            elif "rate" in exc_str.lower() and "limit" in exc_str.lower():
                reason = "REPLICATE_RATE_LIMIT"
                fix    = "reduce generation frequency or upgrade Replicate plan"
            elif "model" in exc_str.lower() and ("not found" in exc_str.lower() or "404" in exc_str.lower()):
                reason = "REPLICATE_MODEL_NOT_FOUND"
                fix    = f"check REPLICATE_MODEL env var — {exc_str[:80]}"
            else:
                reason = f"REPLICATE_API_ERROR:{exc_type}"
                fix    = exc_str[:120]

            log.error(
                f"[IMAGE_GENERATION_FAILED] slot={slot_name}  "
                f"reason={reason}  "
                f"fix='{fix}'"
            )
            return None


def _build_model_input(model: str, prompt_text: str) -> dict:
    """
    Return model-specific input parameters for the Replicate API call.
    Centralised so both the provider and tests can use the same config.
    """
    base = {
        "prompt":        prompt_text,
        "num_outputs":   1,
        "aspect_ratio":  "16:9",
        "output_format": "jpg",
        "output_quality": 80,
    }
    if model == "black-forest-labs/flux-schnell":
        base["go_fast"] = True
    elif model == "black-forest-labs/flux-dev":
        base["guidance"]             = 3.5
        base["num_inference_steps"]  = 28
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Null provider — explicit no-image fallback
# ─────────────────────────────────────────────────────────────────────────────

class NullImageProvider(ImageProvider):
    """
    Returns None for every request.

    Used when no API token is configured.
    Article publishes without images — never uses warehouse fallback.
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
            f"(set REPLICATE_API_TOKEN to enable AI-generated images)"
        )
        return None

    def start_post(self) -> None:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Provider factory
# ─────────────────────────────────────────────────────────────────────────────

_provider: Optional[ImageProvider] = None


def get_provider(force: str = "") -> ImageProvider:
    """
    Return the best available image provider.

    Priority:
      1. ReplicateImageProvider (if REPLICATE_API_TOKEN set)
      2. NullImageProvider (always)

    Override:
      Set IMAGE_PROVIDER=replicate|none to force a specific provider.
      Set force= argument to override in code (tests only).
    """
    global _provider

    override = force or os.environ.get("IMAGE_PROVIDER", "").lower()

    if override == "replicate":
        p = ReplicateImageProvider()
        if p.available:
            return p
        log.warning(
            "[IMAGE_PROVIDER] IMAGE_PROVIDER=replicate but "
            "REPLICATE_API_TOKEN not set or replicate package missing"
        )
        return NullImageProvider()

    if override == "none":
        return NullImageProvider()

    # Auto-select: Replicate only. No Pexels. No static warehouse catalog.
    if _provider is None:
        rep = ReplicateImageProvider()
        if rep.available:
            _provider = rep
            log.info(
                f"[IMAGE_PROVIDER] Auto-selected: Replicate  "
                f"model={rep._model}"
            )
        else:
            _provider = NullImageProvider()
            log.info(
                "[IMAGE_PROVIDER] Auto-selected: None — "
                "REPLICATE_API_TOKEN not set or replicate package missing. "
                "Articles will publish without images. "
                "Set REPLICATE_API_TOKEN to enable AI-generated images."
            )

    return _provider


def reset_provider() -> None:
    """Force provider re-detection on next call. Tests only."""
    global _provider
    _provider = None
