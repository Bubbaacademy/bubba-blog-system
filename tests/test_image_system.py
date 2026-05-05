"""
tests/test_image_system.py — AI-first image architecture verification.

Run from project root:
    python3 tests/test_image_system.py

Tests all correctness requirements for the v3 AI-first pipeline:
  1. Food/onion images rejected by static gate (BLOCKED_TAGS)
  2. PPC article routes correctly and uses non-warehouse images
  3. AI tools article routes correctly and uses non-warehouse images
  4. PPC and AI posts produce distinct, topic-specific images
  5. Cross-post section image deduplication (global registry)
  6. Visual cluster diversity within a post
  7. CTA images are reusable across posts (static catalog only)
  8. No image available → section returns None (no static fallback)
  9. Registry persists used IDs across simulated runs
 10. Full simulation: PPC + AI posts — correct sources, no duplicates, distinct IDs
 11. Module structure check — all required modules present
 12. image_selector.py contains no HubSpot-specific imports

All provider calls are MOCKED — no OPENAI_API_KEY or PEXELS_API_KEY required.
"""
from __future__ import annotations

import sys
import re
import logging
import os
import ast

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
# Mock image provider — no real API calls in tests
# ─────────────────────────────────────────────────────────────────────────────

from exporters.image_provider import ImageAsset, ImageProvider


class MockImageProvider(ImageProvider):
    """
    Returns pre-defined ImageAsset objects based on topic.
    Simulates the provider chain without making real API calls.

    Each asset has:
    - A URL containing the topic keyword (e.g. "ppc", "ai", "fba")
    - provider="pexels" (simulates Pexels fallback mode)
    - Distinct image IDs per slot to allow dedup testing
    """

    _MOCK_POOLS: dict = {
        "ppc": [
            ImageAsset(
                url            = "https://images.pexels.com/photos/ppc001/pexels-ppc001.jpg",
                provider       = "pexels",
                provider_id    = "ppc_001",
                prompt_hash    = "ppc001hash000000",
                search_query   = "amazon advertising campaign analytics",
                visual_cluster = "ads_dashboard",
                alt_text       = "Amazon advertising campaign performance dashboard",
            ),
            ImageAsset(
                url            = "https://images.pexels.com/photos/ppc002/pexels-ppc002.jpg",
                provider       = "pexels",
                provider_id    = "ppc_002",
                prompt_hash    = "ppc002hash000000",
                search_query   = "digital marketing performance metrics",
                visual_cluster = "marketing_metrics",
                alt_text       = "Digital marketing performance analytics dashboard",
            ),
            ImageAsset(
                url            = "https://images.pexels.com/photos/ppc003/pexels-ppc003.jpg",
                provider       = "pexels",
                provider_id    = "ppc_003",
                prompt_hash    = "ppc003hash000000",
                search_query   = "ecommerce advertising optimization ROI",
                visual_cluster = "ecommerce_analytics",
                alt_text       = "Ecommerce advertising ROI optimization chart",
            ),
        ],
        "ai": [
            ImageAsset(
                url            = "https://images.pexels.com/photos/ai001/pexels-ai001.jpg",
                provider       = "pexels",
                provider_id    = "ai_001",
                prompt_hash    = "ai001hash0000000",
                search_query   = "artificial intelligence business software",
                visual_cluster = "ai_software_ui",
                alt_text       = "AI-powered business analytics software interface",
            ),
            ImageAsset(
                url            = "https://images.pexels.com/photos/ai002/pexels-ai002.jpg",
                provider       = "pexels",
                provider_id    = "ai_002",
                prompt_hash    = "ai002hash0000000",
                search_query   = "AI ecommerce automation analytics",
                visual_cluster = "automation_dashboard",
                alt_text       = "AI ecommerce automation and analytics dashboard",
            ),
            ImageAsset(
                url            = "https://images.pexels.com/photos/ai003/pexels-ai003.jpg",
                provider       = "pexels",
                provider_id    = "ai_003",
                prompt_hash    = "ai003hash0000000",
                search_query   = "machine learning business data dashboard",
                visual_cluster = "ml_analytics",
                alt_text       = "Machine learning business data visualization",
            ),
        ],
        "fba": [
            ImageAsset(
                url            = "https://images.pexels.com/photos/fba001/pexels-fba001.jpg",
                provider       = "pexels",
                provider_id    = "fba_001",
                prompt_hash    = "fba001hash000000",
                search_query   = "amazon warehouse fulfillment center operations",
                visual_cluster = "warehouse_interior",
                alt_text       = "Amazon FBA warehouse fulfillment center operations",
            ),
            ImageAsset(
                url            = "https://images.pexels.com/photos/fba002/pexels-fba002.jpg",
                provider       = "pexels",
                provider_id    = "fba_002",
                prompt_hash    = "fba002hash000000",
                search_query   = "ecommerce shipping logistics delivery",
                visual_cluster = "shipping_logistics",
                alt_text       = "Ecommerce fulfillment shipping logistics",
            ),
        ],
        "general": [
            ImageAsset(
                url            = "https://images.pexels.com/photos/gen001/pexels-gen001.jpg",
                provider       = "pexels",
                provider_id    = "gen_001",
                prompt_hash    = "gen001hash000000",
                search_query   = "professional business analytics dashboard",
                visual_cluster = "business_workspace",
                alt_text       = "Professional business analytics and strategy workspace",
            ),
        ],
    }

    def __init__(self, topic: str = "general", empty: bool = False):
        self.topic = topic
        self._empty = empty
        self.call_log: list = []

    @property
    def name(self) -> str:
        return "mock"

    @property
    def available(self) -> bool:
        return True

    def get_image(self, prompt, article_slug, slot_name, registry, used_urls) -> "ImageAsset | None":
        if self._empty:
            return None

        self.call_log.append(slot_name)
        pool = self._MOCK_POOLS.get(self.topic, self._MOCK_POOLS["general"])

        for asset in pool:
            if asset.url in used_urls:
                continue
            if registry.is_globally_used(asset.image_id):
                continue
            return asset

        return None  # pool exhausted (all deduped)


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


