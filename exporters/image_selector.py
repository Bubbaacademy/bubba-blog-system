"""
image_selector.py — ImageSelectionService: per-post image selection engine.

ARCHITECTURE
------------
Two image sources, one selector:

  A. Section / Hero images — fetched DYNAMICALLY from Pexels API
     ─────────────────────────────────────────────────────────────
     1. Route keyword+cluster → topic_category
     2. Generate topic-specific search queries (image_router.get_search_queries)
     3. Fetch candidates from Pexels API (image_fetcher.fetch_and_score)
     4. Apply gates: global dedup, blocked tags, topic negative terms, min score
     5. Pick highest-scoring candidate. None if nothing passes.
     6. No fallback — article publishes without section images when None.

  B. CTA images — from STATIC CATALOG (image_catalog.py)
     ─────────────────────────────────────────────────────
     1. Load approved CTA entries (reusable_cta=True)
     2. Apply gates 1–6 from the static pipeline
     3. Rotate through CTA pool to avoid within-post duplicates
     4. CTA images are reusable across posts (never in global dedup set)

PUBLIC API
----------
    service = ImageSelectionService(keyword, topic_cluster, title, slug)
    hero_url    = service.hero(article_intro_text)      # str | None
    section_url = service.section(heading_text, index)  # str | None
    cta_url     = service.cta(slot)                     # str (never None)
    # After validation + API success:
    service.commit(post_slug, post_title, keyword, topic_cluster)
    report = service.validation_report()

COMMIT FLOW
-----------
commit() is called ONCE from hubspot_api.py AFTER:
  • validate_post_package() passes
  • HubSpot API POST succeeds (or DRY_RUN)
During selection, no Sheets writes occur.
"""
from __future__ import annotations

import re
import datetime
import logging
from dataclasses import dataclass, field
from collections import Counter
from typing import Optional

from exporters.image_policy import (
    BLOCKED_TAGS, BLOCKED_DESCRIPTION_WORDS, NOISE_TAGS, STOPWORDS,
    QUALITY_THRESHOLD, RELEVANCE_THRESHOLD, TAG_RECALL_FLOOR, DESC_RECALL_FLOOR,
    NOISE_PENALTY, MAX_NOISE_PENALTY,
    VISUAL_CLUSTER_PENALTY,
    CROSS_POST_CLUSTER_PENALTY_PER_USE, MAX_CROSS_POST_CLUSTER_PENALTY,
    STATUS_APPROVED, ROLE_HERO, ROLE_SECTION, ROLE_CTA,
    MAX_SECTION_IMAGES,
)
from exporters.image_catalog import (
    ImageEntry, IMAGE_CATALOG, get_approved,
    APPROVED_IDS, APPROVED_PEXELS_IDS,
)
from exporters.image_router import route, get_search_queries
from exporters.image_registry import get_registry, RegistryEntry, ImageRegistry
from exporters.image_fetcher import (
    FetchedImage, fetch_and_score, get_pexels_client,
)
import exporters.image_logging as ilog

log = logging.getLogger("image_selector")


# ─────────────────────────────────────────────────────────────────────────────
# ImageSelection — result object for one selected image
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
    source:          str         # "pexels_api" | "static_catalog"
    search_query:    str         # Pexels query that found this image (or "" for static)


# ─────────────────────────────────────────────────────────────────────────────
# Static catalog gate evaluator (used for CTA images only)
# ─────────────────────────────────────────────────────────────────────────────

