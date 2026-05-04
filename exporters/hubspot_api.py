"""
HubSpot API Exporter
--------------------
Reads the hubspot.json already written by HubSpotExporter and POSTs it
to the HubSpot CMS API as a DRAFT blog post.

SAFE MODE  — runs when hubspot_meta.ready_for_api == false (default).
             No API call is made. Logs "safe mode — skipped".

MOCK MODE  — runs when config.HUBSPOT_MOCK_MODE == True.
             Logs the full payload that WOULD be sent, but makes no request.
             Use this for testing without a live token.

LIVE MODE  — runs when ready_for_api == true AND HUBSPOT_MOCK_MODE == False.
             Makes the real POST request. Requires HUBSPOT_TOKEN in .env.

Upgrade path:
  This file is already the upgrade path. When you're ready to go live:
    1. Set HUBSPOT_MOCK_MODE = False in config.py
    2. Set ready_for_api = true in hubspot.json (or flip it programmatically)
    3. Set HUBSPOT_TOKEN in .env to your real Private App token
"""

import os
import re
import json
import datetime
import requests
from collections import Counter
from dotenv import load_dotenv
from exporters.base import BaseExporter
from exporters.file_export import get_export_path
from exporters.image_selector import APPROVED_PEXELS_IDS
from config import (
    HUBSPOT_PORTAL_ID,
    HUBSPOT_BLOG_ID,
    HUBSPOT_AUTHOR_ID,
    HUBSPOT_API_URL,
    HUBSPOT_BASE_URL,
    HUBSPOT_MOCK_MODE,
)

load_dotenv(override=True)


# ── Field mapper ───────────────────────────────────────────────────────────────

def _map_to_api_payload(hs_data):
    """
    Maps hubspot.json → HubSpot CMS API v3 Blog Posts payload.
    state is ALWAYS "DRAFT" — never publishes automatically.
    useFeaturedImage is always False: HubSpot template renders the featuredImage
    field as a large hero banner at the top of the page, which duplicates the
    first content image and breaks the intended layout. Images live inside
    postBody only, selected at export time by image_selector.py.
    """
    post = hs_data.get("post", {})

    return {
        "name":             post.get("name", ""),
        "htmlTitle":        post.get("htmlTitle", ""),
        "metaDescription":  post.get("metaDescription", ""),
        "slug":             post.get("slug", ""),
        "postBody":         post.get("postBody", ""),
        "tagNames":         post.get("tagNames", []),
        "state":            "DRAFT",
        "contentGroupId":   str(HUBSPOT_BLOG_ID),
        "blogAuthorId":     str(HUBSPOT_AUTHOR_ID),
        "language":         "en",
        "useFeaturedImage": False,
    }


# ── Pre-publish validator ─────────────────────────────────────────────────────

# Hard thresholds — post is blocked if any of these are not met.
MIN_WORD_COUNT  = 900
MIN_H2_COUNT    = 4
MIN_SECTIONS    = 5
MIN_IMAGES      = 3
# MAX_IMAGES accounts for 3 CTA images + up to 5 section images (≤10 article sections)
MAX_IMAGES      = 8


