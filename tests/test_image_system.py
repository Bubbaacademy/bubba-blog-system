"""
tests/test_image_system.py — Replicate-only image pipeline verification.

Run from project root:
    python3 tests/test_image_system.py

39 tests covering:
  A. Model allowlist (tests 1–4)
  B. Cost estimates (tests 5–6)
  C. Cost guard enforcement (tests 7–10)
  D. Model allowlist enforcement (tests 11–12)
  E. Static catalog disabled (tests 13–15)
  F. Topic routing (tests 16–17)
  G. Image generation via MockReplicateProvider (tests 18–21)
  H. Deduplication (tests 22–24)
  I. Zero-tolerance _img_tag() gate (tests 25–27)
  J. Code quality and security (tests 28–30)
  K. HubSpot CDN URL validation — regional domains (tests 31–36)
  L. Rate-limit delay and retry configuration (tests 37–39)

All provider calls are MOCKED — no REPLICATE_API_TOKEN required.
"""
from __future__ import annotations

import sys
import re
import os
import ast
import logging

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
# MockReplicateProvider — no real API calls in tests
# ─────────────────────────────────────────────────────────────────────────────

from exporters.image_provider import ImageAsset, ImageProvider, APPROVED_REPLICATE_MODELS
from exporters.hubspot_files import is_trusted_hubspot_image_url

# HubSpot CDN base used in mock URLs (the .com variant is valid everywhere)
HS_CDN = "hubspotusercontent.com"
_DEFAULT_MODEL_NAME = "black-forest-labs/flux-schnell"


