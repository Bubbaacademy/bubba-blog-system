"""
hubspot_files.py — Upload images to HubSpot Files API for permanent hosting.

WHY THIS EXISTS
---------------
DALL-E 3 generates images with temporary URLs (valid ~1 hour).
We need permanent, publicly accessible URLs to embed in HubSpot blog posts.
HubSpot's Files API accepts binary uploads and returns permanent CDN URLs.

HOW IT WORKS
------------
1. Download image from temporary URL (AI-generated or any source)
2. POST to HubSpot Files API with the binary data
3. HubSpot returns a permanent URL (e.g., hs.hubspotusercontent.com/...)
4. Use that URL in blog post <img> tags

FALLBACK
--------
If upload fails for any reason, returns None.
Caller (image_provider.py) then returns None for this slot.
Article publishes without this image — never uses warehouse fallback.

CONFIGURATION
-------------
Requires HUBSPOT_TOKEN in environment (already required for publishing).
No additional credentials needed.
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


def download_image(url: str, timeout: int = TIMEOUT) -> bytes | None:
    """
    Download an image from a URL into memory.

    Returns raw bytes or None on failure.
    Works for DALL-E temporary URLs and Pexels CDN URLs.
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
    image_bytes  : raw image bytes (from DALL-E or Pexels download)
    filename     : filename to use in HubSpot (e.g., "ppc-article-section-0.jpg")
    folder_path  : HubSpot folder path (created automatically if missing)
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
                    f"[HUBSPOT_FILES] Uploaded  file_id={hs_id}  "
                    f"url={public_url[:80]}  filename={safe_filename}"
                )
                return public_url

            elif resp.status_code == 409:
                # Duplicate — HubSpot already has this file; get existing URL
                log.info(
                    f"[HUBSPOT_FILES] Duplicate file '{safe_filename}' — "
                    f"fetching existing URL"
                )
                existing = _find_existing_file(safe_filename, folder_path, token)
                if existing:
                    return existing
                # If we can't find it, retry with a timestamped filename
                ts_name  = _sanitize_filename(f"{filename}-{int(time.time())}")
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

    log.warning(f"[HUBSPOT_FILES] All upload attempts failed for '{safe_filename}'")
    return None


def _find_existing_file(filename: str, folder_path: str, token: str) -> str | None:
    """Search HubSpot Files API for an existing file by name."""
    try:
        resp = requests.get(
            "https://api.hubapi.com/files/v3/files",
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
    source_url   : temporary AI-generated URL or any image URL to download
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
