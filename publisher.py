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
    APPROVAL_TRIGGER,
    EXPORTS_DIR,
)

load_dotenv(override=True)

log = logging.getLogger("publisher")

# DRY_RUN=true  → builds + validates everything but skips the live API publish call.
# DRY_RUN=false → full publish (default).
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

_ALL_EXPORTERS = [
    GoogleDocsExporter(),
    FileExporter(),
    HubSpotExporter(),
    HubSpotAPIExporter(),
]

# In DRY_RUN mode the HubSpotAPIExporter (live POST) is excluded.
EXPORTERS = [
    e for e in _ALL_EXPORTERS
    if not (DRY_RUN and isinstance(e, HubSpotAPIExporter))
]

if DRY_RUN:
    log.info("DRY_RUN=true — HubSpotAPIExporter will be skipped (no live publish)")


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


def write_export_results(sheet, row_index, results):
    doc_url = ""
    notes_lines = [f"Exported at {datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"]

    for result in results:
        if result.get("doc_url"):
            doc_url = result["doc_url"]
        notes_lines.append(f"- {result.get('message', '')}")

    if doc_url:
        sheet.update_cell(row_index, COLUMNS["blog_draft"], doc_url)

    sheet.update_cell(row_index, COLUMNS["notes"], "\n".join(notes_lines))


def write_failure_note(sheet, row_index, error_msg):
    timestamp = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    sheet.update_cell(
        row_index,
        COLUMNS["notes"],
        f"Export FAILED at {timestamp}\nError: {error_msg}",
    )


def run_publisher():
    print("\n" + "="*50)
    print("  Bubba Academy Publisher Agent")
    print("="*50)

    os.makedirs(EXPORTS_DIR, exist_ok=True)

    print("\n[1/3] Connecting to Google Sheet...")
    try:
        sheet = get_sheet()
        print("      Connected successfully.")
    except Exception as e:
        print(f"      ERROR: {e}")
        return

    print(f"\n[2/3] Scanning for rows where Status = '{STATUS_DRAFT_READY}' and Approval = '{APPROVAL_TRIGGER}'...")
    try:
        approved = get_approved_rows(sheet)
    except Exception as e:
        print(f"      ERROR reading rows: {e}")
        return

    if not approved:
        print(f"      No approved rows found. Nothing to export.")
        return {"approved_found": 0, "exported": 0, "failed": 0}

    print(f"      Found {len(approved)} row(s) to export.")

    print("\n[3/3] Running export pipeline...\n")
    success = 0
    failed  = 0

    for item in approved:
        row_index = item["row_index"]
        row_data  = item["row_data"]
        title     = row_data.get("Content Title", "Untitled")
        keyword   = row_data.get("Main Keyword", "")

        log.info(f"  Processing: \"{title}\" (keyword: {keyword})")
        print(f"  Exporting: \"{title}\"")

        content = read_content_from_row(row_data)

        all_results = []
        row_failed  = False

        for exporter in EXPORTERS:
            print(f"  -> Running {exporter.name()}...")
            try:
                result = exporter.export(row_data, content)
                all_results.append(result)
                if result.get("success"):
                    print(f"     OK: {result.get('message', '')}")
                    # ── Structured metrics log (after HubSpotExporter builds postBody) ──
                    if "HubSpotExporter" in exporter.name():
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
                    if "HubSpotAPIExporter" in exporter.name():
                        mode = result.get("mode", "")
                        if mode == "live" and result.get("success"):
                            log.info(f"  [HUBSPOT] draft created — post_id={result.get('post_id')}")
                            log.info(f"  [HUBSPOT] draft_url={result.get('draft_url')}")
                        elif mode == "safe":
                            log.info("  [HUBSPOT] safe mode — no API call made")
                        elif mode == "mock":
                            log.info("  [HUBSPOT] mock mode — payload logged, no API call")
                else:
                    log.warning(f"  [FAIL] {exporter.name()}: {result.get('message', '')}")
                    print(f"     FAILED: {result.get('message', '')}")
                    row_failed = True
            except Exception as e:
                msg = f"{exporter.name()} raised exception: {e}"
                log.error(f"  [ERROR] {msg}")
                print(f"     ERROR: {msg}")
                all_results.append({"success": False, "message": msg})
                row_failed = True

        # ── Validation report (from HubSpotAPIExporter if it ran) ─────────────
        for r in all_results:
            if r.get("validation"):
                vr = r["validation"].get("report", {})
                log.info(f"  [VALIDATE] cta_blocks={vr.get('cta_count')} "
                         f"cluster_links={vr.get('cluster_links_to_posts')} "
                         f"meta_title={vr.get('meta_title_present')} "
                         f"meta_desc={vr.get('meta_description_present')} "
                         f"slug={vr.get('slug_present')}")
                if r["validation"].get("errors"):
                    log.error(f"  [VALIDATE] BLOCKED: {r['validation']['errors']}")

        if not row_failed:
            write_export_results(sheet, row_index, all_results)
            update_status(sheet, row_index, STATUS_EXPORTED)
            print(f"  Status updated to '{STATUS_EXPORTED}'.")
            print(f"  Done.\n")
            success += 1
        else:
            write_failure_note(sheet, row_index, "One or more exporters failed — see logs above.")
            print(f"  Partial failure logged to Notes column.\n")
            failed += 1

    print("="*50)
    print(f"  Export complete. Success: {success} | Failed: {failed}")
    print("="*50 + "\n")
    return {"approved_found": len(approved), "exported": success, "failed": failed}


if __name__ == "__main__":
    run_publisher()