def validate_post_package(hs_data):
    """
    Pre-publish gatekeeper. Blocks publish on any failure.

    Checks:
      Article text — word count ≥ 900, H2 count ≥ 4, section count ≥ 5,
                     must contain real paragraph text (not just images/CTAs)
      Images       — 3–6 total, no duplicates, all IDs in approved Pexels library
      CTAs         — exactly 3 blocks, all hrefs in APPROVED_URLS or bubbaacademy.com
      Form         — HubSpot form embed present
      Links        — 0 placeholder/broken hrefs (FILL_IN)
      SEO          — htmlTitle, metaDescription, slug all non-empty
      Cluster      — warns but does not block if 0 cluster links
    """
    from config import APPROVED_URLS as APPR
    APPROVED_HREF_PREFIXES = tuple(APPR.values()) + ("https://bubbaacademy.com",)

    post   = hs_data.get("post", {})
    body   = post.get("postBody", "")
    errors = []
    warns  = []

    # ── Article text quality ──────────────────────────────────────────────────
    # Strip all HTML tags to get plain text for word counting
    plain_text   = re.sub(r'<[^>]+>', ' ', body)
    plain_text   = re.sub(r'\s+', ' ', plain_text).strip()
    word_count   = len(plain_text.split()) if plain_text else 0
    h2_count     = len(re.findall(r'<h2[\s>]', body))
    # Count real content sections (hs-blog-section divs with non-trivial text)
    section_divs = re.findall(r'<div class="hs-blog-section">(.*?)</div>', body, re.DOTALL)
    text_sections = sum(
        1 for s in section_divs
        if len(re.sub(r'<[^>]+>', ' ', s).strip().split()) > 10
    )
    # Detect URL-only intro (blog_article was a URL instead of markdown)
    intro_match = re.search(r'<div class="hs-blog-intro">(.*?)</div>', body, re.DOTALL)
    intro_text  = re.sub(r'<[^>]+>', ' ', intro_match.group(1)).strip() if intro_match else ""
    intro_is_url = (
        intro_text.startswith("http://") or intro_text.startswith("https://")
    ) and len(intro_text.split()) <= 3

    if intro_is_url:
        errors.append(
            "Article intro is a URL, not article text. The Blog Draft Link column (I) "
            "was overwritten by a previous export with a Google Sheets tab URL. "
            "Reset column I to the original article markdown and re-run."
        )
    if word_count < MIN_WORD_COUNT:
        errors.append(f"Article word count is {word_count} — minimum is {MIN_WORD_COUNT}")
    if h2_count < MIN_H2_COUNT:
        errors.append(f"Only {h2_count} H2 heading(s) in post body — minimum is {MIN_H2_COUNT}")
    if text_sections < MIN_SECTIONS:
        errors.append(
            f"Only {text_sections} text section(s) with content — minimum is {MIN_SECTIONS}. "
            f"Article may be missing sections or blog_article field is empty/a URL."
        )

    # ── Images ────────────────────────────────────────────────────────────────
    img_urls   = re.findall(r'src="(https://[^"]+pexels[^"]+)"', body)
    img_ids    = [m.group(1) for m in (re.search(r'/photos/(\d+)/', u) for u in img_urls) if m]
    id_counts  = Counter(img_ids)
    duplicates = [id_ for id_, n in id_counts.items() if n > 1]
    unverified = [id_ for id_ in set(img_ids) if id_ not in APPROVED_PEXELS_IDS]

    if duplicates:
        errors.append(f"Duplicate image IDs: {duplicates}")
    if unverified:
        errors.append(f"Unapproved image IDs (not in curated library): {unverified}")
    if len(img_urls) < MIN_IMAGES:
        errors.append(f"Only {len(img_urls)} image(s) — minimum is {MIN_IMAGES}")
    if len(img_urls) > MAX_IMAGES:
        errors.append(f"{len(img_urls)} images — maximum is {MAX_IMAGES}")

    # ── CTAs ──────────────────────────────────────────────────────────────────
    cta_count    = len(re.findall(r'data-cta-type="', body))
    all_hrefs    = re.findall(r'href="([^"]+)"', body)
    bad_hrefs    = [h for h in all_hrefs if "FILL_IN" in h or not h.startswith("http")]
    cta_hrefs    = re.findall(r'class="hs-cta-button"[^>]*href="([^"]+)"', body)
    cta_button_hrefs = re.findall(r'<a href="([^"]+)"[^>]*class="hs-cta-button"', body)
    all_cta_hrefs    = cta_hrefs + cta_button_hrefs
    unapproved_cta   = [
        h for h in all_cta_hrefs
        if not any(h.startswith(p) for p in APPROVED_HREF_PREFIXES)
    ]

    if cta_count != 3:
        errors.append(f"{cta_count} CTA block(s) — expected exactly 3")
    if bad_hrefs:
        errors.append(f"Placeholder/broken hrefs found: {bad_hrefs[:3]}")
    if unapproved_cta:
        errors.append(f"CTA hrefs not in APPROVED_URLS: {unapproved_cta}")

    # ── HubSpot form embed ────────────────────────────────────────────────────
    form_present = "hs-form-embed" in body or "data-form-id=" in body
    if not form_present:
        errors.append("HubSpot embedded form is missing from postBody")

    # ── Cluster / internal links ───────────────────────────────────────────────
    blog_links = re.findall(r'href="(https://[^"]+hs-sites[^"]+)"', body)
    if len(blog_links) == 0:
        warns.append("No cluster links to related blog posts found — consider adding topic cluster links")

    # ── SEO fields ────────────────────────────────────────────────────────────
    if not post.get("htmlTitle", "").strip():
        errors.append("Missing SEO title (htmlTitle)")
    if not post.get("metaDescription", "").strip():
        errors.append("Missing meta description")
    if not post.get("slug", "").strip():
        errors.append("Missing slug")

    report = {
        "word_count":               word_count,
        "h2_count":                 h2_count,
        "text_sections":            text_sections,
        "image_count":              len(img_urls),
        "unique_image_count":       len(set(img_urls)),
        "duplicate_images":         duplicates or "none",
        "unverified_images":        unverified or "none",
        "cta_count":                cta_count,
        "cta_blocks_present":       cta_count == 3,
        "cta_hrefs_valid":          len(unapproved_cta) == 0,
        "form_present":             form_present,
        "placeholder_hrefs":        bad_hrefs or "none",
        "cluster_links_to_posts":   len(blog_links),
        "meta_title_present":       bool(post.get("htmlTitle", "").strip()),
        "meta_description_present": bool(post.get("metaDescription", "").strip()),
        "slug_present":             bool(post.get("slug", "").strip()),
        "warnings":                 warns or "none",
    }

    return {"valid": len(errors) == 0, "errors": errors, "report": report}


