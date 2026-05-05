"""
image_logging.py — Structured log helpers for the image selection pipeline.

All log tag constants are defined here. Never hardcode tags in other modules.
Every selection decision — approved or rejected — is logged via these helpers.

LOG TAGS (for grepping Render logs)
-------------------------------------
[IMAGE_ROUTE]            — topic routing result (category assigned)
[IMAGE_CANDIDATE]        — summary after scoring a pool (counts per rejection reason)
[IMAGE_REJECTED]         — individual image rejected with reason + score
[IMAGE_APPROVED]         — individual image passed all gates (pre-commit)
[IMAGE_SELECTED]         — final winner committed to this post
[IMAGE_SKIPPED]          — slot skipped (no valid candidate, or budget reached)
[IMAGE_REGISTRY_LOADED]  — registry loaded from Sheets at startup
[IMAGE_REGISTRY_WRITTEN] — image(s) written to Sheets registry after publish
"""
from __future__ import annotations

import logging

log = logging.getLogger("image_selector")

# ── Log tag constants — grep-friendly ─────────────────────────────────────────
TAG_ROUTE    = "[IMAGE_ROUTE]"
TAG_CAND     = "[IMAGE_CANDIDATE]"
TAG_REJECTED = "[IMAGE_REJECTED]"
TAG_APPROVED = "[IMAGE_APPROVED]"
TAG_SELECTED = "[IMAGE_SELECTED]"
TAG_SKIPPED  = "[IMAGE_SKIPPED]"
TAG_LOADED   = "[IMAGE_REGISTRY_LOADED]"
TAG_WRITTEN  = "[IMAGE_REGISTRY_WRITTEN]"


# ─────────────────────────────────────────────────────────────────────────────
# Per-decision helpers
# ─────────────────────────────────────────────────────────────────────────────

def log_route(keyword: str, cluster: str, matched: str, categories: list) -> None:
    log.info(
        f"{TAG_ROUTE} keyword='{keyword}'  cluster='{cluster}'  "
        f"matched='{matched}'  categories={categories or 'NONE'}"
    )


def log_rejected(
    image_id: str,
    reason: str,
    score: float = 0.0,
    context: str = "",
) -> None:
    log.info(
        f"{TAG_REJECTED} id={image_id}  reason={reason}  "
        f"score={score:.4f}  context='{context[:60]}'"
    )


def log_approved(
    image_id: str,
    category: str,
    visual_cluster: str,
    score: float,
    context: str,
    matched_keywords: list,
) -> None:
    log.info(
        f"{TAG_APPROVED} id={image_id}  category={category}  "
        f"cluster={visual_cluster}  score={score:.4f}  "
        f"matched_keywords={matched_keywords}  context='{context[:60]}'"
    )


def log_selected(
    role: str,
    image_id: str,
    url: str,
    category: str,
    visual_cluster: str,
    score: float,
) -> None:
    log.info(
        f"{TAG_SELECTED} role={role}  id={image_id}  category={category}  "
        f"cluster={visual_cluster}  score={score:.4f}  url={url[:70]}"
    )


def log_skipped(role: str, reason: str, context: str = "") -> None:
    log.info(
        f"{TAG_SKIPPED} role={role}  reason={reason}  context='{context[:60]}'"
    )


def log_pool_summary(
    context: str,
    role: str,
    pool_size: int,
    globally_used: int,
    category_mismatch: int,
    blocked: int,
    low_quality: int,
    low_relevance: int,
    approved: int,
) -> None:
    """Log a one-line summary after scoring an entire pool."""
    log.info(
        f"{TAG_CAND} context='{context[:40]}'  role={role}  "
        f"pool={pool_size}  "
        f"skipped_global_dup={globally_used}  "
        f"skipped_category={category_mismatch}  "
        f"skipped_blocked={blocked}  "
        f"skipped_quality={low_quality}  "
        f"skipped_relevance={low_relevance}  "
        f"passed={approved}"
    )


def log_registry_loaded(used_count: int, connected: bool) -> None:
    log.info(
        f"{TAG_LOADED} used_section_ids={used_count}  connected={connected}"
    )


def log_registry_written(post_slug: str, count: int) -> None:
    log.info(
        f"{TAG_WRITTEN} slug='{post_slug}'  images_committed={count}"
    )
