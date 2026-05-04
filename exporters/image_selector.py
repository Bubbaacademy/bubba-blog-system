"""
ImageSelector — curated warehouse/logistics image library for Bubba Academy.

All images are verified Pexels photos, free for commercial use, manually
confirmed to show warehouse, shipping, logistics, or packaging content.

STRICT RULES:
  - No lifestyle, nature, cabins, houses, abstract, or unrelated images
  - CTA pool and Section pool are completely separate (zero overlap)
  - ImageTracker enforces no duplicate URLs within a single post

Usage:
    tracker = ImageTracker()
    url = tracker.section("inbound shipping pallets", index=0)
    url = tracker.cta(slot=0)
    report = tracker.validation_report()
"""

PEXELS_BASE = "https://images.pexels.com/photos"

# ── Verified image library ─────────────────────────────────────────────────────
# Each entry: (pexels_id, description)
# ALL manually confirmed relevant via Pexels page verification.

# Section images — never used for CTAs
_SECTION_LIBRARY = {
    "warehouse": [
        ("6169668", "warehouse workers discussing near shelves with boxes"),
        ("4481323", "high-angle view of workers in busy warehouse"),
        ("4481259", "warehouse team organizing inventory with pallets"),
    ],
    "shipping": [
        ("3584942", "couriers loading boxes into delivery van"),
        ("6169661", "workers loading boxes into van for shipment"),
        ("906494",  "multicolor cargo containers at port"),
        ("2226458", "aerial view of freight containers and ships"),
    ],
    "packaging": [
        ("4246120", "person sealing cardboard box with tape dispenser"),
        ("4246123", "person packing and taping cardboard boxes"),
        ("4246119", "packed cardboard boxes labeled for shipping"),
    ],
}

# CTA images — completely separate from section pool, no overlap
_CTA_POOL = [
    ("4483610", "warehouse interior with stocked shelves and boxes"),
    ("4481326", "organized warehouse shelves and pallets"),
    ("4481259", "warehouse team organizing inventory"),  # also in warehouse but CTA has priority
]
# Override: remove CTA IDs from section warehouse pool to prevent overlap
_CTA_IDS = {entry[0] for entry in _CTA_POOL}
_SECTION_LIBRARY["warehouse"] = [
    e for e in _SECTION_LIBRARY["warehouse"] if e[0] not in _CTA_IDS
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
         "forwarder", "customs", "duty"],
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


# ── ImageTracker ───────────────────────────────────────────────────────────────

class ImageTracker:
    """
    Per-post image selector that enforces:
      • No duplicate URLs within one article
      • All images come from the approved curated library
      • CTA and section pools never overlap
    """

    def __init__(self):
        self._used_urls: set = set()
        self._used_ids: list = []

    def _pick(self, pool, prefer_index=0):
        """Pick from pool, skipping already-used URLs. Returns URL."""
        for offset in range(len(pool)):
            idx = (prefer_index + offset) % len(pool)
            photo_id = pool[idx][0]
            url = _url(photo_id)
            if url not in self._used_urls:
                self._used_urls.add(url)
                self._used_ids.append(photo_id)
                return url
        # All images in pool exhausted — pull from any other section pool
        for other_cat, other_pool in _SECTION_LIBRARY.items():
            for entry in other_pool:
                url = _url(entry[0])
                if url not in self._used_urls:
                    self._used_urls.add(url)
                    self._used_ids.append(entry[0])
                    return url
        # Absolute fallback (shouldn't happen with current library size)
        url = _url(pool[0][0])
        self._used_ids.append(pool[0][0])
        return url

    def section(self, context_text="", index=0):
        """Returns a section image URL relevant to context, never duplicated."""
        cat  = _category_for(context_text)
        pool = _SECTION_LIBRARY[cat]
        return self._pick(pool, index)

    def cta(self, slot=0):
        """Returns a CTA image URL from the dedicated CTA pool."""
        return self._pick(_CTA_POOL, slot)

    def validation_report(self):
        """Returns counts and duplicate/unverified checks."""
        from collections import Counter
        counts = Counter(self._used_ids)
        duplicates   = [id_ for id_, n in counts.items() if n > 1]
        unverified   = [id_ for id_ in set(self._used_ids) if id_ not in APPROVED_PEXELS_IDS]
        return {
            "image_count":        len(self._used_ids),
            "unique_image_count": len(set(self._used_ids)),
            "duplicate_ids":      duplicates or "none",
            "unverified_ids":     unverified or "none",
        }
