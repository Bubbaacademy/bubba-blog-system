"""
tests/test_idea_generator.py — Self-feeding pipeline verification.

Run from project root:
    python3 tests/test_idea_generator.py

Tests cover:
  A. Module structure and constants (tests 1–3)
  B. has_active_work() sheet detection (tests 4–7)
  C. generate_ideas() sheet-write logic — mocked Claude + mocked sheet (tests 8–12)
  D. REQUIRE_APPROVAL flag behaviour (tests 13–14)
  E. app.py pipeline flow flags (tests 15–16)

No real Anthropic API calls, no real Google Sheets calls.
"""
from __future__ import annotations

import sys
import os
import json
import logging
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
for noisy in ("urllib3", "google", "gspread", "oauth2client", "anthropic"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

# ── helpers ───────────────────────────────────────────────────────────────────

PASS = "✓ PASS"
FAIL = "✗ FAIL"
_results: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    _results.append((label, condition, detail))
    tag = "  OK  " if condition else " FAIL "
    print(f"[{tag}] {label}")
    if detail:
        print(f"         {detail}")
    if not condition:
        print(f"         ^^^ FAILED ^^^")


def section(title: str) -> None:
    print(f"\n{'─' * 70}")
    print(f"  {title}")
    print(f"{'─' * 70}")


# ── imports ───────────────────────────────────────────────────────────────────

import idea_generator as ig
from idea_generator import has_active_work, generate_ideas, CONTENT_PILLARS
from config import STATUS_TRIGGER, STATUS_DRAFT_READY, STATUS_EXPORTED, APPROVAL_TRIGGER, COLUMNS


# ─────────────────────────────────────────────────────────────────────────────
# SECTION A — Module structure and constants (tests 1–3)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION A — Tests 1–3: Module structure and constants")

# Test 1: idea_generator module is importable
check(
    "Test 1: idea_generator module imports without error",
    True,  # reaching here means the import above succeeded
    "Imported OK",
)

# Test 2: CONTENT_PILLARS covers at least 7 distinct pillars
pillar_count = CONTENT_PILLARS.count("Pillar")
check(
    "Test 2: CONTENT_PILLARS contains at least 7 pillars",
    pillar_count >= 7,
    f"Found {pillar_count} pillar entries",
)

# Test 3: Default batch size is a positive integer
check(
    "Test 3: _BATCH_SIZE is a positive integer (default 5)",
    isinstance(ig._BATCH_SIZE, int) and ig._BATCH_SIZE > 0,
    f"_BATCH_SIZE={ig._BATCH_SIZE}",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION B — has_active_work() detection (tests 4–7)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION B — Tests 4–7: has_active_work() sheet detection")


def _mock_sheet(statuses: list[str]) -> MagicMock:
    """Build a mock gspread worksheet returning rows with given statuses."""
    sheet = MagicMock()
    sheet.get_all_records.return_value = [
        {"Status": s, "Content Title": f"Row {i}"}
        for i, s in enumerate(statuses)
    ]
    return sheet


# Test 4: Empty sheet → no active work
check(
    "Test 4: has_active_work([]) returns False for empty sheet",
    has_active_work(_mock_sheet([])) is False,
    "Empty sheet has no active work",
)

# Test 5: Only "Exported" rows → no active work
check(
    "Test 5: has_active_work(['Exported', 'Exported']) returns False",
    has_active_work(_mock_sheet(["Exported", "Exported"])) is False,
    "Only Exported rows — queue is fully drained",
)

# Test 6: At least one "Idea" row → active work exists
check(
    "Test 6: has_active_work(['Exported', 'Idea']) returns True",
    has_active_work(_mock_sheet(["Exported", "Idea"])) is True,
    "'Idea' row means queue is not empty",
)

# Test 7: At least one "Draft Ready" row → active work exists
check(
    "Test 7: has_active_work(['Draft Ready']) returns True",
    has_active_work(_mock_sheet(["Draft Ready"])) is True,
    "'Draft Ready' row is in-progress work — don't flood queue",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION C — generate_ideas() sheet-write logic (tests 8–12)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION C — Tests 8–12: generate_ideas() with mocked Claude + sheet")

_SAMPLE_IDEAS = [
    {
        "content_title": "How to Calculate Amazon FBA Fees Before You Source",
        "main_keyword":  "amazon fba fees calculator",
        "topic_cluster": "Amazon FBA Fees",
        "audience_level": "Beginner",
        "content_type":  "Blog Post",
    },
    {
        "content_title": "Amazon PPC Bidding Strategy for New Sellers",
        "main_keyword":  "amazon ppc bidding strategy",
        "topic_cluster": "Amazon PPC",
        "audience_level": "Intermediate",
        "content_type":  "Blog Post",
    },
    {
        "content_title": "How to Find Your First Winning Amazon Product",
        "main_keyword":  "amazon product research beginner",
        "topic_cluster": "Product Research",
        "audience_level": "Beginner",
        "content_type":  "Blog Post",
    },
]


def _run_generate(require_approval: bool = True, ideas: list = _SAMPLE_IDEAS) -> tuple:
    """
    Run generate_ideas() with mocked Claude and sheet.
    Returns (result_dict, mock_sheet, appended_rows).
    """
    mock_sheet = MagicMock()
    appended   = []
    mock_sheet.append_row.side_effect = lambda row, **kw: appended.append(row)

    mock_resp         = MagicMock()
    mock_resp.content = [MagicMock(text=json.dumps(ideas))]
    mock_client       = MagicMock()
    mock_client.messages.create.return_value = mock_resp

    env_patch = {"REQUIRE_APPROVAL": "false" if not require_approval else "true",
                 "ANTHROPIC_API_KEY": "sk-test"}

    with patch("idea_generator.anthropic.Anthropic", return_value=mock_client), \
         patch.dict(os.environ, env_patch, clear=False):
        result = generate_ideas(mock_sheet, count=len(ideas))

    return result, mock_sheet, appended


# Test 8: Returns correct counts when Claude provides valid ideas
result8, _, rows8 = _run_generate()
check(
    "Test 8: generate_ideas() returns ideas_generated=3 ideas_written=3",
    result8["ideas_generated"] == 3 and result8["ideas_written"] == 3,
    f"result={result8}",
)

# Test 9: append_row called once per idea
check(
    "Test 9: sheet.append_row() called exactly 3 times",
    len(rows8) == 3,
    f"append_row call count: {len(rows8)}",
)

# Test 10: Each written row has Status = "Idea" in the correct column
status_col_idx = COLUMNS["status"] - 1
statuses_written = [row[status_col_idx] for row in rows8]
check(
    "Test 10: Every written row has Status='Idea'",
    all(s == STATUS_TRIGGER for s in statuses_written),
    f"Statuses written: {statuses_written}",
)

# Test 11: With REQUIRE_APPROVAL=true, Approval column is blank
approval_col_idx = COLUMNS["approval"] - 1
_, _, rows11 = _run_generate(require_approval=True)
approvals_with = [row[approval_col_idx] for row in rows11]
check(
    "Test 11: REQUIRE_APPROVAL=true → Approval column is blank (user must approve manually)",
    all(a == "" for a in approvals_with),
    f"Approval values: {approvals_with}",
)

# Test 12: With REQUIRE_APPROVAL=false, Approval column = "Yes"
_, _, rows12 = _run_generate(require_approval=False)
approvals_without = [row[approval_col_idx] for row in rows12]
check(
    "Test 12: REQUIRE_APPROVAL=false → Approval column = 'Yes' (auto-approved)",
    all(a == APPROVAL_TRIGGER for a in approvals_without),
    f"Approval values: {approvals_without}",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION D — REQUIRE_APPROVAL flag behaviour (tests 13–14)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION D — Tests 13–14: REQUIRE_APPROVAL env-var behaviour")

# Test 13: Default (env not set) → approval IS required
with patch.dict(os.environ, {}, clear=False):
    os.environ.pop("REQUIRE_APPROVAL", None)
    check(
        "Test 13: REQUIRE_APPROVAL unset → _require_approval() returns True (approval needed)",
        ig._require_approval() is True,
        "Default: approval required",
    )

# Test 14: REQUIRE_APPROVAL=false → _require_approval() returns False
with patch.dict(os.environ, {"REQUIRE_APPROVAL": "false"}):
    check(
        "Test 14: REQUIRE_APPROVAL=false → _require_approval() returns False",
        ig._require_approval() is False,
        "Approval not required — auto-approve flow enabled",
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION E — Pipeline integration flags (tests 15–16)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION E — Tests 15–16: Pipeline integration guards")

# Test 15: generate_ideas() returns error cleanly when ANTHROPIC_API_KEY is absent
with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
    result15 = generate_ideas(MagicMock())
check(
    "Test 15: generate_ideas() with no API key returns ideas_written=0 and error string",
    result15["ideas_written"] == 0 and result15["error"] is not None,
    f"result={result15}",
)

# Test 16: generate_ideas() handles malformed Claude JSON gracefully (no crash)
mock_sheet16  = MagicMock()
mock_resp16   = MagicMock()
mock_resp16.content = [MagicMock(text="THIS IS NOT JSON {{{{")]
mock_client16 = MagicMock()
mock_client16.messages.create.return_value = mock_resp16

with patch("idea_generator.anthropic.Anthropic", return_value=mock_client16), \
     patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
    result16 = generate_ideas(mock_sheet16)

check(
    "Test 16: generate_ideas() handles malformed Claude JSON without crashing — returns error",
    result16["ideas_written"] == 0 and result16["error"] is not None,
    f"result={result16}",
)


# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 70}")
print(f"  TEST RESULTS — Self-feeding pipeline (idea_generator)")
print(f"{'=' * 70}")
passed_count = sum(1 for _, ok, _ in _results if ok)
failed_count = sum(1 for _, ok, _ in _results if not ok)
for label, ok, detail in _results:
    tag = "  OK  " if ok else " FAIL "
    print(f"[{tag}] {label}")

print(f"\n  Total: {len(_results)}  |  Passed: {passed_count}  |  Failed: {failed_count}")
if failed_count == 0:
    print(f"\n  ✓  ALL {len(_results)} TESTS PASSED — Self-feeding pipeline verified")
    print("     has_active_work() correctly detects Idea / Draft-Ready rows")
    print("     generate_ideas() writes correct Status and Approval values")
    print("     REQUIRE_APPROVAL flag controls auto-approval correctly")
    print("     Malformed Claude responses handled without crash")
else:
    print(f"\n  ✗  {failed_count} TEST(S) FAILED — fix before deploying")

sys.exit(0 if failed_count == 0 else 1)
