"""
hubspot_files.py — Upload images to HubSpot Files API for permanent hosting.

HOW IT WORKS
------------
1. Download image from temporary URL (Replicate CDN)
2. POST to HubSpot Files API with the binary data
3. HubSpot returns a permanent URL (e.g., hs.hubspotusercontent.com/...)
4. Use that URL in blog post <img> tags

SCOPE CHECK
-----------
Before any Replicate generation, call check_hubspot_files_scope() to confirm
the HUBSPOT_TOKEN has the 'files' scope. On 403, logs [HUBSPOT_FILES_SCOPE_MISSING]
and returns False — no Replicate calls will be made.

CONFIGURATION
-------------
Requires HUBSPOT_TOKEN in environment (already required for publishing).
No additional credentials needed.

REQUIRED SCOPES (HubSpot private app)
--------------------------------------
  files  (read + write)
  crm.objects.blogs  (for blog publishing)

To fix a 403: Private Apps → your app → Scopes → add files → Save →
copy the new token → update HUBSPOT_TOKEN in Render env vars → redeploy.
"""
from __future__ import annotations

import io
import os
import re
import time
import logging
import requests

log = logging.getLogger("hubspot_files")

HUBSPOT_FILES_API = "https://api.hubapi.com/files/v3/files"
DEFAULT_FOLDER    = "/bubba-blog-images"
MAX_RETRIES       = 2
TIMEOUT           = 30   # seconds for download + upload


def _get_token() -> str:
    return os.environ.get("HUBSPOT_TOKEN", "").strip()


def _sanitize_filename(name: str) -> str:
    """Convert any string to a safe filename."""
    clean = re.sub(r"[^a-z0-9\-_]", "-", name.lower())
    clean = re.sub(r"-+", "-", clean).strip("-")
    return clean[:80] or "image"


# ─────────────────────────────────────────────────────────────────────────────
# Scope check — call once at provider startup
# ─────────────────────────────────────────────────────────────────────────────

def check_hubspot_files_scope() -> bool:
    """
    Lightweight check that HUBSPOT_TOKEN has the 'files' scope.

    Makes a GET /files/v3/files?limit=1 request.
      200 → scope OK  → [HUBSPOT_FILES_SCOPE_CHECK_PASS]
      403 → missing   → [HUBSPOT_FILES_SCOPE_MISSING]   → returns False
      other / error   → inconclusive (network/5xx)      → returns True
                        (don't block on transient infra failures)

    Returns True if scope is confirmed or check was inconclusive.
    Returns False ONLY on a confirmed 403 (token definitely missing files scope).
    """
    token = _get_token()
    if not token:
        log.warning(
            "[HUBSPOT_FILES] HUBSPOT_TOKEN not set — "
            "skipping scope check  "
            "fix='set HUBSPOT_TOKEN in Render environment variables'"
        )
        return False

    try:
        resp = requests.get(
            HUBSPOT_FILES_API,
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 1},
            timeout=10,
        )
        if resp.status_code == 200:
            log.info(
                "[HUBSPOT_FILES_SCOPE_CHECK_PASS] "
                "HubSpot Files API is accessible — token has required 'files' scope"
            )
            return True

        if resp.status_code == 403:
            log.error(
                "[HUBSPOT_FILES_SCOPE_MISSING] "
                "HubSpot Files API returned 403 Forbidden — token is missing 'files' scope.  "
                "fix='In HubSpot: Settings → Private Apps → your app → Scopes → "
                "enable files (read+write) → Save → copy new token → "
                "update HUBSPOT_TOKEN in Render env vars → Redeploy.  "
                "No Replicate images will be generated until this is fixed.'"
            )
            return False

        # 401, 429, 500, etc. — treat as inconclusive
        log.warning(
            f"[HUBSPOT_FILES] Scope check inconclusive  "
            f"status={resp.status_code}  treating_as=OK  "
            f"body={resp.text[:120]}"
        )
        return True

    except Exception as exc:
        log.warning(
            f"[HUBSPOT_FILES] Scope check failed with exception  "
            f"error={exc}  treating_as=OK (may be transient network issue)"
        )
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Download helper
# ─────────────────────────────────────────────────────────────────────────────

