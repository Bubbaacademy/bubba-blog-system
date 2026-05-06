"""
idea_generator.py — Auto-fill the content queue when it is empty.

WHY THIS EXISTS
---------------
The pipeline needs "Idea" rows in Google Sheets to generate content.
Without this module, the user must manually create those rows.
This module removes that requirement entirely.

HOW IT WORKS
------------
1. Called by run_pipeline() in app.py when run_agent() found zero "Idea" rows.
2. Checks whether any active work exists ("Idea" or "Draft Ready" rows):
   - If yes → queue is not empty, just waiting for approval → log and exit.
   - If no  → generate IDEA_BATCH_SIZE new ideas via Claude.
3. Writes each idea as a new Google Sheet row with Status = "Idea".
4. If REQUIRE_APPROVAL=false → also sets Approval = "Yes" immediately,
   so run_agent() + run_publisher() can complete the full publish in one run.
5. If REQUIRE_APPROVAL=true (default) → leaves Approval blank;
   user sets "Yes" manually to trigger publishing.

ENVIRONMENT VARIABLES
---------------------
REQUIRE_APPROVAL     "true" (default) | "false"
                     false → auto-approves ideas, one post can publish per run
                     true  → user must set Approval="Yes" in the sheet
IDEA_BATCH_SIZE      How many ideas to generate per queue-fill (default: 5)
IDEA_GENERATOR_MODEL Claude model for idea generation (default: claude-haiku-4-5)
ANTHROPIC_API_KEY    Required (same key used by content_generator.py)

STATUS FLOW (approval required)
---------------------------------
  [auto]         [main.py]          [manual]       [publisher.py]
  Status=Idea  →  Draft Ready  →  Approval=Yes  →  Exported

STATUS FLOW (no approval required, REQUIRE_APPROVAL=false)
-----------------------------------------------------------
  [auto]         [main.py]          [auto]         [publisher.py]
  Status=Idea  →  Draft Ready  →  Approval=Yes  →  Exported
  (Approval=Yes written at idea creation time; all steps run in one daily run)
"""
from __future__ import annotations

import os
import json
import logging
import datetime
import anthropic
from config import COLUMNS, STATUS_TRIGGER, STATUS_DRAFT_READY, APPROVAL_TRIGGER, BRAND

log = logging.getLogger("idea_generator")

# ── Configuration ─────────────────────────────────────────────────────────────

_IDEA_MODEL  = os.environ.get("IDEA_GENERATOR_MODEL", "claude-haiku-4-5")
_BATCH_SIZE  = int(os.environ.get("IDEA_BATCH_SIZE", "5"))

def _require_approval() -> bool:
    """Read at call-time so runtime env changes are respected."""
    return os.environ.get("REQUIRE_APPROVAL", "true").lower() != "false"

# ── Content pillars for Bubba Academy ─────────────────────────────────────────
# Idea generation is seeded from these; Claude produces fresh titles + keywords.

CONTENT_PILLARS = """
Pillar 1 — Amazon FBA Fees
  Topics: fulfillment fees, referral fees, FBA fee calculator, per-unit cost breakdown,
          fee changes, comparing FBA vs FBM costs

Pillar 2 — FBA Inbound Shipping
  Topics: creating inbound shipments, SPD vs LTL, carrier selection, shipping cost
          optimization, FBA shipment rejected errors, prep requirements

Pillar 3 — Amazon Storage & Inventory
  Topics: monthly storage fees, aged inventory surcharge, IPI score, FIFO inventory,
          removal orders, reorder point calculation, avoid long-term storage fees

Pillar 4 — Amazon Product Research
  Topics: finding winning products, demand validation, competition analysis,
          private label product criteria, product research tools, niche selection

Pillar 5 — Amazon PPC & Advertising
  Topics: sponsored products setup, ACOS vs TACOS, keyword bidding strategy,
          campaign structure, negative keywords, dayparting, PPC optimization

Pillar 6 — Amazon Listing Optimization
  Topics: product title formula, bullet points best practices, A+ content,
          backend keywords, main image rules, listing quality score

Pillar 7 — Supplier Sourcing
  Topics: Alibaba sourcing guide, verifying suppliers, MOQ negotiation,
          sample ordering, private label vs wholesale, quality inspection

Pillar 8 — Amazon Seller Account
  Topics: Seller Central account setup, health metrics, account suspension recovery,
          intellectual property complaints, FBA vs FBM selection, Buy Box

Pillar 9 — E-commerce Business Strategy
  Topics: scaling from 1 to 10 SKUs, brand building on Amazon, profit margin goals,
          reinvesting revenue, Amazon brand registry, multi-channel selling
"""

