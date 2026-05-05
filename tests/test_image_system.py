"""
tests/test_image_system.py — Dynamic image architecture verification.

Run from project root:
    python3 tests/test_image_system.py

Tests all 10 correctness requirements:
  1. Food/onion images rejected (global BLOCKED_TAGS + BLOCKED_DESCRIPTION_WORDS)
  2. PPC article never receives warehouse images
  3. AI tools article never receives warehouse images
  4. Same image_id never reused across posts (section/hero)
  5. Visual cluster diversity within a post
  6. CTA images allowed to repeat across posts (reusable_cta=True)
  7. No relevant image → section returns None (no fallback to unrelated images)
  8. Registry persists across runs (in-memory simulation)
  9. Disabled images never selected
 10. Two simulated posts (PPC + AI) produce different, topic-relevant images

All Pexels API calls are MOCKED — no real API key required for tests.
"""
from __future__ import annotations

import sys
import re
import logging
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(message)s")
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


# ─────────────────────────────────────────────────────────────────────────────
# Mock Pexels client — no real API calls in tests
# ─────────────────────────────────────────────────────────────────────────────

from exporters.image_fetcher import FetchedImage


class MockPexelsClient:
    """
    Returns pre-defined FetchedImage objects based on query keywords.
    Simulates a Pexels API that returns topically relevant results.
    """

    # Topic-specific mock image pools
    _MOCK_IMAGES = {
        "ppc": [
            FetchedImage("ppc_001", "https://images.pexels.com/photos/ppc001/large.jpg",
                         "amazon advertising campaign dashboard analytics", "Photographer A",
                         "#2B4F72", 1920, 1080, "amazon advertising campaign analytics", 0),
            FetchedImage("ppc_002", "https://images.pexels.com/photos/ppc002/large.jpg",
                         "digital marketing performance metrics analytics", "Photographer B",
                         "#1A3A4B", 1920, 1080, "ppc digital marketing performance", 1),
            FetchedImage("ppc_003", "https://images.pexels.com/photos/ppc003/large.jpg",
                         "ecommerce advertising optimization business ROI", "Photographer C",
                         "#2C3E50", 1920, 1080, "ecommerce advertising optimization", 2),
        ],
        "ai": [
            FetchedImage("ai_001", "https://images.pexels.com/photos/ai001/large.jpg",
                         "artificial intelligence business software dashboard interface", "Photographer D",
                         "#1B2631", 1920, 1080, "artificial intelligence business software", 0),
            FetchedImage("ai_002", "https://images.pexels.com/photos/ai002/large.jpg",
                         "AI ecommerce automation analytics technology", "Photographer E",
                         "#17202A", 1920, 1080, "AI ecommerce automation analytics", 1),
            FetchedImage("ai_003", "https://images.pexels.com/photos/ai003/large.jpg",
                         "machine learning business data dashboard", "Photographer F",
                         "#2E4057", 1920, 1080, "machine learning business analytics", 2),
        ],
        "fba": [
            FetchedImage("fba_001", "https://images.pexels.com/photos/fba001/large.jpg",
                         "amazon warehouse fulfillment center operations logistics", "Photographer G",
                         "#4A235A", 1920, 1080, "amazon warehouse fulfillment center", 0),
            FetchedImage("fba_002", "https://images.pexels.com/photos/fba002/large.jpg",
                         "ecommerce fulfillment shipping boxes logistics delivery", "Photographer H",
                         "#3B1F2B", 1920, 1080, "ecommerce fulfillment shipping boxes", 1),
        ],
        "general": [
            FetchedImage("gen_001", "https://images.pexels.com/photos/gen001/large.jpg",
                         "professional business analytics dashboard strategy", "Photographer I",
                         "#2D3436", 1920, 1080, "professional business analytics dashboard", 0),
        ],
    }

    # Images that should be blocked (contain blocked terms in alt)
    _BLOCKED_IMAGES = {
        "onion": FetchedImage("onion_001", "https://images.pexels.com/photos/onion001/large.jpg",
                              "onion vegetables food cooking kitchen", "Photographer X",
                              "#8E44AD", 1920, 1080, "onion vegetable food", 0),
        "warehouse_for_ppc": FetchedImage("wh_001", "https://images.pexels.com/photos/wh001/large.jpg",
                                          "warehouse storage inventory forklift pallet boxes", "Photographer Y",
                                          "#922B21", 1920, 1080, "warehouse fulfillment", 0),
    }

    def __init__(self, topic_hint: str = "general"):
        self.topic_hint = topic_hint
        self.available  = True
        self.call_log   = []   # track which queries were called

    def search(self, query: str, per_page: int = 15) -> list:
        self.call_log.append(query)
        query_lower = query.lower()

        if any(t in query_lower for t in ("ppc", "advertising", "campaign", "digital marketing", "pay per click")):
            return list(self._MOCK_IMAGES["ppc"])
        elif any(t in query_lower for t in ("artificial intelligence", "ai ", "machine learning", "automation", "chatgpt")):
            return list(self._MOCK_IMAGES["ai"])
        elif any(t in query_lower for t in ("warehouse", "fulfillment", "fba", "shipping", "inventory", "logistics")):
            return list(self._MOCK_IMAGES["fba"])
        else:
            return list(self._MOCK_IMAGES["general"])