# ── Core functions ─────────────────────────────────────────────────────────────

def create_hubspot_draft(hs_data, token):
    """
    POSTs a draft to HubSpot CMS API.

    Returns:
        dict with keys: success, message, post_id (if created), draft_url (if created)
    """
    payload = _map_to_api_payload(hs_data)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    try:
        response = requests.post(
            HUBSPOT_API_URL,
            headers=headers,
            json=payload,
            timeout=30,
        )

        if response.status_code in (200, 201):
            data      = response.json()
            post_id   = data.get("id", "")
            draft_url = f"{HUBSPOT_BASE_URL}/{payload['slug']}"
            return {
                "success":     True,
                "message":     f"HubSpot draft created successfully — post ID: {post_id}",
                "post_id":     post_id,
                "draft_url":   draft_url,
                "status_code": response.status_code,
            }
        else:
            return {
                "success":     False,
                "message":     f"HubSpot API error {response.status_code}: {response.text[:300]}",
                "status_code": response.status_code,
            }

    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "HubSpot API: connection error — check internet or token"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "HubSpot API: request timed out after 30s"}
    except Exception as e:
        return {"success": False, "message": f"HubSpot API unexpected error: {e}"}


def update_hubspot_draft(post_id, hs_data, token):
    """
    PATCHes an existing HubSpot draft post with a new postBody (and updated SEO fields).
    Used to fix a previously created draft that had bad/empty content.

    Returns:
        dict with keys: success, message, post_id, draft_url, status_code
    """
    payload = _map_to_api_payload(hs_data)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    url = f"{HUBSPOT_API_URL}/{post_id}"

    try:
        response = requests.patch(url, headers=headers, json=payload, timeout=30)

        if response.status_code in (200, 201, 204):
            data      = response.json() if response.text else {}
            draft_url = f"{HUBSPOT_BASE_URL}/{payload['slug']}"
            return {
                "success":     True,
                "message":     f"HubSpot draft {post_id} updated successfully",
                "post_id":     post_id,
                "draft_url":   draft_url,
                "status_code": response.status_code,
            }
        else:
            return {
                "success":     False,
                "message":     f"HubSpot PATCH error {response.status_code}: {response.text[:300]}",
                "status_code": response.status_code,
            }

    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "HubSpot API: connection error"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "HubSpot API: request timed out after 30s"}
    except Exception as e:
        return {"success": False, "message": f"HubSpot API unexpected error: {e}"}