def build_service(keyword: str, cluster: str, registry=None, provider=None):
    """Build ImageSelectionService with injected registry and mock provider."""
    from exporters.image_selector import ImageSelectionService
    return ImageSelectionService(
        article_keyword       = keyword,
        article_topic_cluster = cluster,
        article_title         = f"Test: {keyword}",
        article_slug          = re.sub(r"[^a-z0-9\-]", "-", keyword.lower())[:60],
        _provider             = provider or MockImageProvider(),
        _registry             = registry or build_mock_registry(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1 — Food/onion images blocked by static CTA gate
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 1: Food/onion images rejected by CTA gate (BLOCKED_TAGS)")

from exporters.image_policy import BLOCKED_TAGS, BLOCKED_DESCRIPTION_WORDS, STATUS_APPROVED
from exporters.image_catalog import IMAGE_CATALOG

# Static CTA gate should reject food-tagged entries
from exporters.image_selector import _evaluate_cta_gates
from exporters.image_catalog import ImageEntry
from exporters.image_policy import CAT_FBA_LOGISTICS, ROLE_CTA

food_entry = ImageEntry(
    image_id="test_food",
    url="https://images.pexels.com/photos/99999/large.jpg",
    category=CAT_FBA_LOGISTICS,
    allowed_topic_clusters=(),
    blocked_topic_clusters=(),
    tags=("onion", "vegetable", "food", "cooking"),
    description="onion vegetable food cooking",
    visual_cluster="food",
    quality_score=0.90,
    relevance_keywords=("onion",),
    reusable_cta=True,
    status=STATUS_APPROVED,
    roles=(ROLE_CTA,),
)
passed_food, reason_food, _ = _evaluate_cta_gates(food_entry)
check(
    "CTA gate rejects food-tagged entry (BLOCKED_TAG)",
    not passed_food and "BLOCKED_TAG" in reason_food,
    f"reason='{reason_food}'",
)

# Static catalog approved CTA images must have no blocked tags
food_in_catalog = [
    e.image_id for e in IMAGE_CATALOG
    if e.status == STATUS_APPROVED and {t.lower() for t in e.tags} & BLOCKED_TAGS
]
check(
    "No approved static catalog image has a BLOCKED_TAG",
    len(food_in_catalog) == 0,
    f"Violations: {food_in_catalog}" if food_in_catalog else "Clean",
)

# Section/hero entries must all be STATUS_DISABLED
from exporters.image_policy import ROLE_SECTION, ROLE_HERO
active_section_hero = [
    e.image_id for e in IMAGE_CATALOG
    if e.status == STATUS_APPROVED
    and any(r in e.roles for r in (ROLE_SECTION, ROLE_HERO))
]
check(
    "All section/hero catalog entries are disabled (AI-generated images only)",
    len(active_section_hero) == 0,
    f"Still active: {active_section_hero}" if active_section_hero else "All disabled",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — PPC article routes correctly and gets non-warehouse images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 2: PPC article — routes to amazon_ads_digital, gets analytics images")

from exporters.image_policy import CAT_AMAZON_ADS
from exporters.image_router import route

ppc_category = route("amazon ppc budget optimization", "Amazon Advertising")
check(
    "PPC keyword routes to amazon_ads_digital",
    ppc_category == CAT_AMAZON_ADS,
    f"Got: '{ppc_category}'",
)

ppc_svc = build_service(
    "amazon ppc budget optimization",
    "Amazon Advertising",
    provider=MockImageProvider("ppc"),
)
ppc_url = ppc_svc.section("How to Set Your PPC Budget", 0)

check(
    "PPC section returns a URL (mock provider delivered an asset)",
    ppc_url is not None,
    f"Got: {ppc_url[:80] if ppc_url else 'None'}",
)
check(
    "PPC section URL contains 'ppc' (topic-specific asset)",
    ppc_url is None or "ppc" in ppc_url.lower(),
    f"URL: {ppc_url}",
)
check(
    "PPC section URL has no warehouse imagery (not 'wh' / 'warehouse')",
    ppc_url is None or ("wh" not in ppc_url.lower() and "warehouse" not in ppc_url.lower()),
    f"URL: {ppc_url}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — AI tools article routes correctly and gets technology images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 3: AI tools article — routes to ai_tools_automation, gets tech images")

from exporters.image_policy import CAT_AI_TOOLS

ai_category = route("AI tools for online sellers in 2026", "AI Tools Automation")
check(
    "AI tools keyword routes to ai_tools_automation",
    ai_category == CAT_AI_TOOLS,
    f"Got: '{ai_category}'",
)

ai_svc = build_service(
    "AI tools for online sellers in 2026",
    "AI Tools Automation",
    provider=MockImageProvider("ai"),
)
ai_url = ai_svc.section("Best AI Tools for Amazon Sellers", 0)

check(
    "AI section returns a URL (mock provider delivered an asset)",
    ai_url is not None,
    f"Got: {ai_url[:80] if ai_url else 'None'}",
)
check(
    "AI section URL contains 'ai' (topic-specific asset)",
    ai_url is None or "ai" in ai_url.lower(),
    f"URL: {ai_url}",
)
check(
    "AI section URL has no warehouse imagery",
    ai_url is None or ("wh" not in ai_url.lower() and "warehouse" not in ai_url.lower()),
    f"URL: {ai_url}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — PPC and AI posts produce different, non-overlapping images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 4: PPC and AI articles produce distinct, non-overlapping section images")

ppc_svc2 = build_service(
    "amazon ppc budget optimization", "Amazon Advertising",
    provider=MockImageProvider("ppc"),
)
ai_svc2 = build_service(
    "AI tools for online sellers 2026", "AI Tools Automation",
    provider=MockImageProvider("ai"),
)

ppc_url2 = ppc_svc2.section("Managing PPC Campaign Budget", 0)
ai_url2  = ai_svc2.section("Best AI Tools for Amazon Sellers 2026", 0)

print(f"\n  PPC section URL:      {ppc_url2}")
print(f"  AI tools section URL: {ai_url2}")

check(
    "PPC and AI section images are different URLs",
    ppc_url2 != ai_url2,
    f"PPC={ppc_url2}  AI={ai_url2}",
)
check(
    "PPC image ID contains 'ppc' (topic-specific)",
    ppc_url2 is None or "ppc" in ppc_url2.lower(),
    f"URL: {ppc_url2}",
)
check(
    "AI image ID contains 'ai' (topic-specific)",
    ai_url2 is None or "ai" in ai_url2.lower(),
    f"URL: {ai_url2}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — Cross-post section image deduplication
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 5: Cross-post section image deduplication (registry dedup)")

shared_registry = build_mock_registry()

svc5a = build_service(
    "amazon fba inbound shipping", "FBA Shipping",
    registry=shared_registry, provider=MockImageProvider("fba"),
)
url5a_0 = svc5a.section("Inbound Shipping Overview fba", 0)
url5a_1 = svc5a.section("Shipping Cost Calculator fba", 1)

# Simulate commit: mark selected image IDs as globally used
for sel in svc5a._selections:
    if sel.role == "section":
        shared_registry._used_section_ids.add(sel.image_id)

post1_ids = {sel.image_id for sel in svc5a._selections if sel.role == "section"}
print(f"\n  Post 1 section IDs: {sorted(post1_ids)}")

svc5b = build_service(
    "amazon fba storage fees", "FBA Fees",
    registry=shared_registry, provider=MockImageProvider("fba"),
)
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

svc6 = build_service(
    "amazon fba inbound shipping", "FBA Logistics",
    provider=MockImageProvider("fba"),
)
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
# TEST 7 — CTA images are reusable across posts
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 7: CTA images reusable across posts, always from static catalog")

registry_cta = build_mock_registry()

svc7a = build_service("amazon fba shipping", "FBA", registry=registry_cta)
cta_urls_post1 = [svc7a.cta(i) for i in range(3)]
cta_ids_post1  = [
    re.search(r'/photos/(\d+)/', u).group(1)
    for u in cta_urls_post1
    if re.search(r'/photos/(\d+)/', u)
]

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
    "Post 1: 3 CTA slots served",
    len(cta_urls_post1) == 3,
    f"CTA URLs: {cta_urls_post1}",
)
check(
    "Post 2: 3 CTA slots served",
    len(cta_urls_post2) == 3,
    f"CTA URLs: {cta_urls_post2}",
)

from exporters.image_catalog import get_approved
all_cta = get_approved(role=ROLE_CTA, reusable_cta=True)
check(
    "At least 3 CTA images available in static catalog",
    len(all_cta) >= 3,
    f"CTA count: {len(all_cta)}",
)

# All CTA URLs must be from Pexels static catalog
cta_sources = [s.source for s in svc7a._selections if s.role == "cta"]
check(
    "All CTA images sourced from static_catalog",
    all(src == "static_catalog" for src in cta_sources),
    f"Sources: {cta_sources}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 8 — No image available → section returns None, no static fallback
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 8: No image available → section returns None (no static fallback)")

svc8 = build_service(
    "amazon ppc acos optimization", "Amazon Advertising",
    provider=MockImageProvider(empty=True),
)
result8 = svc8.section("How to reduce ACOS in your campaigns", 0)
check(
    "Section returns None when provider has no image (no static fallback)",
    result8 is None,
    f"Got: {result8}",
)

# CTA still works even when section returns None
cta_result8 = svc8.cta(0)
check(
    "CTA still returns a valid URL even when section is skipped",
    cta_result8 is not None and cta_result8.startswith("https://"),
    f"CTA URL: {cta_result8[:60] if cta_result8 else 'None'}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 9 — Registry persists used IDs across runs (in-memory simulation)
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 9: Registry persists used IDs across runs (in-memory simulation)")

reg9 = build_mock_registry()

svc9a = build_service(
    "amazon fba packaging prep", "FBA Prep",
    registry=reg9, provider=MockImageProvider("fba"),
)
run1_url = svc9a.section("Packaging Prep Guide amazon fba", 0)

if run1_url:
    run1_sel = next((s for s in svc9a._selections if s.role == "section"), None)
    run1_id  = run1_sel.image_id if run1_sel else None
    if run1_id:
        reg9._used_section_ids.add(run1_id)
else:
    run1_id = None

print(f"\n  Run 1 selected ID: {run1_id}")
print(f"  Registry after run 1: {sorted(reg9._used_section_ids)}")

svc9b = build_service(
    "amazon fba packaging prep", "FBA Prep",
    registry=reg9, provider=MockImageProvider("fba"),
)
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
# TEST 10 — Full simulation: PPC + AI posts, logged output
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 10: Full simulation — PPC post + AI post with validation report")
print()

# ── PPC post ──────────────────────────────────────────────────────────────────
svc_ppc = build_service(
    "amazon ppc budget management campaign strategy",
    "Amazon Advertising",
    provider=MockImageProvider("ppc"),
)
ppc_hero  = svc_ppc.hero("Complete guide to amazon ppc budget management")
ppc_sec0  = svc_ppc.section("How to set your PPC campaign budget", 0)
ppc_sec1  = svc_ppc.section("Advanced bidding strategies for sponsored products", 1)
ppc_cta0  = svc_ppc.cta(0)
ppc_cta1  = svc_ppc.cta(1)
ppc_cta2  = svc_ppc.cta(2)

rpt_ppc = svc_ppc.validation_report()
print(f"\n  [PPC POST] topic_category={rpt_ppc['topic_category']}  provider={rpt_ppc['provider']}")
for sel in rpt_ppc["selections"]:
    print(f"    {sel['role']:8s}  id={sel['id']:16s}  source={sel['source']}  "
          f"cluster={sel['visual_cluster']}")

# ── AI tools post ──────────────────────────────────────────────────────────────
svc_ai = build_service(
    "AI tools for online sellers in 2026",
    "AI Tools Automation",
    provider=MockImageProvider("ai"),
)
ai_hero  = svc_ai.hero("AI tools every amazon seller needs in 2026")
ai_sec0  = svc_ai.section("Best AI tools for product research", 0)
ai_sec1  = svc_ai.section("How to use AI for listing optimization", 1)
ai_cta0  = svc_ai.cta(0)
ai_cta1  = svc_ai.cta(1)
ai_cta2  = svc_ai.cta(2)

rpt_ai = svc_ai.validation_report()
print(f"\n  [AI POST] topic_category={rpt_ai['topic_category']}  provider={rpt_ai['provider']}")
for sel in rpt_ai["selections"]:
    print(f"    {sel['role']:8s}  id={sel['id']:16s}  source={sel['source']}  "
          f"cluster={sel['visual_cluster']}")

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
    "PPC section image IDs contain 'ppc' (topic-relevant assets)",
    all("ppc" in id_ for id_ in ppc_section_ids),
    f"IDs: {sorted(ppc_section_ids)}",
)
check(
    "AI section image IDs contain 'ai' (topic-relevant assets)",
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
    "All section/hero images NOT from static_catalog (dynamic provider only)",
    all(
        s.source != "static_catalog"
        for svc in (svc_ppc, svc_ai)
        for s in svc._selections
        if s.role in ("section", "hero")
    ),
    "All section/hero images from AI or Pexels provider",
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


# ─────────────────────────────────────────────────────────────────────────────
# TEST 11 — Module structure check
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 11: All required image_*.py modules present")

_base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "exporters")
_required_modules = [
    "image_policy",
    "image_catalog",
    "image_router",
    "image_registry",
    "image_logging",
    "image_selector",
    "image_fetcher",
    "image_prompt_generator",
    "image_provider",
    "hubspot_files",
]
_missing = [m for m in _required_modules if not os.path.exists(os.path.join(_base, f"{m}.py"))]
check(
    "All required image pipeline modules present",
    len(_missing) == 0,
    f"Missing: {_missing}" if _missing else "All present",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 12 — image_selector.py contains no HubSpot-specific imports
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 12: image_selector.py contains no HubSpot-specific imports (clean separation)")

_selector_path = os.path.join(_base, "image_selector.py")
with open(_selector_path, "r") as _f:
    _src = _f.read()

_tree = ast.parse(_src)
_hubspot_imports = [
    node for node in ast.walk(_tree)
    if isinstance(node, (ast.Import, ast.ImportFrom))
    and any(
        "hubspot" in (getattr(alias, "name", "") or "").lower()
        or "hubspot" in (getattr(node, "module", "") or "").lower()
        for alias in getattr(node, "names", [])
    )
]
check(
    "image_selector.py contains no HubSpot-specific imports",
    len(_hubspot_imports) == 0,
    f"Found imports: {[ast.dump(n) for n in _hubspot_imports]}" if _hubspot_imports else "Clean",
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
    print("\n  ✓  ALL TESTS PASSED — AI-first image pipeline is production-ready")
else:
    print(f"\n  ✗  {failed_count} TEST(S) FAILED — fix before deploying")

sys.exit(0 if failed_count == 0 else 1)
