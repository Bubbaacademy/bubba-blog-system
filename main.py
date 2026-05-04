import os
import time
from dotenv import load_dotenv
from sheets_client import get_sheet, get_pending_rows, write_content, update_status
from content_generator import generate_all_content
from config import STATUS_OUTPUT

load_dotenv(override=True)


def run_agent():
    print("\n" + "="*50)
    print("  Bubba Academy AI Content Agent")
    print("="*50)

    print("\n[1/3] Connecting to Google Sheet...")
    try:
        sheet = get_sheet()
        print("      Connected successfully.")
    except Exception as e:
        print(f"      ERROR connecting to sheet: {e}")
        return

    print("\n[2/3] Scanning for rows with Status = 'Idea'...")
    try:
        pending = get_pending_rows(sheet)
    except Exception as e:
        print(f"      ERROR reading rows: {e}")
        return

    if not pending:
        print("      No rows found with Status = 'Idea'. Nothing to do.")
        return {"ideas_found": 0, "drafts_created": 0, "failed": 0}

    print(f"      Found {len(pending)} row(s) to process.")

    print("\n[3/3] Generating content...\n")
    success = 0
    failed = 0

    for item in pending:
        title    = item["content_title"]
        keyword  = item["main_keyword"]
        audience = item["audience_level"]
        row      = item["row_index"]

        print(f"  Processing: \"{title}\"")
        print(f"  Keyword: {keyword} | Audience: {audience}")

        try:
            content = generate_all_content(title, keyword, audience)
            write_content(sheet, row, content)
            update_status(sheet, row, STATUS_OUTPUT)
            print(f"  Status updated to '{STATUS_OUTPUT}'.")
            print(f"  Done.\n")
            success += 1
            time.sleep(2)  # brief pause between rows to avoid API rate limits

        except Exception as e:
            print(f"  ERROR processing \"{title}\": {e}")
            print(f"  Skipping this row.\n")
            failed += 1
            continue

    print("="*50)
    print(f"  Run complete. Success: {success} | Failed: {failed}")
    print("="*50 + "\n")
    return {"ideas_found": len(pending), "drafts_created": success, "failed": failed}


if __name__ == "__main__":
    run_agent()

