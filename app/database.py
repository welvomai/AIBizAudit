import json
import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as exc:
        print(f"[Warn] Supabase client initialization failed: {exc}")
else:
    print("[Warn] SUPABASE_URL or SUPABASE_KEY not set; Supabase client disabled")


def find_value(form_data: dict, *possible_keys) -> str:
    for key in possible_keys:
        if key in form_data:
            return form_data[key]
        for existing_key, value in form_data.items():
            if existing_key.lower().strip() == key.lower().strip():
                return value
    return ""


def reserve_response_id(response_id: str) -> bool:
    """
    Try to insert just the response_id immediately.
    Returns True if successful.
    Returns False if it already exists.
    """
    if not response_id:
        return True

    if supabase is None:
        print("[Warn] Supabase is not configured; skipping response_id reservation")
        return True

    try:
        result = supabase.table("leads").select("id").eq("response_id", response_id).execute()
        if len(result.data) > 0:
            return False

        supabase.table("leads").insert(
            {
                "response_id": response_id,
                "report_sent": False,
                "name": "_processing_",
            }
        ).execute()
        return True
    except Exception as exc:
        print(f"[Warn] reserve_response_id error: {exc}")
        return True


def save_lead(form_data: dict, response_id: str = "", report_sent: bool = False):
    if supabase is None:
        print("[Warn] Supabase is not configured; skipping lead save")
        return None
    print("[Info] Form data keys received:", list(form_data.keys()))

    record = {
        "name": find_value(form_data, "Contact Person Name", "Contact person name"),
        "email": find_value(form_data, "Email Address", "Email address"),
        "company": find_value(form_data, "Company name", "Company Name"),
        "industry": find_value(form_data, "Industry"),
        "employees": find_value(form_data, "Company size"),
        "revenue": find_value(form_data, "Annual Revenue"),
        "website": find_value(form_data, "Website"),
        "mobile": find_value(form_data, "Mobile number", "Mobile Number"),
        "response_id": response_id,
        "raw_answers": json.dumps(form_data),
        "report_sent": report_sent,
    }

    try:
        if response_id:
            result = supabase.table("leads").update(record).eq("response_id", response_id).execute()
        else:
            result = supabase.table("leads").insert(record).execute()
    except Exception as exc:
        print(f"[Warn] Lead save failed, continuing without DB write: {exc}")
        return None

    print(f"[Info] Lead saved: {record['name']} - {record['email']}")
    return result
