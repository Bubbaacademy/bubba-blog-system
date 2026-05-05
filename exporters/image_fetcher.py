"""
image_fetcher.py — Dynamic image fetching from the Pexels API.

This module replaces the static IMAGE_CATALOG as the primary source for
section and hero images. The static catalog (image_catalog.py) is now used
exclusively for CTA images (brand-consistent, reusable across all posts).

ARCHITECTURE
------------
- PexelsClient: thin API wrapper with rate limiting and error handling.
- FetchedImage: result dataclass returned by search().
- fetch_and_score(): high-level entry point used by ImageSelectionService.
  Runs multiple queries, deduplicates results, filters blocked terms,
  applies registry dedup, scores survivors, returns sorted list.

ENVIRONMENT
-----------
Set PEXELS_API_KEY in .env or Render environment variables.
Free tier: 200 requests/hour, 20,000/month.
If key is absent, fetch returns [] — section images are skipped gracefully.
Article still publishes. No error raised.

RELEVANCE SCORING (for fetched images)
--------------------------------------
Score = position_score * 0.5 + alt_match * 0.5
  - position_score: 1.0 for first result, decreasing by 1/per_page per position
    (Pexels returns most-relevant images first)
  - alt_match: fraction of topic_keywords found in the image alt text

Images are pre-filtered by the Pexels search relevance — any candidate
that reaches scoring has already survived the API's own relevance filter.
Our score adds a lightweight alt-text signal on top.
"""
from __future__ import annotations

import os
import re
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

from exporters.image_policy import (
    BLOCKED_TAGS, BLOCKED_DESCRIPTION_WORDS,
    TOPIC_NEGATIVE_TERMS,
    PEXELS_RESULTS_PER_QUERY,
    PEXELS_MIN_SCORE,
    STOPWORDS,
    CAT_GENERAL_BUSINESS,
)

log = logging.getLogger("image_fetcher")

PEXELS_API_BASE = "https://api.pexels.com/v1"


# ─────────────────────────────────────────────────────────────────────────────
# FetchedImage dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FetchedImage:
    """
    A single image result from the Pexels API.

    Fields
    ------
    image_id      Pexels photo ID as string (e.g. "12345678")
    url           Landscape/large CDN URL (pexels.com/photos/.../...)
    alt           Alt text / description from Pexels
    photographer  Photographer name (for attribution)
    avg_color     Dominant color hex string (e.g. "#3A4F6B")
    width         Original image width in pixels
    height        Original image height in pixels
    search_query  The query string that produced this result
    position      0-based position in search results (lower = more relevant)
    """
    image_id:     str
    url:          str
    alt:          str
    photographer: str
    avg_color:    str
    width:        int
    height:       int
    search_query: str
    position:     int

    @property
    def visual_cluster(self) -> str:
        """
        Derive a visual cluster label from the search query.
        Used for cross-post diversity tracking.
        """
        words = re.sub(r"[^a-z0-9\s]", " ", self.search_query.lower()).split()
        meaningful = [w for w in words if w not in STOPWORDS and len(w) > 3]
        return "_".join(meaningful[:3]) if meaningful else "pexels_general"

    @property
    def alt_words(self) -> set:
        """Alt text tokenised into lowercase words for relevance scoring."""
        return set(re.sub(r"[^a-z0-9\s]", " ", self.alt.lower()).split())


# ─────────────────────────────────────────────────────────────────────────────
# Pexels API client
# ─────────────────────────────────────────────────────────────────────────────

