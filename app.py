"""
app.py — Bubba Academy Content Agent: Cloud Entrypoint
=======================================================

Modes
-----
  gunicorn app:flask_app           → Render Web Service (health endpoint, always-on)
  python app.py --run-once         → Full pipeline run (generation + publishing)
  python app.py --run-once --dry-run  → Build + validate without live publish

Environment variables (all required in production)
---------------------------------------------------
  ANTHROPIC_API_KEY          Anthropic Claude API key
  HUBSPOT_TOKEN              HubSpot Private App token
  GOOGLE_CREDENTIALS_JSON    Full contents of credentials.json (service account)
  GOOGLE_SHEET_ID            Google Sheet ID (optional — default baked into config)
  DRY_RUN                    "true" to skip live HubSpot publish (default: "false")

See .env.example for the full list.
"""

import os
import sys
import logging
import argparse
import datetime
from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv(override=True)

# ── Logging setup ─────────────────────────────────────────────────────────────
# Logs to stdout (Render captures it) + daily file in logs/

LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

_today    = datetime.datetime.utcnow().strftime("%Y-%m-%d")
_log_file = os.path.join(LOGS_DIR, f"{_today}.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("app")


# ── Flask app (Render Web Service / health endpoint) ──────────────────────────

flask_app = Flask(__name__)


@flask_app.route("/health")
def health():
    """Render health check — returns 200 when service is up."""
    return jsonify({
        "status":  "ok",
        "service": "bubba-content-agent",
        "version": "2.0",
    })


@flask_app.route("/status")
def status():
    """
    Live sheet status — returns row counts by status column.
    Useful for a quick dashboard check without opening Google Sheets.
    """
    try:
        from sheets_client import get_sheet
        sheet = get_sheet()
        rows  = sheet.get_all_records()
        counts: dict = {}
        for r in rows:
            s = str(r.get("Status", "")).strip() or "Empty"
            counts[s] = counts.get(s, 0) + 1
        return jsonify({"status": "ok", "row_counts": counts, "total": len(rows)})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ── Startup validation ────────────────────────────────────────────────────────

def _check_env():
    """
    Warn loudly if required secrets are missing.
    Does not abort — lets the pipeline fail with a clear error at runtime.
    """
    required = {
        "ANTHROPIC_API_KEY":       "Anthropic Claude API key",
        "HUBSPOT_TOKEN":           "HubSpot Private App token",
        "GOOGLE_CREDENTIALS_JSON": "Google service-account JSON (or ensure credentials.json exists locally)",
    }
    missing = [
        f"{var} ({desc})"
        for var, desc in required.items()
        if not os.environ.get(var, "").strip()
    ]
    if missing:
        for m in missing:
            log.warning(f"  ENV NOT SET: {m}")
        # Fall back to local credentials.json if present
        if not os.path.exists("credentials.json") and "GOOGLE_CREDENTIALS_JSON" in str(missing):
            log.error("  No Google credentials available — sheet access will fail.")
    else:
        log.info("  All required environment variables present.")


# ── Idea refill runner ────────────────────────────────────────────────────────

def run_refill(dry_run: bool = False) -> dict:
    """
    --refill-ideas mode: keep the content queue stocked.

    Runs at 6 AM PT / 0 13 * * * UTC (PDT).
    # DST: PDT=UTC-7 → 13:00UTC=6AM; PST=UTC-8 → update to 0 14 * * *
    # (and shift publishing cron to 0 15 * * * when PST is in effect)

    Checks active_queue_count (rows with Status = 'Idea' or 'Draft Ready').
    If count < IDEA_REFILL_THRESHOLD (default 3):
        → generate IDEA_BATCH_SIZE (default 5) new ideas
        → log [IDEA_REFILL_CREATED]
    If count >= threshold:
        → do nothing
        → log [IDEA_REFILL_SKIPPED]

    This runs one hour BEFORE publishing so the 7 AM --run-once always
    has ideas ready for content generation and never skips a publish day.
    """
    from idea_generator import _REFILL_THRESHOLD, _BATCH_SIZE

    run_start = datetime.datetime.utcnow()
    log.info(
        f"[IDEA_REFILL_START] date={_today}  utc={run_start.strftime('%H:%M:%S')}  "
        f"threshold={_REFILL_THRESHOLD}  batch_size={_BATCH_SIZE}  dry_run={dry_run}  "
        f"# DST note: cron=0 13 * * * UTC is correct for PDT (UTC-7); "
        f"update to 0 14 * * * when PST (UTC-8) is in effect"
    )
    log.info("=" * 56)
    log.info("  Bubba Content Agent — idea refill job")
    log.info(f"  Date:     {_today}  |  UTC: {run_start.strftime('%H:%M:%S')}")
    log.info("=" * 56)

    _check_env()

    result: dict = {}
    try:
        from sheets_client import get_sheet as _get_sheet
        from idea_generator import count_active_queue, refill_if_needed

        _sheet = _get_sheet()
        active = count_active_queue(_sheet)

        log.info(
            f"[IDEA_QUEUE_STATUS] active_queue_count={active}  "
            f"threshold={_REFILL_THRESHOLD}"
        )

        if dry_run:
            if active < _REFILL_THRESHOLD:
                log.info(
                    f"[IDEA_REFILL_CREATED] DRY_RUN — would generate {_BATCH_SIZE} ideas  "
                    f"active_queue_count={active}"
                )
            else:
                log.info(
                    f"[IDEA_REFILL_SKIPPED] DRY_RUN — queue healthy  "
                    f"active_queue_count={active}  threshold={_REFILL_THRESHOLD}"
                )
            result = {"dry_run": True, "active_queue_count": active}
        else:
            result = refill_if_needed(_sheet, batch_size=_BATCH_SIZE)

    except Exception as exc:
        log.error(f"  Idea refill failed: {exc}")
        result = {"ideas_written": 0, "error": str(exc)}

    elapsed = (datetime.datetime.utcnow() - run_start).total_seconds()
    success = not result.get("error")
    log.info("=" * 56)
    log.info(
        f"[IDEA_REFILL_END] success={success}  "
        f"elapsed_s={elapsed:.1f}  "
        f"ideas_written={result.get('ideas_written', 0)}  "
        f"skipped={result.get('skipped', False)}"
    )
    return result


# ── Pipeline runner ───────────────────────────────────────────────────────────

def run_pipeline(dry_run: bool = False):
    """
    Execute the full content pipeline:
      Step 1 — Content Generator: Idea → Draft Ready
      Step 2 — Publisher:         Draft Ready + Approval=Yes → Exported

    dry_run=True  → builds + validates, skips live HubSpot API publish.
    dry_run=False → full live publish (default).
    """
    if dry_run:
        os.environ["DRY_RUN"] = "true"
        log.info("DRY_RUN mode enabled — no live publish will occur.")
    else:
        os.environ.setdefault("DRY_RUN", "false")

    run_start = datetime.datetime.utcnow()
    log.info(
        f"[CRON_START] date={_today}  utc={run_start.strftime('%H:%M:%S')}  "
        f"dry_run={dry_run}  "
        f"schedule='0 14 * * * UTC (7:00 AM PDT)'  "
        f"# DST: PDT=UTC-7 → 14:00UTC=7AM; PST=UTC-8 → update to 0 15 * * *"
    )
    log.info("=" * 56)
    log.info("  Bubba Content Agent — pipeline started")
    log.info(f"  Date:     {_today}  |  UTC: {run_start.strftime('%H:%M:%S')}")
    log.info(f"  Dry-run:  {dry_run}")
    log.info("=" * 56)

    _check_env()

    gen_stats  = {}
    pub_stats  = {}
    idea_stats = {}

    # ── Step 1: Content Generation ────────────────────────────────────────────
    # Processes any rows with Status = "Idea" → generates blog content → "Draft Ready"
    log.info("")
    log.info("── STEP 1: Content Generation ──────────────────────────")
    try:
        from main import run_agent
        gen_stats = run_agent() or {}
        log.info(f"  Generation: {gen_stats}")
    except Exception as e:
        log.error(f"  Content generation failed: {e}")
        gen_stats = {"error": str(e)}

    # ── Step 1b: Emergency fallback ──────────────────────────────────────────
    # Normal queue refill happens at 6 AM via:  python app.py --refill-ideas
    # This block only fires when the 7 AM run lands on a completely empty queue
    # (no "Idea" or "Draft Ready" rows at all), which means the 6 AM cron failed.
    if gen_stats.get("ideas_found", 0) == 0 and not gen_stats.get("error"):
        log.info("")
        log.info("── STEP 1b: Emergency Queue Check ─────────────────────")
        try:
            from sheets_client import get_sheet as _get_sheet
            from idea_generator import has_active_work, generate_ideas, _require_approval

            _sheet = _get_sheet()

            if has_active_work(_sheet):
                # "Draft Ready" rows exist but none approved yet — just wait
                log.info(
                    "[WAITING_FOR_APPROVAL] Draft Ready rows exist but Approval='Yes' "
                    "not set — set it in Google Sheets column M to publish"
                )
            else:
                # Queue is completely empty — 6 AM refill must have failed
                log.warning(
                    "[IDEA_QUEUE_EMPTY] No active work found at publish time. "
                    "Emergency refill triggered. Check that --refill-ideas 6 AM "
                    "cron (0 13 * * * UTC) is running correctly on Render."
                )
                idea_stats = generate_ideas(_sheet)

                # If no-approval mode, process the new ideas immediately
                if idea_stats.get("ideas_written", 0) > 0 and not _require_approval():
                    log.info(
                        "  REQUIRE_APPROVAL=false — running second content-generation "
                        "pass to process emergency-generated ideas in this cycle"
                    )
                    try:
                        gen_stats2 = run_agent() or {}
                        gen_stats["ideas_found"]    = (gen_stats.get("ideas_found", 0)
                                                       + gen_stats2.get("ideas_found", 0))
                        gen_stats["drafts_created"] = (gen_stats.get("drafts_created", 0)
                                                       + gen_stats2.get("drafts_created", 0))
                        log.info(f"  Emergency second-pass generation: {gen_stats2}")
                    except Exception as e2:
                        log.error(f"  Emergency second-pass content generation failed: {e2}")

        except Exception as e:
            log.error(f"  Emergency queue check/generation failed: {e}")

    # ── Step 2: Publishing ────────────────────────────────────────────────────
    # Processes first row with Status = "Draft Ready" + Approval = "Yes" → Exported
    log.info("")
    log.info("── STEP 2: Publishing ──────────────────────────────────")
    try:
        from publisher import run_publisher
        pub_stats = run_publisher() or {}
        log.info(f"  Publisher:  {pub_stats}")
    except Exception as e:
        log.error(f"  Publisher failed: {e}")
        pub_stats = {"error": str(e)}

    # Log a clear waiting message when nothing was approved
    if pub_stats.get("approved_found", 0) == 0 and not pub_stats.get("error"):
        if idea_stats.get("ideas_written", 0) > 0:
            log.info(
                "[NO_APPROVED_ROWS_FOUND] New ideas were just created — "
                "content generation will run on next scheduled run"
            )
        log.info(
            "[WAITING_FOR_APPROVAL] No posts published this run. "
            "To publish: set Approval='Yes' in Google Sheets column M "
            "for any row with Status='Draft Ready'."
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (datetime.datetime.utcnow() - run_start).total_seconds()
    log.info("")
    log.info("=" * 56)
    log.info("  Pipeline complete")
    log.info(f"  Elapsed:       {elapsed:.1f}s")
    log.info(f"  Ideas queued:  generated={idea_stats.get('ideas_generated', 0)}  "
             f"written={idea_stats.get('ideas_written', 0)}")
    log.info(f"  Generation:    ideas_processed={gen_stats.get('ideas_found', 0)}  "
             f"drafts={gen_stats.get('drafts_created', 0)}  "
             f"failed={gen_stats.get('failed', 0)}")
    log.info(f"  Publishing:    approved={pub_stats.get('approved_found', 0)}  "
             f"exported={pub_stats.get('exported', 0)}  "
             f"failed={pub_stats.get('failed', 0)}")
    has_errors = bool(gen_stats.get("error") or pub_stats.get("error"))
    if has_errors:
        log.error("  ERRORS DETECTED — review logs above.")
    else:
        log.info("  All steps completed without errors.")
    log.info("=" * 56)
    log.info(
        f"[CRON_END] success={not has_errors}  elapsed_s={elapsed:.1f}  "
        f"ideas_auto_generated={idea_stats.get('ideas_written', 0)}  "
        f"ideas_processed={gen_stats.get('ideas_found', 0)}  "
        f"drafts_created={gen_stats.get('drafts_created', 0)}  "
        f"posts_exported={pub_stats.get('exported', 0)}  "
        f"posts_failed={pub_stats.get('failed', 0)}"
    )

    return {
        "ideas":       idea_stats,
        "generation":  gen_stats,
        "publishing":  pub_stats,
        "elapsed_s":   elapsed,
    }


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bubba Academy Content Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Render cron jobs (two separate jobs required):

  Idea refill  — 6 AM PT / 0 13 * * * UTC (PDT)
    python app.py --refill-ideas
    Checks queue depth; generates 5 ideas if < 3 active rows.

  Daily publish — 7 AM PT / 0 14 * * * UTC (PDT)
    python app.py --run-once
    Generates content from Idea rows, publishes one approved post.

DST reminder: PDT (UTC-7) Mar–Nov; PST (UTC-8) Nov–Mar.
Update cron expressions when DST changes:
  PST:  refill  → 0 14 * * *    publish → 0 15 * * *

Examples:
  python app.py --refill-ideas            # queue check + refill
  python app.py --refill-ideas --dry-run  # preview without writing
  python app.py --run-once                # full live pipeline
  python app.py --run-once --dry-run      # build + validate, skip publish
        """,
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run the full content pipeline (generate + publish) and exit.",
    )
    parser.add_argument(
        "--refill-ideas",
        action="store_true",
        help=(
            "Check queue depth and generate ideas if below threshold. "
            "Run at 6 AM PT / 0 13 * * * UTC (PDT). "
            "Does NOT publish anything."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without writing to HubSpot or Google Sheets.",
    )
    args = parser.parse_args()

    if args.refill_ideas:
        result = run_refill(dry_run=args.dry_run)
        sys.exit(0 if not result.get("error") else 1)
    elif args.run_once:
        result = run_pipeline(dry_run=args.dry_run)
        sys.exit(0 if not result["generation"].get("error") and
                      not result["publishing"].get("error") else 1)
    else:
        # Start Flask dev server (use gunicorn in production)
        port = int(os.environ.get("PORT", 10000))
        log.info(f"Starting Flask server on port {port}  (use gunicorn in production)")
        flask_app.run(host="0.0.0.0", port=port, debug=False)