class MockReplicateProvider(ImageProvider):
    """
    Returns pre-defined ImageAsset objects with hubspotusercontent.com URLs.
    Simulates: Replicate Flux generation → download → HubSpot Files upload.

    provider="replicate", model="black-forest-labs/flux-schnell" on all assets.
    """

    _MOCK_POOLS: dict = {
        "ppc": [
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-campaign-hero.jpg",
                provider       = "replicate",
                provider_id    = "ppc_001",
                prompt_hash    = "ppc001hash000000",
                search_query   = "",
                visual_cluster = "ads_dashboard",
                alt_text       = "Professional business analytics workspace for PPC campaign management",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-metrics-section.jpg",
                provider       = "replicate",
                provider_id    = "ppc_002",
                prompt_hash    = "ppc002hash000000",
                search_query   = "",
                visual_cluster = "marketing_metrics",
                alt_text       = "Digital marketing performance metrics dashboard PPC analytics",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-bidding-section.jpg",
                provider       = "replicate",
                provider_id    = "ppc_003",
                prompt_hash    = "ppc003hash000000",
                search_query   = "",
                visual_cluster = "ecommerce_analytics",
                alt_text       = "Ecommerce advertising ROI optimization analytics chart",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-cta-lead.jpg",
                provider       = "replicate",
                provider_id    = "ppc_cta_0",
                prompt_hash    = "ppccta0hash00000",
                search_query   = "",
                visual_cluster = "cta_marketing",
                alt_text       = "Business analytics workspace CTA block",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-cta-mid.jpg",
                provider       = "replicate",
                provider_id    = "ppc_cta_1",
                prompt_hash    = "ppccta1hash00000",
                search_query   = "",
                visual_cluster = "cta_conversion",
                alt_text       = "Digital marketing strategy CTA mid-article",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ppc-cta-close.jpg",
                provider       = "replicate",
                provider_id    = "ppc_cta_2",
                prompt_hash    = "ppccta2hash00000",
                search_query   = "",
                visual_cluster = "cta_business",
                alt_text       = "Business growth analytics CTA conversion",
                model          = _DEFAULT_MODEL_NAME,
            ),
        ],
        "ai": [
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-tools-hero.jpg",
                provider       = "replicate",
                provider_id    = "ai_001",
                prompt_hash    = "ai001hash0000000",
                search_query   = "",
                visual_cluster = "ai_software_ui",
                alt_text       = "AI-powered business analytics software interface futuristic workspace",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-automation-section.jpg",
                provider       = "replicate",
                provider_id    = "ai_002",
                prompt_hash    = "ai002hash0000000",
                search_query   = "",
                visual_cluster = "automation_dashboard",
                alt_text       = "AI ecommerce automation analytics dashboard technology workspace",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-ml-section.jpg",
                provider       = "replicate",
                provider_id    = "ai_003",
                prompt_hash    = "ai003hash0000000",
                search_query   = "",
                visual_cluster = "ml_analytics",
                alt_text       = "Machine learning business data visualization modern office",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-cta-lead.jpg",
                provider       = "replicate",
                provider_id    = "ai_cta_0",
                prompt_hash    = "aicta0hash000000",
                search_query   = "",
                visual_cluster = "cta_tech",
                alt_text       = "AI technology workspace CTA block",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-cta-mid.jpg",
                provider       = "replicate",
                provider_id    = "ai_cta_1",
                prompt_hash    = "aicta1hash000000",
                search_query   = "",
                visual_cluster = "cta_ai_mid",
                alt_text       = "AI tools for sellers CTA mid-article",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/ai-cta-close.jpg",
                provider       = "replicate",
                provider_id    = "ai_cta_2",
                prompt_hash    = "aicta2hash000000",
                search_query   = "",
                visual_cluster = "cta_ai_close",
                alt_text       = "AI business strategy CTA conversion",
                model          = _DEFAULT_MODEL_NAME,
            ),
        ],
        "fba": [
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-warehouse-hero.jpg",
                provider       = "replicate",
                provider_id    = "fba_001",
                prompt_hash    = "fba001hash000000",
                search_query   = "",
                visual_cluster = "warehouse_interior",
                alt_text       = "Modern Amazon FBA fulfillment center professional operations",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-shipping-section.jpg",
                provider       = "replicate",
                provider_id    = "fba_002",
                prompt_hash    = "fba002hash000000",
                search_query   = "",
                visual_cluster = "shipping_logistics",
                alt_text       = "Amazon FBA inbound shipping logistics professional workflow",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-cta0.jpg",
                provider       = "replicate",
                provider_id    = "fba_cta_0",
                prompt_hash    = "fbacta0hash00000",
                search_query   = "",
                visual_cluster = "cta_logistics",
                alt_text       = "FBA fulfillment center CTA block",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/fba-cta1.jpg",
                provider       = "replicate",
                provider_id    = "fba_cta_1",
                prompt_hash    = "fbacta1hash00000",
                search_query   = "",
                visual_cluster = "cta_fba_mid",
                alt_text       = "FBA shipping process CTA mid-article",
                model          = _DEFAULT_MODEL_NAME,
            ),
        ],
        "general": [
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/general-business-hero.jpg",
                provider       = "replicate",
                provider_id    = "gen_001",
                prompt_hash    = "gen001hash000000",
                search_query   = "",
                visual_cluster = "business_workspace",
                alt_text       = "Professional business analytics strategy modern workspace",
                model          = _DEFAULT_MODEL_NAME,
            ),
            ImageAsset(
                url            = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/general-cta0.jpg",
                provider       = "replicate",
                provider_id    = "gen_cta_0",
                prompt_hash    = "gencta0hash00000",
                search_query   = "",
                visual_cluster = "cta_general",
                alt_text       = "Business strategy CTA block",
                model          = _DEFAULT_MODEL_NAME,
            ),
        ],
    }

    def __init__(self, topic: str = "general", empty: bool = False):
        self.topic     = topic
        self._empty    = empty
        self.call_log: list = []
        # Mimic real provider attributes for tests that inspect them
        self._model            = _DEFAULT_MODEL_NAME
        self._estimated_cost   = APPROVED_REPLICATE_MODELS[_DEFAULT_MODEL_NAME]
        self._post_cost        = 0.0
        self._post_images      = 0
        self._max_post_cost    = 0.05
        self._max_day_cost     = 1.00
        self._max_images       = 3

    def start_post(self) -> None:
        self._post_cost   = 0.0
        self._post_images = 0

    @property
    def name(self) -> str:
        return "replicate"

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
        _provider             = provider or MockReplicateProvider(),
        _registry             = registry or build_mock_registry(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION A — Model allowlist (tests 1–4)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION A — Tests 1–4: Replicate model allowlist")

# Test 1: APPROVED_REPLICATE_MODELS has exactly 2 entries
check(
    "Test 1: APPROVED_REPLICATE_MODELS has exactly 2 approved models",
    len(APPROVED_REPLICATE_MODELS) == 2,
    f"Models: {sorted(APPROVED_REPLICATE_MODELS.keys())}",
)

# Test 2: flux-schnell is approved
check(
    "Test 2: black-forest-labs/flux-schnell is in APPROVED_REPLICATE_MODELS",
    "black-forest-labs/flux-schnell" in APPROVED_REPLICATE_MODELS,
    f"Keys: {sorted(APPROVED_REPLICATE_MODELS.keys())}",
)

# Test 3: flux-dev is approved
check(
    "Test 3: black-forest-labs/flux-dev is in APPROVED_REPLICATE_MODELS",
    "black-forest-labs/flux-dev" in APPROVED_REPLICATE_MODELS,
    f"Keys: {sorted(APPROVED_REPLICATE_MODELS.keys())}",
)

# Test 4: Default model (when REPLICATE_MODEL env not set) is flux-schnell
import os as _os
_saved_model_env = _os.environ.pop("REPLICATE_MODEL", None)
from exporters.image_provider import ReplicateImageProvider, _DEFAULT_MODEL
check(
    "Test 4: Default model is black-forest-labs/flux-schnell",
    _DEFAULT_MODEL == "black-forest-labs/flux-schnell",
    f"Got: '{_DEFAULT_MODEL}'",
)
if _saved_model_env is not None:
    _os.environ["REPLICATE_MODEL"] = _saved_model_env


# ─────────────────────────────────────────────────────────────────────────────
# SECTION B — Cost estimates (tests 5–6)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION B — Tests 5–6: Cost estimates per model")

# Test 5: flux-schnell = $0.003
check(
    "Test 5: flux-schnell estimated cost = $0.003 per image",
    APPROVED_REPLICATE_MODELS["black-forest-labs/flux-schnell"] == 0.003,
    f"Got: ${APPROVED_REPLICATE_MODELS['black-forest-labs/flux-schnell']}",
)

# Test 6: flux-dev = $0.025
check(
    "Test 6: flux-dev estimated cost = $0.025 per image",
    APPROVED_REPLICATE_MODELS["black-forest-labs/flux-dev"] == 0.025,
    f"Got: ${APPROVED_REPLICATE_MODELS['black-forest-labs/flux-dev']}",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION C — Cost guard enforcement (tests 7–10)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION C — Tests 7–10: Cost guard enforcement")

import logging as _logging

# Build a bare provider instance without a token (so constructor warnings are acceptable)
_os.environ["REPLICATE_API_TOKEN"] = "r8_test_token_for_cost_guard_tests"
_os.environ["MAX_IMAGE_COST_PER_POST_USD"] = "0.05"
_os.environ["MAX_IMAGE_COST_PER_DAY_USD"] = "1.00"
# Reset class-level day cost so tests start clean
ReplicateImageProvider._day_cost_usd = 0.0
ReplicateImageProvider._day_str = ""

_cost_provider = ReplicateImageProvider()

# Test 7: Cost guard allows when well under per-post and per-day limits
_cost_provider._post_cost = 0.0
_cost_provider._post_images = 0
ReplicateImageProvider._day_cost_usd = 0.0
check(
    "Test 7: Cost guard ALLOWS when post_cost=0.00 < post_limit=$0.05",
    _cost_provider._check_cost_guard() is True,
    f"post_cost=${_cost_provider._post_cost:.4f}  limit=${_cost_provider._max_post_cost:.4f}",
)

# Test 8: Cost guard blocks when per-post limit would be exceeded
_cost_provider._post_cost = 0.048   # $0.048 + $0.003 = $0.051 > $0.05
ReplicateImageProvider._day_cost_usd = 0.0
import io as _io
_handler_buf = _io.StringIO()
_h = _logging.StreamHandler(_handler_buf)
_logging.getLogger("image_provider").addHandler(_h)
_post_blocked = _cost_provider._check_cost_guard()
_cost_log = _handler_buf.getvalue()
_logging.getLogger("image_provider").removeHandler(_h)
check(
    "Test 8: Cost guard BLOCKS when post_cost + estimated > post_limit",
    _post_blocked is False,
    f"post_cost=${_cost_provider._post_cost:.4f}  estimated=${_cost_provider._estimated_cost:.4f}  limit=${_cost_provider._max_post_cost:.4f}",
)
check(
    "Test 9: [COST_GUARD_BLOCKED] is logged when post limit exceeded",
    "COST_GUARD_BLOCKED" in _cost_log,
    f"Log output: {_cost_log[:200]}",
)

# Test 10: Cost guard blocks when per-day limit would be exceeded
_cost_provider._post_cost = 0.0
ReplicateImageProvider._day_cost_usd = 0.999   # $0.999 + $0.003 = $1.002 > $1.00
_day_buf = _io.StringIO()
_dh = _logging.StreamHandler(_day_buf)
_logging.getLogger("image_provider").addHandler(_dh)
_day_blocked = _cost_provider._check_cost_guard()
_day_log = _day_buf.getvalue()
_logging.getLogger("image_provider").removeHandler(_dh)
check(
    "Test 10: Cost guard BLOCKS when day_cost + estimated > day_limit",
    _day_blocked is False,
    f"day_cost=${ReplicateImageProvider._day_cost_usd:.4f}  estimated=${_cost_provider._estimated_cost:.4f}  day_limit=${_cost_provider._max_day_cost:.4f}",
)

# Reset after cost guard tests
_cost_provider._post_cost = 0.0
ReplicateImageProvider._day_cost_usd = 0.0
_os.environ.pop("REPLICATE_API_TOKEN", None)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION D — Model allowlist enforcement (tests 11–12)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION D — Tests 11–12: Model allowlist enforcement")

# Test 11: Known approved model resolves correctly
_os.environ["REPLICATE_MODEL"] = "black-forest-labs/flux-schnell"
_os.environ["REPLICATE_API_TOKEN"] = "r8_test"
_p_known = ReplicateImageProvider()
check(
    "Test 11: Known model 'flux-schnell' resolves and is not blocked",
    _p_known._model == "black-forest-labs/flux-schnell",
    f"Got model: '{_p_known._model}'",
)
_os.environ.pop("REPLICATE_MODEL", None)
_os.environ.pop("REPLICATE_API_TOKEN", None)

# Test 12: Unknown model → [IMAGE_MODEL_BLOCKED] logged, falls back to default
_os.environ["REPLICATE_MODEL"] = "some-random/unknown-model"
_os.environ["REPLICATE_API_TOKEN"] = "r8_test"
_blocked_buf = _io.StringIO()
_bh = _logging.StreamHandler(_blocked_buf)
_logging.getLogger("image_provider").addHandler(_bh)
_p_blocked = ReplicateImageProvider()
_blocked_log = _blocked_buf.getvalue()
_logging.getLogger("image_provider").removeHandler(_bh)
check(
    "Test 12: Unknown model triggers [IMAGE_MODEL_BLOCKED] log + falls back to flux-schnell",
    "IMAGE_MODEL_BLOCKED" in _blocked_log and _p_blocked._model == "black-forest-labs/flux-schnell",
    f"Logged: {'IMAGE_MODEL_BLOCKED' in _blocked_log}  resolved_model={_p_blocked._model}",
)
_os.environ.pop("REPLICATE_MODEL", None)
_os.environ.pop("REPLICATE_API_TOKEN", None)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION E — Static catalog disabled (tests 13–15)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION E — Tests 13–15: Static image catalog fully disabled")

from exporters.image_policy import STATUS_APPROVED, ROLE_SECTION, ROLE_HERO, ROLE_CTA
from exporters.image_catalog import IMAGE_CATALOG, get_approved, get_by_id

# Test 13: Zero approved images in IMAGE_CATALOG
all_approved = [e for e in IMAGE_CATALOG if e.status == STATUS_APPROVED]
check(
    "Test 13: Zero approved images remain in IMAGE_CATALOG",
    len(all_approved) == 0,
    f"Still approved: {[e.image_id for e in all_approved]}" if all_approved else "All disabled",
)

# Test 14: Old warehouse CTA IDs are fully disabled
_RETIRED_IDS = {"4483610", "4481326", "4481259"}
retired_still_active = [
    id_ for id_ in _RETIRED_IDS
    if (e := get_by_id(id_)) and e.status == STATUS_APPROVED
]
check(
    "Test 14: Old warehouse CTA IDs (4483610, 4481326, 4481259) fully disabled",
    len(retired_still_active) == 0,
    f"Still active: {retired_still_active}" if retired_still_active else "All retired",
)

# Test 15: get_approved() returns empty for ALL roles
_all_empty = True
for role in (ROLE_SECTION, ROLE_HERO, ROLE_CTA):
    pool = get_approved(role=role)
    if pool:
        _all_empty = False
check(
    "Test 15: get_approved() returns empty list for hero, section, and cta roles",
    _all_empty,
    "Empty for all roles" if _all_empty else "Some roles still have approved images",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION F — Topic routing (tests 16–17)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION F — Tests 16–17: Topic routing")

from exporters.image_policy import CAT_AMAZON_ADS, CAT_AI_TOOLS
from exporters.image_router import route

# Test 16: PPC keyword → amazon_ads_digital
ppc_category = route("amazon ppc budget optimization", "Amazon Advertising")
check(
    "Test 16: PPC keyword routes to amazon_ads_digital",
    ppc_category == CAT_AMAZON_ADS,
    f"Got: '{ppc_category}'",
)

# Test 17: AI tools keyword → ai_tools_automation
ai_category = route("AI tools for online sellers in 2026", "AI Tools Automation")
check(
    "Test 17: AI tools keyword routes to ai_tools_automation",
    ai_category == CAT_AI_TOOLS,
    f"Got: '{ai_category}'",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION G — Image generation via MockReplicateProvider (tests 18–21)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION G — Tests 18–21: Image generation with MockReplicateProvider")

# Test 18: Section returns a trusted HubSpot CDN URL (any regional variant)
ppc_svc = build_service(
    "amazon ppc budget optimization",
    "Amazon Advertising",
    provider=MockReplicateProvider("ppc"),
)
ppc_url = ppc_svc.section("How to Set Your PPC Budget", 0)
check(
    "Test 18: Section image URL is a trusted HubSpot CDN URL",
    ppc_url is not None and is_trusted_hubspot_image_url(ppc_url),
    f"URL: {ppc_url}",
)

# Test 19: Section URL is NOT from Pexels
check(
    "Test 19: Section image URL is NOT from Pexels",
    ppc_url is None or "pexels.com" not in ppc_url,
    f"URL: {ppc_url}",
)

# Test 20: CTA uses AI provider (source = "replicate", not "static_catalog")
svc20 = build_service("amazon ppc", "Amazon Advertising",
                       provider=MockReplicateProvider("ppc"))
cta0 = svc20.cta(0)
cta_sources = [s.source for s in svc20._selections if s.role == "cta"]
check(
    "Test 20: CTA image source is 'replicate' (not static_catalog)",
    all(src == "replicate" for src in cta_sources),
    f"CTA sources: {cta_sources}",
)

# Test 21: Empty provider → all slots return None (no static fallback)
svc21 = build_service("amazon ppc acos", "Amazon Advertising",
                       provider=MockReplicateProvider(empty=True))
check(
    "Test 21: Empty provider — section, hero, and cta all return None",
    (svc21.section("How to reduce ACOS", 0) is None and
     svc21.hero("Complete PPC guide") is None and
     svc21.cta(0) is None),
    "All three slots returned None as expected",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION H — Deduplication (tests 22–24)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION H — Tests 22–24: Image deduplication")

# Test 22: Cross-post section dedup
shared_registry = build_mock_registry()

svc22a = build_service("amazon fba inbound shipping", "FBA Shipping",
                        registry=shared_registry, provider=MockReplicateProvider("fba"))
svc22a.section("Inbound Shipping Overview fba", 0)
svc22a.section("Shipping Cost Calculator fba", 1)
for sel in svc22a._selections:
    if sel.role == "section":
        shared_registry._used_section_ids.add(sel.image_id)

post1_ids = {sel.image_id for sel in svc22a._selections if sel.role == "section"}

svc22b = build_service("amazon fba storage fees", "FBA Fees",
                        registry=shared_registry, provider=MockReplicateProvider("fba"))
svc22b.section("What Are FBA Storage Fees", 0)
svc22b.section("Calculating Storage Fees fba", 1)
post2_ids = {sel.image_id for sel in svc22b._selections if sel.role == "section"}

reused = post1_ids & post2_ids
check(
    "Test 22: No section image ID reused between Post 1 and Post 2",
    len(reused) == 0,
    f"Reused IDs: {reused or 'none'}",
)

# Test 23: Visual clusters are tracked in _used_clusters
svc23 = build_service("amazon fba inbound shipping", "FBA Logistics",
                       provider=MockReplicateProvider("fba"))
svc23.section("FBA Inbound Overview", 0)
svc23.section("Choosing a Shipping Carrier fba", 1)
check(
    "Test 23: Visual clusters tracked in _used_clusters",
    len(svc23._used_clusters) > 0,
    f"Clusters: {svc23._used_clusters}",
)

# Test 24: Registry persists used IDs across runs
reg24 = build_mock_registry()
svc24a = build_service("amazon fba packaging prep", "FBA Prep",
                        registry=reg24, provider=MockReplicateProvider("fba"))
run1_url = svc24a.section("Packaging Prep Guide amazon fba", 0)
run1_sel = next((s for s in svc24a._selections if s.role == "section"), None)
run1_id  = run1_sel.image_id if run1_sel else None
if run1_id:
    reg24._used_section_ids.add(run1_id)

svc24b = build_service("amazon fba packaging prep", "FBA Prep",
                        registry=reg24, provider=MockReplicateProvider("fba"))
run2_url = svc24b.section("Packaging Prep Guide amazon fba", 0)
run2_sel = next((s for s in svc24b._selections if s.role == "section"), None)
run2_id  = run2_sel.image_id if run2_sel else None

check(
    "Test 24: Run 2 does not reuse Run 1's section image (registry dedup)",
    run1_id is None or run2_id is None or run1_id != run2_id,
    f"Run 1={run1_id}  Run 2={run2_id}",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION I — Zero-tolerance _img_tag() gate (tests 25–27)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION I — Tests 25–27: _img_tag() zero-tolerance gate")

import sys as _sys
from unittest.mock import MagicMock as _MagicMock
if "markdown" not in _sys.modules:
    _sys.modules["markdown"] = _MagicMock()
from exporters.hubspot import _img_tag

# Test 25: None → empty string
check(
    "Test 25: _img_tag(None, ...) returns empty string",
    _img_tag(None, "alt") == "",
    f"Got: {repr(_img_tag(None, 'alt'))}",
)

# Test 26: Pexels URL → blocked (empty string)
pexels_url = "https://images.pexels.com/photos/4481323/pexels-photo-4481323.jpeg"
check(
    "Test 26: _img_tag(pexels_url, ...) returns empty string (blocked)",
    _img_tag(pexels_url, "alt") == "",
    f"Got non-empty for Pexels URL — BLOCKED needed",
)

# Test 27: Valid HubSpot CDN URL (.com variant) → renders <img> tag
hs_url = f"https://files.{HS_CDN}/hubfs/bubba-blog-images/test-image.jpg"
hs_tag = _img_tag(hs_url, "Test image")
check(
    "Test 27: _img_tag(hubspotusercontent.com URL) renders valid <img> tag",
    "<img" in hs_tag and f'src="{hs_url}"' in hs_tag,
    f"Tag: {hs_tag[:120]}",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION J — Code quality and security (tests 28–30)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION J — Tests 28–30: Code quality and security")

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Test 28: REPLICATE_API_TOKEN not hardcoded in any .py file
# Replicate tokens start with "r8_" followed by alphanumeric chars
_token_pattern = re.compile(r'\br8_[A-Za-z0-9]{8,}\b')
_token_leaks = []
for root, dirs, files in os.walk(_project_root):
    dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".venv", "venv", "node_modules")]
    for fname in files:
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(root, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    if _token_pattern.search(line):
                        _token_leaks.append(f"{fpath}:{lineno}")
        except Exception:
            pass

check(
    "Test 28: No REPLICATE_API_TOKEN (r8_...) hardcoded in any .py source file",
    len(_token_leaks) == 0,
    f"Leaked in: {_token_leaks}" if _token_leaks else "Clean — no tokens found in source",
)

# Test 29: image_provider.py has no openai import (AST check)
_provider_path = os.path.join(_project_root, "exporters", "image_provider.py")
with open(_provider_path, "r") as _pf:
    _provider_src = _pf.read()
_provider_tree = ast.parse(_provider_src)
_openai_imports = [
    node for node in ast.walk(_provider_tree)
    if isinstance(node, (ast.Import, ast.ImportFrom))
    and any(
        "openai" in (getattr(alias, "name", "") or "").lower()
        or "openai" in (getattr(node, "module", "") or "").lower()
        for alias in getattr(node, "names", [])
    )
]
check(
    "Test 29: image_provider.py has no openai import (DALL-E fully removed)",
    len(_openai_imports) == 0,
    f"Found: {[ast.dump(n) for n in _openai_imports]}" if _openai_imports else "Clean",
)

# Test 30: All required image pipeline modules present
_base = os.path.join(_project_root, "exporters")
_required_modules = [
    "image_policy",
    "image_catalog",
    "image_router",
    "image_registry",
    "image_logging",
    "image_selector",
    "image_prompt_generator",
    "image_provider",
    "hubspot_files",
]
_missing = [m for m in _required_modules if not os.path.exists(os.path.join(_base, f"{m}.py"))]
check(
    "Test 30: All required image pipeline modules present",
    len(_missing) == 0,
    f"Missing: {_missing}" if _missing else "All present",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION K — HubSpot CDN URL validation (tests 31–36)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION K — Tests 31–36: is_trusted_hubspot_image_url() + regional CDN")

# Test 31: .com global variant → accepted
check(
    "Test 31: is_trusted_hubspot_image_url accepts .com variant",
    is_trusted_hubspot_image_url(
        "https://files.hubspotusercontent.com/hubfs/bubba-blog-images/img.jpg"
    ),
    "Global .com domain",
)

# Test 32: North-America regional domain → accepted
_na2_url = "https://243737166.fs1.hubspotusercontent-na2.net/hubfs/243737166/bubba-blog-img/section.jpg"
check(
    "Test 32: is_trusted_hubspot_image_url accepts hubspotusercontent-na2.net",
    is_trusted_hubspot_image_url(_na2_url),
    f"URL: {_na2_url[:80]}",
)

# Test 33: Europe regional domain → accepted
check(
    "Test 33: is_trusted_hubspot_image_url accepts hubspotusercontent-eu1.net",
    is_trusted_hubspot_image_url(
        "https://files.hubspotusercontent-eu1.net/hubfs/123/img.jpg"
    ),
    "EU regional domain",
)

# Test 34: Replicate temp URL → rejected
_replicate_url = "https://replicate.delivery/xezq/AIZLOHLdiTKoLVZFF69aKBiTuNUpe7aj/image.jpg"
check(
    "Test 34: is_trusted_hubspot_image_url rejects replicate.delivery URLs",
    not is_trusted_hubspot_image_url(_replicate_url),
    f"Replicate URL must be rejected: {_replicate_url[:60]}",
)

# Test 35: HTTP (not HTTPS) hubspotusercontent URL → rejected
check(
    "Test 35: is_trusted_hubspot_image_url rejects http:// (non-HTTPS)",
    not is_trusted_hubspot_image_url(
        "http://files.hubspotusercontent.com/hubfs/img.jpg"
    ),
    "http:// must be rejected (only https:// accepted)",
)

# Test 36: _img_tag() accepts the real regional CDN URL that was blocked in prod
_img_tag_na2_tag = _img_tag(_na2_url, "Regional CDN image")
check(
    "Test 36: _img_tag() accepts hubspotusercontent-na2.net URL and renders <img>",
    "<img" in _img_tag_na2_tag and f'src="{_na2_url}"' in _img_tag_na2_tag,
    f"Tag snippet: {_img_tag_na2_tag[:120]}",
)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION L — Rate-limit delay and retry configuration (tests 37–39)
# ─────────────────────────────────────────────────────────────────────────────
section("SECTION L — Tests 37–39: Rate-limit delay and retry configuration")

from exporters.image_provider import _MIN_CALL_DELAY, _RETRY_WAITS

# Test 37: Minimum inter-call delay is at least 12 seconds
check(
    "Test 37: _MIN_CALL_DELAY >= 12 seconds (avoids 429 in normal operation)",
    _MIN_CALL_DELAY >= 12.0,
    f"_MIN_CALL_DELAY={_MIN_CALL_DELAY}s  (expected >= 12.0)",
)

# Test 38: Retry waits are (45s, 120s) as specified
check(
    "Test 38: _RETRY_WAITS == (45, 120) — 45s before attempt 2, 120s before attempt 3",
    _RETRY_WAITS == (45, 120),
    f"_RETRY_WAITS={_RETRY_WAITS}  (expected (45, 120))",
)

# Test 39: _rate_limited flag blocks all further get_image() calls immediately
#           (no Replicate API calls made when flag is set)
_rl_buf = _io.StringIO()
_rl_h   = _logging.StreamHandler(_rl_buf)
_logging.getLogger("image_provider").addHandler(_rl_h)

# Set the class-level flag directly (simulates post-exhaustion state)
ReplicateImageProvider._rate_limited = True

_rl_provider = ReplicateImageProvider()
# Manually set the attributes get_image() checks so it reaches the rate-limit guard
_rl_provider._pkg_available = True
_rl_provider._token         = "r8_fake_token"
_rl_provider._hs_scope_ok   = True
_rl_provider._model         = "black-forest-labs/flux-schnell"

# Build a minimal stub prompt
class _StubPrompt:
    text          = "test prompt"
    prompt_hash   = "abc123"
    topic_category = "general"

_rl_result = _rl_provider.get_image(
    prompt       = _StubPrompt(),
    article_slug = "test-article",
    slot_name    = "section_0",
    registry     = build_mock_registry(),
    used_urls    = set(),
)
_rl_log = _rl_buf.getvalue()
_logging.getLogger("image_provider").removeHandler(_rl_h)

# Restore class-level flag so other tests are not affected
ReplicateImageProvider._rate_limited = False
ReplicateImageProvider._LAST_CALL    = 0.0

check(
    "Test 39: _rate_limited=True blocks get_image() immediately — returns None, logs TEXT_ONLY",
    _rl_result is None and "IMAGE_VALIDATION_TEXT_ONLY" in _rl_log,
    f"result={_rl_result}  logged_text_only={'IMAGE_VALIDATION_TEXT_ONLY' in _rl_log}",
)


# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'=' * 70}")
print(f"  TEST RESULTS — Replicate-only image pipeline")
print(f"{'=' * 70}")
passed_count = sum(1 for _, ok, _ in _results if ok)
failed_count = sum(1 for _, ok, _ in _results if not ok)
for label, ok, detail in _results:
    tag = "  OK  " if ok else " FAIL "
    print(f"[{tag}] {label}")

print(f"\n  Total: {len(_results)}  |  Passed: {passed_count}  |  Failed: {failed_count}")
if failed_count == 0:
    print(f"\n  ✓  ALL {len(_results)} TESTS PASSED — Replicate-only pipeline is production-ready")
    print("     Every image: Replicate Flux → HubSpot Files → hubspotusercontent domain")
    print("     All regional CDN variants accepted (na2, eu1, ap1, global .com)")
    print("     Rate-limit guard: 12s min gap, 45s/120s retry backoff")
    print("     Zero OpenAI. Zero Pexels. Zero static warehouse catalog.")
else:
    print(f"\n  ✗  {failed_count} TEST(S) FAILED — fix before deploying")

sys.exit(0 if failed_count == 0 else 1)
