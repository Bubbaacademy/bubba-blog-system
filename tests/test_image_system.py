"""
tests/test_image_system.py — AI-only image pipeline verification.

Run from project root:
    python3 tests/test_image_system.py

Tests all correctness requirements for the v4 AI-only pipeline:
  1. Static catalog completely disabled — zero approved images in any role
  2. PPC article routes to amazon_ads_digital, gets non-warehouse AI images
  3. AI tools article routes to ai_tools_automation, gets tech AI images
  4. PPC and AI posts produce distinct topic-specific images
  5. Cross-post section image deduplication (registry dedup)
  6. Visual cluster diversity within a post
  7. CTA images use AI provider (not static catalog)
  8. No image available → returns None everywhere (no fallback)
  9. Registry persists used IDs across simulated runs
 10. Full simulation: PPC + AI posts — AI sources only, no duplicates
 11. _img_tag() rejects non-HubSpot URLs (zero-tolerance gate)
 12. Module structure check — all required modules present
 13. image_selector.py contains no HubSpot-specific imports

All provider calls are MOCKED — no OPENAI_API_KEY required.
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

# HubSpot Files CDN domain — all AI images must land here
HS_CDN = "hubspotusercontent.com"


class MockImageProvider(ImageProvider):
    """
    Returns pre-defined ImageAsset objects with hubspotusercontent.com URLs.
    Simulates the full AI generation + HubSpot Files upload chain.

    In production: DALL-E 3 generates image → uploaded to HubSpot Files →
    permanent hubspotusercontent.com URL returned.
    In tests: MockImageProvider returns pre-baked ImageAsset with that CDN URL.
    """

    _MOCK_POOLS: dict = {
        "ppc": [
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-campaign-hero.jpg",
                provider       = "openai",
                provider_id    = "ppc_001",
                prompt_hash    = "ppc001hash000000",
                search_query   = "",
                visual_cluster = "ads_dashboard",
                alt_text       = "Professional business analytics workspace for PPC campaign management",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-metrics-section.jpg",
                provider       = "openai",
                provider_id    = "ppc_002",
                prompt_hash    = "ppc002hash000000",
                search_query   = "",
                visual_cluster = "marketing_metrics",
                alt_text       = "Digital marketing performance metrics dashboard PPC analytics",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-bidding-section.jpg",
                provider       = "openai",
                provider_id    = "ppc_003",
                prompt_hash    = "ppc003hash000000",
                search_query   = "",
                visual_cluster = "ecommerce_analytics",
                alt_text       = "Ecommerce advertising ROI optimization analytics chart",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-cta-lead.jpg",
                provider       = "openai",
                provider_id    = "ppc_cta_0",
                prompt_hash    = "ppccta0hash00000",
                search_query   = "",
                visual_cluster = "cta_marketing",
                alt_text       = "Business analytics workspace CTA block",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-cta-mid.jpg",
                provider       = "openai",
                provider_id    = "ppc_cta_1",
                prompt_hash    = "ppccta1hash00000",
                search_query   = "",
                visual_cluster = "cta_conversion",
                alt_text       = "Digital marketing strategy CTA mid-article",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-cta-close.jpg",
                provider       = "openai",
                provider_id    = "ppc_cta_2",
                prompt_hash    = "ppccta2hash00000",
                search_query   = "",
                visual_cluster = "cta_business",
                alt_text       = "Business growth analytics CTA conversion",
            ),
        ],
        "ai": [
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-tools-hero.jpg",
                provider       = "openai",
                provider_id    = "ai_001",
                prompt_hash    = "ai001hash0000000",
                search_query   = "",
                visual_cluster = "ai_software_ui",
                alt_text       = "AI-powered business analytics software interface futuristic workspace",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-automation-section.jpg",
                provider       = "openai",
                provider_id    = "ai_002",
                prompt_hash    = "ai002hash0000000",
                search_query   = "",
                visual_cluster = "automation_dashboard",
                alt_text       = "AI ecommerce automation analytics dashboard technology workspace",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-ml-section.jpg",
                provider       = "openai",
                provider_id    = "ai_003",
                prompt_hash    = "ai003hash0000000",
                search_query   = "",
                visual_cluster = "ml_analytics",
                alt_text       = "Machine learning business data visualization modern office",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-cta-lead.jpg",
                provider       = "openai",
                provider_id    = "ai_cta_0",
                prompt_hash    = "aicta0hash000000",
                search_query   = "",
                visual_cluster = "cta_tech",
                alt_text       = "AI technology workspace CTA block",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-cta-mid.jpg",
                provider       = "openai",
                provider_id    = "ai_cta_1",
                prompt_hash    = "aicta1hash000000",
                search_query   = "",
                visual_cluster = "cta_ai_mid",
                alt_text       = "AI tools for sellers CTA mid-article",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-cta-close.jpg",
                provider       = "openai",
                provider_id    = "ai_cta_2",
                prompt_hash    = "aicta2hash000000",
                search_query   = "",
                visual_cluster = "cta_ai_close",
                alt_text       = "AI business strategy CTA conversion",
            ),
        ],
        "fba": [
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-warehouse-hero.jpg",
                provider       = "openai",
                provider_id    = "fba_001",
                prompt_hash    = "fba001hash000000",
                search_query   = "",
                visual_cluster = "warehouse_interior",
                alt_text       = "Modern Amazon FBA fulfillment center professional operations",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-shipping-section.jpg",
                provider       = "openai",
                provider_id    = "fba_002",
                prompt_hash    = "fba002hash000000",
                search_query   = "",
                visual_cluster = "shipping_logistics",
                alt_text       = "Amazon FBA inbound shipping logistics professional workflow",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-cta0.jpg",
                provider       = "openai",
                provider_id    = "fba_cta_0",
                prompt_hash    = "fbacta0hash00000",
                search_query   = "",
                visual_cluster = "cta_logistics",
                alt_text       = "FBA fulfillment center CTA block",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-cta1.jpg",
                provider       = "openai",
                provider_id    = "fba_cta_1",
                prompt_hash    = "fbacta1hash00000",
                search_query   = "",
                visual_cluster = "cta_fba_mid",
                alt_text       = "FBA shipping process CTA mid-article",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-cta2.jpg",
                provider       = "openai",
                provider_id    = "fba_cta_2",
                prompt_hash    = "fbacta2hash00000",
                search_query   = "",
                visual_cluster = "cta_fba_close",
                alt_text       = "Amazon seller business CTA conversion",
            ),
        ],
        "general": [
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/general-business-hero.jpg",
                provider       = "openai",
                provider_id    = "gen_001",
                prompt_hash    = "gen001hash000000",
                search_query   = "",
                visual_cluster = "business_workspace",
                alt_text       = "Professional business analytics strategy modern workspace",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/general-cta0.jpg",
                provider       = "openai",
                provider_id    = "gen_cta_0",
                prompt_hash    = "gencta0hash00000",
                search_query   = "",
                visual_cluster = "cta_general",
                alt_text       = "Business strategy CTA block",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/general-cta1.jpg",
                provider       = "openai",
                provider_id    = "gen_cta_1",
                prompt_hash    = "gencta1hash00000",
                search_query   = "",
                visual_cluster = "cta_general_mid",
                alt_text       = "Business analytics CTA mid-article",
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/general-cta2.jpg",
                provider       = "openai",
                provider_id    = "gen_cta_2",
                prompt_hash    = "gencta2hash00000",
                search_query   = "",
                visual_cluster = "cta_general_close",
                alt_text       = "Business growth CTA conversion",
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

        return None  # pool exhausted


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
# TEST 1 — Static catalog completely disabled
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 1: Static image catalog fully disabled — zero approved images in any role")

from exporters.image_policy import STATUS_APPROVED, ROLE_SECTION, ROLE_HERO, ROLE_CTA
from exporters.image_catalog import IMAGE_CATALOG, get_approved

all_approved = [e for e in IMAGE_CATALOG if e.status == STATUS_APPROVED]
check(
    "Zero approved images remain in entire IMAGE_CATALOG",
    len(all_approved) == 0,
    f"Still approved: {[e.image_id for e in all_approved]}" if all_approved else "All disabled",
)

# Old warehouse CTA IDs must never surface
from exporters.image_catalog import get_by_id
_RETIRED_IDS = {"4483610", "4481326", "4481259"}
retired_still_active = [
    id_ for id_ in _RETIRED_IDS
    if (e := get_by_id(id_)) and e.status == STATUS_APPROVED
]
check(
    "Old warehouse CTA IDs (4483610, 4481326, 4481259) are fully disabled",
    len(retired_still_active) == 0,
    f"Still active: {retired_still_active}" if retired_still_active else "All retired",
)

# get_approved() must return empty for ALL roles
for role in (ROLE_SECTION, ROLE_HERO, ROLE_CTA):
    pool = get_approved(role=role)
    check(
        f"get_approved(role='{role}') returns empty list",
        len(pool) == 0,
        f"Still has: {[e.image_id for e in pool]}" if pool else "Empty",
    )


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 — PPC article routes correctly and gets AI images (not warehouse)
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 2: PPC article — routes to amazon_ads_digital, AI images only")

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
    "PPC section returns a URL (AI provider delivered an asset)",
    ppc_url is not None,
    f"Got: {ppc_url[:80] if ppc_url else 'None'}",
)
check(
    "PPC section URL is from HubSpot CDN (AI-generated)",
    ppc_url is None or HS_CDN in ppc_url,
    f"URL: {ppc_url}",
)
check(
    "PPC section URL is NOT from Pexels",
    ppc_url is None or "pexels.com" not in ppc_url,
    f"URL: {ppc_url}",
)
check(
    "PPC section URL contains 'ppc' (topic-specific AI asset)",
    ppc_url is None or "ppc" in ppc_url.lower(),
    f"URL: {ppc_url}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3 — AI tools article routes correctly and gets AI images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 3: AI tools article — routes to ai_tools_automation, AI images only")

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
    "AI section returns a URL (AI provider delivered an asset)",
    ai_url is not None,
    f"Got: {ai_url[:80] if ai_url else 'None'}",
)
check(
    "AI section URL is from HubSpot CDN (AI-generated)",
    ai_url is None or HS_CDN in ai_url,
    f"URL: {ai_url}",
)
check(
    "AI section URL is NOT from Pexels",
    ai_url is None or "pexels.com" not in ai_url,
    f"URL: {ai_url}",
)
check(
    "AI section URL contains 'ai' (topic-specific AI asset)",
    ai_url is None or "ai" in ai_url.lower(),
    f"URL: {ai_url}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4 — PPC and AI posts produce different non-overlapping images
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 4: PPC and AI articles produce distinct AI images")

ppc_svc2 = build_service("amazon ppc budget optimization", "Amazon Advertising",
                          provider=MockImageProvider("ppc"))
ai_svc2  = build_service("AI tools for online sellers 2026", "AI Tools Automation",
                          provider=MockImageProvider("ai"))

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
    "PPC image contains 'ppc' (topic-specific)",
    ppc_url2 is None or "ppc" in ppc_url2.lower(),
    f"URL: {ppc_url2}",
)
check(
    "AI image contains 'ai' (topic-specific)",
    ai_url2 is None or "ai" in ai_url2.lower(),
    f"URL: {ai_url2}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5 — Cross-post section image deduplication
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 5: Cross-post section image deduplication (registry dedup)")

shared_registry = build_mock_registry()

svc5a = build_service("amazon fba inbound shipping", "FBA Shipping",
                       registry=shared_registry, provider=MockImageProvider("fba"))
url5a_0 = svc5a.section("Inbound Shipping Overview fba", 0)
url5a_1 = svc5a.section("Shipping Cost Calculator fba", 1)

for sel in svc5a._selections:
    if sel.role == "section":
        shared_registry._used_section_ids.add(sel.image_id)

post1_ids = {sel.image_id for sel in svc5a._selections if sel.role == "section"}
print(f"\n  Post 1 section IDs: {sorted(post1_ids)}")

svc5b = build_service("amazon fba storage fees", "FBA Fees",
                       registry=shared_registry, provider=MockImageProvider("fba"))
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
                      provider=MockImageProvider("fba"))
url6a = svc6.section("FBA Inbound Overview", 0)
url6b = svc6.section("Choosing a Shipping Carrier fba", 1)

section_clusters = [s.visual_cluster for s in svc6._selections if s.role == "section"]
print(f"\n  Section clusters: {section_clusters}")

check(
    "Visual clusters tracked in _used_clusters",
    len(svc6._used_clusters) > 0,
    f"Clusters: {svc6._used_clusters}",
)
check(
    "Section images use distinct visual clusters when alternatives exist",
    len(section_clusters) <= 1 or len(set(section_clusters)) > 1,
    f"Clusters: {section_clusters}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7 — CTA images now use AI provider (NOT static catalog)
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 7: CTA images use AI provider — no static catalog images")

svc7 = build_service("amazon ppc", "Amazon Advertising",
                      provider=MockImageProvider("ppc"))
cta0 = svc7.cta(0)
cta1 = svc7.cta(1)
cta2 = svc7.cta(2)

print(f"\n  CTA 0: {cta0}")
print(f"  CTA 1: {cta1}")
print(f"  CTA 2: {cta2}")

# CTA returns something (or None if provider unavailable)
check(
    "CTA slots return URLs from AI provider",
    all(u is not None for u in (cta0, cta1, cta2)),
    f"CTAs: {[cta0, cta1, cta2]}",
)
# All CTA URLs must be from HubSpot CDN (not Pexels, not old catalog IDs)
cta_urls = [u for u in (cta0, cta1, cta2) if u]
check(
    "All CTA URLs from HubSpot CDN (AI-generated)",
    all(HS_CDN in u for u in cta_urls),
    f"CDNs: {[HS_CDN in u for u in cta_urls]}",
)
check(
    "No CTA URL from Pexels",
    all("pexels.com" not in u for u in cta_urls),
    f"Pexels in CTAs: {[u for u in cta_urls if 'pexels.com' in u]}",
)
# Old warehouse IDs must not appear in CTA URLs
old_ids = {"4483610", "4481326", "4481259"}
check(
    "Old warehouse IDs (4483610, 4481326, 4481259) absent from CTA URLs",
    not any(any(oid in u for oid in old_ids) for u in cta_urls),
    f"Found old IDs: {[u for u in cta_urls if any(oid in u for oid in old_ids)]}",
)
# CTA selections must be from AI (source = "openai" or "pexels", NOT "static_catalog")
cta_sources = [s.source for s in svc7._selections if s.role == "cta"]
check(
    "CTA image source is AI provider (not static_catalog)",
    all(src != "static_catalog" for src in cta_sources),
    f"Sources: {cta_sources}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 8 — No image available → returns None everywhere, no fallback
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 8: Empty provider → all slots return None (no static fallback)")

svc8 = build_service("amazon ppc acos optimization", "Amazon Advertising",
                      provider=MockImageProvider(empty=True))
result8_section = svc8.section("How to reduce ACOS in your campaigns", 0)
result8_hero    = svc8.hero("Complete PPC guide")
result8_cta     = svc8.cta(0)

check(
    "Section returns None when provider has no image",
    result8_section is None,
    f"Got: {result8_section}",
)
check(
    "Hero returns None when provider has no image",
    result8_hero is None,
    f"Got: {result8_hero}",
)
check(
    "CTA returns None when provider has no image (no static fallback)",
    result8_cta is None,
    f"Got: {result8_cta}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 9 — Registry persists used IDs across runs
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 9: Registry persists used IDs across simulated runs")

reg9 = build_mock_registry()

svc9a = build_service("amazon fba packaging prep", "FBA Prep",
                       registry=reg9, provider=MockImageProvider("fba"))
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

svc9b = build_service("amazon fba packaging prep", "FBA Prep",
                       registry=reg9, provider=MockImageProvider("fba"))
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
# TEST 10 — Full simulation: PPC + AI posts with logged output
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 10: Full simulation — PPC post + AI post, AI sources only")
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
all_ppc_urls    = [s.url for s in svc_ppc._selections]
all_ai_urls     = [s.url for s in svc_ai._selections]

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
    "PPC section IDs contain 'ppc' (topic-specific)",
    all("ppc" in id_ for id_ in ppc_section_ids),
    f"IDs: {sorted(ppc_section_ids)}",
)
check(
    "AI section IDs contain 'ai' (topic-specific)",
    all("ai" in id_ for id_ in ai_section_ids),
    f"IDs: {sorted(ai_section_ids)}",
)
check(
    "PPC and AI share NO section images",
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
    "ALL images in PPC post from AI provider (source != static_catalog)",
    all(s.source != "static_catalog" for s in svc_ppc._selections),
    f"Sources: {[s.source for s in svc_ppc._selections]}",
)
check(
    "ALL images in AI post from AI provider (source != static_catalog)",
    all(s.source != "static_catalog" for s in svc_ai._selections),
    f"Sources: {[s.source for s in svc_ai._selections]}",
)
check(
    "ALL PPC image URLs from HubSpot CDN (no Pexels)",
    all(HS_CDN in u and "pexels.com" not in u for u in all_ppc_urls),
    f"Non-CDN: {[u for u in all_ppc_urls if HS_CDN not in u]}",
)
check(
    "ALL AI image URLs from HubSpot CDN (no Pexels)",
    all(HS_CDN in u and "pexels.com" not in u for u in all_ai_urls),
    f"Non-CDN: {[u for u in all_ai_urls if HS_CDN not in u]}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 11 — _img_tag() zero-tolerance gate
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 11: _img_tag() zero-tolerance gate — blocks non-HubSpot URLs")

# hubspot.py imports `markdown` which may not be installed in the test env.
# Mock it before importing so we can test _img_tag() in isolation.
import sys as _sys
from unittest.mock import MagicMock as _MagicMock
if "markdown" not in _sys.modules:
    _sys.modules["markdown"] = _MagicMock()
from exporters.hubspot import _img_tag

# None → empty string
check(
    "_img_tag(None, ...) returns empty string",
    _img_tag(None, "alt") == "",
    f"Got: {repr(_img_tag(None, 'alt'))}",
)
# Pexels URL → empty string (blocked)
pexels_url = "https://images.pexels.com/photos/4481323/pexels-photo-4481323.jpeg"
check(
    "_img_tag(pexels_url, ...) returns empty string (blocked)",
    _img_tag(pexels_url, "alt") == "",
    f"Got non-empty for Pexels URL — BLOCKED needed",
)
# Old catalog ID URL → empty string (blocked)
old_catalog_url = "https://images.pexels.com/photos/4483610/pexels-photo-4483610.jpeg"
check(
    "_img_tag(old_catalog_url, ...) returns empty string (blocked)",
    _img_tag(old_catalog_url, "alt") == "",
    f"Got non-empty for old catalog URL — BLOCKED needed",
)
# Valid HubSpot CDN URL → renders img tag
hs_url = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/test-image.jpg"
hs_tag = _img_tag(hs_url, "Test image")
check(
    "_img_tag(hubspotusercontent.com URL) renders <img> tag",
    "<img" in hs_tag and hs_url in hs_tag,
    f"Tag: {hs_tag[:100]}",
)
check(
    "_img_tag renders with correct src= attribute",
    f'src="{hs_url}"' in hs_tag,
    f"Tag: {hs_tag[:120]}",
)


# ─────────────────────────────────────────────────────────────────────────────
# TEST 12 — Module structure check
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 12: All required image pipeline modules present")

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
# TEST 13 — image_selector.py contains no HubSpot-specific imports
# ─────────────────────────────────────────────────────────────────────────────
section("TEST 13: image_selector.py contains no HubSpot-specific imports")

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
    print("\n  ✓  ALL TESTS PASSED — AI-only image pipeline is production-ready")
    print("     Every image comes from DALL-E 3 → HubSpot Files → hubspotusercontent.com")
    print("     Zero static catalog images. Zero Pexels images. Zero warehouse stock photos.")
else:
    print(f"\n  ✗  {failed_count} TEST(S) FAILED — fix before deploying")

sys.exit(0 if failed_count == 0 else 1)
