import os
import logging
import datetime
from dotenv import load_dotenv
from sheets_client import get_sheet, update_status
from exporters.google_docs import GoogleDocsExporter
from exporters.file_export import FileExporter
from exporters.hubspot import HubSpotExporter
from exporters.hubspot_api import HubSpotAPIExporter
from config import (
    COLUMNS,
    STATUS_DRAFT_READY,
    STATUS_EXPORTED,
    STATUS_FAILED,
    APPROVAL_TRIGGER,
    EXPORTS_DIR,
)

load_dotenv(override=True)

log = logging.getLogger("publisher")

# DRY_RUN=true  → builds + validates everything but skips the live HubSpot API call.
# DRY_RUN=false → full publish pipeline including real API POST (default).
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

_ALL_EXPORTERS = [
    GoogleDocsExporter(),
    FileExporter(),
    HubSpotExporter(),
    HubSpotAPIExporter(),
]

# In DRY_RUN mode the HubSpotAPIExporter (live POST) is excluded entirely.
EXPORTERS = [
    e for e in _ALL_EXPORTERS
    if not (DRY_RUN and isinstance(e, HubSpotAPIExporter))
]

if DRY_RUN:
    log.info("DRY_RUN=true — HubSpotAPIExporter excluded (no live HubSpot publish)")


# ── Sheet helpers ──────────────────────────────────────────────────────────────

def get_approved_rows(sheet):
    all_rows = sheet.get_all_records()
    approved = []
    for i, row in enumerate(all_rows):
        status   = str(row.get("Status", "")).strip().lower()
        approval = str(row.get("Approval", "")).strip().lower()
        if status == STATUS_DRAFT_READY.lower() and approval == APPROVAL_TRIGGER.lower():
            approved.append({
                "row_index": i + 2,
                "row_data":  row,
            })
    return approved


def read_content_from_row(row):
    return {
        "seo_title":        row.get("SEO Title", ""),
        "meta_description": row.get("Meta Description", ""),
        "blog_article":     row.get("Blog Draft Link", ""),
        "social_caption":   row.get("Social Caption", ""),
        "video_script":     row.get("Video Script", ""),
        "email_copy":       row.get("Email Copy", ""),
    }


def write_export_results(sheet, row_index, results, api_result=None):
    """
    Writes export metadata to the Notes column.
    If api_result is provided and has post_id/draft_url, those are included.

    IMPORTANT: We deliberately do NOT write doc_url back to COLUMNS["blog_draft"]
    (column I).  That column holds the article markdown written by main.py.
    Overwriting it with the Google Sheets tab URL destroys the markdown and causes
    the next export run to build a postBody with only images/CTAs and no article text.
    The doc_url is already captured in the message field and appears in Notes.
    """
    ts          = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    notes_lines = [f"Exported at {ts}"]

    for result in results:
        msg = result.get("message", "")
        if msg:
            notes_lines.append(f"- {msg}")

    # Append HubSpot API details if available
    if api_result and api_result.get("post_id"):
        notes_lines.append(f"- HubSpot Post ID: {api_result['post_id']}")
        notes_lines.append(f"- HubSpot URL: {api_result.get('draft_url', '')}")
        notes_lines.append(f"- API status: {api_result.get('status_code', '')}")

    sheet.update_cell(row_index, COLUMNS["notes"], "\n".join(notes_lines))


def write_hubspot_url(sheet, row_index, url):
    """Writes the HubSpot draft URL to the Published URL column (N)."""
    if url:
        sheet.update_cell(row_index, COLUMNS["published_url"], url)


def write_failure_note(sheet, row_index, error_msg):
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    sheet.update_cell(
        row_index,
        COLUMNS["notes"],
        f"FAILED at {timestamp}\nError: {error_msg}",
    )


# ── Main publisher ─────────────────────────────────────────────────────────────

