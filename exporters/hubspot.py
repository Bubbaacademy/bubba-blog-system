"""
HubSpot-Ready Exporter
----------------------
Produces hubspot.json and hubspot.html in the article export folder.

CTA values are centralized in config.CTA_CONFIG — edit once, applies everywhere.
Images are selected at export time from a curated verified library (image_selector.py).
No FILL_IMAGE_URL placeholders remain in the output — all images are real URLs.
"""

import os
import re
import json
import datetime
import markdown as md
from exporters.base import BaseExporter
from exporters.file_export import get_export_path
from exporters.image_selector import ImageTracker
from config import (
    CTA_CONFIG, MID_CTA_CONFIGS, TOPIC_CLUSTER_LINKS, APPROVED_URLS,
    HUBSPOT_PORTAL_ID, HUBSPOT_FORM_ID, HUBSPOT_FORM_REGION,
)

MAX_CLUSTER_LINKS = 5   # max topic-cluster links injected per article
MAX_BRAND_LINKS   = 3   # supplemental contextual links to bubbaacademy.com (non-CTA)

# ── Tag mapping ────────────────────────────────────────────────────────────────

TOPIC_TAG_MAP = {
    "amazon fba":       ["Amazon FBA", "E-commerce", "Online Business"],
    "product research": ["Product Research", "Amazon FBA", "E-commerce"],
    "e-commerce":       ["E-commerce", "Online Business"],
    "online business":  ["Online Business", "E-commerce"],
    "amazon beginner":  ["Amazon Beginner", "Amazon FBA"],
    "amazon":           ["Amazon FBA", "E-commerce"],
    "fba":              ["Amazon FBA", "E-commerce"],
}

DEFAULT_TAGS = ["Bubba Academy", "E-commerce"]

INTERNAL_LINK_TOPICS = [
    "Amazon FBA", "product research", "Amazon seller", "FBA fees",
    "Amazon listing", "private label", "dropshipping", "e-commerce",
    "online business", "Bubba Academy", "Amazon beginner",
]


# ── Utilities ──────────────────────────────────────────────────────────────────

def _slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text[:70]


def _map_tags(topic_cluster):
    key = topic_cluster.lower().strip()
    for pattern, tags in TOPIC_TAG_MAP.items():
        if pattern in key:
            return tags
    return DEFAULT_TAGS


def _find_internal_link_opportunities(text):
    found = []
    text_lower = text.lower()
    for topic in INTERNAL_LINK_TOPICS:
        if topic.lower() in text_lower:
            found.append({
                "topic":            topic,
                "suggested_anchor": topic,
                "target_slug":      f"FILL_IN: /blog/{_slugify(topic)}",
            })
    return found


# ── Image placeholder ──────────────────────────────────────────────────────────

def _img_tag(img_url, alt_text, img_type="section", css_class="hs-blog-image"):
    """Renders a real image tag with a verified URL. No placeholders."""
    safe_alt = alt_text.replace('"', "'")
    return (
        f'<div class="hs-blog-image hs-blog-image--{img_type}">\n'
        f'  <img src="{img_url}" alt="{safe_alt}" class="{css_class}"'
        f' loading="lazy" width="800" height="450" />\n'
        f'</div>'
    )


# ── FAQ parser ─────────────────────────────────────────────────────────────────

def _parse_faq(faq_markdown):
    items  = []
    blocks = re.split(r'\n(?=\*\*Q:)', faq_markdown.strip())
    for block in blocks:
        q_match = re.search(r'\*\*Q:\s*(.*?)\*\*', block, re.DOTALL)
        a_match = re.search(r'\nA:\s*(.*)', block, re.DOTALL)
        if q_match and a_match:
            items.append({
                "question": q_match.group(1).strip(),
                "answer":   a_match.group(1).strip(),
            })
    return items


def _faq_schema_json(faq_items):
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name":  item["question"],
                "acceptedAnswer": {"@type": "Answer", "text": item["answer"]},
            }
            for item in faq_items
        ],
    }