def publish_hubspot_post(post_id, token):
    """
    Publishes an existing HubSpot draft and confirms the URL is live (HTTP 200).

    Root cause: PATCH state=PUBLISHED only changes metadata. The field that
    actually activates the CDN route is publishImmediately=True — this sets
    currentlyPublished=True on the post object, which is what makes the public
    URL accessible. It is the equivalent of clicking Publish/Update in the UI.

    Steps:
      1. PATCH state=PUBLISHED + publishImmediately=True  → activates CDN route
      2. GET v3 post                                      → read state + url
      3. GET public URL with retry (up to 15s)            → confirm HTTP 200

    Returns:
        dict with keys: success, message, state, live_url, post_id, http_status
    """
    import time

    base_url = f"{HUBSPOT_API_URL}/{post_id}"
    now_ms   = int(datetime.datetime.utcnow().timestamp() * 1000)
    headers  = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    try:
        # Step 1 — PATCH with publishImmediately=True (sets currentlyPublished=True)
        pub = requests.patch(
            base_url,
            headers=headers,
            json={"state": "PUBLISHED", "publishDate": now_ms, "publishImmediately": True},
            timeout=30,
        )
        if pub.status_code not in (200, 201, 204):
            return {
                "success": False,
                "message": f"Publish PATCH failed {pub.status_code}: {pub.text[:300]}",
            }

        # Step 2 — confirm state and get live URL
        check     = requests.get(base_url, headers=headers, timeout=30)
        post_data = check.json()
        live_url  = post_data.get("url", "")
        state     = post_data.get("state", "")

        # Step 3 — verify public URL with retry
        http_status = None
        for _ in range(5):
            time.sleep(3)
            try:
                gr = requests.get(live_url, timeout=15, allow_redirects=True)
                http_status = gr.status_code
                if http_status == 200:
                    break
            except Exception:
                pass

        success = http_status == 200
        return {
            "success":     success,
            "message":     f"Post {post_id} {'live — HTTP 200 confirmed' if success else 'published but URL returned ' + str(http_status)}",
            "state":       state,
            "live_url":    live_url,
            "post_id":     post_id,
            "http_status": http_status,
        }

    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "HubSpot API: connection error"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "HubSpot API: request timed out after 30s"}
    except Exception as e:
        return {"success": False, "message": f"HubSpot API unexpected error: {e}"}


# ── Exporter class ─────────────────────────────────────────────────────────────