def run_publisher():
    print("\n" + "=" * 50)
    print("  Bubba Academy Publisher Agent")
    print("=" * 50)

    os.makedirs(EXPORTS_DIR, exist_ok=True)

    print("\n[1/3] Connecting to Google Sheet...")
    try:
        sheet = get_sheet()
        print("      Connected successfully.")
    except Exception as e:
        print(f"      ERROR: {e}")
        return

    print(f"\n[2/3] Scanning for rows where Status = '{STATUS_DRAFT_READY}'"
          f" and Approval = '{APPROVAL_TRIGGER}'...")
    try:
        approved = get_approved_rows(sheet)
    except Exception as e:
        print(f"      ERROR reading rows: {e}")
        return

    if not approved:
        print("      No approved rows found. Nothing to export.")
        return {"approved_found": 0, "exported": 0, "failed": 0}

    print(f"      Found {len(approved)} row(s) to export.")
    print("\n[3/3] Running export pipeline...\n")

    success = 0
    failed  = 0

    for item in approved:
        row_index  = item["row_index"]
        row_data   = item["row_data"]
        title      = row_data.get("Content Title", "Untitled")
        keyword    = row_data.get("Main Keyword", "")

        log.info(f"  Processing: \"{title}\"  (keyword: {keyword})")
        print(f"  Exporting: \"{title}\"")

        content     = read_content_from_row(row_data)
        all_results = []
        row_failed  = False
        api_result  = None   # result from HubSpotAPIExporter specifically

        # ── Run each exporter ─────────────────────────────────────────────────
        for exporter in EXPORTERS:
            print(f"  -> Running {exporter.name()}...")
            try:
                result = exporter.export(row_data, content)
                result["_exporter"] = exporter.name()   # tag for post-loop analysis
                all_results.append(result)

                if result.get("success"):
                    print(f"     OK: {result.get('message', '')}")

                    # Structured metrics after HubSpotExporter builds the post body
                    if "HubSpotExporter" in exporter.name() and "API" not in exporter.name():
                        tracker = content.get("_image_tracker")
                        if tracker:
                            rpt = tracker.validation_report()
                            log.info(
                                f"  [IMAGES]  total={rpt['image_count']}"
                                f"  unique={rpt['unique_image_count']}"
                                f"  duplicates={rpt['duplicate_ids']}"
                                f"  unverified={rpt['unverified_ids']}"
                            )
                        log.info(
                            f"  [LINKS]   cluster={content.get('_cluster_links_count', 0)}"
                            f"  brand={content.get('_brand_links_count', 0)}"
                        )

                    # Capture HubSpot API result for post-loop handling
                    if isinstance(exporter, HubSpotAPIExporter):
                        api_result = result
                        mode = result.get("mode", "")
                        if mode == "live":
                            log.info(f"  [HUBSPOT] Post ID: {result.get('post_id')}")
                            log.info(f"  [HUBSPOT] URL: {result.get('draft_url')}")
                        elif mode == "safe":
                            log.info("  [HUBSPOT] safe mode — no API call made")
                        elif mode == "mock":
                            log.info("  [HUBSPOT] mock mode — payload logged, no API call")

                else:
                    log.warning(f"  [FAIL] {exporter.name()}: {result.get('message', '')}")
                    print(f"     FAILED: {result.get('message', '')}")
                    if isinstance(exporter, HubSpotAPIExporter):
                        api_result = result
                    row_failed = True

            except Exception as e:
                msg = f"{exporter.name()} raised exception: {e}"
                log.error(f"  [ERROR] {msg}")
                print(f"     ERROR: {msg}")
                all_results.append({"success": False, "message": msg,
                                    "_exporter": exporter.name()})
                row_failed = True

        # ── Validation report log (if API ran it) ─────────────────────────────
        for r in all_results:
            if r.get("validation"):
                vr = r["validation"].get("report", {})
                log.info(
                    f"  [VALIDATE] cta_blocks={vr.get('cta_count')} "
                    f"cluster_links={vr.get('cluster_links_to_posts')} "
                    f"meta_title={vr.get('meta_title_present')} "
                    f"meta_desc={vr.get('meta_description_present')} "
                    f"slug={vr.get('slug_present')}"
                )
                if r["validation"].get("errors"):
                    log.error(f"  [VALIDATE] BLOCKED: {r['validation']['errors']}")

        # ── Status decision ───────────────────────────────────────────────────
        #
        # Three outcomes:
        #   A. HubSpot API succeeded (post_id present) → write URL + mark Exported
        #   B. HubSpot API failed or was skipped in live mode → mark Failed
        #   C. DRY_RUN=true (API not in pipeline, api_result is None) → don't advance
        #
        api_in_pipeline = any(isinstance(e, HubSpotAPIExporter) for e in EXPORTERS)

        if DRY_RUN or not api_in_pipeline:
            # ── Case C: DRY_RUN — don't advance status, just log exports ─────
            write_export_results(sheet, row_index, all_results, api_result=None)
            log.info(f"  DRY_RUN: exports written, status remains '{STATUS_DRAFT_READY}'")
            print(f"  DRY_RUN: no live publish — status kept as '{STATUS_DRAFT_READY}'.\n")
            success += 1  # counted as a success for the dry-run summary

        elif api_result and api_result.get("mode") == "live" and api_result.get("success"):
            # ── Case A: API succeeded ─────────────────────────────────────────
            post_id   = api_result.get("post_id", "")
            live_url  = api_result.get("draft_url", "")  # draft_url is now the live URL
            published = api_result.get("published", False)
            write_export_results(sheet, row_index, all_results, api_result=api_result)
            write_hubspot_url(sheet, row_index, live_url)
            update_status(sheet, row_index, STATUS_EXPORTED)
            pub_label = "PUBLISHED" if published else "DRAFT (publish step failed — manual publish needed)"
            log.info(f"  Status → '{STATUS_EXPORTED}'  Post ID: {post_id}  HubSpot: {pub_label}  URL: {live_url}")
            print(f"  Status updated to '{STATUS_EXPORTED}'.")
            print(f"  HubSpot Post ID: {post_id}")
            print(f"  HubSpot state:   {pub_label}")
            print(f"  HubSpot URL:     {live_url}\n")
            success += 1

        else:
            # ── Case B: API failed, skipped unexpectedly, or other exporter error
            if api_result and api_result.get("skipped"):
                # Skipped in live mode — means ready_for_api was False in the JSON
                # This should not happen with DRY_RUN=false; treat as misconfiguration
                error_msg = (
                    "HubSpot API was in SAFE MODE despite DRY_RUN=false. "
                    "This is a configuration error — post NOT created."
                )
            elif api_result and not api_result.get("success"):
                error_msg = api_result.get("message", "HubSpot API call failed")
            else:
                error_msg = "One or more exporters failed — check logs for details."

            log.error(f"  [FAILED] \"{title}\": {error_msg}")
            write_failure_note(sheet, row_index, error_msg)
            update_status(sheet, row_index, STATUS_FAILED)
            print(f"  Status updated to '{STATUS_FAILED}'.")
            print(f"  Error: {error_msg}\n")
            failed += 1

    print("=" * 50)
    print(f"  Export complete. Success: {success} | Failed: {failed}")
    print("=" * 50 + "\n")
    return {"approved_found": len(approved), "exported": success, "failed": failed}


if __name__ == "__main__":
    run_publisher()