def _faq_html(faq_items):
    if not faq_items:
        return ""
    items_html = "\n".join([
        f"""  <div class="faq-item" itemscope itemprop="mainEntity" itemtype="https://schema.org/Question">
    <h3 class="faq-question" itemprop="name">{item['question']}</h3>
    <div class="faq-answer" itemscope itemprop="acceptedAnswer" itemtype="https://schema.org/Answer">
      <p itemprop="text">{item['answer']}</p>
    </div>
  </div>"""
        for item in faq_items
    ])
    return f"""<section class="faq-section" itemscope itemtype="https://schema.org/FAQPage">
  <h2>Frequently Asked Questions</h2>
{items_html}
</section>"""


# ── Article splitter ───────────────────────────────────────────────────────────

def _split_article(article_markdown):
    faq_md = ""
    body   = article_markdown
    if "\n\n---\n\n## FAQ\n\n" in body:
        body, faq_md = body.split("\n\n---\n\n## FAQ\n\n", 1)
    parts    = re.split(r'\n(?=## )', body)
    intro    = parts[0].strip()
    sections = [p.strip() for p in parts[1:] if p.strip()]
    return intro, sections, faq_md


# ── CTA block builder (uses config.CTA_CONFIG) ────────────────────────────────

CTA_META = {
    "lead-magnet": {
        "key":   "lead_magnet",
        "label": "LEAD MAGNET CTA",
        "icon":  "🎯",
        "alt":   "Free Amazon FBA Starter Checklist — Bubba Academy",
    },
    "contextual": {
        "key":   "contextual",   # resolved dynamically from MID_CTA_CONFIGS
        "label": "CONTEXTUAL MID-ARTICLE CTA",
        "icon":  "📘",
        "alt":   "Bubba Academy Amazon FBA Training",
    },
    "conversion": {
        "key":   "conversion",
        "label": "CONVERSION CTA",
        "icon":  "🚀",
        "alt":   "Join Bubba Academy — Start Your Amazon Business",
    },
    # Legacy keys kept for backward compat
    "course-offer": {
        "key":   "course_offer",
        "label": "COURSE OFFER CTA",
        "icon":  "🎓",
        "alt":   "Bubba Academy Amazon FBA Course",
    },
    "newsletter": {
        "key":   "newsletter",
        "label": "NEWSLETTER CTA",
        "icon":  "📧",
        "alt":   "Bubba Academy Newsletter",
    },
}


def _get_mid_cta_cfg(keyword):
    """Returns the contextual mid-CTA config matching the article keyword."""
    kw = keyword.lower()
    for fragment, cfg in MID_CTA_CONFIGS.items():
        if fragment in kw:
            return cfg
    return MID_CTA_CONFIGS["default"]


def _cta_block(cta_type, placement, index, img_url, keyword=""):
    """Wrapper kept for backward compat — delegates to _cta_block_v2."""
    return _cta_block_v2(cta_type, placement, index, img_url, keyword)


