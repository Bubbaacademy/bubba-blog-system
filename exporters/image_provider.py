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

STARTUP CHECKS (in __init__)
-----------------------------
1. Package check: is `replicate` importable?
2. Token check:   is REPLICATE_API_TOKEN set?
3. Model check:   is REPLICATE_MODEL in the approved allowlist?
4. Scope check:   does HUBSPOT_TOKEN have the 'files' scope? (GET /files/v3/files)
   → If scope is missing: [HUBSPOT_FILES_SCOPE_MISSING] logged, available=False,
     NO Replicate calls are ever made. Fix: add files scope to HubSpot private app.

RATE-LIMIT PROTECTION
---------------------
- Minimum 6-second gap between consecutive Replicate API calls (class-level).
- On HTTP 429: retry up to 2 times with exponential backoff (30s, then 90s).
- After all retries exhausted: _rate_limited=True, [IMAGE_VALIDATION_TEXT_ONLY] logged.
  All subsequent get_image() calls in this process return None immediately.
  Article publishes text-only. No more Replicate calls until process restarts.

COST GUARD
----------
Before every Replicate API call, estimated cost is checked against:
  - MAX_IMAGE_COST_PER_POST_USD  (per-post limit, default $0.05)
  - MAX_IMAGE_COST_PER_DAY_USD   (per-day limit, default $1.00)

APPROVED MODELS
---------------
  black-forest-labs/flux-schnell → $0.003  (default)
  black-forest-labs/flux-dev     → $0.025

SECURITY
--------
REPLICATE_API_TOKEN is read ONLY from environment variables.
It is NEVER logged, hardcoded, or written to any file.

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
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("image_provider")

# ─────────────────────────────────────────────────────────────────────────────
# Approved Replicate model allowlist
# ─────────────────────────────────────────────────────────────────────────────

APPROVED_REPLICATE_MODELS: dict[str, float] = {
    "black-forest-labs/flux-schnell": 0.003,
    "black-forest-labs/flux-dev":     0.025,
}

