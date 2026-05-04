"""
SheetsImageRegistry — Google Sheets-backed persistent image registry.

WHY SHEETS (NOT JSON)
---------------------
Render's filesystem is ephemeral: every new deploy wipes /exports/used_images.json.
A JSON-based registry is lost after each deploy → same images are reused post after post.
Google Sheets persists across all deploys and is visible/editable by the team.

TAB STRUCTURE
-------------
Tab name : "Image Registry"  (auto-created on first use)
Columns  :
  A  Post Slug     — the HubSpot slug for the article that used this image
  B  Image ID      — Pexels photo ID
  C  Image URL     — full CDN URL
  D  Registered At — UTC timestamp (YYYY-MM-DD HH:MM UTC)
  E  Type          — "section" or "cta"

USAGE
-----
  from exporters.sheets_image_registry import get_sheets_registry

  registry = get_sheets_registry()          # singleton; reads Sheets on first call
  if registry.is_globally_used("4481323"):
      ...                                   # image already used in a prior post
  registry.register_post(slug, entries)     # entries = [{"id": ..., "url": ..., "type": ...}]

FALLBACK
--------
If Sheets is unreachable (network error, missing credentials), the registry
operates in degraded mode — in-memory only for this run. A warning is logged.
The system NEVER blocks publishing because the registry is unavailable.
"""

from __future__ import annotations

import os
import json
import logging
import datetime
import gspread
from google.oauth2.service_account import Credentials

log = logging.getLogger("sheets_image_registry")

# ── Sheets configuration ──────────────────────────────────────────────────────

TAB_NAME = "Image Registry"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_HEADER_ROW = ["Post Slug", "Image ID", "Image URL", "Registered At", "Type"]


def _get_sheets_client():
    """Load credentials from env var or local file (same pattern as google_docs.py)."""
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


# ── Registry class ────────────────────────────────────────────────────────────

class SheetsImageRegistry:
    """
    Persistent cross-post image registry backed by a Google Sheets tab.

    On init: loads all previously registered image IDs from Sheets.
    On register_post: appends new rows to the Sheets tab.
    Falls back to in-memory-only mode if Sheets is unreachable.
    """

    def __init__(self):
        self._used_ids:  set  = set()   # globally used section image IDs
        self._connected: bool = False
        self._ws               = None   # gspread Worksheet handle
        self._load()

    # ── Internal: connect and load ────────────────────────────────────────────

    def _load(self):
        try:
            client      = _get_sheets_client()
            spreadsheet = client.open_by_key(_get_sheet_id())

            # Find or create the Image Registry tab
            existing_titles = [ws.title for ws in spreadsheet.worksheets()]
            if TAB_NAME in existing_titles:
                self._ws = spreadsheet.worksheet(TAB_NAME)
            else:
                self._ws = spreadsheet.add_worksheet(
                    title=TAB_NAME, rows=500, cols=5
                )
                self._ws.update("A1", [_HEADER_ROW])
                log.info(f"[ImageRegistry] Created '{TAB_NAME}' tab in Google Sheet")

            # Load all rows
            all_rows = self._ws.get_all_records()
            for row in all_rows:
                img_id = str(row.get("Image ID", "")).strip()
                if img_id:
                    self._used_ids.add(img_id)

            self._connected = True
            log.info(
                f"[ImageRegistry] Loaded {len(self._used_ids)} used image ID(s) from Sheets"
            )

        except Exception as exc:
            log.warning(
                f"[ImageRegistry] Sheets unavailable ({exc}) — "
                "running in-memory only this session"
            )
            self._connected = False
            self._ws        = None

    # ── Public API ────────────────────────────────────────────────────────────

    def is_globally_used(self, photo_id: str) -> bool:
        """Return True if this image ID was used in any previous post."""
        return photo_id in self._used_ids

    def register_post(self, slug: str, entries: list):
        """
        Mark a set of images as used by a post.

        Args:
            slug    — HubSpot slug for the article
            entries — list of dicts: [{"id": "4481323", "url": "...", "type": "section"}, ...]

        Appends one row per image to the Sheets tab.
        Logs [IMAGE_REGISTRY_WRITTEN] for each committed image.
        Falls back to in-memory update if Sheets write fails.
        """
        if not entries:
            log.info(f"[ImageRegistry] No entries to register for '{slug}'")
            return

        ts    = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        rows  = []
        new_ids = []

        for entry in entries:
            img_id  = str(entry.get("id", "")).strip()
            img_url = str(entry.get("url", "")).strip()
            img_type = str(entry.get("type", "section")).strip()

            if not img_id:
                continue

            self._used_ids.add(img_id)
            new_ids.append(img_id)
            rows.append([slug, img_id, img_url, ts, img_type])

            log.info(
                f"[IMAGE_REGISTRY_WRITTEN] slug='{slug}'  id={img_id}  "
                f"type={img_type}  url={img_url[:60]}"
            )

        if not rows:
            return

        if self._connected and self._ws is not None:
            try:
                self._ws.append_rows(rows, value_input_option="RAW")
                log.info(
                    f"[ImageRegistry] Wrote {len(rows)} row(s) to Sheets for '{slug}'"
                )
            except Exception as exc:
                log.warning(
                    f"[ImageRegistry] Sheets write failed ({exc}) — "
                    f"IDs committed in-memory: {new_ids}"
                )
        else:
            log.warning(
                f"[ImageRegistry] Not connected to Sheets — "
                f"IDs committed in-memory only: {new_ids}"
            )

    def status_report(self) -> dict:
        return {
            "used_count": len(self._used_ids),
            "connected":  self._connected,
            "tab_name":   TAB_NAME,
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_sheets_registry: SheetsImageRegistry | None = None


def get_sheets_registry() -> SheetsImageRegistry:
    global _sheets_registry
    if _sheets_registry is None:
        _sheets_registry = SheetsImageRegistry()
        rpt = _sheets_registry.status_report()
        log.info(
            f"[ImageRegistry] Sheets registry ready — "
            f"used={rpt['used_count']}  connected={rpt['connected']}"
        )
    return _sheets_registry
