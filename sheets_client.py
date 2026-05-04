import os
import json
import gspread
from google.oauth2.service_account import Credentials
from config import SHEET_ID, SHEET_NAME, CREDENTIALS_FILE, COLUMNS, STATUS_TRIGGER

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_credentials():
    """
    Load Google service-account credentials from environment or local file.

    Priority:
      1. GOOGLE_CREDENTIALS_JSON env var — full JSON string (Render / cloud).
         Paste the entire contents of credentials.json as the env var value.
      2. CREDENTIALS_FILE path (local dev, defaults to 'credentials.json').
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "").strip()
    if creds_json:
        info = json.loads(creds_json)
        return Credentials.from_service_account_info(info, scopes=SCOPES)
    return Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)


def get_sheet():
    creds  = _get_credentials()
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).worksheet(SHEET_NAME)


def get_pending_rows(sheet):
    all_rows = sheet.get_all_records()
    pending = []
    for i, row in enumerate(all_rows):
        if str(row.get("Status", "")).strip().lower() == STATUS_TRIGGER.lower():
            pending.append({
                "row_index": i + 2,
                "content_id":     row.get("Content ID", ""),
                "topic_cluster":  row.get("Topic Cluster", ""),
                "main_keyword":   row.get("Main Keyword", ""),
                "content_title":  row.get("Content Title", ""),
                "audience_level": row.get("Audience Level", ""),
                "content_type":   row.get("Content Type", ""),
            })
    return pending


def write_content(sheet, row_index, content):
    updates = [
        (row_index, COLUMNS["seo_title"],      content.get("seo_title", "")),
        (row_index, COLUMNS["meta_desc"],      content.get("meta_description", "")),
        (row_index, COLUMNS["blog_draft"],     content.get("blog_article", "")),
        (row_index, COLUMNS["social_caption"], content.get("social_caption", "")),
        (row_index, COLUMNS["video_script"],   content.get("video_script", "")),
        (row_index, COLUMNS["email_copy"],     content.get("email_copy", "")),
    ]
    for row, col, value in updates:
        sheet.update_cell(row, col, value)


def update_status(sheet, row_index, status):
    sheet.update_cell(row_index, COLUMNS["status"], status)