def _clean_words(text: str) -> set:
    return set(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def _meaningful_words(text: str) -> set:
    return _clean_words(text) - STOPWORDS


def _evaluate_cta_gates(
    entry: ImageEntry,
    used_clusters_this_post: set,
    registry: ImageRegistry,
) -> tuple:
    """
    Run a static catalog CTA entry through its gates.

    Gates for CTA (decorative, topic-independent):
      1  status == approved
      2  quality_score >= QUALITY_THRESHOLD
      3  no BLOCKED_TAGS in tags
      4  no BLOCKED_DESCRIPTION_WORDS in description
      5  reusable_cta=True (CTA images only — enforced upstream in .cta())

    Returns (passed: bool, reason: str, score: float)
    CTA score is always 1.0 if it passes — they're decorative, not relevance-scored.
    """
    pid = entry.image_id

    if not entry.is_approved():
        return False, f"REJECTED_DISABLED:status={entry.status}", 0.0

    if entry.quality_score < QUALITY_THRESHOLD:
        return False, f"REJECTED_LOW_QUALITY:{entry.quality_score:.2f}<{QUALITY_THRESHOLD}", 0.0

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
    Per-post image selection service.

    One instance per article. Two image sources:
    - Dynamic Pexels API for section + hero (topic-relevant, globally deduped)
    - Static catalog for CTA (brand-consistent, reusable across posts)

    Usage
    -----
        service = ImageSelectionService(keyword, cluster, title, slug)
        service.hero(intro_text)
        service.section(heading, 0)
        service.cta(0); service.cta(1); service.cta(2)
        # Later, after validation passes:
        service.commit(slug, title, keyword, cluster)
    """

    def __init__(
        self,
        article_keyword: str = "",
        article_topic_cluster: str = "",
        article_title: str = "",
        article_slug: str = "",
        _fetcher=None,              # injectable for tests
        _registry=None,             # injectable for tests
    ):
        self._keyword  = article_keyword
        self._cluster  = article_topic_cluster
        self._title    = article_title
        self._slug     = article_slug

        # Resolve topic category once at init
        self._topic_category: str = route(article_keyword, article_topic_cluster)

        # Registry — singleton (loaded from Sheets at startup)
        self._registry: ImageRegistry = _registry if _registry is not None else get_registry()

        # Pexels client — injectable for tests
        self._fetcher = _fetcher if _fetcher is not None else get_pexels_client()

        # Per-post state
        self._used_urls:     set  = set()
        self._used_clusters: set  = set()
        self._selections:    list = []   # list[ImageSelection]
        self._section_count: int  = 0

        log.info(
            f"[IMAGE_ROUTE] article='{article_title[:60]}'  "
            f"keyword='{article_keyword}'  cluster='{article_topic_cluster}'  "
            f"topic_category='{self._topic_category}'"
        )

    # ── Internal: record a selection locally ─────────────────────────────────

    def _record(self, sel: ImageSelection) -> None:
        """Commit selection to local post state. Does NOT write to Sheets."""
        self._used_urls.add(sel.url)
        self._used_clusters.add(sel.visual_cluster)
        self._selections.append(sel)

    # ── Dynamic section / hero selection (Pexels API) ─────────────────────────

    def _select_dynamic(self, context: str, role: str) -> "str | None":
        """
        Fetch and select an image from Pexels API for section or hero role.

        Returns URL string or None.
        None means no relevant image found — slot is silently skipped.
        """
        queries = get_search_queries(
            self._topic_category,
            self._keyword,
            section_heading=context,
        )

        log.info(
            f"[IMAGE_FETCHER] role={role}  "
            f"topic='{self._topic_category}'  "
            f"queries={queries}  "
            f"context='{context[:60]}'"
        )

        candidates = fetch_and_score(
            queries         = queries,
            context         = context,
            keyword         = self._keyword,
            topic_category  = self._topic_category,
            registry        = self._registry,
            used_urls       = self._used_urls,
            used_clusters   = self._used_clusters,
            client          = self._fetcher,
        )

        if not candidates:
            log.info(
                f"[IMAGE_SKIPPED] role={role}  "
                f"reason=NO_RELEVANT_IMAGE_FOUND  "
                f"context='{context[:60]}'"
            )
            return None

        score, best = candidates[0]

        # Prefer visual cluster diversity within the post
        # If best uses a cluster already used, try second-best unless score gap is large
        if len(candidates) > 1 and best.visual_cluster in self._used_clusters:
            for alt_score, alt in candidates[1:]:
                if alt.visual_cluster not in self._used_clusters:
                    # Accept the alternative if its score is within 20% of best
                    if alt_score >= score * 0.80:
                        score, best = alt_score, alt
                    break

        sel = ImageSelection(
            image_id       = best.image_id,
            url            = best.url,
            role           = role,
            category       = self._topic_category,
            visual_cluster = best.visual_cluster,
            relevance_score= score,
            context        = context,
            source         = "pexels_api",
            search_query   = best.search_query,
        )
        self._record(sel)

        log.info(
            f"[IMAGE_SELECTED] role={role}  "
            f"id={best.image_id}  "
            f"score={score:.4f}  "
            f"cluster={best.visual_cluster}  "
            f"alt='{best.alt[:60]}'  "
            f"url={best.url[:80]}"
        )
        return best.url

    # ── Public selection API ──────────────────────────────────────────────────

    def hero(self, context: str = "") -> "str | None":
        """
        Select hero image for the article.

        Returns URL string or None (if no relevant image found).
        Hero is omitted rather than filled with a weak/irrelevant image.
        """
        return self._select_dynamic(context or self._keyword, ROLE_HERO)

    def section(self, context: str = "", index: int = 0) -> "str | None":
        """
        Select section image for the given heading/context.

        Returns URL string or None. None means: no relevant image found for
        this section — the slot is silently skipped. Article still publishes.

        Max MAX_SECTION_IMAGES per article.
        """
        if self._section_count >= MAX_SECTION_IMAGES:
            log.info(
                f"[IMAGE_SKIPPED] role=section  "
                f"reason=MAX_SECTION_IMAGES_REACHED({MAX_SECTION_IMAGES})  "
                f"context='{context[:60]}'"
            )
            return None

        url = self._select_dynamic(context, ROLE_SECTION)
        if url:
            self._section_count += 1
        return url

    def cta(self, slot: int = 0) -> str:
        """
        Select CTA image for the given slot index from the static catalog.

        Never returns None — always provides an image for CTA blocks.
        CTA images are reusable across posts (reusable_cta=True).
        Rotates within-post to avoid duplicate CTA images in the same article.
        """
        cta_pool = get_approved(role=ROLE_CTA, reusable_cta=True)
        survivors = []

        for entry in cta_pool:
            # Skip URLs already used in this post
            if entry.url in self._used_urls:
                continue

            passed, reason, score = _evaluate_cta_gates(
                entry, self._used_clusters, self._registry
            )
            if passed:
                survivors.append((score, entry))
            else:
                log.debug(f"[IMAGE_REJECTED_CTA] id={entry.image_id} reason={reason}")

        if survivors:
            survivors.sort(key=lambda x: -x[0])
            score, best = survivors[0]

            sel = ImageSelection(
                image_id       = best.image_id,
                url            = best.url,
                role           = ROLE_CTA,
                category       = best.category,
                visual_cluster = best.visual_cluster,
                relevance_score= score,
                context        = f"cta_slot_{slot}",
                source         = "static_catalog",
                search_query   = "",
            )
            self._record(sel)

            log.info(
                f"[IMAGE_SELECTED] role=cta  "
                f"id={best.image_id}  "
                f"slot={slot}  "
                f"url={best.url[:80]}"
            )
            return best.url

        # Hard fallback — should not happen with a correct catalog (3+ CTA images)
        if cta_pool:
            fallback = cta_pool[slot % len(cta_pool)]
            log.warning(
                f"[IMAGE_SKIPPED] CTA slot={slot} pool exhausted — "
                f"force-cycling to {fallback.image_id}"
            )
            self._used_urls.discard(fallback.url)  # allow reuse as last resort
            return self.cta(slot)   # retry with cleared URL

        raise RuntimeError(
            "CTA pool is empty — add at least 3 approved CTA images to IMAGE_CATALOG"
        )

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
          1. All image selection is complete.
          2. Pre-publish validation has passed.
          3. HubSpot API call succeeded (or DRY_RUN).

        Writes are one-way and permanent. Never call during selection.
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
                selected_at          = ts,
            ))

        self._registry.register(entries)
        ilog.log_registry_written(post_slug, len(entries))

    # ── Validation report ─────────────────────────────────────────────────────

    def validation_report(self) -> dict:
        """
        Summary for publisher.py logging and hubspot.json 'images' field.

        'unverified_ids' now only flags static-catalog images whose IDs are not
        in APPROVED_IDS. Pexels API images are always considered verified
        (they came from our authenticated API request).
        """
        all_ids  = [s.image_id for s in self._selections]
        counts   = Counter(all_ids)
        dupes    = [i for i, n in counts.items() if n > 1]

        # Only flag static catalog images as unverified (pexels_api images are safe)
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
            "selections": [
                {
                    "role":           s.role,
                    "id":             s.image_id,
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