_IDEA_PROMPT = """\
You are a content strategist for {brand_name} — an online education company teaching
{brand_focus} to beginners and intermediate online sellers.

Generate exactly {count} unique blog post ideas. Requirements:
- Each idea targets a different pillar (spread them out, no two from the same pillar)
- Practical, searchable, beginner-to-intermediate level
- No duplicate topics or near-duplicates
- Titles must be compelling and clearly benefit-driven

Content pillars to draw from:
{pillars}

Return ONLY a valid JSON array. No markdown fences, no commentary. Schema:
[
  {{
    "content_title": "Full blog post title — clear, benefit-driven, SEO-friendly",
    "main_keyword": "primary keyword phrase, 3-6 words, how a real person would search",
    "topic_cluster": "short pillar name (e.g. Amazon FBA Fees, FBA Shipping, Product Research)",
    "audience_level": "Beginner or Intermediate",
    "content_type": "Blog Post"
  }}
]

Today's date: {today}. Avoid seasonal or time-sensitive topics. Every title must be \
evergreen and usable on a future publish date.\
"""


# ── Sheet helpers ─────────────────────────────────────────────────────────────

_ACTIVE_STATUSES = {STATUS_TRIGGER.lower(), STATUS_DRAFT_READY.lower()}


def has_active_work(sheet) -> bool:
    """
    Return True if the sheet has any rows where Status is 'Idea' or 'Draft Ready'.

    Used to avoid over-filling the queue: if work is already in progress
    (waiting for content generation or waiting for approval) we do not
    generate more ideas.
    """
    try:
        rows = sheet.get_all_records()
        return any(
            str(row.get("Status", "")).strip().lower() in _ACTIVE_STATUSES
            for row in rows
        )
    except Exception as exc:
        log.warning(f"[IDEA_GENERATOR] Could not read sheet for queue check: {exc}")
        return False  # treat as empty so we attempt generation


# ── Claude client ─────────────────────────────────────────────────────────────

def _get_client() -> "anthropic.Anthropic | None":
    token = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not token:
        log.error(
            "[IDEA_GENERATION_FAILED] ANTHROPIC_API_KEY not set — "
            "cannot generate ideas. Set it in Render environment variables."
        )
        return None
    return anthropic.Anthropic(api_key=token)


# ── Main public function ──────────────────────────────────────────────────────

