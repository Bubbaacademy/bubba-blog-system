"""
image_selector.py — ImageSelectionService: per-post image selection engine.

ARCHITECTURE
------------
This module is the only public entry point for image selection.
All other image modules are consumed internally — callers only import this file.

Public API
----------
    service = ImageSelectionService(keyword, topic_cluster, title, slug)
    hero_url    = service.hero(article_intro_text)       # str | None
    section_url = service.section(heading_text, index)   # str | None
    cta_url     = service.cta(slot)                      # str (never None)

    # After validation passes (called from hubspot_api.py):
    service.commit(post_slug, post_title, keyword, topic_cluster)

    # Expose for publisher.py log and hubspot.json:
    report = service.validation_report()

SELECTION PIPELINE (per image slot)
-------------------------------------
  1. Build candidate pool from IMAGE_CATALOG (approved status, correct role).
  2. Filter by allowed_categories (from image_router.route()).
  3. Score each candidate through gates:
       Gate 1  status == approved
       Gate 2  quality_score >= QUALITY_THRESHOLD
       Gate 3  no BLOCKED_TAGS in tags
       Gate 4  no BLOCKED_DESCRIPTION_WORDS in description
       Gate 5  category in allowed_categories  (skipped for CTA)
       Gate 6  topic not in blocked_topic_clusters
       Gate 7  not globally used in prior post  (skipped for CTA)
       Gate 8  tag_recall >= TAG_RECALL_FLOOR   (skipped for CTA)
       Gate 9  desc_recall >= DESC_RECALL_FLOOR (skipped for CTA)
       Gate 10 final_score >= RELEVANCE_THRESHOLD (skipped for CTA)
  4. Sort survivors by score descending. Pick best.
  5. Commit winner to local state (url, visual_cluster).
  6. Log [IMAGE_SELECTED].

COMMIT FLOW
-----------
  commit() is called ONCE from hubspot_api.py AFTER:
    • validate_post_package() passes
    • HubSpot API POST succeeds (or DRY_RUN)
  commit() writes RegistryEntry rows to Google Sheets.
  During selection, no Sheets writes occur.

REUSE IN OTHER PROJECTS
-----------------------
Replace image_catalog, image_router, and image_policy with project-specific
versions. ImageSelectionService logic is fully generic.
"""
from __future__ import annotations

import re
import datetime
import logging
from dataclasses import dataclass, field
from collections import Counter

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
    ImageEntry, IMAGE_CATALOG, APPROVED_IDS, get_approved, get_by_id,
    # backward-compat alias used by hubspot_api.py
    APPROVED_PEXELS_IDS,
)
from exporters.image_router import route
from exporters.image_registry import get_registry, RegistryEntry, ImageRegistry
import exporters.image_logging as ilog

log = logging.getLogger("image_selector")


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _clean_words(text: str) -> set:
    return set(re.sub(r"[^a-z0-9\s]", " ", text.lower()).split())


def _meaningful_words(text: str) -> set:
    return _clean_words(text) - STOPWORDS


# ─────────────────────────────────────────────────────────────────────────────
# ImageSelection result object
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ImageSelection:
    """Immutable record of a single image selection decision."""
    image_id:       str
    url:            str
    role:           str       # hero / section / cta
    category:       str
    visual_cluster: str
    relevance_score: float
    context:        str       # section heading, "" for hero/cta


# ─────────────────────────────────────────────────────────────────────────────
# Gate evaluator
# ─────────────────────────────────────────────────────────────────────────────

