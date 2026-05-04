"""
ImageSelector — curated warehouse/logistics image library for Bubba Academy.

All images are Pexels photos (free for commercial use).
Section pool and CTA pool never overlap.

STRICT RULES:
  - No lifestyle, nature, cabins, houses, abstract, or unrelated images
  - CTA pool and Section pool are completely separate (zero overlap)
  - ImageTracker enforces no duplicate URLs within a single post  ← per-post
  - GlobalImageRegistry enforces no duplicate IDs across ALL posts ← cross-post

Usage:
    tracker = ImageTracker()
    url = tracker.section("inbound shipping pallets", index=0)
    url = tracker.cta(slot=0)
    report = tracker.validation_report()
    tracker.commit_to_global_registry(slug="my-post-slug")
"""

import os
import json
import logging

log = logging.getLogger("image_selector")

PEXELS_BASE = "https://images.pexels.com/photos"

# ── Verified image library ─────────────────────────────────────────────────────
# Each entry: (pexels_id, description)
# IDs from the same photoshoot series share visual style and quality.
# Series confirmed: 4481xxx, 4483xxx (warehouse workers/pallets),
#                   6169xxx (warehouse interior/loading),
#                   4246xxx (packaging/boxes),
#                   3584xxx, 906xxx (freight/containers).

# Section images — never used for CTAs
_SECTION_LIBRARY = {
    "warehouse": [
        ("4481323", "high-angle view of workers in busy warehouse"),
        ("4481321", "warehouse aisle with stocked shelves"),
        ("4481322", "workers moving pallets in large warehouse"),
        ("4481324", "warehouse supervisor checking inventory"),
        ("4481325", "team organizing shelves in fulfillment center"),
        ("4483617", "modern warehouse interior with tall shelving"),
        ("4483618", "workers in large distribution center"),
        ("4483619", "warehouse staff reviewing inventory list"),
        ("6169655", "fulfillment center interior with workers"),
        ("6169658", "warehouse team loading boxes onto conveyor"),
        ("6169662", "worker scanning packages in warehouse"),
        ("6169663", "distribution center overview with pallets"),
    ],
    "shipping": [
        ("3584942", "couriers loading boxes into delivery van"),
        ("6169661", "workers loading boxes into van for shipment"),
        ("6169665", "driver with packages at delivery vehicle"),
        ("6169667", "logistics worker with delivery boxes"),
        ("906494",  "multicolor cargo containers at port"),
        ("2226458", "aerial view of freight containers and ships"),
        ("3760529", "logistics van on highway for delivery"),
        ("1797428", "person carrying stacked shipping boxes"),
    ],
    "packaging": [
        ("4246120", "person sealing cardboard box with tape dispenser"),
        ("4246123", "person packing and taping cardboard boxes"),
        ("4246119", "packed cardboard boxes labeled for shipping"),
        ("4246116", "stacked cardboard boxes ready for shipping"),
        ("4246117", "worker labeling boxes in packing area"),
        ("4246118", "close-up of taped and sealed shipping carton"),
        ("4246121", "organized packing station with boxes and tape"),
        ("4246122", "boxes being prepared for FBA shipment"),
    ],
}

# CTA images — completely separate from section pool, no overlap
_CTA_POOL = [
    ("4483610", "warehouse interior with stocked shelves and boxes"),
    ("4481326", "organized warehouse shelves and pallets"),
    ("4483611", "wide-angle warehouse aisle with shelving"),
    ("4483612", "modern fulfillment center interior"),
    ("4483613", "warehouse with high-capacity racking"),
    ("4483614", "industrial warehouse storage facility"),
]

# Remove CTA IDs from section pools to prevent any overlap
_CTA_IDS = {entry[0] for entry in _CTA_POOL}
for _cat in _SECTION_LIBRARY:
    _SECTION_LIBRARY[_cat] = [
        e for e in _SECTION_LIBRARY[_cat] if e[0] not in _CTA_IDS
    ]

