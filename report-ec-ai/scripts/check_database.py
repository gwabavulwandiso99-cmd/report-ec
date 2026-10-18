"""Checks that the Supabase database is ready for the AI chatbot.

Run from the report-ec folder (where .env and db.py are):
    python report-ec-ai/scripts/check_database.py

READ-ONLY: it only reads one row from each table/view. It never inserts,
changes or deletes anything.
"""
import sys
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO))

CHECKS = [
    ("reports table (existing columns)", "reports", "id, ref, phone, category, urgency, status, suburb, landmark, ward, channel"),
    ("reports: new AI columns (migration 001)", "reports", "idempotency_key, location_text, municipality, ai_suggestion"),
    ("report_updates table", "report_updates", "report_id, text, created_at"),
    ("report_verifications table (migration 001)", "report_verifications", "report_id, phone, stance, ai_advisory, idempotency_key"),
    ("report_verification_summary view (migration 001)", "report_verification_summary", "report_id, confirms, disputes, contradiction_flagged"),
    ("bot_sessions table", "bot_sessions", "phone, step, data, updated_at"),
]


def main():
    try:
        from db import supabase
    except Exception as error:
        print("Could not connect. Run this from the report-ec folder (where db.py and .env are).")
        print(f"  {type(error).__name__}: {error}")
        sys.exit(1)

    failed = 0
    for label, table, columns in CHECKS:
        try:
            supabase.table(table).select(columns).limit(1).execute()
            print(f"  OK    {label}")
        except Exception as error:
            failed += 1
            message = getattr(error, "message", None) or str(error)
            print(f"  FAIL  {label}\n        {message[:200]}")

    print()
    if failed:
        print(f"{failed} check(s) failed. Has database/001_ai_chatbot.sql been run in the Supabase SQL editor?")
        sys.exit(1)
    print("Database is ready for the AI chatbot.")


if __name__ == "__main__":
    main()
