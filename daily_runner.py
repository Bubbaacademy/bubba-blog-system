"""
Bubba Academy Daily Runner
--------------------------
Orchestrates the full content pipeline in safe order:
  Step 1 — Content Generator (main.py): Idea → Draft Ready
  Step 2 — Publisher (publisher.py):    Draft Ready + Approval=Yes → Exported

Does NOT auto-approve anything.
Safe to run every day — skips rows that are already Exported.

Future agents to wire in here:
  Step 3 — Video Agent: Draft Ready video_script → Scene structure + voiceover + visual prompts
  Step 4 — Distribution Agent: Exported → push to CMS / social / email
"""

import os
import sys
import logging
import datetime
from dotenv import load_dotenv

load_dotenv(override=True)

# ── Logging setup ─────────────────────────────────────────────────────────────

LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

_today     = datetime.datetime.now().strftime("%Y-%m-%d")
_log_file  = os.path.join(LOGS_DIR, f"{_today}.log")
_timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("daily_runner")


# ── Sheet snapshot (for summary stats) ────────────────────────────────────────

def _snapshot(sheet):
    """Count rows by status — used to diff before/after each step."""
    rows = sheet.get_all_records()
    counts = {}
    video_ready = 0
    for row in rows:
        status = str(row.get("Status", "")).strip()
        counts[status] = counts.get(status, 0) + 1
        if row.get("Video Script", "").strip():
            video_ready += 1
    return counts, video_ready


def _safe_run(label, fn):
    """Call fn(), return its stats dict. Never raises — logs exceptions."""
    try:
        result = fn()
        return result or {}
    except Exception as e:
        log.error(f"{label} raised an unexpected error: {e}")
        return {"error": str(e)}


# ── Summary printer ────────────────────────────────────────────────────────────

def _print_summary(run_start, gen_stats, pub_stats, pre_counts, post_counts, video_ready):
    duration = (datetime.datetime.now() - run_start).seconds
    divider  = "=" * 54

    lines = [
        "",
        divider,
        "  BUBBA ACADEMY — DAILY RUN SUMMARY",
        f"  {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  |  Duration: {duration}s",
        divider,
        "",
        "  CONTENT GENERATION (main.py)",
        f"    Ideas found       : {gen_stats.get('ideas_found', 0)}",
        f"    Drafts created    : {gen_stats.get('drafts_created', 0)}",
        f"    Failures          : {gen_stats.get('failed', 0)}",
        "",
        "  PUBLISHING (publisher.py)",
        f"    Approved rows     : {pub_stats.get('approved_found', 0)}",
        f"    Exported          : {pub_stats.get('exported', 0)}",
        f"    Failures          : {pub_stats.get('failed', 0)}",
        "",
        "  PIPELINE STATUS (after this run)",
    ]

    all_statuses = sorted(set(list(pre_counts.keys()) + list(post_counts.keys())))
    for status in all_statuses:
        if status:
            lines.append(f"    {status:<20}: {post_counts.get(status, 0)} row(s)")

    lines += [
        "",
        "  VIDEO PIPELINE",
        f"    Scripts ready     : {video_ready} row(s) with Video Script populated",
        f"    Video Agent       : Not yet active — scripts queued for future use",
        "",
    ]

    if gen_stats.get("error") or pub_stats.get("error"):
        lines.append("  ERRORS DETECTED — check log file for details.")
    else:
        lines.append("  All steps completed without critical errors.")

    lines += [
        divider,
        f"  Log saved to: {_log_file}",
        divider,
        "",
    ]

    output = "\n".join(lines)
    print(output)
    # Also write summary to log file
    with open(_log_file, "a", encoding="utf-8") as f:
        f.write("\n" + output)


# ── Main runner ────────────────────────────────────────────────────────────────

def run_daily():
    run_start = datetime.datetime.now()

    log.info("=" * 54)
    log.info("  Bubba Academy Daily Runner starting")
    log.info(f"  Date: {_today}")
    log.info("=" * 54)

    # Connect to sheet for snapshots
    log.info("Connecting to Google Sheet for pre-run snapshot...")
    try:
        from sheets_client import get_sheet
        sheet = get_sheet()
        pre_counts, _ = _snapshot(sheet)
        log.info(f"Pre-run row counts: {pre_counts}")
    except Exception as e:
        log.error(f"Could not connect to sheet for snapshot: {e}")
        pre_counts = {}
        sheet = None

    # ── Step 1: Content Generation ────────────────────────────────────────────
    log.info("")
    log.info("── STEP 1: Content Generation ──")
    from main import run_agent
    gen_stats = _safe_run("Content Generator", run_agent)
    log.info(f"Generation stats: {gen_stats}")

    # ── Step 2: Publishing ────────────────────────────────────────────────────
    log.info("")
    log.info("── STEP 2: Publisher ──")
    from publisher import run_publisher
    pub_stats = _safe_run("Publisher", run_publisher)
    log.info(f"Publisher stats: {pub_stats}")

    # ── Step 3 placeholder: Video Agent ───────────────────────────────────────
    # When ready, wire in video_agent.run_video_agent() here.
    # It will read rows where video_script is populated and Status = "Draft Ready"
    # and generate: scene structure, voiceover, visual prompts, platform formats.
    log.info("")
    log.info("── STEP 3: Video Agent — not yet active ──")

    # Post-run snapshot
    if sheet:
        try:
            post_counts, video_ready = _snapshot(sheet)
            log.info(f"Post-run row counts: {post_counts}")
        except Exception:
            post_counts = {}
            video_ready = 0
    else:
        post_counts = {}
        video_ready = 0

    # Summary
    _print_summary(run_start, gen_stats, pub_stats, pre_counts, post_counts, video_ready)


if __name__ == "__main__":
    run_daily()