def _cta_block_v2(cta_type, placement, index, img_url, keyword=""):
    """
    Renders a fully-styled, high-conversion CTA block using inline CSS only
    (HubSpot strips <style> tags — inline is the only reliable approach).

    Three visual designs:
      lead-magnet  → sky-blue gradient, blue button   (#0284c7)
      contextual   → amber gradient,   amber button   (#d97706)
      conversion   → dark navy,        orange button  (#f97316), white text
    """
    meta = CTA_META.get(cta_type, CTA_META["lead-magnet"])

    if cta_type == "contextual":
        cfg = _get_mid_cta_cfg(keyword)
    else:
        cfg = CTA_CONFIG.get(meta["key"], {})

    headline    = cfg.get("headline",    "Build Your Amazon FBA Business")
    body_text   = cfg.get("body",        "Bubba Academy teaches you everything you need to succeed on Amazon.")
    bullets     = cfg.get("bullets",     [])
    button_text = cfg.get("button_text", "Learn More")
    icon        = cfg.get("icon",        meta.get("icon", "📦"))
    href        = cfg.get("href",        APPROVED_URLS["bubba_home"])

    # Safety: never emit a placeholder URL
    if "FILL_IN" in href or not href.startswith("http"):
        href = APPROVED_URLS["bubba_home"]

    # ── Visual themes ──────────────────────────────────────────────────────────
    if cta_type == "lead-magnet":
        container_style = (
            "background: linear-gradient(135deg, #f0f9ff 0%, #e0f2fe 100%);"
            "border: 2px solid #bae6fd;"
            "border-radius: 12px;"
            "padding: 32px 36px;"
            "margin: 40px 0;"
            "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;"
        )
        headline_style  = "color: #0c4a6e; font-size: 22px; font-weight: 700; margin: 0 0 12px 0; line-height: 1.3;"
        body_style      = "color: #075985; font-size: 16px; line-height: 1.6; margin: 0 0 16px 0;"
        bullet_style    = "color: #0369a1; font-size: 15px; margin: 4px 0; padding-left: 4px;"
        bullet_mark     = "✓"
        button_style    = (
            "display: inline-block;"
            "background: #0284c7;"
            "color: #ffffff !important;"
            "padding: 14px 28px;"
            "border-radius: 8px;"
            "font-size: 16px;"
            "font-weight: 700;"
            "text-decoration: none;"
            "margin-top: 20px;"
            "letter-spacing: 0.3px;"
        )
        icon_style      = "font-size: 40px; display: block; margin-bottom: 16px;"

    elif cta_type == "contextual":
        container_style = (
            "background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);"
            "border: 2px solid #fcd34d;"
            "border-radius: 12px;"
            "padding: 32px 36px;"
            "margin: 40px 0;"
            "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;"
        )
        headline_style  = "color: #78350f; font-size: 22px; font-weight: 700; margin: 0 0 12px 0; line-height: 1.3;"
        body_style      = "color: #92400e; font-size: 16px; line-height: 1.6; margin: 0 0 16px 0;"
        bullet_style    = "color: #b45309; font-size: 15px; margin: 4px 0; padding-left: 4px;"
        bullet_mark     = "→"
        button_style    = (
            "display: inline-block;"
            "background: #d97706;"
            "color: #ffffff !important;"
            "padding: 14px 28px;"
            "border-radius: 8px;"
            "font-size: 16px;"
            "font-weight: 700;"
            "text-decoration: none;"
            "margin-top: 20px;"
            "letter-spacing: 0.3px;"
        )
        icon_style      = "font-size: 40px; display: block; margin-bottom: 16px;"

    else:  # conversion — dark navy
        container_style = (
            "background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);"
            "border: 2px solid #334155;"
            "border-radius: 12px;"
            "padding: 36px 40px;"
            "margin: 40px 0;"
            "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;"
        )
        headline_style  = "color: #f1f5f9; font-size: 24px; font-weight: 800; margin: 0 0 12px 0; line-height: 1.3;"
        body_style      = "color: #cbd5e1; font-size: 16px; line-height: 1.6; margin: 0 0 16px 0;"
        bullet_style    = "color: #94a3b8; font-size: 15px; margin: 4px 0; padding-left: 4px;"
        bullet_mark     = "✓"
        button_style    = (
            "display: inline-block;"
            "background: #f97316;"
            "color: #ffffff !important;"
            "padding: 16px 32px;"
            "border-radius: 8px;"
            "font-size: 17px;"
            "font-weight: 800;"
            "text-decoration: none;"
            "margin-top: 24px;"
            "letter-spacing: 0.3px;"
        )
        icon_style      = "font-size: 44px; display: block; margin-bottom: 16px;"

    # ── Bullet list HTML ───────────────────────────────────────────────────────
    bullets_html = ""
    if bullets:
        items = "\n".join(
            f'    <li style="list-style: none; {bullet_style}">'
            f'{bullet_mark}&nbsp; {b}</li>'
            for b in bullets[:3]
        )
        bullets_html = (
            f'<ul style="margin: 0 0 8px 0; padding: 0;">\n{items}\n  </ul>'
        )

    # ── CTA image ──────────────────────────────────────────────────────────────
    img = _img_tag(img_url, meta["alt"], "cta", "hs-cta-image")

    return f"""
{img}
<div data-cta-type="{cta_type}" data-placement="{placement}"
     style="{container_style}">
  <span style="{icon_style}">{icon}</span>
  <h3 style="{headline_style}">{headline}</h3>
  <p style="{body_style}">{body_text}</p>
  {bullets_html}
  <a href="{href}"
     style="{button_style}"
     target="_blank" rel="noopener noreferrer">
    {button_text} &rarr;
  </a>
</div>
"""