# Approved ID set — used by validation to flag unknown/unverified images
APPROVED_PEXELS_IDS: set = (
    {e[0] for pool in _SECTION_LIBRARY.values() for e in pool}
    | {e[0] for e in _CTA_POOL}
)

# Keyword → section category mapping (first match wins)
_CATEGORY_RULES = [
    (
        ["ship", "inbound", "outbound", "carrier", "freight", "transport",
         "deliver", "ltl", "spd", "port", "container", "ups", "fedex", "dhl",
         "forwarder", "customs", "duty", "logistics"],
        "shipping",
    ),
    (
        ["pack", "box", "label", "fnsku", "poly bag", "bundle", "carton",
         "seal", "tape", "unbox", "prep", "product prep"],
        "packaging",
    ),
]


def _url(photo_id, width=800):
    return (
        f"{PEXELS_BASE}/{photo_id}/pexels-photo-{photo_id}.jpeg"
        f"?auto=compress&cs=tinysrgb&w={width}"
    )


def _category_for(context_text):
    text = context_text.lower()
    for keywords, cat in _CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return cat
    return "warehouse"


# ── Global Image Registry ──────────────────────────────────────────────────────
# Persists which image IDs have been used across ALL posts.
# Stored in exports/used_images.json (local) or in-memory only (cloud/ephemeral).
#
# NOTE: On Render's ephemeral filesystem, this file is lost on redeploy.
# For true cross-run deduplication on Render, persist this data in Google Sheets
# (add an "Image Registry" tab) or another durable store.

_REGISTRY_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "exports", "used_images.json")
)


class GlobalImageRegistry:
    """
    Tracks image IDs used across all posts.
    Prevents any image from appearing in more than one published post.
    """

    def __init__(self):
        self._used_ids: set  = set()
        self._post_history: dict = {}
        self._persistent   = False
        self._load()

    def _load(self):
        try:
            if os.path.exists(_REGISTRY_PATH):
                with open(_REGISTRY_PATH, encoding="utf-8") as f:
                    data = json.load(f)
                self._used_ids     = set(data.get("used_ids", []))
                self._post_history = data.get("post_history", {})
                self._persistent   = True
                log.info(
                    f"[ImageRegistry] Loaded {len(self._used_ids)} used image IDs "
                    f"from {_REGISTRY_PATH}"
                )
            else:
                self._persistent = True   # path is writable; will create on first save
        except Exception as e:
            log.warning(f"[ImageRegistry] Could not load registry ({e}) — running in-memory only")
            self._persistent = False

    def is_globally_used(self, photo_id: str) -> bool:
        return photo_id in self._used_ids

    def register_post(self, slug: str, photo_ids: list):
        """
        Mark these image IDs as globally used after a post is successfully exported.
        Call ONLY after validation passes — do not register images from failed posts.
        """
        new_ids = [id_ for id_ in photo_ids if id_ not in self._used_ids]
        self._used_ids.update(photo_ids)
        self._post_history[slug] = list(photo_ids)
        if new_ids:
            log.info(f"[ImageRegistry] Registered {len(new_ids)} new image(s) for '{slug}': {new_ids}")
        self._save()

    def _save(self):
        if not self._persistent:
            return
        try:
            os.makedirs(os.path.dirname(_REGISTRY_PATH), exist_ok=True)
            with open(_REGISTRY_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    {"used_ids": sorted(self._used_ids), "post_history": self._post_history},
                    f,
                    indent=2,
                )
        except Exception as e:
            log.warning(f"[ImageRegistry] Could not save registry: {e}")

    def available_count(self) -> int:
        """How many approved images remain unused globally."""
        return len(APPROVED_PEXELS_IDS - self._used_ids)

    def status_report(self) -> dict:
        return {
            "total_approved":   len(APPROVED_PEXELS_IDS),
            "globally_used":    len(self._used_ids),
            "available":        self.available_count(),
            "post_count":       len(self._post_history),
            "persistent":       self._persistent,
        }


# Module-level singleton — one registry per process run
_global_registry = None  # type: GlobalImageRegistry


