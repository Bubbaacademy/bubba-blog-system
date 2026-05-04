def build_context(title, keyword, audience, brand):
    return f"""
Brand: {brand['name']}
Focus: {brand['focus']}
Tone: {brand['tone']}
Style: {brand['style']}
Language: {brand['language']} only

Content Title: {title}
Main Keyword: {keyword}
Audience Level: {audience}
""".strip()


def seo_title_prompt(title, keyword, audience, brand):
    ctx = build_context(title, keyword, audience, brand)
    return f"""{ctx}

Task: Write ONE SEO-optimized title for this blog post.

Rules:
- Include the main keyword naturally
- 50–60 characters max
- Must be compelling and click-worthy
- No clickbait, no ALL CAPS
- Return the title only, no labels, no extra text
"""


def meta_description_prompt(title, keyword, audience, brand):
    ctx = build_context(title, keyword, audience, brand)
    return f"""{ctx}

Task: Write ONE meta description for this blog post.

Rules:
- Include the main keyword naturally
- 140–160 characters max
- Summarize the value the reader gets
- End with a soft call to action
- Return the meta description only, no labels, no extra text
"""


def blog_article_prompt(title, keyword, audience, brand):
    ctx = build_context(title, keyword, audience, brand)
    return f"""{ctx}

Task: Write a complete, high-quality blog article.

Structure:
1. Introduction (hook + what the reader will learn, 2–3 short paragraphs)
2. At least 5 main sections with H2 headings
3. Each section: 150–250 words, practical and actionable
4. Use bullet points or numbered lists where they add clarity
5. Conclusion (summarize key points + one clear next step)

Rules:
- Total length: 1000–1500 words
- Use the main keyword in the first 100 words and naturally throughout
- No fluff, no filler sentences
- Write for a {audience} audience
- Do not include FAQ section (that is separate)
- Use plain markdown formatting (## for H2, ### for H3, **bold**, - for bullets)
"""


def faq_prompt(title, keyword, audience, brand):
    ctx = build_context(title, keyword, audience, brand)
    return f"""{ctx}

Task: Write a FAQ section optimized for AEO (Answer Engine Optimization).

Rules:
- Write exactly 5 questions and answers
- Questions must be phrased the way real people search (conversational)
- Each answer: 2–4 sentences, direct and complete
- Include the main keyword in at least 2 questions
- Format exactly like this:

**Q: [Question here]**
A: [Answer here]

Return only the 5 Q&A pairs, no intro text, no labels.
"""


def social_caption_prompt(title, keyword, audience, brand):
    ctx = build_context(title, keyword, audience, brand)
    return f"""{ctx}

Task: Write ONE social media caption for this content.

Rules:
- Platform: Instagram / LinkedIn (works for both)
- Length: 150–220 words
- Start with a strong hook (first line must stop the scroll)
- Share 3–5 practical insights from the article in short punchy lines
- End with a clear call to action (e.g. save this, follow for more, comment below)
- Add 5–8 relevant hashtags at the end
- No emojis unless they add real value
- Return the caption only, no labels
"""


def video_script_prompt(title, keyword, audience, brand):
    ctx = build_context(title, keyword, audience, brand)
    return f"""{ctx}

Task: Write a short video script (60–90 seconds when spoken).

Structure:
- Hook (0–5 sec): One bold statement or question that grabs attention immediately
- Problem (5–15 sec): Name the pain point the audience has
- Solution (15–55 sec): 3 clear, fast tips or steps — numbered, one sentence each
- CTA (55–75 sec): Tell them exactly what to do next

Rules:
- Write in spoken, natural language — how a confident expert talks
- No jargon unless explained immediately
- Each section clearly labeled: [HOOK], [PROBLEM], [SOLUTION], [CTA]
- Total word count: 150–200 words
- Return the script only
"""


def email_copy_prompt(title, keyword, audience, brand):
    ctx = build_context(title, keyword, audience, brand)
    return f"""{ctx}

Task: Write a marketing email to promote this blog article.

Structure:
- Subject line (compelling, under 50 characters, includes keyword)
- Preview text (under 90 characters, complements the subject line)
- Email body:
  - Greeting: Keep it simple ("Hi [First Name],")
  - Opening: 1–2 sentences connecting to the reader's pain point
  - Value bridge: 2–3 sentences explaining what the article teaches
  - 3 bullet points: the top things they will learn
  - CTA button text + surrounding sentence (e.g. "Read the full guide →")
  - Sign-off: short, warm, professional

Rules:
- Total body: 150–200 words
- No fluff, no long-winded intros
- Format clearly with labels: SUBJECT:, PREVIEW:, BODY:
- Return the full email, no extra commentary
"""