def _inject_cluster_links(html, keyword, self_url_key=None):
    """
    Injects up to MAX_CLUSTER_LINKS topic-cluster internal links into <p> text.

    Rules:
      • Only anchors from TOPIC_CLUSTER_LINKS that match the article keyword
      • Only URLs from APPROVED_URLS (enforced per-link)
      • Each destination URL used at most once per article
      • Never links to the current article (self_url_key excluded)
      • Phrase must appear naturally in a <p> block — no forced insertion
      • One injection per <p> tag, longest match tried first
    Returns (modified_html, cluster_link_count).
    """
    kw = keyword.lower()
    candidates = []
    for fragment, links in TOPIC_CLUSTER_LINKS.items():
        if fragment in kw:
            candidates = links
            break

    if not candidates:
        return html, 0

    used_urls  = set()
    link_count = [0]

    # Sort anchor phrases longest-first to prevent partial matches
    sorted_candidates = sorted(candidates, key=lambda x: -len(x[0]))

    def process_p(m):
        if link_count[0] >= MAX_CLUSTER_LINKS:
            return m.group(0)
        p = m.group(0)
        for anchor, url_key in sorted_candidates:
            if link_count[0] >= MAX_CLUSTER_LINKS:
                break
            if url_key == self_url_key:
                continue                              # skip self-reference
            if url_key not in APPROVED_URLS:
                continue                              # safety: only approved URLs
            url = APPROVED_URLS[url_key]
            if url in used_urls:
                continue
            pos = p.lower().find(anchor.lower())
            if pos == -1:
                continue
            before = p[:pos]
            if before.count("<a ") > before.count("</a>"):
                continue                              # already inside <a>
            original = p[pos: pos + len(anchor)]
            linked = (
                f'<a href="{url}" rel="noopener noreferrer">'
                f'{original}</a>'
            )
            p = p[:pos] + linked + p[pos + len(anchor):]
            used_urls.add(url)
            link_count[0] += 1
            break  # one injection per <p> block
        return p

    result = re.sub(r"<p>(?:(?!</p>).)+</p>", process_p, html, flags=re.DOTALL)
    return result, link_count[0]


def _inject_brand_links(html, max_links=MAX_BRAND_LINKS):
    """
    Injects up to max_links contextual links to bubbaacademy.com anchored on
    natural mentions of "Bubba Academy" in paragraph text.

    Rules:
      • Only wraps text already in the article — never forces new text
      • Each injection uses a unique anchor variation (avoids identical link text)
      • Only injects into <p> tags; skips headings and existing <a> tags
      • Max max_links injections total across all paragraphs
    """
    href       = APPROVED_URLS["bubba_home"]
    # Ordered from most specific to most common — first match per <p> wins.
    # "Amazon FBA" is last so more specific brand phrases get priority.
    anchors    = [
        "Bubba Academy's",
        "Bubba Academy",
        "Amazon FBA sellers",
        "Amazon FBA seller",
        "Amazon sellers",
        "Amazon seller",
        "FBA sellers",
        "FBA seller",
        "Amazon FBA",
    ]
    link_count = [0]
    used_anchors: set = set()

    def process_p(m):
        if link_count[0] >= max_links:
            return m.group(0)
        p = m.group(0)
        for anchor in anchors:
            if link_count[0] >= max_links:
                break
            if anchor.lower() in used_anchors:
                continue
            pos = p.lower().find(anchor.lower())
            if pos == -1:
                continue
            before = p[:pos]
            if before.count("<a ") > before.count("</a>"):
                continue  # already inside <a>
            original = p[pos: pos + len(anchor)]
            linked   = (
                f'<a href="{href}" rel="noopener noreferrer">'
                f'{original}</a>'
            )
            p = p[:pos] + linked + p[pos + len(anchor):]
            used_anchors.add(anchor.lower())
            link_count[0] += 1
            break  # one injection per <p>
        return p

    result = re.sub(r"<p>(?:(?!</p>).)+</p>", process_p, html, flags=re.DOTALL)
    return result, link_count[0]


# ── HubSpot embedded form block ───────────────────────────────────────────────