class PexelsClient:
    """
    Thin Pexels API wrapper.

    - Reads PEXELS_API_KEY from environment.
    - Rate-limits to avoid hitting 200 req/hour cap.
    - Returns [] on any error (never raises; article still publishes).
    """

    _MIN_INTERVAL: float = 0.35   # seconds between requests (~170 req/min)

    def __init__(self):
        self._api_key    = os.environ.get("PEXELS_API_KEY", "").strip()
        self._last_call  = 0.0
        if self._api_key:
            log.info("[IMAGE_FETCHER] PexelsClient ready")
        else:
            log.warning(
                "[IMAGE_FETCHER] PEXELS_API_KEY not set — "
                "section/hero images will be skipped for all topics"
            )

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self._MIN_INTERVAL:
            time.sleep(self._MIN_INTERVAL - elapsed)
        self._last_call = time.time()

    def search(self, query: str, per_page: int = PEXELS_RESULTS_PER_QUERY) -> list:
        """
        Search Pexels for landscape images matching query.

        Returns list[FetchedImage], empty on any error.
        """
        if not self._api_key:
            return []

        self._rate_limit()

        try:
            resp = requests.get(
                f"{PEXELS_API_BASE}/search",
                headers={"Authorization": self._api_key},
                params={
                    "query":       query,
                    "per_page":    per_page,
                    "orientation": "landscape",
                    "size":        "large",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data   = resp.json()
            photos = data.get("photos", [])

            results = []
            for i, photo in enumerate(photos):
                src = photo.get("src", {})
                # Prefer large2x (2x density), fall back to large, then medium
                url = (
                    src.get("large2x")
                    or src.get("large")
                    or src.get("medium", "")
                )
                if not url:
                    continue

                results.append(FetchedImage(
                    image_id     = str(photo["id"]),
                    url          = url,
                    alt          = photo.get("alt", ""),
                    photographer = photo.get("photographer", ""),
                    avg_color    = photo.get("avg_color", ""),
                    width        = photo.get("width", 0),
                    height       = photo.get("height", 0),
                    search_query = query,
                    position     = i,
                ))

            log.info(
                f"[IMAGE_FETCHER] query='{query}'  "
                f"fetched={len(results)}  "
                f"total_results={data.get('total_results', '?')}"
            )
            return results

        except requests.exceptions.Timeout:
            log.warning(f"[IMAGE_FETCHER] Timeout on query='{query}'")
            return []
        except requests.exceptions.HTTPError as exc:
            log.warning(f"[IMAGE_FETCHER] HTTP {exc.response.status_code} on query='{query}'")
            return []
        except Exception as exc:
            log.warning(f"[IMAGE_FETCHER] Error on query='{query}': {exc}")
            return []


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_client: Optional[PexelsClient] = None


def get_pexels_client() -> PexelsClient:
    """Return the shared PexelsClient instance (created once per process)."""
    global _client
    if _client is None:
        _client = PexelsClient()
    return _client


def reset_pexels_client() -> None:
    """Force client recreation on next call. Tests only."""
    global _client
    _client = None


# ─────────────────────────────────────────────────────────────────────────────
# Gate helpers — applied to fetched images
# ─────────────────────────────────────────────────────────────────────────────

def _is_blocked(img: FetchedImage, topic_category: str) -> tuple:
    """
    Return (blocked: bool, reason: str).

    Gates:
      G1 — global BLOCKED_TAGS in alt text
      G2 — BLOCKED_DESCRIPTION_WORDS in alt text
      G3 — topic-specific TOPIC_NEGATIVE_TERMS in alt text
    """
    alt_lower = img.alt.lower()
    alt_words = set(alt_lower.split())

    # G1: global blocked tags
    hit = alt_words & BLOCKED_TAGS
    if hit:
        return True, f"BLOCKED_TAG:{sorted(hit)}"

    # G2: blocked description words (applied to alt text)
    hit2 = alt_words & BLOCKED_DESCRIPTION_WORDS
    if hit2:
        return True, f"BLOCKED_DESCRIPTION:{sorted(hit2)}"

    # G3: topic-specific negative terms
    neg = TOPIC_NEGATIVE_TERMS.get(topic_category, frozenset())
    for term in neg:
        if term in alt_lower:
            return True, f"TOPIC_NEGATIVE_TERM:'{term}'"

    return False, ""


def _score_fetched(
    img: FetchedImage,
    topic_keywords: set,
    per_page: int = PEXELS_RESULTS_PER_QUERY,
) -> float:
    """
    Score a fetched image.

    Formula:
        position_score = 1.0 - (position / per_page)       [0.0 – 1.0]
        alt_match      = |topic_keywords ∩ alt_words| / max(|topic_keywords|, 1)
        score          = position_score * 0.5 + alt_match * 0.5

    Position signal is strong — Pexels already sorts by relevance.
    Alt match adds topic-specificity.
    """
    position_score = max(0.0, 1.0 - (img.position / max(per_page, 1)))
    alt_words      = img.alt_words
    alt_match      = len(topic_keywords & alt_words) / max(len(topic_keywords), 1)
    return round(position_score * 0.5 + alt_match * 0.5, 4)


# ─────────────────────────────────────────────────────────────────────────────
# High-level entry point
# ─────────────────────────────────────────────────────────────────────────────

def fetch_and_score(
    queries: list,
    context: str,
    keyword: str,
    topic_category: str,
    registry,            # ImageRegistry — for global dedup check
    used_urls: set,      # URLs already used in this post
    used_clusters: set,  # visual_clusters already used in this post
    per_query: int = PEXELS_RESULTS_PER_QUERY,
    client: Optional[PexelsClient] = None,
) -> list:
    """
    Run queries, deduplicate, gate-filter, score, and return sorted candidates.

    Parameters
    ----------
    queries        : list of Pexels search query strings (most specific first)
    context        : section heading or role context string (for logging)
    keyword        : article main keyword (used to build topic_keywords set)
    topic_category : routing category string (e.g. "amazon_ads_digital")
    registry       : ImageRegistry instance for cross-post dedup
    used_urls      : URLs already selected in this post (within-post dedup)
    used_clusters  : visual clusters already selected in this post
    per_query      : images to fetch per query
    client         : optional injected PexelsClient (for tests)

    Returns
    -------
    List of (score: float, img: FetchedImage) sorted by score descending.
    Empty list means no suitable images — selector will skip this slot.
    """
    if client is None:
        client = get_pexels_client()

    if not client.available:
        log.info(
            f"[IMAGE_FETCHER] No API key — skipping fetch for context='{context[:50]}'"
        )
        return []

    # Build topic keywords from keyword + context for alt-text matching
    topic_kw_text  = f"{keyword} {context} {topic_category.replace('_', ' ')}"
    topic_keywords = set(
        re.sub(r"[^a-z0-9\s]", " ", topic_kw_text.lower()).split()
    ) - STOPWORDS

    # Collect all candidates across queries, dedup by image_id
    seen_ids:    dict  = {}   # image_id → FetchedImage (keep first occurrence)
    total_fetched = 0

    for query in queries:
        imgs = client.search(query, per_page=per_query)
        total_fetched += len(imgs)
        for img in imgs:
            if img.image_id not in seen_ids:
                seen_ids[img.image_id] = img

    candidates = list(seen_ids.values())
    log.info(
        f"[IMAGE_FETCHER] context='{context[:50]}'  "
        f"queries={len(queries)}  fetched={total_fetched}  unique={len(candidates)}"
    )

    survivors = []
    stats = {
        "used_url": 0, "global_dup": 0, "blocked": 0,
        "low_score": 0, "approved": 0,
    }

    for img in candidates:
        # Within-post URL dedup
        if img.url in used_urls:
            stats["used_url"] += 1
            log.debug(f"[IMAGE_REJECTED] id={img.image_id} reason=WITHIN_POST_DUPLICATE")
            continue

        # Cross-post image ID dedup (via registry)
        if registry.is_globally_used(img.image_id):
            stats["global_dup"] += 1
            log.info(
                f"[IMAGE_REJECTED] id={img.image_id}  "
                f"reason=REJECTED_GLOBAL_DUPLICATE:used_in_prior_post  "
                f"context='{context[:40]}'"
            )
            continue

        # Blocked tags / negative terms
        blocked, reason = _is_blocked(img, topic_category)
        if blocked:
            stats["blocked"] += 1
            log.info(
                f"[IMAGE_REJECTED] id={img.image_id}  "
                f"reason={reason}  "
                f"alt='{img.alt[:60]}'  "
                f"context='{context[:40]}'"
            )
            continue

        # Relevance score
        score = _score_fetched(img, topic_keywords, per_query)

        if score < PEXELS_MIN_SCORE:
            stats["low_score"] += 1
            log.info(
                f"[IMAGE_REJECTED] id={img.image_id}  "
                f"reason=REJECTED_LOW_RELEVANCE:score={score:.4f}<{PEXELS_MIN_SCORE}  "
                f"alt='{img.alt[:60]}'  context='{context[:40]}'"
            )
            continue

        # PASSED — log and add
        stats["approved"] += 1
        log.info(
            f"[IMAGE_APPROVED] id={img.image_id}  "
            f"score={score:.4f}  "
            f"cluster={img.visual_cluster}  "
            f"alt='{img.alt[:60]}'  "
            f"query='{img.search_query[:50]}'  "
            f"context='{context[:40]}'"
        )
        survivors.append((score, img))

    # Sort by score descending
    survivors.sort(key=lambda x: -x[0])

    log.info(
        f"[IMAGE_CANDIDATE] context='{context[:50]}'  "
        f"unique={len(candidates)}  "
        f"skipped_url={stats['used_url']}  "
        f"skipped_global_dup={stats['global_dup']}  "
        f"skipped_blocked={stats['blocked']}  "
        f"skipped_low_score={stats['low_score']}  "
        f"passed={stats['approved']}"
    )

    return survivors
