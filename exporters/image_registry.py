"""
image_registry.py — Persistent Google Sheets image registry.

SOURCE OF TRUTH for which images have been used across all published posts.

WHY SHEETS (NOT JSON / IN-MEMORY)
----------------------------------
Render's filesystem resets on every deploy → JSON files are lost.
In-memory state resets on every run → no cross-post dedup.
Google Sheets persists forever → cross-post dedup works across deploys and runs.

REGISTRY TAB SCHEMA
--------------------
Tab: "Image Registry"  (auto-created on first use if missing)
Columns:
  A  post_slug             HubSpot URL slug for the article
  B  post_title            Article content title
  C  article_keyword       Main Keyword column value
  D  topic_cluster         Topic Cluster column value
  E  image_id              Pexels photo ID
  F  image_url             Full CDN URL
  G  image_type            hero / section / cta
  H  category              Image catalog category (e.g. fba_logistics)
  I  visual_cluster        Visual cluster label (e.g. warehouse_workers)
  J  selected_for_section  Section heading this image was selected for
  K  selected_at           UTC timestamp

DEDUP RULES (enforced by registry)
------------------------------------
- section and hero images: globally deduped — never reused across posts.
- cta images with reusable_cta=True: freely reused (not tracked in used_ids).
- CTA images with reusable_cta=False would be deduped — but none exist by design.

FALLBACK
--------
If Sheets is unreachable, registry runs in-memory only for this session.
Publishing is NEVER blocked because of a registry connectivity failure.
In-memory dedup still prevents repeats within a single run.

REUSE IN OTHER PROJECTS
-----------------------
Only _get_client() and _get_sheet_id() need changing. All logic is generic.
"""
from __future__ import annotations

import os
import json
import logging
import datetime
from dataclasses import dataclass

from exporters import image_logging as ilog

log = logging.getLogger("image_registry")

# ── Sheets config ─────────────────────────────────────────────────────────────
TAB_NAME = "Image Registry"