def _evaluate_gates(
    entry: ImageEntry,
    context: str,
    keyword: str,
    topic_cluster: str,
    used_clusters_this_post: set,
    allowed_categories: list,
    registry: ImageRegistry,
    role: str,
) -> tuple:
    """
    Run a catalog entry through all selection gates.

    Returns
    -------
    (passed: bool, rejection_reason: str, score: float, matched_keywords: list)
    """
    pid = entry.image_id

    # ── Gate 1: status ────────────────────────────────────────────────────────
    if not entry.is_approved():
        return False, f"REJECTED_DISABLED:status={entry.status}", 0.0, []

    # ── Gate 2: quality ───────────────────────────────────────────────────────
    if entry.quality_score < QUALITY_THRESHOLD:
        return (
            False,
            f"REJECTED_LOW_QUALITY:quality={entry.quality_score:.2f}<{QUALITY_THRESHOLD}",
            0.0, [],
        )

    # ── Gate 3: blocked tags ──────────────────────────────────────────────────
    tags_lower  = {t.lower() for t in entry.tags}
    blocked_tag = tags_lower & BLOCKED_TAGS
    if blocked_tag:
        return False, f"REJECTED_BLOCKED_TAG:{sorted(blocked_tag)}", 0.0, []

    # ── Gate 4: blocked description words ────────────────────────────────────
    desc_raw_words = set(entry.description.lower().split())
    blocked_desc   = desc_raw_words & BLOCKED_DESCRIPTION_WORDS
    if blocked_desc:
        return False, f"REJECTED_BLOCKED_DESCRIPTION:{sorted(blocked_desc)}", 0.0, []

    # ── Gate 5: category match (section/hero only) ────────────────────────────
    if role != ROLE_CTA and allowed_categories and entry.category not in allowed_categories:
        return (
            False,
            f"REJECTED_CATEGORY_MISMATCH:{entry.category} not in {allowed_categories}",
            0.0, [],
        )

    # ── Gate 6: blocked topic cluster ────────────────────────────────────────
    if entry.is_blocked_for_topic(keyword, topic_cluster):
        blocked_match = next(
            (b for b in entry.blocked_topic_clusters
             if b.lower() in f"{keyword} {topic_cluster}".lower()),
            "unknown",
        )
        return False, f"REJECTED_BLOCKED_TOPIC:{blocked_match}", 0.0, []

    # ── CTA: skip scoring gates — decorative, topic-independent ──────────────
    if role == ROLE_CTA:
        return True, "APPROVED_CTA:gates_1_to_6_passed", 1.0, []

    # ── Gate 7: global dedup (section + hero) ─────────────────────────────────
    if registry.is_globally_used(pid):
        return False, "REJECTED_GLOBAL_DUPLICATE:used_in_prior_post", 0.0, []

    # ── Gates 8–10: semantic relevance scoring ────────────────────────────────
    full_ctx       = f"{context} {keyword} {topic_cluster}"
    ctx_meaningful = _meaningful_words(full_ctx)
    ctx_size       = max(len(ctx_meaningful), 1)

    kw_words   = _clean_words(" ".join(entry.relevance_keywords))
    tag_words  = _clean_words(" ".join(entry.tags))
    desc_words = _clean_words(entry.description)

    kw_recall   = len(kw_words   & ctx_meaningful) / ctx_size
    tag_recall  = len(tag_words  & ctx_meaningful) / ctx_size
    desc_recall = len(desc_words & ctx_meaningful) / ctx_size

    # Gate 8
    if tag_recall < TAG_RECALL_FLOOR:
        return (
            False,
            f"REJECTED_LOW_TAG_RECALL:tag_recall={tag_recall:.4f}<{TAG_RECALL_FLOOR}  "
            f"ctx_size={ctx_size}",
            0.0, [],
        )

    # Gate 9
    if desc_recall < DESC_RECALL_FLOOR:
        return (
            False,
            f"REJECTED_LOW_DESC_RECALL:desc_recall={desc_recall:.4f}<{DESC_RECALL_FLOOR}  "
            f"ctx_size={ctx_size}",
            0.0, [],
        )

    # Composite base score
    base_score = kw_recall * 0.40 + tag_recall * 0.35 + desc_recall * 0.25

    # Anti-corruption: noise tag penalty
    noise_count   = len(tags_lower & NOISE_TAGS)
    noise_penalty = min(MAX_NOISE_PENALTY, noise_count * NOISE_PENALTY)

    # Within-post visual diversity penalty
    cluster_penalty = (
        VISUAL_CLUSTER_PENALTY if entry.visual_cluster in used_clusters_this_post
        else 0.0
    )

    # Cross-post visual diversity penalty (softer — reduces score, not hard gate)
    cross_usage       = registry.get_cluster_usage_count(entry.visual_cluster)
    cross_penalty     = min(MAX_CROSS_POST_CLUSTER_PENALTY,
                            cross_usage * CROSS_POST_CLUSTER_PENALTY_PER_USE)

    # Quality multiplier: better images score higher on the same content
    final_score = round(
        (base_score - noise_penalty - cluster_penalty - cross_penalty)
        * entry.quality_score,
        4,
    )

    matched_kws = sorted((kw_words | tag_words | desc_words) & ctx_meaningful)

    # Gate 10
    if final_score < RELEVANCE_THRESHOLD:
        return (
            False,
            f"REJECTED_LOW_RELEVANCE:score={final_score:.4f}<{RELEVANCE_THRESHOLD}  "
            f"(base={base_score:.4f}  noise=-{noise_penalty:.2f}  "
            f"cluster_penalty=-{cluster_penalty:.2f}  "
            f"cross_penalty=-{cross_penalty:.2f}  "
            f"quality×{entry.quality_score})",
            final_score,
            matched_kws,
        )

    return True, f"APPROVED:score={final_score:.4f}", final_score, matched_kws