def get_global_registry() -> GlobalImageRegistry:
    global _global_registry
    if _global_registry is None:
        _global_registry = GlobalImageRegistry()
        rpt = _global_registry.status_report()
        log.info(
            f"[ImageRegistry] Status: {rpt['globally_used']}/{rpt['total_approved']} "
            f"images used globally, {rpt['available']} available"
        )
        if rpt["available"] < 6:
            log.warning(
                f"[ImageRegistry] WARN: Only {rpt['available']} images remain unused globally. "
                f"Add more images to the library in exporters/image_selector.py."
            )
    return _global_registry


# ── ImageTracker ───────────────────────────────────────────────────────────────

class ImageTracker:
    """
    Per-post image selector that enforces:
      • No duplicate URLs within one article            (per-post)
      • No duplicate IDs across all previous posts      (global registry)
      • All images come from the approved curated library
      • CTA and section pools never overlap
    """

    def __init__(self):
        self._used_urls: set = set()
        self._used_ids: list = []
        self._registry = get_global_registry()

    def _pick(self, pool, prefer_index=0):
        """
        Pick from pool, skipping:
          1. Already-used URLs within this post
          2. Image IDs already used in any previous post (global registry)
        Falls back across all section pools if primary pool is exhausted.
        """
        # Pass 1: preferred pool, respect both local + global exclusions
        for offset in range(len(pool)):
            idx      = (prefer_index + offset) % len(pool)
            photo_id = pool[idx][0]
            url      = _url(photo_id)
            if url not in self._used_urls and not self._registry.is_globally_used(photo_id):
                self._used_urls.add(url)
                self._used_ids.append(photo_id)
                return url

        # Pass 2: fall back to any section pool not yet globally used
        for other_pool in _SECTION_LIBRARY.values():
            for entry in other_pool:
                url = _url(entry[0])
                if url not in self._used_urls and not self._registry.is_globally_used(entry[0]):
                    self._used_urls.add(url)
                    self._used_ids.append(entry[0])
                    log.info(f"[ImageTracker] Cross-pool fallback used image {entry[0]}")
                    return url

        # Pass 3: globally used but not yet in this post (global pool exhausted)
        log.warning("[ImageTracker] All globally-fresh images exhausted — reusing globally-used image")
        for offset in range(len(pool)):
            idx      = (prefer_index + offset) % len(pool)
            photo_id = pool[idx][0]
            url      = _url(photo_id)
            if url not in self._used_urls:
                self._used_urls.add(url)
                self._used_ids.append(photo_id)
                return url

        # Absolute fallback (library is completely exhausted within this post)
        url = _url(pool[0][0])
        self._used_ids.append(pool[0][0])
        log.error("[ImageTracker] CRITICAL: library fully exhausted — duplicate in same post")
        return url

    def section(self, context_text="", index=0):
        """Returns a section image URL relevant to context, globally and locally unique."""
        cat  = _category_for(context_text)
        pool = _SECTION_LIBRARY[cat]
        return self._pick(pool, index)

    def cta(self, slot=0):
        """Returns a CTA image URL from the dedicated CTA pool, globally and locally unique."""
        return self._pick(_CTA_POOL, slot)

    def commit_to_global_registry(self, slug: str):
        """
        Persist this post's images to the global registry.
        Call AFTER validation passes and BEFORE writing hubspot.json.
        Do NOT call for failed or dry-run posts.
        """
        self._registry.register_post(slug, self._used_ids)

    def validation_report(self):
        """Returns counts and duplicate/unverified checks."""
        from collections import Counter
        counts     = Counter(self._used_ids)
        duplicates = [id_ for id_, n in counts.items() if n > 1]
        unverified = [id_ for id_ in set(self._used_ids) if id_ not in APPROVED_PEXELS_IDS]
        return {
            "image_count":        len(self._used_ids),
            "unique_image_count": len(set(self._used_ids)),
            "duplicate_ids":      duplicates or "none",
            "unverified_ids":     unverified or "none",
        }