def download_image(url: str, timeout: int = TIMEOUT) -> bytes | None:
    """
    Download an image from a URL into memory.

    Returns raw bytes or None on failure.
    Works for Replicate temporary CDN URLs.
    """
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
        resp.raise_for_status()
        content = resp.content
        if not content:
            log.warning(f"[HUBSPOT_FILES] Empty response from {url[:80]}")
            return None
        log.info(f"[HUBSPOT_FILES] Downloaded {len(content):,} bytes from {url[:80]}")
        return content
    except requests.exceptions.Timeout:
        log.warning(f"[HUBSPOT_FILES] Timeout downloading {url[:80]}")
        return None
    except Exception as exc:
        log.warning(f"[HUBSPOT_FILES] Download error: {exc}  url={url[:80]}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Upload helper
# ─────────────────────────────────────────────────────────────────────────────

def upload_to_hubspot(
    image_bytes: bytes,
    filename: str,
    folder_path: str = DEFAULT_FOLDER,
    content_type: str = "image/jpeg",
) -> str | None:
    """
    Upload image bytes to HubSpot Files API.

    Parameters
    ----------
    image_bytes  : raw image bytes
    filename     : filename to use in HubSpot (e.g., "ppc-article-section-0.jpg")
    folder_path  : HubSpot folder path (auto-created if missing)
    content_type : MIME type of the image

    Returns
    -------
    Permanent public URL string, or None on failure.
    """
    token = _get_token()
    if not token:
        log.warning("[HUBSPOT_FILES] HUBSPOT_TOKEN not set — cannot upload image")
        return None

    if not image_bytes:
        log.warning("[HUBSPOT_FILES] Empty image bytes — skipping upload")
        return None

    safe_filename = _sanitize_filename(filename)
    if not safe_filename.endswith(".jpg"):
        safe_filename += ".jpg"

    log.info(
        f"[HUBSPOT_FILE_UPLOAD_STARTED] filename={safe_filename}  "
        f"folder={folder_path}  size={len(image_bytes):,}_bytes"
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                HUBSPOT_FILES_API,
                headers={"Authorization": f"Bearer {token}"},
                files={
                    "file": (safe_filename, io.BytesIO(image_bytes), content_type),
                },
                data={
                    "folderPath": folder_path,
                    "fileName":   safe_filename,
                    "options": '{"access":"PUBLIC_NOT_INDEXABLE","overwrite":false,'
                               '"duplicateValidationStrategy":"REJECT",'
                               '"duplicateValidationScope":"EXACT_FOLDER"}',
                },
                timeout=TIMEOUT,
            )

            if resp.status_code == 201:
                data       = resp.json()
                public_url = data.get("url", "")
                hs_id      = str(data.get("id", ""))
                log.info(
                    f"[HUBSPOT_FILE_UPLOADED] file_id={hs_id}  "
                    f"url={public_url[:80]}  filename={safe_filename}"
                )
                return public_url

            elif resp.status_code == 403:
                # Permission error — retrying won't help, bail immediately
                log.error(
                    f"[HUBSPOT_FILES_SCOPE_MISSING] Upload returned 403 Forbidden  "
                    f"filename={safe_filename}  "
                    f"fix='Add files scope to HubSpot private app token, save, "
                    f"copy new token, update HUBSPOT_TOKEN in Render env vars'"
                )
                return None

            elif resp.status_code == 409:
                # Duplicate — HubSpot already has this file; get existing URL
                log.info(
                    f"[HUBSPOT_FILES] Duplicate file '{safe_filename}' — "
                    f"fetching existing URL"
                )
                existing = _find_existing_file(safe_filename, folder_path, token)
                if existing:
                    return existing
                # Retry with a timestamped filename
                ts_name = _sanitize_filename(f"{filename}-{int(time.time())}")
                if not ts_name.endswith(".jpg"):
                    ts_name += ".jpg"
                safe_filename = ts_name
                continue

            else:
                log.warning(
                    f"[HUBSPOT_FILES] Upload failed  status={resp.status_code}  "
                    f"attempt={attempt}  body={resp.text[:200]}"
                )
                if attempt < MAX_RETRIES:
                    time.sleep(1)
                continue

        except requests.exceptions.Timeout:
            log.warning(f"[HUBSPOT_FILES] Upload timeout  attempt={attempt}")
            if attempt < MAX_RETRIES:
                time.sleep(2)
        except Exception as exc:
            log.warning(f"[HUBSPOT_FILES] Upload error: {exc}  attempt={attempt}")
            if attempt < MAX_RETRIES:
                time.sleep(1)

    log.warning(
        f"[HUBSPOT_FILE_UPLOAD_FAILED] filename={safe_filename}  "
        f"folder={folder_path}  attempts={MAX_RETRIES}"
    )
    return None


def _find_existing_file(filename: str, folder_path: str, token: str) -> str | None:
    """Search HubSpot Files API for an existing file by name."""
    try:
        resp = requests.get(
            HUBSPOT_FILES_API,
            headers={"Authorization": f"Bearer {token}"},
            params={"name": filename, "path": folder_path},
            timeout=10,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return results[0].get("url", "")
    except Exception as exc:
        log.debug(f"[HUBSPOT_FILES] Could not find existing file: {exc}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# High-level helper
# ─────────────────────────────────────────────────────────────────────────────

def upload_image_to_hubspot(
    source_url: str,
    article_slug: str,
    slot_name: str,
    folder_path: str = DEFAULT_FOLDER,
) -> str | None:
    """
    High-level helper: download from source_url, upload to HubSpot.

    Parameters
    ----------
    source_url   : temporary Replicate CDN URL
    article_slug : used to build a meaningful filename
    slot_name    : "hero" | "section_0" | "section_1" | "cta_0" etc.
    folder_path  : HubSpot folder (default: /bubba-blog-images)

    Returns
    -------
    Permanent HubSpot CDN URL, or None if any step fails.
    """
    image_bytes = download_image(source_url)
    if not image_bytes:
        return None

    filename = f"{article_slug}-{slot_name}"
    return upload_to_hubspot(image_bytes, filename, folder_path)
