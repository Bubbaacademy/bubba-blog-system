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
    log.info("=" * 56)
    log.info("  Bubba Content Agent — pipeline started")
    log.info(f"  Date:     {_today}  |  UTC: {run_start.strftime('%H:%M:%S')}")
    log.info(f"  Dry-run:  {dry_run}")
    log.info("=" * 56)

    _check_env()

    gen_stats = pub_stats = {}

    # ── Step 1: Content Generation ────────────────────────────────────────────
    log.info("")
    log.info("── STEP 1: Content Generation ──────────────────────────")
    try:
        from main import run_agent
        gen_stats = run_agent() or {}
        log.info(f"  Generation: {gen_stats}")
    except Exception as e:
        log.error(f"  Content generation failed: {e}")
        gen_stats = {"error": str(e)}

    # ── Step 2: Publishing ────────────────────────────────────────────────────
    log.info("")
    log.info("── STEP 2: Publishing ──────────────────────────────────")
    try:
        from publisher import run_publisher
        pub_stats = run_publisher() or {}
        log.info(f"  Publisher:  {pub_stats}")
    except Exception as e:
        log.error(f"  Publisher failed: {e}")
        pub_stats = {"error": str(e)}

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (datetime.datetime.utcnow() - run_start).total_seconds()
    log.info("")
    log.info("=" * 56)
    log.info("  Pipeline complete")
    log.info(f"  Elapsed:     {elapsed:.1f}s")
    log.info(f"  Generation:  ideas={gen_stats.get('ideas_found', 0)}  "
             f"drafts={gen_stats.get('drafts_created', 0)}  "
             f"failed={gen_stats.get('failed', 0)}")
    log.info(f"  Publishing:  approved={pub_stats.get('approved_found', 0)}  "
             f"exported={pub_stats.get('exported', 0)}  "
             f"failed={pub_stats.get('failed', 0)}")
    if gen_stats.get("error") or pub_stats.get("error"):
        log.error("  ERRORS DETECTED — review logs above.")
    else:
        log.info("  All steps completed without errors.")
    log.info("=" * 56)

    return {"generation": gen_stats, "publishing": pub_stats, "elapsed_s": elapsed}


# ── CLI entrypoint ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bubba Academy Content Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app.py --run-once            # full live pipeline
  python app.py --run-once --dry-run  # build + validate, skip publish
        """,
    )
    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run the full content pipeline once and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and validate without publishing to HubSpot.",
    )
    args = parser.parse_args()

    if args.run_once:
        result = run_pipeline(dry_run=args.dry_run)
        sys.exit(0 if not result["generation"].get("error") and
                      not result["publishing"].get("error") else 1)
    else:
        # Start Flask dev server (use gunicorn in production)
        port = int(os.environ.get("PORT", 10000))
        log.info(f"Starting Flask server on port {port}  (use gunicorn in production)")
        flask_app.run(host="0.0.0.0", port=port, debug=False)
