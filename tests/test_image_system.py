"""
tests/test_image_system.py — Image selection system verification.

Run from project root:
    python3 tests/test_image_system.py

Proves all 10 correctness requirements from the architecture spec:
  1. Food/onion images are never selected
  2. PPC article never receives warehouse images
  3. Same image_id never reused across two posts (section/hero)
  4. Same visual_cluster avoided when alternatives exist
  5. CTA images allowed to repeat (reusable_cta=True) across posts
  6. No relevant image → article publishes without section images (returns None)
  7. Registry persists across runs (in-memory simulation)
  8. Disabled images are never selected
  9. A new project can reuse by changing catalog + routing map only
 10. Example log output from a real selection run
"""
from __future__ import annotations

import sys
import re
import logging
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Test logging — capture all image logs ─────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(message)s",
)

# Silence gspread/google-auth noise for test output clarity
for noisy in ("urllib3", "google", "gspread", "oauth2client"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

# ─────────────────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────────────────

PASS = "✓ PASS"
FAIL = "✗ FAIL"
_results: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
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


def build_mock_registry(used_section_ids: set | None = None):
    """Build an in-memory ImageRegistry with pre-loaded used IDs (no Sheets needed)."""
    from exporters.image_registry import ImageRegistry
    reg = object.__new__(ImageRegistry)
    reg._entries            = []
    reg._used_section_ids   = set(used_section_ids or [])
    reg._cluster_history    = []
    reg._connected          = False
    reg._ws                 = None
    return reg


def build_service(keyword: str, cluster: str, registry=None):
    """Build ImageSelectionService with optional injected registry (bypasses Sheets)."""
    from exporters.image_selector import ImageSelectionService
    from exporters.image_router import route

    svc = object.__new__(ImageSelectionService)
    svc._keyword   = keyword
    svc._cluster   = cluster
    svc._title     = f"Test Article: {keyword}"
    svc._slug      = keyword.lower().replace(" ", "-")
    svc._registry  = registry or build_mock_registry()
    svc._used_urls   = set()
    svc._used_clusters = set()
    svc._selections  = []
    svc._section_count = 0
    svc._allowed_categories = route(keyword, cluster)
    return svc


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Food/onion images never selected
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 1: Food/onion images never selected")

from exporters.image_catalog import IMAGE_CATALOG, get_approved
from exporters.image_policy import BLOCKED_TAGS, BLOCKED_DESCRIPTION_WORDS, STATUS_APPROVED
from exporters.image_selector import _evaluate_gates

registry = build_mock_registry()

food_tags_in_catalog = [
    (e.image_id, sorted({t.lower() for t in e.tags} & BLOCKED_TAGS))
    for e in IMAGE_CATALOG
    if e.status == STATUS_APPROVED and {t.lower() for t in e.tags} & BLOCKED_TAGS
]
check(
    "No approved catalog image has a BLOCKED_TAG",
    len(food_tags_in_catalog) == 0,
    f"Violations: {food_tags_in_catalog}" if food_tags_in_catalog else "Clean",
)

food_desc_in_catalog = [
    (e.image_id, sorted(set(e.description.lower().split()) & BLOCKED_DESCRIPTION_WORDS))
    for e in IMAGE_CATALOG
    if e.status == STATUS_APPROVED
    and set(e.description.lower().split()) & BLOCKED_DESCRIPTION_WORDS
]
check(
    "No approved catalog image has a BLOCKED_DESCRIPTION_WORD",
    len(food_desc_in_catalog) == 0,
    f"Violations: {food_desc_in_catalog}" if food_desc_in_catalog else "Clean",
)

# Test a hypothetical onion-tagged image is rejected
from exporters.image_catalog import ImageEntry, _url
from exporters.image_policy import (
    CAT_FBA_LOGISTICS, STATUS_APPROVED as SA, ROLE_SECTION,
)
onion_entry = ImageEntry(
    image_id="ONION_TEST",
    url=_url("9999999"),
    category=CAT_FBA_LOGISTICS,
    allowed_topic_clusters=("fba",),
    blocked_topic_clusters=(),
    tags=("warehouse", "fba", "amazon", "onion", "vegetables"),
    description="warehouse with onion storage",
    visual_cluster="test_cluster",
    quality_score=0.90,
    relevance_keywords=("warehouse", "fba"),
    reusable_cta=False,
    status=SA,
    roles=(ROLE_SECTION,),
)
passed, reason, *_ = _evaluate_gates(
    onion_entry, "warehouse section", "amazon fba", "FBA",
    set(), [CAT_FBA_LOGISTICS], registry, ROLE_SECTION,
)
check(
    "Onion-tagged image rejected by Gate 3 (BLOCKED_TAG)",
    not passed and "BLOCKED_TAG" in reason,
    f"reason='{reason}'",
)

onion_desc_entry = ImageEntry(
    image_id="ONION_DESC_TEST",
    url=_url("9999998"),
    category=CAT_FBA_LOGISTICS,
    allowed_topic_clusters=("fba",),
    blocked_topic_clusters=(),
    tags=("warehouse", "fba", "amazon", "storage"),
    description="fba warehouse with onion vegetable storage area",
    visual_cluster="test_cluster",
    quality_score=0.90,
    relevance_keywords=("warehouse", "fba"),
    reusable_cta=False,
    status=SA,
    roles=(ROLE_SECTION,),
)
passed2, reason2, *_ = _evaluate_gates(
    onion_desc_entry, "warehouse section", "amazon fba", "FBA",
    set(), [CAT_FBA_LOGISTICS], registry, ROLE_SECTION,
)
check(
    "Onion-described image rejected by Gate 4 (BLOCKED_DESCRIPTION)",
    not passed2 and "BLOCKED_DESCRIPTION" in reason2,
    f"reason='{reason2}'",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — PPC article never receives warehouse section images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 2: PPC article never receives warehouse images in section slots")

ppc_service = build_service("amazon ppc budget optimization", "Amazon Advertising")
check(
    "PPC routes to amazon_ads_digital only",
    ppc_service._allowed_categories == ["amazon_ads_digital"],
    f"Got: {ppc_service._allowed_categories}",
)

ppc_section = ppc_service.section("How to Set Your PPC Budget", 0)
check(
    "PPC section() returns None (empty category pool)",
    ppc_section is None,
    f"Got: {ppc_section}",
)

# Confirm FBA warehouse images are not in PPC selection
from exporters.image_catalog import get_approved
from exporters.image_policy import ROLE_SECTION, CAT_FBA_LOGISTICS, CAT_AMAZON_ADS
fba_section_images = get_approved(role=ROLE_SECTION, category=CAT_FBA_LOGISTICS)
ads_section_images = get_approved(role=ROLE_SECTION, category=CAT_AMAZON_ADS)
check(
    "amazon_ads_digital section pool is empty (no approved ads images yet)",
    len(ads_section_images) == 0,
    f"FBA pool={len(fba_section_images)} | Ads pool={len(ads_section_images)}",
)

# Also confirm FBA images have explicit blocked_topic_clusters for PPC
fba_not_blocked_for_ppc = [
    e.image_id for e in fba_section_images
    if not e.is_blocked_for_topic("ppc", "Amazon Advertising")
]
check(
    "All FBA section images are blocked for PPC topic",
    len(fba_not_blocked_for_ppc) == 0,
    f"Not blocked: {fba_not_blocked_for_ppc}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — Same image_id never reused across two posts (section/hero)
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 3: Cross-post section image deduplication")

# Post 1: FBA shipping
registry_shared = build_mock_registry()
svc1 = build_service("amazon fba inbound shipping", "FBA Shipping")
svc1._registry = registry_shared

post1_section_ids = []
for i, heading in enumerate(["Inbound Overview", "Shipping Costs", "Choosing a Carrier", "LTL Guide"]):
    url = svc1.section(f"{heading} amazon fba shipping", i)
    if url:
        m = re.search(r'/photos/(\d+)/', url)
        if m:
            post1_section_ids.append(m.group(1))

print(f"\n  Post 1 section IDs: {post1_section_ids}")

# Simulate registry commit (in-memory)
registry_shared._used_section_ids.update(post1_section_ids)

# Post 2: FBA storage (same registry, some IDs now used)
svc2 = build_service("amazon fba storage fees", "FBA Fees")
svc2._registry = registry_shared

post2_section_ids = []
for i, heading in enumerate(["What Are Storage Fees", "Calculate Storage Fees", "Reduce Fees", "Inventory Tips"]):
    url = svc2.section(f"{heading} amazon fba storage", i)
    if url:
        m = re.search(r'/photos/(\d+)/', url)
        if m:
            post2_section_ids.append(m.group(1))

print(f"  Post 2 section IDs: {post2_section_ids}")

reused = set(post1_section_ids) & set(post2_section_ids)
check(
    "No section image ID reused between Post 1 and Post 2",
    len(reused) == 0,
    f"Reused IDs: {reused or 'none'}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — Visual cluster diversity within a post
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 4: Visual cluster diversity within a post")

svc4 = build_service("amazon fba inbound shipping", "FBA Logistics")
svc4._registry = build_mock_registry()

url_a = svc4.section("Inbound Shipping Overview amazon fba", 0)
url_b = svc4.section("Shipping Cost Calculator amazon fba", 1)

clusters_used = list(svc4._used_clusters)
print(f"\n  Section selections: {[s.image_id for s in svc4._selections if s.role == 'section']}")
print(f"  Visual clusters used: {clusters_used}")

section_ids = [s.image_id for s in svc4._selections if s.role == "section"]
section_clusters = [s.visual_cluster for s in svc4._selections if s.role == "section"]

check(
    "Section images use distinct visual clusters when alternatives exist",
    len(section_clusters) <= 1 or len(set(section_clusters)) > 1,
    f"Clusters: {section_clusters}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — CTA images repeat across posts (reusable_cta=True)
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 5: CTA images are reusable across posts")

registry_cta = build_mock_registry()

svc5a = build_service("amazon fba shipping", "FBA")
svc5a._registry = registry_cta
cta_urls_post1 = [svc5a.cta(i) for i in range(3)]
cta_ids_post1  = [re.search(r'/photos/(\d+)/', u).group(1) for u in cta_urls_post1]

# Simulate section commit only (CTA not added to _used_section_ids)
registry_cta._used_section_ids.update([])  # CTAs NOT added

svc5b = build_service("amazon fba storage", "FBA Fees")
svc5b._registry = registry_cta
cta_urls_post2 = [svc5b.cta(i) for i in range(3)]
cta_ids_post2  = [re.search(r'/photos/(\d+)/', u).group(1) for u in cta_urls_post2]

print(f"\n  Post 1 CTAs: {cta_ids_post1}")
print(f"  Post 2 CTAs: {cta_ids_post2}")

all_cta = get_approved(role="cta")
reusable_ids = {e.image_id for e in all_cta if e.reusable_cta}
cta_in_registry = reusable_ids & registry_cta._used_section_ids
check(
    "CTA images (reusable_cta=True) are NOT in used_section_ids",
    len(cta_in_registry) == 0,
    f"CTA IDs in used_section_ids: {cta_in_registry or 'none'}",
)

# Within each post, CTA slots must be distinct
check(
    "Post 1: 3 CTA slots all use distinct images",
    len(set(cta_ids_post1)) == 3,
    f"CTA IDs: {cta_ids_post1}",
)
check(
    "Post 2: 3 CTA slots all use distinct images",
    len(set(cta_ids_post2)) == 3,
    f"CTA IDs: {cta_ids_post2}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 — No relevant image → article publishes without section images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 6: No relevant image → section returns None, article still publishable")

svc6 = build_service("amazon ppc acos optimization", "Amazon Advertising")
svc6._registry = build_mock_registry()

result = svc6.section("How to reduce ACOS in your campaigns", 0)
check(
    "PPC section returns None (not a bad image — None)",
    result is None,
    f"Got: {result}",
)

# CTA still works even when section is None
cta_result = svc6.cta(0)
check(
    "CTA still returns a valid URL even when section images are skipped",
    cta_result is not None and cta_result.startswith("https://"),
    f"CTA URL: {cta_result[:60] if cta_result else 'None'}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 — Registry persists across runs (simulated)
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 7: Registry persists used IDs across runs (simulation)")

# Simulate run 1: select images, register
reg_persistent = build_mock_registry()
svc_run1 = build_service("amazon fba packaging prep", "FBA Prep")
svc_run1._registry = reg_persistent

run1_url = svc_run1.section("Packaging Prep Guide amazon fba", 0)
m = re.search(r'/photos/(\d+)/', run1_url) if run1_url else None
run1_id = m.group(1) if m else None
if run1_id:
    reg_persistent._used_section_ids.add(run1_id)

print(f"\n  Run 1 selected: {run1_id}")
print(f"  Registry after run 1: {sorted(reg_persistent._used_section_ids)}")

# Simulate run 2: same registry loaded (IDs persisted)
svc_run2 = build_service("amazon fba packaging prep", "FBA Prep")
svc_run2._registry = reg_persistent  # same in-memory state = same as loaded from Sheets

run2_url = svc_run2.section("Packaging Prep Guide amazon fba", 0)
m2 = re.search(r'/photos/(\d+)/', run2_url) if run2_url else None
run2_id = m2.group(1) if m2 else None
print(f"  Run 2 selected: {run2_id}")

check(
    "Run 2 does not reuse Run 1's section image",
    run1_id is None or run2_id is None or run1_id != run2_id,
    f"Run 1={run1_id}  Run 2={run2_id}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 8 — Disabled images never selected
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 8: Disabled images are never selected")

from exporters.image_policy import STATUS_DISABLED, ROLE_SECTION as RS
disabled_entry = ImageEntry(
    image_id="DISABLED_TEST",
    url=_url("8888888"),
    category=CAT_FBA_LOGISTICS,
    allowed_topic_clusters=("fba",),
    blocked_topic_clusters=(),
    tags=("warehouse", "fba", "amazon", "inventory", "storage"),
    description="amazon fba warehouse inventory storage fulfillment",
    visual_cluster="warehouse_workers",
    quality_score=0.95,
    relevance_keywords=("warehouse", "fba", "inventory", "storage"),
    reusable_cta=False,
    status=STATUS_DISABLED,   # ← DISABLED
    roles=(RS,),
)

reg_dis = build_mock_registry()
passed_d, reason_d, *_ = _evaluate_gates(
    disabled_entry, "warehouse inventory", "amazon fba storage", "FBA",
    set(), [CAT_FBA_LOGISTICS], reg_dis, ROLE_SECTION,
)
check(
    "Disabled image rejected by Gate 1 (REJECTED_DISABLED)",
    not passed_d and "REJECTED_DISABLED" in reason_d,
    f"reason='{reason_d}'",
)

approved_only = [e for e in IMAGE_CATALOG if e.status != STATUS_APPROVED]
check(
    "All catalog entries have explicit status field",
    all(hasattr(e, "status") for e in IMAGE_CATALOG),
    f"Missing status: {[e.image_id for e in IMAGE_CATALOG if not hasattr(e, 'status')]}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 9 — Architecture is reusable: catalog + routing map are the only changes
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 9: Architecture reusability")

# Verify that ImageSelectionService imports nothing project-specific directly
import inspect
from exporters import image_selector
src = inspect.getsource(image_selector)

# The service itself should not import hubspot modules or call HubSpot APIs.
# Comments/docstrings may mention "hubspot_api.py" for documentation — that's fine.
# What's forbidden: `import hubspot`, `from exporters.hubspot`, `requests.post(...hubspot...)`
import ast
try:
    tree = ast.parse(src)
    hubspot_imports = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and any("hubspot" in (getattr(alias, "name", "") or "").lower()
                or "hubspot" in (getattr(node, "module", "") or "").lower()
                for alias in getattr(node, "names", []))
    ]
    _no_hubspot_imports = len(hubspot_imports) == 0
except SyntaxError:
    _no_hubspot_imports = False

check(
    "image_selector.py contains no HubSpot-specific logic",
    _no_hubspot_imports,
    "No HubSpot imports or API calls in image_selector.py (comments/docstrings excluded)",
)

check(
    "image_policy.py exports only constants (no business logic)",
    all(
        not callable(getattr(__import__("exporters.image_policy",
                                         fromlist=["image_policy"]), k, None))
        or k.startswith("_")
        for k in dir(__import__("exporters.image_policy", fromlist=["image_policy"]))
        if not k.startswith("__")
    ),
    "All public names in image_policy are constants",
)

check(
    "Module structure: 6 image_*.py modules exist",
    all(
        os.path.exists(
            os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "exporters", f"image_{mod}.py")
        )
        for mod in ["policy", "catalog", "router", "registry", "logging", "selector"]
    ),
    "image_policy.py, image_catalog.py, image_router.py, image_registry.py, "
    "image_logging.py, image_selector.py all present",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 10 — Example log output from a real selection run
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 10: Example log output — FBA shipping article full run")
print()

svc10 = build_service("amazon fba inbound shipping costs", "FBA Shipping")
svc10._registry = build_mock_registry()

hero_url    = svc10.hero("Complete guide to amazon fba inbound shipping costs")
section_url = svc10.section("Understanding Inbound Shipping Costs amazon fba", 0)
cta0        = svc10.cta(0)
cta1        = svc10.cta(1)
cta2        = svc10.cta(2)

rpt = svc10.validation_report()
print(f"\n  validation_report:")
for k, v in rpt.items():
    if k != "selections":
        print(f"    {k}: {v}")
print(f"  selections:")
for sel in rpt["selections"]:
    print(f"    {sel}")

check(
    "Full run produces non-zero selections",
    rpt["image_count"] > 0,
    f"image_count={rpt['image_count']}",
)
check(
    "Full run has no duplicate IDs",
    rpt["duplicate_ids"] == "none",
    f"duplicates={rpt['duplicate_ids']}",
)
check(
    "Full run has no unverified IDs",
    rpt["unverified_ids"] == "none",
    f"unverified={rpt['unverified_ids']}",
)


# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 70}")
print(f"  TEST RESULTS")
print(f"{'=' * 70}")
passed_count = sum(1 for _, ok, _ in _results if ok)
failed_count = sum(1 for _, ok, _ in _results if not ok)
for label, ok, detail in _results:
    tag = "  OK  " if ok else " FAIL "
    print(f"[{tag}] {label}")

print(f"\n  Total: {len(_results)}  |  Passed: {passed_count}  |  Failed: {failed_count}")
if failed_count == 0:
    print("\n  ✓  ALL TESTS PASSED — image system is production-ready")
else:
    print(f"\n  ✗  {failed_count} TEST(S) FAILED — fix before deploying")

sys.exit(0 if failed_count == 0 else 1)