def generate_ideas(sheet, count: int = _BATCH_SIZE) -> dict:
    """
    Generate `count` fresh content idea rows and append them to the sheet.

    Parameters
    ----------
    sheet  : gspread worksheet object (already authenticated)
    count  : number of ideas to generate (default: IDEA_BATCH_SIZE env var or 5)

    Returns
    -------
    dict with keys:
        ideas_generated  : number Claude returned
        ideas_written    : number successfully written to sheet
        error            : error string if something failed, else None
    """
    require_approval = _require_approval()

    log.info(
        f"[IDEA_GENERATION_START] count={count}  "
        f"model={_IDEA_MODEL}  "
        f"require_approval={require_approval}  "
        f"auto_approval={'Yes' if not require_approval else 'manual'}"
    )

    client = _get_client()
    if client is None:
        return {"ideas_generated": 0, "ideas_written": 0, "error": "ANTHROPIC_API_KEY not set"}

    today = datetime.date.today().isoformat()
    prompt = _IDEA_PROMPT.format(
        brand_name = BRAND["name"],
        brand_focus = BRAND["focus"],
        count      = count,
        pillars    = CONTENT_PILLARS.strip(),
        today      = today,
    )

    # ── Call Claude ───────────────────────────────────────────────────────────
    try:
        resp = client.messages.create(
            model     = _IDEA_MODEL,
            max_tokens = 1200,
            messages  = [{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()

        # Strip markdown code fences if Claude wrapped the response
        if raw.startswith("```"):
            parts = raw.split("```")
            raw   = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        ideas = json.loads(raw)
        if not isinstance(ideas, list):
            raise ValueError(f"Expected JSON array, got {type(ideas).__name__}")

    except json.JSONDecodeError as exc:
        log.error(f"[IDEA_GENERATION_FAILED] Claude returned invalid JSON: {exc}  raw={raw[:200]}")
        return {"ideas_generated": 0, "ideas_written": 0, "error": f"JSON parse error: {exc}"}
    except Exception as exc:
        log.error(f"[IDEA_GENERATION_FAILED] Claude call failed: {exc}")
        return {"ideas_generated": 0, "ideas_written": 0, "error": str(exc)}

    # ── Write ideas to sheet ──────────────────────────────────────────────────
    written = 0
    ts      = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    num_cols = max(COLUMNS.values())  # 17 columns total

    for i, idea in enumerate(ideas[:count]):
        try:
            title    = str(idea.get("content_title", "")).strip()
            keyword  = str(idea.get("main_keyword", "")).strip()
            cluster  = str(idea.get("topic_cluster", "")).strip()
            audience = str(idea.get("audience_level", "Beginner")).strip()
            ctype    = str(idea.get("content_type", "Blog Post")).strip()

            if not title or not keyword:
                log.warning(
                    f"[IDEA_CREATED] Skipping idea {i + 1}/{len(ideas)} — "
                    f"missing title or keyword  raw={idea}"
                )
                continue

            content_id     = f"IDEA-{ts}-{i + 1:02d}"
            approval_value = APPROVAL_TRIGGER if not require_approval else ""

            # Build a full-width row aligned to COLUMNS (1-based indexing)
            new_row = [""] * num_cols
            new_row[COLUMNS["content_id"]    - 1] = content_id
            new_row[COLUMNS["topic_cluster"] - 1] = cluster
            new_row[COLUMNS["main_keyword"]  - 1] = keyword
            new_row[COLUMNS["content_title"] - 1] = title
            new_row[COLUMNS["audience_level"]- 1] = audience
            new_row[COLUMNS["content_type"]  - 1] = ctype
            new_row[COLUMNS["status"]        - 1] = STATUS_TRIGGER   # "Idea"
            new_row[COLUMNS["approval"]      - 1] = approval_value

            sheet.append_row(new_row, value_input_option="RAW")
            written += 1

            log.info(
                f"[IDEA_CREATED] id={content_id}  "
                f"title={title!r}  "
                f"keyword={keyword!r}  "
                f"cluster={cluster!r}  "
                f"audience={audience}  "
                f"approval={approval_value!r}"
            )

        except Exception as exc:
            log.warning(f"[IDEA_CREATED] Failed writing idea {i + 1}: {exc}")

    # ── Summary log ───────────────────────────────────────────────────────────
    if written > 0:
        if not require_approval:
            approval_note = (
                "Approval='Yes' pre-set (REQUIRE_APPROVAL=false) — "
                "content generation + publish will run in same pipeline cycle"
            )
        else:
            approval_note = (
                "Approval column is blank — "
                "set Approval='Yes' in Google Sheets column M to trigger publishing"
            )
        log.info(
            f"[IDEA_QUEUE_FILLED] ideas_written={written}  "
            f"status='{STATUS_TRIGGER}'  "
            f"{approval_note}"
        )
    else:
        log.warning(
            "[IDEA_GENERATION_FAILED] Zero ideas written to sheet — "
            "check ANTHROPIC_API_KEY and sheet write permissions"
        )

    return {
        "ideas_generated": len(ideas),
        "ideas_written":   written,
        "error":           None,
    }