HEADER_ROW = [
    "post_slug", "post_title", "article_keyword", "topic_cluster",
    "image_id", "image_url", "image_type", "category",
    "visual_cluster", "selected_for_section",
    "search_query", "image_source", "relevance_score",
    "prompt_used", "provider_image_id",
    "selected_at",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


# ─────────────────────────────────────────────────────────────────────────────
# RegistryEntry dataclass — one row in the Sheets tab
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RegistryEntry:
    post_slug: str
    post_title: str
    article_keyword: str
    topic_cluster: str
    image_id: str
    image_url: str
    image_type: str           # hero / section / cta
    category: str
    visual_cluster: str
    selected_for_section: str  # section heading text (or "" for hero/cta)
    search_query: str          # Pexels search query used (or "" for AI/static)
    image_source: str          # "openai" | "pexels" | "static_catalog"
    relevance_score: float     # composite score assigned at selection time
    prompt_used: str           # sha256[:16] of DALL-E prompt (or "" for Pexels/static)
    provider_image_id: str     # provider's own ID (HubSpot file ID, Pexels photo ID)
    selected_at: str           # UTC timestamp string

    def to_row(self) -> list:
        return [
            self.post_slug, self.post_title, self.article_keyword,
            self.topic_cluster, self.image_id, self.image_url,
            self.image_type, self.category, self.visual_cluster,
            self.selected_for_section,
            self.search_query, self.image_source,
            str(round(self.relevance_score, 4)),
            self.prompt_used, self.provider_image_id,
            self.selected_at,
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Sheets client helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_client():
    import gspread
    from google.oauth2.service_account import Credentials
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if creds_json:
        info  = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        from config import CREDENTIALS_FILE
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_sheet_id() -> str:
    from config import SHEET_ID
    return SHEET_ID


# ─────────────────────────────────────────────────────────────────────────────
# Registry class
# ─────────────────────────────────────────────────────────────────────────────

class ImageRegistry:
    """
    Persistent cross-post image registry backed by Google Sheets.

    Lifecycle
    ---------
    1. __init__() calls _load() — reads Sheets, populates _used_section_ids.
    2. ImageSelectionService calls is_globally_used() during scoring.
    3. ImageSelectionService calls get_cluster_usage() for diversity penalties.
    4. After final validation, ImageSelectionService calls register() once.
    5. register() appends rows to Sheets AND updates in-memory state.
    """

    def __init__(self):
        # Loaded from Sheets (or empty on first run / connectivity failure)
        self._entries: list           = []   # list[RegistryEntry]
        self._used_section_ids: set   = set()
        self._cluster_history: list   = []   # visual_cluster strings, all posts ordered
        self._connected: bool         = False
        self._ws                      = None
        self._load()

    # ── Internal: header repair ───────────────────────────────────────────────

    def _ensure_header(self, ws) -> None:
        """
        Guarantee row 1 of the worksheet matches HEADER_ROW exactly.

        Repairs three failure modes without touching any data rows:
          1. Header has duplicate column names (gspread raises on get_all_records)
          2. Header has fewer columns than HEADER_ROW (schema was extended)
          3. Header is completely missing or empty

        Safe: only overwrites row 1. Rows 2+ (data) are never modified.
        """
        try:
            current = ws.row_values(1)
        except Exception as exc:
            log.warning(f"[IMAGE_REGISTRY_LOADED] Could not read header row: {exc}")
            current = []

        has_duplicates = len(current) != len(set(current))
        needs_repair   = (current != HEADER_ROW) or has_duplicates

        if needs_repair:
            reason = "duplicates" if has_duplicates else "schema mismatch or missing columns"
            log.warning(
                f"[IMAGE_REGISTRY_LOADED] Header repair triggered ({reason})  "
                f"old={current}  new={HEADER_ROW}"
            )
            try:
                ws.update("A1", [HEADER_ROW])
                log.info("[IMAGE_REGISTRY_LOADED] Header row repaired successfully")
            except Exception as exc:
                log.warning(f"[IMAGE_REGISTRY_LOADED] Header repair write failed: {exc}")

    # ── Internal: load from Sheets ────────────────────────────────────────────

    def _load(self):
        try:
            client      = _get_client()
            spreadsheet = client.open_by_key(_get_sheet_id())
            titles      = [ws.title for ws in spreadsheet.worksheets()]

            if TAB_NAME in titles:
                self._ws  = spreadsheet.worksheet(TAB_NAME)

                # Always verify/repair header before loading records.
                # Prevents gspread "duplicate headers" error on get_all_records().
                self._ensure_header(self._ws)

                try:
                    all_rows = self._ws.get_all_records(expected_headers=HEADER_ROW)
                except TypeError:
                    # gspread <6.x doesn't have expected_headers — fall back
                    all_rows = self._ws.get_all_records()

                for row in all_rows:
                    img_id   = str(row.get("image_id", "")).strip()
                    img_type = str(row.get("image_type", "section")).strip().lower()
                    cluster  = str(row.get("visual_cluster", "")).strip()

                    if not img_id:
                        continue

                    # relevance_score is new — old rows have "" → default 0.0
                    try:
                        rel_score = float(row.get("relevance_score", 0) or 0)
                    except (ValueError, TypeError):
                        rel_score = 0.0

                    entry = RegistryEntry(
                        post_slug            = str(row.get("post_slug", "")),
                        post_title           = str(row.get("post_title", "")),
                        article_keyword      = str(row.get("article_keyword", "")),
                        topic_cluster        = str(row.get("topic_cluster", "")),
                        image_id             = img_id,
                        image_url            = str(row.get("image_url", "")),
                        image_type           = img_type,
                        category             = str(row.get("category", "")),
                        visual_cluster       = cluster,
                        selected_for_section = str(row.get("selected_for_section", "")),
                        search_query         = str(row.get("search_query", "")),
                        image_source         = str(row.get("image_source", "static_catalog")),
                        relevance_score      = rel_score,
                        prompt_used          = str(row.get("prompt_used", "")),
                        provider_image_id    = str(row.get("provider_image_id", "")),
                        selected_at          = str(row.get("selected_at", "")),
                    )
                    self._entries.append(entry)

                    # Only hero and section images are globally deduped
                    if img_type in ("hero", "section"):
                        self._used_section_ids.add(img_id)

                    # All image types contribute to visual cluster history
                    if cluster:
                        self._cluster_history.append(cluster)

            else:
                # First-ever run — create the tab
                self._ws = spreadsheet.add_worksheet(
                    title=TAB_NAME, rows=2000, cols=len(HEADER_ROW)
                )
                self._ws.update("A1", [HEADER_ROW])
                log.info(f"[IMAGE_REGISTRY_LOADED] Created '{TAB_NAME}' tab")

            self._connected = True
            ilog.log_registry_loaded(len(self._used_section_ids), True)

        except Exception as exc:
            log.warning(
                f"[IMAGE_REGISTRY_LOADED] Sheets unavailable ({exc})  "
                f"in-memory dedup only this session  connected=False"
            )
            self._connected = False
            self._ws        = None

    # ── Public query API ──────────────────────────────────────────────────────

    def is_globally_used(self, image_id: str) -> bool:
        """
        True if this image_id was used as section or hero in any prior post.
        CTA images with reusable_cta=True are never in this set.
        """
        return image_id in self._used_section_ids

    def get_cluster_usage_count(self, visual_cluster: str) -> int:
        """
        How many times this visual_cluster has appeared across all registered posts.
        Used to apply cross-post diversity penalty in scoring.
        """
        return self._cluster_history.count(visual_cluster)

    # ── Public write API ──────────────────────────────────────────────────────

    def register(self, entries: list) -> None:
        """
        Persist a list of RegistryEntry objects to Sheets.

        Must be called ONLY after:
          1. All image selection for the post is complete.
          2. Pre-publish validation has passed.
          3. HubSpot API call has succeeded (or DRY_RUN mode for test).

        Never called during selection — always after.
        """
        if not entries:
            return

        ts   = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        rows = []

        for entry in entries:
            if not entry.selected_at:
                entry.selected_at = ts

            rows.append(entry.to_row())

            # Update in-memory state immediately so subsequent code in this
            # same run sees the newly registered images as used.
            if entry.image_type in ("hero", "section"):
                self._used_section_ids.add(entry.image_id)
            if entry.visual_cluster:
                self._cluster_history.append(entry.visual_cluster)
            self._entries.append(entry)

            log.info(
                f"[IMAGE_REGISTRY_WRITTEN] slug='{entry.post_slug}'  "
                f"id={entry.image_id}  type={entry.image_type}  "
                f"category={entry.category}  cluster={entry.visual_cluster}"
            )

        if self._connected and self._ws is not None:
            try:
                self._ws.append_rows(rows, value_input_option="RAW")
                ilog.log_registry_written(
                    entries[0].post_slug if entries else "", len(rows)
                )
            except Exception as exc:
                log.warning(
                    f"[IMAGE_REGISTRY_WRITTEN] Sheets write failed ({exc})  "
                    f"in-memory state updated, Sheets not updated"
                )
        else:
            log.warning(
                f"[IMAGE_REGISTRY_WRITTEN] Not connected — "
                f"in-memory only: {[e.image_id for e in entries]}"
            )

    # ── Status ────────────────────────────────────────────────────────────────

    def status_report(self) -> dict:
        return {
            "total_entries":    len(self._entries),
            "used_section_ids": len(self._used_section_ids),
            "cluster_history":  len(self._cluster_history),
            "connected":        self._connected,
            "tab_name":         TAB_NAME,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_registry: "ImageRegistry | None" = None


def get_registry() -> ImageRegistry:
    """
    Return the global ImageRegistry singleton, loading from Sheets on first call.
    Always returns a registry — falls back to in-memory if Sheets is unavailable.
    """
    global _registry
    if _registry is None:
        _registry = ImageRegistry()
        rpt = _registry.status_report()
        log.info(
            f"[IMAGE_REGISTRY_LOADED] Registry ready  "
            f"used_section_ids={rpt['used_section_ids']}  "
            f"connected={rpt['connected']}"
        )
    return _registry


def reset_registry() -> None:
    """Force the singleton to reload on next get_registry() call. Tests only."""
    global _registry
    _registry = None