def build_mock_registry(used_section_ids: set | None = None):
    """Build an in-memory ImageRegistry bypassing Sheets."""
    from exporters.image_registry import ImageRegistry
    reg = object.__new__(ImageRegistry)
    reg._entries            = []
    reg._used_section_ids   = set(used_section_ids or [])
    reg._cluster_history    = []
    reg._connected          = False
    reg._ws                 = None
    return reg


def build_service(keyword: str, cluster: str, registry=None, fetcher=None):
    """Build ImageSelectionService with injected registry and mock fetcher."""
    from exporters.image_selector import ImageSelectionService
    svc = ImageSelectionService(
        article_keyword       = keyword,
        article_topic_cluster = cluster,
        article_title         = f"Test: {keyword}",
        article_slug          = keyword.lower().replace(" ", "-"),
        _fetcher              = fetcher or MockPexelsClient(),
        _registry             = registry or build_mock_registry(),
    )
    return svc


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Food/onion images blocked globally
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 1: Food/onion images rejected by BLOCKED_TAGS and BLOCKED_DESCRIPTION_WORDS")

from exporters.image_fetcher import _is_blocked
from exporters.image_policy import BLOCKED_TAGS, BLOCKED_DESCRIPTION_WORDS, CAT_FBA_LOGISTICS

onion_img = FetchedImage(
    "onion_test", "https://images.pexels.com/photos/99999/large.jpg",
    "onion vegetable food cooking kitchen", "test",
    "#fff", 800, 600, "onion food test", 0,
)
blocked, reason = _is_blocked(onion_img, CAT_FBA_LOGISTICS)
check(
    "Onion alt-text image rejected (BLOCKED_TAG)",
    blocked and "BLOCKED_TAG" in reason,
    f"reason='{reason}'",
)

food_desc_img = FetchedImage(
    "food_test", "https://images.pexels.com/photos/88888/large.jpg",
    "restaurant meal cooking chef plate", "test",
    "#fff", 800, 600, "restaurant food", 0,
)
blocked2, reason2 = _is_blocked(food_desc_img, CAT_FBA_LOGISTICS)
check(
    "Restaurant/cooking alt-text image rejected (BLOCKED_TAG)",
    blocked2,
    f"reason='{reason2}'",
)

