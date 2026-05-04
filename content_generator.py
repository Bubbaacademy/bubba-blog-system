import os
import anthropic
from dotenv import load_dotenv
from prompts import (
    seo_title_prompt,
    meta_description_prompt,
    blog_article_prompt,
    faq_prompt,
    social_caption_prompt,
    video_script_prompt,
    email_copy_prompt,
)
from config import BRAND

load_dotenv(override=True)

MODEL = "claude-opus-4-7"


def call_claude(prompt):
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text.strip()


def generate_all_content(title, keyword, audience):
    print(f"  -> Generating SEO title...")
    seo_title = call_claude(seo_title_prompt(title, keyword, audience, BRAND))

    print(f"  -> Generating meta description...")
    meta_description = call_claude(meta_description_prompt(title, keyword, audience, BRAND))

    print(f"  -> Generating blog article...")
    blog_article = call_claude(blog_article_prompt(title, keyword, audience, BRAND))

    print(f"  -> Generating FAQ section...")
    faq = call_claude(faq_prompt(title, keyword, audience, BRAND))

    print(f"  -> Generating social caption...")
    social_caption = call_claude(social_caption_prompt(title, keyword, audience, BRAND))

    print(f"  -> Generating video script...")
    video_script = call_claude(video_script_prompt(title, keyword, audience, BRAND))

    print(f"  -> Generating email copy...")
    email_copy = call_claude(email_copy_prompt(title, keyword, audience, BRAND))

    return {
        "seo_title":        seo_title,
        "meta_description": meta_description,
        "blog_article":     blog_article + "\n\n---\n\n## FAQ\n\n" + faq,
        "social_caption":   social_caption,
        "video_script":     video_script,
        "email_copy":       email_copy,
    }