# ─────────────────────────────────────────────────────────────────────────────
# ImageSelectionService
# ─────────────────────────────────────────────────────────────────────────────

class ImageSelectionService:
    """
    Per-post image selection service.

    One instance per article. Holds all selection state for that article.
    Thread-safe for sequential use (one post at a time per process).

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
    ):
        self._keyword  = article_keyword
        self._cluster  = article_topic_cluster
        self._title    = article_title
        self._slug     = article_slug

        # Load registry once per service instance.
        # Registry was loaded from Sheets at startup via get_registry() singleton.
        self._registry: ImageRegistry = get_registry()

        # Per-post state — reset for each new article
        self._used_urls:   set  = set()
        self._used_clusters: set = set()
        self._selections:  list  = []   # list[ImageSelection]
        self._section_count: int = 0

        # Resolve allowed categories once at init (from image_router)
        self._allowed_categories: list = route(article_keyword, article_topic_cluster)

        log.info(
            f"[IMAGE_ROUTE] article='{article_title}'  "
            f"keyword='{article_keyword}'  cluster='{article_topic_cluster}'  "
            f"allowed_categories={self._allowed_categories or 'NONE (topic has no image category)'}"
        )

    # ── Internal: score a pool ────────────────────────────────────────────────

    def _score_pool(
        self,
        pool: list,
        context: str,
        role: str,
        allowed_categories: list,
    ) -> list:
        """
        Score every candidate in pool, log each decision, return sorted survivors.

        Returns list of (score, matched_keywords, entry) sorted by score descending.
        """
        survivors = []
        stats = {
            "globally_used": 0, "category_mismatch": 0, "blocked": 0,
            "low_quality": 0, "low_relevance": 0, "approved": 0,
        }

        for entry in pool:
            # Local dedup — skip URLs already committed in this post
            if entry.url in self._used_urls:
                log.debug(f"[IMAGE] {entry.image_id} local-dedup skip")
                continue

            passed, reason, score, matched_kws = _evaluate_gates(
                entry, context, self._keyword, self._cluster,
                self._used_clusters, allowed_categories,
                self._registry, role,
            )

            if passed:
                survivors.append((score, matched_kws, entry))
                stats["approved"] += 1
                ilog.log_approved(
                    entry.image_id, entry.category, entry.visual_cluster,
                    score, context, matched_kws,
                )
            else:
                ilog.log_rejected(entry.image_id, reason, score, context)
                if "DUPLICATE" in reason:
                    stats["globally_used"] += 1
                elif "CATEGORY" in reason or "TOPIC" in reason:
                    stats["category_mismatch"] += 1
                elif "BLOCKED" in reason:
                    stats["blocked"] += 1
                elif "QUALITY" in reason or "DISABLED" in reason:
                    stats["low_quality"] += 1
                else:
                    stats["low_relevance"] += 1

        survivors.sort(key=lambda x: -x[0])

        ilog.log_pool_summary(
            context, role, len(pool),
            stats["globally_used"], stats["category_mismatch"],
            stats["blocked"], stats["low_quality"], stats["low_relevance"],
            stats["approved"],
        )

        return survivors

    # ── Internal: commit a winner locally ────────────────────────────────────

    def _record(self, selection: ImageSelection) -> None:
        """Commit selection to local post state. Does NOT write to Sheets."""
        self._used_urls.add(selection.url)
        self._used_clusters.add(selection.visual_cluster)
        self._selections.append(selection)

    # ── Public selection API ──────────────────────────────────────────────────

    def hero(self, context: str = "") -> "str | None":
        """
        Select hero image for the article.

        Returns URL string or None (if no relevant image exists).
        Hero is omitted rather than filled with a weak/irrelevant image.
        Called at most once per article.
        """
        if not self._allowed_categories:
            ilog.log_skipped(ROLE_HERO, "NO_CATEGORY_FOR_TOPIC", context)
            return None

        pool = [
            e for e in get_approved(role=ROLE_HERO)
            if e.category in self._allowed_categories
        ]

        candidates = self._score_pool(pool, context or self._keyword, ROLE_HERO, self._allowed_categories)

        if not candidates:
            ilog.log_skipped(ROLE_HERO, "NO_CANDIDATE_PASSED_GATES", context)
            return None

        score, matched_kws, best = candidates[0]
        sel = ImageSelection(
            image_id=best.image_id, url=best.url, role=ROLE_HERO,
            category=best.category, visual_cluster=best.visual_cluster,
            relevance_score=score, context=context,
        )
        self._record(sel)
        ilog.log_selected(ROLE_HERO, best.image_id, best.url,
                          best.category, best.visual_cluster, score)
        return best.url

    def section(self, context: str = "", index: int = 0) -> "str | None":
        """
        Select section image for the given context/heading.

        Returns URL string or None. None means: no relevant image found for this
        section — the slot is silently skipped. Article still publishes.

        Max MAX_SECTION_IMAGES section images per article (quality over quantity).
        """
        if not self._allowed_categories:
            ilog.log_skipped(ROLE_SECTION, "NO_CATEGORY_FOR_TOPIC", context)
            return None

        if self._section_count >= MAX_SECTION_IMAGES:
            ilog.log_skipped(
                ROLE_SECTION,
                f"MAX_SECTION_IMAGES_REACHED({MAX_SECTION_IMAGES})",
                context,
            )
            return None

        pool = [
            e for e in get_approved(role=ROLE_SECTION)
            if e.category in self._allowed_categories
        ]

        candidates = self._score_pool(pool, context, ROLE_SECTION, self._allowed_categories)

        if not candidates:
            ilog.log_skipped(ROLE_SECTION, "NO_CANDIDATE_PASSED_GATES", context)
            return None

        score, matched_kws, best = candidates[0]
        sel = ImageSelection(
            image_id=best.image_id, url=best.url, role=ROLE_SECTION,
            category=best.category, visual_cluster=best.visual_cluster,
            relevance_score=score, context=context,
        )
        self._record(sel)
        self._section_count += 1
        ilog.log_selected(ROLE_SECTION, best.image_id, best.url,
                          best.category, best.visual_cluster, score)
        return best.url

    def cta(self, slot: int = 0) -> str:
        """
        Select CTA image for the given slot index.

        Never returns None — always provides an image for CTA blocks.
        Only Gates 1–6 apply (no topic relevance required for decorative CTAs).
        Rotates through available CTA pool to avoid within-post duplicates.
        CTA images with reusable_cta=True may repeat across posts.
        """
        cta_pool  = get_approved(role=ROLE_CTA)
        candidates = self._score_pool(cta_pool, f"cta_slot_{slot}", ROLE_CTA, [])

        if candidates:
            # Always pick the highest-scoring available (unused in this post) CTA.
            # The pool is already filtered for local dedup in _score_pool.
            score, matched_kws, best = candidates[0]
            sel = ImageSelection(
                image_id=best.image_id, url=best.url, role=ROLE_CTA,
                category=best.category, visual_cluster=best.visual_cluster,
                relevance_score=score, context=f"cta_slot_{slot}",
            )
            self._record(sel)
            ilog.log_selected(ROLE_CTA, best.image_id, best.url,
                              best.category, best.visual_cluster, score)
            return best.url

        # Hard fallback — should never happen with a clean catalog (3+ CTA images)
        fallback = cta_pool[slot % len(cta_pool)] if cta_pool else None
        if fallback:
            log.warning(
                f"[IMAGE_SKIPPED] CTA slot={slot} pool exhausted — "
                f"force fallback to {fallback.image_id}"
            )
            self._used_urls.add(fallback.url)
            return fallback.url

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
          1. All image selection is complete (hub.section/hero/cta all done).
          2. Pre-publish validation has passed.
          3. HubSpot API call succeeded (or DRY_RUN).

        Never call during selection. Writes are one-way and permanent.
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
                selected_at          = ts,
            ))

        self._registry.register(entries)
        ilog.log_registry_written(post_slug, len(entries))

    # ── Validation report ─────────────────────────────────────────────────────

    def validation_report(self) -> dict:
        """
        Summary for publisher.py logging and hubspot.json 'images' field.
        """
        all_ids  = [s.image_id for s in self._selections]
        counts   = Counter(all_ids)
        dupes    = [i for i, n in counts.items() if n > 1]
        unverif  = [i for i in set(all_ids) if i not in APPROVED_IDS]
        sections = [s for s in self._selections if s.role == ROLE_SECTION]

        return {
            "image_count":         len(all_ids),
            "unique_image_count":  len(set(all_ids)),
            "section_images":      len(sections),
            "duplicate_ids":       dupes or "none",
            "unverified_ids":      unverif or "none",
            "allowed_categories":  self._allowed_categories or ["none (topic has no image category)"],
            "selections": [
                {
                    "role":           s.role,
                    "id":             s.image_id,
                    "category":       s.category,
                    "visual_cluster": s.visual_cluster,
                    "score":          round(s.relevance_score, 4),
                    "context":        s.context,
                }
                for s in self._selections
            ],
        }