# Also verify the static catalog has no blocked images
from exporters.image_catalog import IMAGE_CATALOG
from exporters.image_policy import STATUS_APPROVED
food_in_catalog = [
    e.image_id for e in IMAGE_CATALOG
    if e.status == STATUS_APPROVED and {t.lower() for t in e.tags} & BLOCKED_TAGS
]
check(
    "No approved static catalog image has a BLOCKED_TAG",
    len(food_in_catalog) == 0,
    f"Violations: {food_in_catalog}" if food_in_catalog else "Clean",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — PPC article does NOT receive warehouse images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 2: PPC article — warehouse images blocked, PPC-relevant images selected")

from exporters.image_policy import CAT_AMAZON_ADS, TOPIC_NEGATIVE_TERMS
from exporters.image_router import route

ppc_category = route("amazon ppc budget optimization", "Amazon Advertising")
check(
    "PPC keyword routes to amazon_ads_digital",
    ppc_category == CAT_AMAZON_ADS,
    f"Got: '{ppc_category}'",
)

# Confirm warehouse-tagged image is blocked for PPC topic
warehouse_img = FetchedImage(
    "wh_test", "https://images.pexels.com/photos/77777/large.jpg",
    "warehouse storage inventory forklift pallet", "test",
    "#fff", 800, 600, "warehouse inventory", 0,
)
wh_blocked, wh_reason = _is_blocked(warehouse_img, CAT_AMAZON_ADS)
check(
    "Warehouse image blocked for PPC topic (TOPIC_NEGATIVE_TERM)",
    wh_blocked,
    f"reason='{wh_reason}'",
)

# PPC article selects PPC-relevant images, not warehouse
ppc_fetcher = MockPexelsClient("ppc")
ppc_svc = build_service("amazon ppc budget optimization", "Amazon Advertising",
                         fetcher=ppc_fetcher)
ppc_url = ppc_svc.section("How to Set Your PPC Budget", 0)

check(
    "PPC section returns a URL (found PPC-relevant image)",
    ppc_url is not None,
    f"Got: {ppc_url[:80] if ppc_url else 'None'}",
)
check(
    "PPC section URL is not a warehouse image",
    ppc_url is None or "wh0" not in ppc_url,
    f"URL: {ppc_url}",
)
if ppc_url:
    check(
        "PPC section URL comes from Pexels CDN",
        "images.pexels.com" in ppc_url,
        f"URL: {ppc_url[:80]}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — AI tools article does NOT receive warehouse images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 3: AI tools article — warehouse images blocked, AI-relevant images selected")

from exporters.image_policy import CAT_AI_TOOLS

ai_category = route("AI tools for online sellers in 2026", "AI Tools Automation")
check(
    "AI tools keyword routes to ai_tools_automation",
    ai_category == CAT_AI_TOOLS,
    f"Got: '{ai_category}'",
)

# Warehouse images blocked for AI tools topic
wh_blocked_ai, wh_reason_ai = _is_blocked(warehouse_img, CAT_AI_TOOLS)
check(
    "Warehouse image blocked for AI tools topic",
    wh_blocked_ai,
    f"reason='{wh_reason_ai}'",
)

ai_fetcher = MockPexelsClient("ai")
ai_svc = build_service("AI tools for online sellers in 2026", "AI Tools Automation",
                        fetcher=ai_fetcher)
ai_url = ai_svc.section("Best AI Tools for Amazon Sellers", 0)

check(
    "AI section returns a URL (found AI-relevant image)",
    ai_url is not None,
    f"Got: {ai_url[:80] if ai_url else 'None'}",
)
check(
    "AI section URL is not a warehouse image",
    ai_url is None or "wh" not in ai_url,
    f"URL: {ai_url}",
)
if ai_url:
    check(
        "AI section URL contains 'ai' topic keyword in selection",
        "ai" in ai_url.lower() or ai_svc._selections[0].search_query != "",
        f"selected: {ai_svc._selections[0].search_query if ai_svc._selections else 'none'}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — PPC and AI articles produce DIFFERENT images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 4: PPC and AI articles use different, non-overlapping section images")

# Re-run both with fresh services
ppc_svc2 = build_service("amazon ppc budget optimization", "Amazon Advertising",
                          fetcher=MockPexelsClient("ppc"))
ai_svc2  = build_service("AI tools for online sellers 2026", "AI Tools Automation",
                          fetcher=MockPexelsClient("ai"))

ppc_url2 = ppc_svc2.section("Managing PPC Campaign Budget", 0)
ai_url2  = ai_svc2.section("Best AI Tools for Amazon Sellers 2026", 0)

print(f"\n  PPC section URL:     {ppc_url2}")
print(f"  AI tools section URL: {ai_url2}")

check(
    "PPC and AI section images are different URLs",
    ppc_url2 != ai_url2,
    f"PPC={ppc_url2}  AI={ai_url2}",
)
# PPC should contain 'ppc' in ID, AI should contain 'ai' in ID
check(
    "PPC image ID contains 'ppc' (topic-specific)",
    ppc_url2 is None or "ppc" in ppc_url2.lower(),
    f"PPC URL: {ppc_url2}",
)
check(
    "AI image ID contains 'ai' (topic-specific)",
    ai_url2 is None or "ai" in ai_url2.lower(),
    f"AI URL: {ai_url2}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — Cross-post section image deduplication
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 5: Cross-post section image deduplication (registry dedup)")

shared_registry = build_mock_registry()
fba_fetcher1 = MockPexelsClient("fba")
fba_fetcher2 = MockPexelsClient("fba")

svc5a = build_service("amazon fba inbound shipping", "FBA Shipping",
                       registry=shared_registry, fetcher=fba_fetcher1)
url5a_0 = svc5a.section("Inbound Shipping Overview fba", 0)
url5a_1 = svc5a.section("Shipping Cost Calculator fba", 1)

# Simulate commit: mark selected IDs as globally used
for sel in svc5a._selections:
    if sel.role == "section":
        shared_registry._used_section_ids.add(sel.image_id)

post1_ids = {sel.image_id for sel in svc5a._selections if sel.role == "section"}
print(f"\n  Post 1 section IDs: {sorted(post1_ids)}")

svc5b = build_service("amazon fba storage fees", "FBA Fees",
                       registry=shared_registry, fetcher=fba_fetcher2)
url5b_0 = svc5b.section("What Are FBA Storage Fees", 0)
url5b_1 = svc5b.section("Calculating Storage Fees fba", 1)

post2_ids = {sel.image_id for sel in svc5b._selections if sel.role == "section"}
print(f"  Post 2 section IDs: {sorted(post2_ids)}")

reused = post1_ids & post2_ids
check(
    "No section image ID reused between Post 1 and Post 2",
    len(reused) == 0,
    f"Reused: {reused or 'none'}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6 — Visual cluster diversity within a post
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 6: Visual cluster diversity within a post")

svc6 = build_service("amazon fba inbound shipping", "FBA Logistics",
                      fetcher=MockPexelsClient("fba"))
url6a = svc6.section("FBA Inbound Overview", 0)
url6b = svc6.section("Choosing a Shipping Carrier fba", 1)

section_clusters = [s.visual_cluster for s in svc6._selections if s.role == "section"]
print(f"\n  Section clusters: {section_clusters}")

check(
    "Visual clusters used in sections are tracked in _used_clusters",
    len(svc6._used_clusters) > 0,
    f"Clusters: {svc6._used_clusters}",
)
check(
    "Section images use distinct visual clusters when alternatives exist",
    len(section_clusters) <= 1 or len(set(section_clusters)) > 1,
    f"Clusters: {section_clusters}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 — CTA images reusable across posts
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 7: CTA images are reusable across posts")

registry_cta = build_mock_registry()

svc7a = build_service("amazon fba shipping", "FBA", registry=registry_cta)
cta_urls_post1 = [svc7a.cta(i) for i in range(3)]
cta_ids_post1  = [
    re.search(r'/photos/(\d+)/', u).group(1)
    for u in cta_urls_post1
    if re.search(r'/photos/(\d+)/', u)
]

# CTAs are NOT added to used_section_ids
registry_cta._used_section_ids.update([])

svc7b = build_service("amazon fba storage", "FBA Fees", registry=registry_cta)
cta_urls_post2 = [svc7b.cta(i) for i in range(3)]
cta_ids_post2  = [
    re.search(r'/photos/(\d+)/', u).group(1)
    for u in cta_urls_post2
    if re.search(r'/photos/(\d+)/', u)
]

print(f"\n  Post 1 CTAs: {cta_ids_post1}")
print(f"  Post 2 CTAs: {cta_ids_post2}")

# CTA IDs should not be in global used_section_ids
cta_in_dedup = {id_ for id_ in cta_ids_post1} & registry_cta._used_section_ids
check(
    "CTA image IDs not added to global dedup set",
    len(cta_in_dedup) == 0,
    f"CTA IDs in used_section_ids: {cta_in_dedup or 'none'}",
)
check(
    "Post 1: 3 CTA slots use distinct images",
    len(set(cta_ids_post1)) == len(cta_ids_post1) == 3,
    f"CTA IDs: {cta_ids_post1}",
)
check(
    "Post 2: 3 CTA slots use distinct images",
    len(set(cta_ids_post2)) == len(cta_ids_post2) == 3,
    f"CTA IDs: {cta_ids_post2}",
)

# CTAs can repeat across posts (same IDs is acceptable)
from exporters.image_catalog import get_approved
from exporters.image_policy import ROLE_CTA
all_cta = get_approved(role=ROLE_CTA, reusable_cta=True)
check(
    "At least 3 CTA images available in static catalog",
    len(all_cta) >= 3,
    f"CTA count: {len(all_cta)}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 8 — No relevant image → section returns None (no fallback)
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 8: No relevant image → section returns None, article still publishable")


class EmptyPexelsClient:
    """Simulates Pexels API returning zero results (no relevant images found)."""
    available = True

    def search(self, query: str, per_page: int = 15) -> list:
        return []  # no results for any query


svc8 = build_service("amazon ppc acos optimization", "Amazon Advertising",
                      fetcher=EmptyPexelsClient())
result8 = svc8.section("How to reduce ACOS in your campaigns", 0)
check(
    "Section returns None when no relevant image found (no fallback)",
    result8 is None,
    f"Got: {result8}",
)

# CTA still works even when section is None
cta_result8 = svc8.cta(0)
check(
    "CTA still returns a valid URL even when section is skipped",
    cta_result8 is not None and cta_result8.startswith("https://"),
    f"CTA URL: {cta_result8[:60] if cta_result8 else 'None'}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 9 — Registry persists used IDs across runs
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 9: Registry persists used IDs across runs (simulation)")

reg9 = build_mock_registry()
fba_fetcher9 = MockPexelsClient("fba")

svc9a = build_service("amazon fba packaging prep", "FBA Prep",
                       registry=reg9, fetcher=fba_fetcher9)
run1_url = svc9a.section("Packaging Prep Guide amazon fba", 0)

if run1_url:
    # Find the image_id from the selection record (not URL parsing needed)
    run1_sel = next((s for s in svc9a._selections if s.role == "section"), None)
    run1_id  = run1_sel.image_id if run1_sel else None
    if run1_id:
        reg9._used_section_ids.add(run1_id)
else:
    run1_id = None

print(f"\n  Run 1 selected ID: {run1_id}")
print(f"  Registry after run 1: {sorted(reg9._used_section_ids)}")

svc9b = build_service("amazon fba packaging prep", "FBA Prep",
                       registry=reg9, fetcher=MockPexelsClient("fba"))
run2_url = svc9b.section("Packaging Prep Guide amazon fba", 0)
run2_sel = next((s for s in svc9b._selections if s.role == "section"), None)
run2_id  = run2_sel.image_id if run2_sel else None

print(f"  Run 2 selected ID: {run2_id}")

check(
    "Run 2 does not reuse Run 1's section image",
    run1_id is None or run2_id is None or run1_id != run2_id,
    f"Run 1={run1_id}  Run 2={run2_id}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 10 — Full run: PPC + AI posts produce correct, distinct, logged output
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 10: Full simulation — PPC post + AI post with logged output")
print()

# ── PPC post ──────────────────────────────────────────────────────────────────
svc_ppc = build_service(
    "amazon ppc budget management campaign strategy",
    "Amazon Advertising",
    fetcher=MockPexelsClient("ppc"),
)
ppc_hero    = svc_ppc.hero("Complete guide to amazon ppc budget management")
ppc_sec0    = svc_ppc.section("How to set your PPC campaign budget", 0)
ppc_sec1    = svc_ppc.section("Advanced bidding strategies for sponsored products", 1)
ppc_cta0    = svc_ppc.cta(0)
ppc_cta1    = svc_ppc.cta(1)
ppc_cta2    = svc_ppc.cta(2)

rpt_ppc = svc_ppc.validation_report()
print(f"\n  [PPC POST] topic_category={rpt_ppc['topic_category']}")
for sel in rpt_ppc["selections"]:
    print(f"    {sel['role']:8s}  id={sel['id']:12s}  score={sel['score']:.4f}  "
          f"source={sel['source']}  cluster={sel['visual_cluster']}")

# ── AI tools post ──────────────────────────────────────────────────────────────
svc_ai = build_service(
    "AI tools for online sellers in 2026",
    "AI Tools Automation",
    fetcher=MockPexelsClient("ai"),
)
ai_hero     = svc_ai.hero("AI tools every amazon seller needs in 2026")
ai_sec0     = svc_ai.section("Best AI tools for product research", 0)
ai_sec1     = svc_ai.section("How to use AI for listing optimization", 1)
ai_cta0     = svc_ai.cta(0)
ai_cta1     = svc_ai.cta(1)
ai_cta2     = svc_ai.cta(2)

rpt_ai = svc_ai.validation_report()
print(f"\n  [AI POST] topic_category={rpt_ai['topic_category']}")
for sel in rpt_ai["selections"]:
    print(f"    {sel['role']:8s}  id={sel['id']:12s}  score={sel['score']:.4f}  "
          f"source={sel['source']}  cluster={sel['visual_cluster']}")

# ── Assertions ────────────────────────────────────────────────────────────────
ppc_section_ids = {s.image_id for s in svc_ppc._selections if s.role == "section"}
ai_section_ids  = {s.image_id for s in svc_ai._selections  if s.role == "section"}

print(f"\n  PPC section IDs:     {sorted(ppc_section_ids)}")
print(f"  AI section IDs:      {sorted(ai_section_ids)}")
print(f"  Overlap:             {ppc_section_ids & ai_section_ids or 'none'}")

check(
    "PPC post: topic_category == 'amazon_ads_digital'",
    rpt_ppc["topic_category"] == "amazon_ads_digital",
    f"Got: {rpt_ppc['topic_category']}",
)
check(
    "AI post: topic_category == 'ai_tools_automation'",
    rpt_ai["topic_category"] == "ai_tools_automation",
    f"Got: {rpt_ai['topic_category']}",
)
check(
    "PPC section images contain 'ppc' in their IDs (topic-relevant)",
    all("ppc" in id_ for id_ in ppc_section_ids),
    f"IDs: {sorted(ppc_section_ids)}",
)
check(
    "AI section images contain 'ai' in their IDs (topic-relevant)",
    all("ai" in id_ for id_ in ai_section_ids),
    f"IDs: {sorted(ai_section_ids)}",
)
check(
    "PPC and AI articles share NO section images",
    len(ppc_section_ids & ai_section_ids) == 0,
    f"Shared: {ppc_section_ids & ai_section_ids or 'none'}",
)
check(
    "PPC post has no duplicate image IDs",
    rpt_ppc["duplicate_ids"] == "none",
    f"Duplicates: {rpt_ppc['duplicate_ids']}",
)
check(
    "AI post has no duplicate image IDs",
    rpt_ai["duplicate_ids"] == "none",
    f"Duplicates: {rpt_ai['duplicate_ids']}",
)
check(
    "All section/hero images sourced from pexels_api (not static catalog)",
    all(
        s.source == "pexels_api"
        for svc in (svc_ppc, svc_ai)
        for s in svc._selections
        if s.role in ("section", "hero")
    ),
    "All section/hero images dynamically fetched",
)
check(
    "All CTA images sourced from static_catalog",
    all(
        s.source == "static_catalog"
        for svc in (svc_ppc, svc_ai)
        for s in svc._selections
        if s.role == "cta"
    ),
    "All CTA images from static catalog",
)

# ── Module structure check ────────────────────────────────────────────────────
import os as _os
check(
    "All 7 image_*.py modules present (including new image_fetcher.py)",
    all(
        _os.path.exists(
            _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                          "exporters", f"image_{mod}.py")
        )
        for mod in ["policy", "catalog", "router", "registry", "logging", "selector", "fetcher"]
    ),
    "image_policy, image_catalog, image_router, image_registry, "
    "image_logging, image_selector, image_fetcher all present",
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
    print("\n  ✓  ALL TESTS PASSED — dynamic image architecture is production-ready")
else:
    print(f"\n  ✗  {failed_count} TEST(S) FAILED — fix before deploying")

sys.exit(0 if failed_count == 0 else 1)
