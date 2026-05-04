import os
import json
import gspread
from google.oauth2.service_account import Credentials
from exporters.base import BaseExporter
from config import CREDENTIALS_FILE, SHEET_ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MAX_TAB_TITLE_LEN = 90


def _get_sheets_client():
    """Load credentials from GOOGLE_CREDENTIALS_JSON env var or local file."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if creds_json:
        info  = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


def _safe_tab_title(title):
    safe = title.replace("/", "-").replace("\\", "-").replace("?", "").replace("*", "").replace("[", "").replace("]", "").replace(":", "-")
    return safe[:MAX_TAB_TITLE_LEN]


class GoogleDocsExporter(BaseExporter):
    """
    Creates a dedicated tab in the existing Google Sheet for each exported article.
    Returns the direct URL to that tab as the doc_url.

    NOTE: When Google Drive API quota is available (paid Workspace), swap this
    implementation for the Drive-based Google Docs creation in exporters/google_docs_drive.py.
    """

    def name(self):
        return "GoogleDocsExporter (Sheets Tab)"

    def export(self, row, content):
        try:
            client      = _get_sheets_client()
            spreadsheet = client.open_by_key(SHEET_ID)
            tab_title   = _safe_tab_title(row.get("Content Title", "Untitled"))

            # Delete existing tab with same name to allow re-export
            existing = [ws for ws in spreadsheet.worksheets() if ws.title == tab_title]
            for ws in existing:
                spreadsheet.del_worksheet(ws)

            ws = spreadsheet.add_worksheet(title=tab_title, rows=200, cols=2)

            rows = [
                ["BUBBA ACADEMY CONTENT EXPORT", ""],
                ["", ""],
                ["Content Title",    row.get("Content Title", "")],
                ["Main Keyword",     row.get("Main Keyword", "")],
                ["Audience Level",   row.get("Audience Level", "")],
                ["Content Type",     row.get("Content Type", "")],
                ["", ""],
                ["── SEO TITLE ──", ""],
                ["SEO Title",        content.get("seo_title", "")],
                ["", ""],
                ["── META DESCRIPTION ──", ""],
                ["Meta Description", content.get("meta_description", "")],
                ["", ""],
                ["── BLOG ARTICLE ──", ""],
                ["Blog Article",     content.get("blog_article", "")],
                ["", ""],
                ["── SOCIAL MEDIA CAPTION ──", ""],
                ["Social Caption",   content.get("social_caption", "")],
                ["", ""],
                ["── VIDEO SCRIPT ──", ""],
                ["Video Script",     content.get("video_script", "")],
                ["", ""],
                ["── EMAIL COPY ──", ""],
                ["Email Copy",       content.get("email_copy", "")],
            ]

            ws.update("A1", rows)

            # Widen column B for readability
            spreadsheet.batch_update({
                "requests": [{
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": ws.id,
                            "dimension": "COLUMNS",
                            "startIndex": 1,
                            "endIndex": 2,
                        },
                        "properties": {"pixelSize": 700},
                        "fields": "pixelSize",
                    }
                }]
            })

            tab_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit#gid={ws.id}"

            return {
                "success": True,
                "message": f"Content tab created: {tab_url}",
                "doc_url": tab_url,
                "tab_id":  ws.id,
            }

        except Exception as e:
            return {"success": False, "message": f"GoogleDocsExporter error: {e}"}
