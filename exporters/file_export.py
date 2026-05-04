import os
import json
import re
import datetime
import markdown as md
from exporters.base import BaseExporter
from config import EXPORTS_DIR


def _slugify(text):
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_-]+', '-', text)
    return text[:60]


def get_export_path(row):
    """Shared utility — returns the deterministic export folder path for a row."""
    content_id  = str(row.get("Content ID", "000")).strip()
    title       = str(row.get("Content Title", "untitled")).strip()
    date_str    = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    folder_name = f"{content_id}_{_slugify(title)}_{date_str}"
    return os.path.join(EXPORTS_DIR, folder_name)


def _img_placeholder(img_type, alt_text):
    safe_alt = alt_text.replace('"', "'")
    return f"""<!-- {img_type} IMAGE: {safe_alt} -->
<div class="image-placeholder image-placeholder--{img_type.lower()}" data-alt="{safe_alt}">
  <img src="FILL_IMAGE_URL" alt="{safe_alt}" loading="lazy" width="800" height="450" />
</div>"""


def _build_html(row, content):
    seo_title    = content.get("seo_title", "")
    meta_desc    = content.get("meta_description", "")
    blog_article = content.get("blog_article", "")
    keyword      = row.get("Main Keyword", "")
    audience     = row.get("Audience Level", "")

    # Split article into intro + sections for image injection
    faq_md = ""
    body   = blog_article
    if "\n\n---\n\n## FAQ\n\n" in body:
        body, faq_md = body.split("\n\n---\n\n## FAQ\n\n", 1)

    parts    = re.split(r'\n(?=## )', body)
    intro    = parts[0].strip()
    sections = [p.strip() for p in parts[1:] if p.strip()]

    section_blocks = []

    # Featured image
    section_blocks.append(_img_placeholder("FEATURED", f"{seo_title} | Bubba Academy"))

    # Introduction
    section_blocks.append(md.markdown(intro, extensions=["extra"]))

    # Sections with image after every 2nd
    for i, section in enumerate(sections):
        section_blocks.append(md.markdown(section, extensions=["extra"]))
        if (i + 1) % 2 == 0:
            heading_match = re.match(r'^## (.+)', section)
            heading_text  = heading_match.group(1).strip() if heading_match else f"Section {i+1}"
            section_blocks.append(_img_placeholder("SECTION", f"{heading_text} - {keyword} | Bubba Academy"))

    # FAQ if present
    if faq_md:
        section_blocks.append(md.markdown("## FAQ\n\n" + faq_md, extensions=["extra"]))

    article_html = "\n".join(section_blocks)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{seo_title}</title>
    <meta name="description" content="{meta_desc}">
    <meta name="keywords" content="{keyword}">
    <meta name="audience" content="{audience}">
    <meta name="generator" content="Bubba Academy AI Content Agent">
</head>
<body>
    <article>
        <h1>{seo_title}</h1>
        {article_html}
    </article>
</body>
</html>"""


def _build_json(row, content, export_dir):
    return {
        "export_meta": {
            "exported_at": datetime.datetime.utcnow().isoformat() + "Z",
            "export_dir":  export_dir,
            "agent":       "Bubba Academy AI Content Agent",
        },
        "row": {
            "content_id":    row.get("Content ID", ""),
            "topic_cluster": row.get("Topic Cluster", ""),
            "main_keyword":  row.get("Main Keyword", ""),
            "content_title": row.get("Content Title", ""),
            "audience_level":row.get("Audience Level", ""),
            "content_type":  row.get("Content Type", ""),
            "publish_date":  row.get("Publish Date", ""),
        },
        "content": {
            "seo_title":       content.get("seo_title", ""),
            "meta_description":content.get("meta_description", ""),
            "blog_article":    content.get("blog_article", ""),
            "social_caption":  content.get("social_caption", ""),
            "video_script":    content.get("video_script", ""),
            "email_copy":      content.get("email_copy", ""),
        },
    }


class FileExporter(BaseExporter):

    def name(self):
        return "FileExporter (JSON + HTML)"

    def export(self, row, content):
        try:
            export_path = get_export_path(row)
            os.makedirs(export_path, exist_ok=True)

            # Write HTML
            html_path = os.path.join(export_path, "blog.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(_build_html(row, content))

            # Write JSON
            json_path = os.path.join(export_path, "content.json")
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(_build_json(row, content, export_path), f, indent=2, ensure_ascii=False)

            return {
                "success":     True,
                "message":     f"Exported to {export_path}",
                "export_path": export_path,
                "html_path":   html_path,
                "json_path":   json_path,
            }

        except Exception as e:
            return {"success": False, "message": f"FileExporter error: {e}"}