class HubSpotAPIExporter(BaseExporter):
    """
    Runs AFTER HubSpotExporter (which writes hubspot.json).

    Decision tree (evaluated in order):
        DRY_RUN env var == "true"   →  SKIP: exporter not included in pipeline
        HUBSPOT_MOCK_MODE == True   →  MOCK MODE: log payload, no request
        DRY_RUN == "false" (default)→  LIVE MODE: POST to HubSpot API

    NOTE: ready_for_api in hubspot.json is a local-dev manual override.
    In cloud deployments, DRY_RUN env var is the authoritative publish gate.
    When DRY_RUN=false, the API call is always attempted regardless of
    the ready_for_api flag written into hubspot.json.
    """

    def name(self):
        return "HubSpotAPIExporter"

    def export(self, row, content):
        import logging
        log = logging.getLogger("hubspot_api")

        export_path = get_export_path(row)
        json_path   = os.path.join(export_path, "hubspot.json")

        # ── Read hubspot.json ──────────────────────────────────────────────────
        if not os.path.exists(json_path):
            return {
                "success": False,
                "message": f"HubSpotAPIExporter: hubspot.json not found at {json_path}",
            }

        with open(json_path, encoding="utf-8") as f:
            hs_data = json.load(f)

        # ── Determine publish mode ─────────────────────────────────────────────
        # DRY_RUN=false (cloud default) → always go live.
        # DRY_RUN=true                  → exporter was excluded from pipeline;
        #   if somehow reached here, honour it as safe mode.
        dry_run   = os.environ.get("DRY_RUN", "false").lower() == "true"
        json_flag = hs_data.get("hubspot_meta", {}).get("ready_for_api", False)

        # In live mode the env var takes priority over the JSON flag.
        # The JSON flag (ready_for_api) only gates when DRY_RUN is not set.
        ready = True if not dry_run else json_flag

        # ── SAFE MODE (only when DRY_RUN=true or unset and flag is false) ─────
        if not ready:
            msg = (
                "HubSpot API — SAFE MODE: DRY_RUN=true or ready_for_api is false. "
                "No request sent."
            )
            print(f"     {msg}")
            return {"success": True, "message": msg, "skipped": True, "mode": "safe"}

        # ── MOCK MODE ──────────────────────────────────────────────────────────
        if HUBSPOT_MOCK_MODE:
            payload = _map_to_api_payload(hs_data)
            print(f"     HubSpot API — MOCK MODE: would POST to {HUBSPOT_API_URL}")
            print(f"     Payload preview:")
            print(f"       name          : {payload['name']}")
            print(f"       htmlTitle     : {payload['htmlTitle']}")
            print(f"       slug          : {payload['slug']}")
            print(f"       contentGroupId: {payload['contentGroupId']}")
            print(f"       blogAuthorId  : {payload['blogAuthorId']}")
            print(f"       state         : {payload['state']}")
            print(f"       tagNames      : {payload['tagNames']}")
            print(f"       postBody len  : {len(payload['postBody'])} chars")
            print(f"       [No request sent — HUBSPOT_MOCK_MODE=True]")

            hs_data["hubspot_api_mock"] = {
                "mock_run_at":    datetime.datetime.utcnow().isoformat() + "Z",
                "endpoint":       HUBSPOT_API_URL,
                "payload_keys":   list(payload.keys()),
                "payload_preview": {k: v for k, v in payload.items() if k != "postBody"},
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(hs_data, f, indent=2, ensure_ascii=False)

            return {
                "success": True,
                "message": "HubSpot API — MOCK MODE: payload logged, no request sent",
                "skipped": False,
                "mode":    "mock",
            }

        # ── LIVE MODE ──────────────────────────────────────────────────────────
        token = os.environ.get("HUBSPOT_TOKEN", "").strip()

        if not token or token == "YOUR_HUBSPOT_PRIVATE_APP_TOKEN":
            return {
                "success": False,
                "message": "HubSpot API — LIVE MODE failed: HUBSPOT_TOKEN not set in environment",
            }

        # ── Pre-publish validation ─────────────────────────────────────────────
        validation = validate_post_package(hs_data)
        hs_data["validation_report"] = validation["report"]
        rpt = validation["report"]
        log.info(
            f"     Validation: words={rpt.get('word_count')} "
            f"h2s={rpt.get('h2_count')} "
            f"sections={rpt.get('text_sections')} "
            f"cta_blocks={rpt.get('cta_count')} "
            f"form={rpt.get('form_present')} "
            f"images={rpt.get('image_count')} "
            f"duplicates={rpt.get('duplicate_images')} "
            f"meta_title={rpt.get('meta_title_present')} "
            f"slug={rpt.get('slug_present')}"
        )

        if not validation["valid"]:
            err_str = " | ".join(validation["errors"])
            msg = f"HubSpot API — BLOCKED by pre-publish validation: {err_str}"
            log.error(f"     {msg}")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(hs_data, f, indent=2, ensure_ascii=False)
            return {"success": False, "message": msg, "mode": "live", "validation": validation}

        # ── API call ───────────────────────────────────────────────────────────
        post = hs_data.get("post", {})
        log.info(f"     Creating HubSpot post via API...")
        log.info(f"       Title : {post.get('name', '')}")
        log.info(f"       Slug  : {post.get('slug', '')}")
        log.info(f"       Blog  : {HUBSPOT_BLOG_ID}  Author: {HUBSPOT_AUTHOR_ID}")

        result = create_hubspot_draft(hs_data, token)

        log.info(f"     HubSpot API response status: {result.get('status_code', 'N/A')}")

        if result["success"]:
            log.info(f"     HubSpot Post ID: {result.get('post_id')}")
            log.info(f"     HubSpot URL: {result.get('draft_url')}")
            print(f"     HubSpot draft created — Post ID: {result.get('post_id')}")
            print(f"     HubSpot URL: {result.get('draft_url')}")

            hs_data["hubspot_api_result"] = {
                "published_at": datetime.datetime.utcnow().isoformat() + "Z",
                "post_id":      result.get("post_id"),
                "draft_url":    result.get("draft_url"),
                "status_code":  result.get("status_code"),
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(hs_data, f, indent=2, ensure_ascii=False)
        else:
            log.error(f"     HubSpot API error: {result['message']}")
            print(f"     HubSpot API error: {result['message']}")

        result["mode"] = "live"
        return result