_DEFAULT_MODEL    = "black-forest-labs/flux-schnell"
_MIN_CALL_DELAY   = 6.0   # minimum seconds between consecutive Replicate API calls
_RETRY_WAITS      = (30, 90)  # seconds to wait on 429 before attempt 2, attempt 3


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
    search_query:   str      # "" for AI-generated images
    visual_cluster: str      # for cross-post diversity tracking
    alt_text:       str      # descriptive alt text for accessibility
    model:          str = "" # model used (e.g. "black-forest-labs/flux-schnell")

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

    Startup: checks package, token, model allowlist, and HubSpot Files scope.
    If any check fails: available=False, no Replicate calls are made.

    Rate-limit protection: 6-second gap between calls, 2 retries on 429
    (30s then 90s backoff). After all retries fail: rate_limited flag set,
    all further calls skipped for this process run.
    """

    # ── Class-level state (shared across all instances) ───────────────────────
    _day_cost_usd: float = 0.0
    _day_str:      str   = ""      # "YYYY-MM-DD" — resets at midnight
    _LAST_CALL:    float = 0.0     # timestamp of last successful API call
    _rate_limited: bool  = False   # True after 429 retries exhausted

    def __init__(self):
        # SECURITY: Replicate token read from env only, never logged in full
        self._token          = os.environ.get("REPLICATE_API_TOKEN", "").strip()
        self._model          = self._resolve_model()
        self._estimated_cost = APPROVED_REPLICATE_MODELS.get(self._model, 0.003)
        self._max_post_cost  = float(os.environ.get("MAX_IMAGE_COST_PER_POST_USD", "0.05"))
        self._max_day_cost   = float(os.environ.get("MAX_IMAGE_COST_PER_DAY_USD", "1.00"))
        self._max_images     = int(os.environ.get("MAX_IMAGES_PER_POST", "3"))
        self._pkg_available  = self._check_package()
        self._post_cost:   float = 0.0
        self._post_images: int   = 0

        # ── HubSpot Files scope check (only when Replicate is also configured) ──
        # Skip the HTTP call when either Replicate token or HubSpot token is absent.
        self._hs_scope_ok: bool = False
        if self._token and self._pkg_available:
            hs_token = os.environ.get("HUBSPOT_TOKEN", "").strip()
            if hs_token:
                from exporters.hubspot_files import check_hubspot_files_scope
                self._hs_scope_ok = check_hubspot_files_scope()
            else:
                log.warning(
                    "[IMAGE_PROVIDER] HUBSPOT_TOKEN not set — "
                    "HubSpot Files upload will fail. "
                    "fix='set HUBSPOT_TOKEN in Render environment variables'"
                )

        # ── Startup log ────────────────────────────────────────────────────────
        if not self._pkg_available:
            log.error(
                "[IMAGE_GENERATION_FAILED] replicate Python package is not installed. "
                "Add 'replicate>=0.25.0' to requirements.txt and redeploy."
            )
        elif not self._token:
            log.warning(
                "[IMAGE_PROVIDER] REPLICATE_API_TOKEN not set — "
                "Replicate provider disabled. "
                "Set REPLICATE_API_TOKEN in Render environment variables."
            )
        elif not self._hs_scope_ok:
            log.error(
                "[IMAGE_PROVIDER] HubSpot Files scope check failed — "
                "provider disabled until scope is fixed. "
                "See [HUBSPOT_FILES_SCOPE_MISSING] above for fix instructions."
            )
        else:
            safe_token = self._token[:4] + "***" if len(self._token) > 4 else "***"
            log.info(
                f"[IMAGE_PROVIDER] ReplicateImageProvider ready  "
                f"model={self._model}  "
                f"token_prefix={safe_token}  "
                f"cost_per_image=${self._estimated_cost:.4f}  "
                f"max_post=${self._max_post_cost:.2f}  "
                f"max_day=${self._max_day_cost:.2f}  "
                f"min_call_delay={_MIN_CALL_DELAY}s"
            )

    # ── Model resolution ──────────────────────────────────────────────────────

    def _resolve_model(self) -> str:
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
        self._post_cost                      += cost
        ReplicateImageProvider._day_cost_usd += cost
        self._post_images                    += 1
        log.info(
            f"[IMAGE_COST_GUARD] model={self._model}  "
            f"image_cost=${cost:.4f}  "
            f"post_total=${self._post_cost:.4f}  "
            f"day_total=${ReplicateImageProvider._day_cost_usd:.4f}  "
            f"post_limit=${self._max_post_cost:.2f}  "
            f"day_limit=${self._max_day_cost:.2f}"
        )

    # ── Replicate API call with 429 retry ─────────────────────────────────────

    def _call_replicate(self, client, model_input: dict, slot_name: str, article_slug: str):
        """
        Call client.run() with up to 2 retries on HTTP 429.

        Retry schedule: wait 30s before attempt 2, wait 90s before attempt 3.
        If all 3 attempts return 429: set _rate_limited=True, log
        [IMAGE_VALIDATION_TEXT_ONLY], return None.

        Non-429 exceptions are re-raised immediately (handled by get_image's
        outer except block).

        Returns model output list on success, None if permanently rate-limited.
        """
        for attempt in range(3):
            try:
                ReplicateImageProvider._LAST_CALL = time.time()
                return client.run(self._model, input=model_input)

            except Exception as exc:
                exc_str  = str(exc).lower()
                is_rate  = ("429" in str(exc) or
                            ("rate" in exc_str and "limit" in exc_str) or
                            "too many requests" in exc_str)

                if not is_rate:
                    raise  # non-429: let outer handler classify

                if attempt < 2:
                    wait = _RETRY_WAITS[attempt]
                    log.warning(
                        f"[REPLICATE_RATE_LIMIT] slot={slot_name}  "
                        f"attempt={attempt + 1}/3  "
                        f"waiting={wait}s before retry"
                    )
                    time.sleep(wait)
                else:
                    # All 3 attempts hit 429 — give up for this process run
                    ReplicateImageProvider._rate_limited = True
                    log.error(
                        f"[IMAGE_VALIDATION_TEXT_ONLY] slot={slot_name}  "
                        f"reason=REPLICATE_RATE_LIMIT_EXHAUSTED  "
                        f"article='{article_slug}'  "
                        f"all_remaining_image_slots=skipped  "
                        f"publishing_text_only=True  "
                        f"note='rate_limited flag set — no more Replicate calls "
                        f"until process restarts'"
                    )
                    return None

        return None  # safety — should not be reached

    # ── Per-post reset ────────────────────────────────────────────────────────

    def start_post(self) -> None:
        """Reset per-post cost and image counters. Called by ImageSelectionService."""
        self._post_cost   = 0.0
        self._post_images = 0

    @classmethod
    def reset_rate_limit(cls) -> None:
        """Manually clear the rate-limit flag. Use in tests or manual recovery."""
        cls._rate_limited = False
        cls._LAST_CALL    = 0.0

    # ── Provider interface ────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "replicate"

    @property
    def available(self) -> bool:
        """
        True only when ALL of the following hold:
          - replicate package is installed
          - REPLICATE_API_TOKEN is set
          - HubSpot Files scope check passed
        """
        return bool(self._token) and self._pkg_available and self._hs_scope_ok

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

        # ── HubSpot Files scope check ──────────────────────────────────────
        if not self._hs_scope_ok:
            log.error(
                f"[HUBSPOT_FILES_SCOPE_MISSING] slot={slot_name}  "
                f"reason=HUBSPOT_FILES_API_403_OR_TOKEN_MISSING  "
                f"fix='Add files scope to HubSpot private app token, save, "
                f"regenerate token if needed, update HUBSPOT_TOKEN in Render'"
            )
            return None

        # ── Model allowlist check ──────────────────────────────────────────
        if self._model not in APPROVED_REPLICATE_MODELS:
            log.error(
                f"[IMAGE_MODEL_BLOCKED] slot={slot_name}  "
                f"model='{self._model}'  reason=NOT_IN_ALLOWLIST"
            )
            return None

        # ── Rate-limit guard ───────────────────────────────────────────────
        if ReplicateImageProvider._rate_limited:
            log.warning(
                f"[IMAGE_VALIDATION_TEXT_ONLY] slot={slot_name}  "
                f"reason=REPLICATE_RATE_LIMIT_EXHAUSTED  "
                f"article='{article_slug}'  publishing_text_only=True"
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

        # ── Minimum delay between calls ────────────────────────────────────
        elapsed = time.time() - ReplicateImageProvider._LAST_CALL
        if elapsed < _MIN_CALL_DELAY:
            sleep_for = _MIN_CALL_DELAY - elapsed
            log.info(
                f"[IMAGE_PROVIDER] Rate-limit delay  "
                f"sleeping={sleep_for:.1f}s  slot={slot_name}"
            )
            time.sleep(sleep_for)

        # ── Replicate API call ─────────────────────────────────────────────
        try:
            import replicate

            prompt_text = prompt.text[:1500]

            log.info(
                f"[IMAGE_GENERATION_STARTED] provider=replicate  "
                f"model={self._model}  "
                f"slot={slot_name}  "
                f"prompt_hash={prompt.prompt_hash}  "
                f"prompt_len={len(prompt_text)}"
            )

            client      = replicate.Client(api_token=self._token)
            model_input = _build_model_input(self._model, prompt_text)

            # Call with retry-on-429 (may return None if permanently rate-limited)
            output = self._call_replicate(client, model_input, slot_name, article_slug)
            if output is None:
                return None

            if not output:
                log.error(
                    f"[IMAGE_GENERATION_FAILED] slot={slot_name}  "
                    f"reason=REPLICATE_EMPTY_RESPONSE  model={self._model}"
                )
                return None

            temp_url    = str(output[0])
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

            # Global dedup check
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
                    f"check=HUBSPOT_TOKEN_and_files_scope"
                )
                return None

            # Within-post dedup
            if permanent_url in used_urls:
                log.info(
                    f"[IMAGE_SKIPPED] url already used in this post  "
                    f"url={permanent_url[:60]}"
                )
                return None

            self._record_cost(self._estimated_cost)

            visual_cluster = (
                f"replicate_{prompt.topic_category}_{slot_name.split('_')[0]}"
            )

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
            elif "model" in exc_str.lower() and (
                "not found" in exc_str.lower() or "404" in exc_str.lower()
            ):
                reason = "REPLICATE_MODEL_NOT_FOUND"
                fix    = f"check REPLICATE_MODEL env var — {exc_str[:80]}"
            else:
                reason = f"REPLICATE_API_ERROR:{exc_type}"
                fix    = exc_str[:120]

            log.error(
                f"[IMAGE_GENERATION_FAILED] slot={slot_name}  "
                f"reason={reason}  fix='{fix}'"
            )
            return None


def _build_model_input(model: str, prompt_text: str) -> dict:
    """Return model-specific input parameters for the Replicate API call."""
    base = {
        "prompt":         prompt_text,
        "num_outputs":    1,
        "aspect_ratio":   "16:9",
        "output_format":  "jpg",
        "output_quality": 80,
    }
    if model == "black-forest-labs/flux-schnell":
        base["go_fast"] = True
    elif model == "black-forest-labs/flux-dev":
        base["guidance"]            = 3.5
        base["num_inference_steps"] = 28
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Null provider — explicit no-image fallback
# ─────────────────────────────────────────────────────────────────────────────

class NullImageProvider(ImageProvider):
    """
    Returns None for every request.
    Article publishes without images — never falls back to warehouse catalog.
    """

    @property
    def name(self) -> str:
        return "none"

    @property
    def available(self) -> bool:
        return True

    def get_image(self, prompt, article_slug, slot_name, registry, used_urls) -> None:
        log.info(
            f"[IMAGE_SKIPPED] slot={slot_name}  "
            f"reason=NO_IMAGE_PROVIDER_CONFIGURED  "
            f"(set REPLICATE_API_TOKEN and ensure HubSpot files scope)"
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

    Priority: ReplicateImageProvider → NullImageProvider.
    Override via IMAGE_PROVIDER=replicate|none or force= argument (tests only).
    """
    global _provider

    override = force or os.environ.get("IMAGE_PROVIDER", "").lower()

    if override == "replicate":
        p = ReplicateImageProvider()
        if p.available:
            return p
        log.warning(
            "[IMAGE_PROVIDER] IMAGE_PROVIDER=replicate forced but provider not available — "
            "check REPLICATE_API_TOKEN and HUBSPOT_TOKEN scopes"
        )
        return NullImageProvider()

    if override == "none":
        return NullImageProvider()

    if _provider is None:
        rep = ReplicateImageProvider()
        if rep.available:
            _provider = rep
            log.info(f"[IMAGE_PROVIDER] Auto-selected: Replicate  model={rep._model}")
        else:
            _provider = NullImageProvider()
            log.info(
                "[IMAGE_PROVIDER] Auto-selected: None — "
                "REPLICATE_API_TOKEN not set, replicate package missing, "
                "or HubSpot Files scope missing. "
                "Articles will publish without images."
            )

    return _provider


def reset_provider() -> None:
    """Force provider re-detection on next call. Tests only."""
    global _provider
    _provider = None
