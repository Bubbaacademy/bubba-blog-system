# Bubba Academy — AI Content Agent

Automated blog content generation and HubSpot publishing pipeline for Bubba Academy.

**Phase 1 (complete):** Local execution on Mac  
**Phase 2 (current):** Render cloud deployment

---

## What it does

| Step | Trigger | Action |
|---|---|---|
| Content Generation | Row status = `Idea` | Generates SEO title, meta description, blog article, social caption, video script, email copy via Claude |
| Publishing | Row status = `Draft Ready` + Approval = `Yes` | Exports to Google Sheets tab, local file, HubSpot JSON, and publishes live HubSpot blog post |

---

## Local development

### 1. Clone and set up

```bash
git clone <your-repo>
cd bubba-content-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your real API keys
```

### 3. Place credentials file

Put your Google service-account `credentials.json` in the project root.  
(On Render, use the `GOOGLE_CREDENTIALS_JSON` env var instead — see below.)

### 4. Run locally

```bash
# Full live pipeline (generate + publish)
python app.py --run-once

# Dry run — generate + validate, no live HubSpot publish
python app.py --run-once --dry-run

# Start health endpoint server (dev mode)
python app.py
```

### 5. Legacy entry points (still work)

```bash
python main.py       # content generation only
python publisher.py  # publishing only
python daily_runner.py  # both steps (original orchestrator)
```

---

## Render deployment

### Recommended service type

| Service | Purpose |
|---|---|
| **Web Service** | Always-on health endpoint (`GET /health`) |
| **Cron Job** | Scheduled pipeline runs (`python app.py --run-once`) |

### Step-by-step

#### 1. Push to GitHub

```bash
git add .
git commit -m "Add Render deployment files"
git push origin main
```

#### 2. Create Web Service in Render

- Dashboard → New → Web Service
- Connect your GitHub repo
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:flask_app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- **Health check path:** `/health`
- **Python version:** `3.11`

#### 3. Create Cron Job in Render

- Dashboard → New → Cron Job
- Connect same GitHub repo
- **Build command:** `pip install -r requirements.txt`
- **Run command:** `python app.py --run-once`
- **Schedule:** `0 9 * * 1-5` (9am UTC Mon–Fri — adjust to your timezone)

#### 4. Set environment variables

Add these in **Dashboard → Environment** for both services:

| Variable | Value | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Required |
| `HUBSPOT_TOKEN` | `pat-na2-...` | Required |
| `GOOGLE_CREDENTIALS_JSON` | *(full JSON string)* | Required — see below |
| `GOOGLE_SHEET_ID` | `1cYzI2qy...` | Optional (default in config) |
| `DRY_RUN` | `false` | Set `true` to skip live publish |
| `HUBSPOT_MOCK_MODE` | `false` | Set `true` to log payload only |

#### 5. Setting `GOOGLE_CREDENTIALS_JSON`

On your Mac, copy your service-account JSON:

```bash
cat credentials.json | pbcopy
```

Paste the **entire JSON string** as the value of `GOOGLE_CREDENTIALS_JSON` in Render.  
It should start with `{"type":"service_account",...}`.

---

## Environment variables reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | — | Claude API key |
| `HUBSPOT_TOKEN` | ✅ | — | HubSpot Private App token |
| `GOOGLE_CREDENTIALS_JSON` | ✅* | — | Full service-account JSON string |
| `GOOGLE_SHEET_ID` | — | baked in | Google Sheet ID |
| `GOOGLE_SHEET_NAME` | — | `Sheet1` | Sheet tab name |
| `GOOGLE_CREDENTIALS_FILE` | — | `credentials.json` | Local fallback path |
| `HUBSPOT_PORTAL_ID` | — | `243737166` | HubSpot portal ID |
| `HUBSPOT_BLOG_ID` | — | `243260089053` | HubSpot blog ID |
| `HUBSPOT_AUTHOR_ID` | — | `340728710883` | HubSpot author ID |
| `HUBSPOT_FORM_ID` | — | `5d3a1165-...` | Embedded form ID |
| `HUBSPOT_FORM_REGION` | — | `na2` | Form region |
| `DRY_RUN` | — | `false` | Skip live HubSpot publish |
| `HUBSPOT_MOCK_MODE` | — | `false` | Log API payload, skip POST |

*`GOOGLE_CREDENTIALS_JSON` is required on Render. Locally, `credentials.json` file works.

---

## Run modes

| Command | What happens |
|---|---|
| `python app.py --run-once` | Full pipeline: generate content → publish to HubSpot |
| `python app.py --run-once --dry-run` | Generate + build + validate. **No publish.** |
| `DRY_RUN=true python app.py --run-once` | Same as `--dry-run` via env var |
| `gunicorn app:flask_app ...` | Start web server with `/health` and `/status` endpoints |

---

## Health endpoints

| Endpoint | Method | Response |
|---|---|---|
| `/health` | GET | `{"status":"ok","service":"bubba-content-agent","version":"2.0"}` |
| `/status` | GET | `{"status":"ok","row_counts":{...},"total":N}` |

---

## Validation gates

Publishing is blocked automatically if any of these fail:

- Duplicate images in post body
- Unapproved image IDs (not in curated Pexels library)
- Missing or invalid CTA hrefs
- Fewer than 3 CTA blocks
- Placeholder (`FILL_IN`) hrefs
- Missing SEO title (`htmlTitle`)
- Missing meta description
- Missing slug
- HubSpot Token not set

---

## Project structure

```
bubba-content-agent/
├── app.py                  ← Cloud entrypoint (new)
├── main.py                 ← Content generation
├── publisher.py            ← Publishing pipeline
├── daily_runner.py         ← Legacy local orchestrator
├── content_generator.py    ← Anthropic API calls
├── sheets_client.py        ← Google Sheets read/write
├── prompts.py              ← Claude prompt templates
├── config.py               ← All config (env-var backed)
├── exporters/
│   ├── base.py
│   ├── file_export.py
│   ├── google_docs.py
│   ├── hubspot.py          ← HTML/JSON builder + CTAs + linking
│   ├── hubspot_api.py      ← HubSpot API publish + validation
│   └── image_selector.py   ← Curated Pexels image library
├── requirements.txt        ← (new)
├── render.yaml             ← (new)
├── .env.example            ← (new)
├── .env                    ← local secrets (never commit)
├── credentials.json        ← Google service account (never commit)
├── exports/                ← local export output
└── logs/                   ← daily log files
```

---

## What still needs manual setup on Render

1. **`GOOGLE_CREDENTIALS_JSON`** — paste your service-account JSON string manually in Render dashboard
2. **`ANTHROPIC_API_KEY`** — set in Render dashboard
3. **`HUBSPOT_TOKEN`** — set in Render dashboard
4. **Cron schedule** — adjust `render.yaml` schedule to your preferred timezone/cadence
5. **`credentials.json`** in `.gitignore` — ensure it's never committed
6. **`exports/` directory** — Render's ephemeral filesystem means exports don't persist across deploys; wire up S3/GCS if persistence needed

---

## .gitignore additions

Ensure these are ignored:

```
.env
credentials.json
exports/
logs/
venv/
__pycache__/
*.pyc
.DS_Store
agent.log
```