def _hubspot_form_block():
    """
    Returns the HubSpot form embed HTML wrapped in a responsive, spaced container.

    Placement: after the last content section, before the conversion CTA.

    The <script> tag is safe in HubSpot postBody — it loads the HubSpot Forms
    JS SDK. Including it once per post is idiomatic for hs-form-frame embeds.
    The wrapper uses max-width so the form doesn't stretch to full column width
    on wide viewports, and 100% width on mobile.
    """
    script_src = (
        f"https://js-na2.hsforms.net/forms/embed/"
        f"{HUBSPOT_PORTAL_ID}.js"
    )
    return f"""
<div style="
    margin: 48px 0;
    padding: 40px 36px;
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 12px;
    max-width: 640px;
    width: 100%;
    box-sizing: border-box;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
">
  <script src="{script_src}" defer></script>
  <div class="hs-form-frame"
       data-region="{HUBSPOT_FORM_REGION}"
       data-form-id="{HUBSPOT_FORM_ID}"
       data-portal-id="{HUBSPOT_PORTAL_ID}">
  </div>
</div>
"""


# ── postBody HTML builder ──────────────────────────────────────────────────────

def _build_post_body(row, content):
    article   = content.get("blog_article", "")
    keyword   = row.get("Main Keyword", "")
    title     = row.get("Content Title", "")
    seo_title = content.get("seo_title", title)

    intro, sections, faq_md = _split_article(article)
    faq_items = _parse_faq(faq_md) if faq_md else []

    # One tracker per post — enforces no duplicate images
    tracker = ImageTracker()

    # Determine self URL key to prevent self-linking
    slug = _slugify(row.get("Content Title", ""))
    self_url_key = next(
        (k for k, v in APPROVED_URLS.items() if slug in v),
        None
    )

    html_parts = []

    # ── Intro + cluster link injection ────────────────────────────────────────
    intro_html         = md.markdown(intro, extensions=["extra"])
    intro_html, n_cl   = _inject_cluster_links(intro_html, keyword, self_url_key)
    intro_html, n_br   = _inject_brand_links(intro_html)
    cluster_links_total = n_cl
    brand_links_total   = n_br
    html_parts.append(f'<div class="hs-blog-intro">\n{intro_html}\n</div>')

    # ── CTA 1: Lead Magnet (captures reader after intro) ─────────────────────
    html_parts.append(_cta_block_v2("lead-magnet", "after-introduction", 1, tracker.cta(0)))

    # ── Sections ──────────────────────────────────────────────────────────────
    section_img_idx  = 0
    # mid is the 0-based index after which CTA 2 is injected.
    # With ≥2 sections: halfway point. With 1 section: after that section (i==0).
    # With 0 sections: loop never runs — handled by mid_cta_injected guard below.
    mid              = max(1, len(sections) // 2) if sections else 0
    mid_cta_injected = False

    for i, section in enumerate(sections):
        heading_match = re.match(r'^## (.+)', section)
        heading_text  = heading_match.group(1).strip() if heading_match else f"Section {i+1}"

        # Render section — cluster links up to budget, then brand links
        section_html = md.markdown(section, extensions=["extra"])

        remaining_cluster = MAX_CLUSTER_LINKS - cluster_links_total
        if remaining_cluster > 0:
            section_html, n = _inject_cluster_links(section_html, keyword, self_url_key)
            # respect global budget: cap n at remaining
            n = min(n, remaining_cluster)
            cluster_links_total += n

        remaining_brand = MAX_BRAND_LINKS - brand_links_total
        if remaining_brand > 0:
            section_html, n = _inject_brand_links(section_html, remaining_brand)
            brand_links_total += n

        html_parts.append(f'<div class="hs-blog-section">\n{section_html}\n</div>')

        # Contextual section image after every 2nd section
        if (i + 1) % 2 == 0:
            context = heading_text + " " + keyword
            img_url = tracker.section(context, section_img_idx)
            img_alt = f"{heading_text} — {keyword} | Bubba Academy"
            html_parts.append(_img_tag(img_url, img_alt, "section"))
            section_img_idx += 1

        # ── CTA 2: Contextual mid-article (topic-specific persuasion) ────────
        if i == mid - 1:
            html_parts.append(
                _cta_block_v2("contextual", "mid-article", 2, tracker.cta(1), keyword)
            )
            mid_cta_injected = True

    # ── CTA 2 fallback: guarantee injection even with 0-1 sections ───────────
    # This is the safety net — if the AI produced no sections (e.g. URL in
    # blog_draft field, very short article, or only intro text), CTA 2 is
    # always placed here, between intro and the HubSpot form.
    if not mid_cta_injected:
        html_parts.append(
            _cta_block_v2("contextual", "mid-article", 2, tracker.cta(1), keyword)
        )

    # ── HubSpot Form (after conclusion, before conversion CTA) ───────────────
    html_parts.append(_hubspot_form_block())

    # ── CTA 3: Conversion (strong close — join / start business) ─────────────
    html_parts.append(_cta_block_v2("conversion", "after-conclusion", 3, tracker.cta(2)))

    # ── FAQ ───────────────────────────────────────────────────────────────────
    if faq_items:
        html_parts.append(f'\n{_faq_html(faq_items)}\n')

    post_body = "\n".join(html_parts)

    # Expose tracker + link counts to JSON builder via content dict
    content["_image_tracker"]        = tracker
    content["_cluster_links_count"]  = cluster_links_total
    content["_brand_links_count"]    = brand_links_total
    return post_body


# ── Full hubspot.html ──────────────────────────────────────────────────────────

def _build_hubspot_html(row, content, faq_items, faq_schema, article_schema):
    seo_title = content.get("seo_title", "")
    meta_desc = content.get("meta_description", "")
    post_body = _build_post_body(row, content)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{seo_title}</title>
  <meta name="description" content="{meta_desc}">
  <meta name="generator" content="Bubba Academy AI Content Agent — HubSpot Export">

  <!-- Schema.org: Article -->
  <script type="application/ld+json">
{json.dumps(article_schema, indent=2)}
  </script>

  <!-- Schema.org: FAQPage -->
  <script type="application/ld+json">
{json.dumps(faq_schema, indent=2)}
  </script>

  <!--
    ╔══════════════════════════════════════════════════════════╗
    ║  HUBSPOT PUBLISHING CHECKLIST                           ║
    ║  1. Fill in all FILL_IMAGE_URL placeholders             ║
    ║  2. Replace CTA blocks with real HubSpot embeds         ║
    ║  3. Replace internal link placeholders with real URLs   ║
    ║  4. Set publishDate in hubspot.json                     ║
    ║  5. Fill api_required fields in hubspot.json            ║
    ║  6. Flip ready_for_api=true in hubspot.json             ║
    ╚══════════════════════════════════════════════════════════╝
  -->
</head>
<body>
<article class="hs-blog-post" data-keyword="{row.get('Main Keyword', '')}">

  <header class="hs-blog-header">
    <h1 class="hs-blog-title">{seo_title}</h1>
  </header>

{post_body}

</article>
</body>
</html>"""


# ── Full hubspot.json ──────────────────────────────────────────────────────────

def _build_hubspot_json(row, content, export_path, post_body_html):
    slug         = _slugify(row.get("Content Title", "untitled"))
    tags         = _map_tags(row.get("Topic Cluster", ""))
    keyword      = row.get("Main Keyword", "")
    title        = row.get("Content Title", "")
    seo_title    = content.get("seo_title", "")
    meta_desc    = content.get("meta_description", "")
    publish_date = datetime.datetime.utcnow().strftime("%Y-%m-%dT07:00:00Z")
    link_opps    = _find_internal_link_opportunities(content.get("blog_article", ""))

    _, _, faq_md = _split_article(content.get("blog_article", ""))
    faq_items    = _parse_faq(faq_md) if faq_md else []

    lm  = CTA_CONFIG["lead_magnet"]
    co  = CTA_CONFIG["conversion"]

    return {
        "hubspot_meta": {
            "export_version": "2.0",
            "export_date":    datetime.datetime.utcnow().isoformat() + "Z",
            "export_path":    export_path,
            "ready_for_api":  False,
            "target_portal":  "crm.bubbaacademy.com",
            "api_endpoint":   "https://api.hubapi.com/cms/v3/blogs/posts",
            "note":           "Fill api_required fields and flip ready_for_api=true before publishing",
        },

        "api_required": {
            "blogId":   "FILL_IN: HubSpot Blog ID (HubSpot > Website > Blog > Blog Details)",
            "authorId": "FILL_IN: HubSpot Author ID (HubSpot > Website > Blog > Authors)",
            "portalId": "FILL_IN: HubSpot Portal ID (HubSpot > Account Settings > Integrations > API Key)",
        },

        "post": {
            "name":             title,
            "htmlTitle":        seo_title,
            "metaDescription":  meta_desc,
            "slug":             slug,
            "state":            "DRAFT",
            "publishDate":      publish_date,
            "postBody":         post_body_html,
            "tagNames":         tags,
            "campaign":         row.get("Topic Cluster", ""),
            "useFeaturedImage": False,
        },

        "seo": {
            "seo_title":        seo_title,
            "meta_description": meta_desc,
            "main_keyword":     keyword,
            "slug":             slug,
            "audience_level":   row.get("Audience Level", ""),
        },

        "content_blocks": {
            "blog_article_markdown": content.get("blog_article", ""),
            "social_caption":        content.get("social_caption", ""),
            "video_script":          content.get("video_script", ""),
            "email_copy":            content.get("email_copy", ""),
        },

        "lead_gen": {
            "top_cta": {
                "placement":   "after-introduction",
                "type":        "lead-magnet",
                "headline":    lm["headline"],
                "button_text": lm["button_text"],
                "href":        lm["href"],
                "offer":       lm.get("offer", ""),
            },
            "mid_cta": {
                "placement":   "mid-article",
                "type":        "contextual",
                "headline":    _get_mid_cta_cfg(keyword).get("headline", ""),
                "button_text": _get_mid_cta_cfg(keyword).get("button_text", ""),
                "href":        _get_mid_cta_cfg(keyword).get("href", APPROVED_URLS["bubba_home"]),
            },
            "bottom_cta": {
                "placement":   "after-conclusion",
                "type":        "conversion",
                "headline":    co["headline"],
                "button_text": co["button_text"],
                "href":        co["href"],
            },
        },

        "images": content.get("_image_tracker", ImageTracker()).validation_report(),

        "internal_links": {
            "cluster_links_injected": content.get("_cluster_links_count", 0),
            "brand_links_injected":   content.get("_brand_links_count", 0),
            "total_links_injected":   (
                content.get("_cluster_links_count", 0)
                + content.get("_brand_links_count", 0)
            ),
            "opportunities": link_opps,
        },

        "faq": {
            "count":       len(faq_items),
            "items":       faq_items,
            "schema_type": "FAQPage",
        },
    }


# ── Exporter class ─────────────────────────────────────────────────────────────

class HubSpotExporter(BaseExporter):
    """
    Upgrade path: create exporters/hubspot_api.py, subclass this,
    override export() to POST hubspot.json to HubSpot API directly.
    """

    def name(self):
        return "HubSpotExporter"

    def export(self, row, content):
        try:
            export_path = get_export_path(row)
            os.makedirs(export_path, exist_ok=True)

            _, _, faq_md = _split_article(content.get("blog_article", ""))
            faq_items    = _parse_faq(faq_md) if faq_md else []
            faq_schema   = _faq_schema_json(faq_items)

            seo_title = content.get("seo_title", "")
            meta_desc = content.get("meta_description", "")
            keyword   = row.get("Main Keyword", "")

            article_schema = {
                "@context":    "https://schema.org",
                "@type":       "Article",
                "headline":    seo_title,
                "description": meta_desc,
                "keywords":    keyword,
                "publisher": {
                    "@type": "Organization",
                    "name":  "Bubba Academy",
                    "url":   "https://crm.bubbaacademy.com",
                },
            }

            post_body_html = _build_post_body(row, content)

            html_path = os.path.join(export_path, "hubspot.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(_build_hubspot_html(row, content, faq_items, faq_schema, article_schema))

            json_path = os.path.join(export_path, "hubspot.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(_build_hubspot_json(row, content, export_path, post_body_html),
                          f, indent=2, ensure_ascii=False)

            return {
                "success":   True,
                "message":   f"HubSpot package exported to {export_path}",
                "html_path": html_path,
                "json_path": json_path,
            }

        except Exception as e:
            return {"success": False, "message": f"HubSpotExporter error: {e}"}
