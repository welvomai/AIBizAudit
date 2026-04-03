import os
import json
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)


def find_value(form_data: dict, *possible_keys) -> str:
    for key in possible_keys:
        if key in form_data:
            return form_data[key]
        for k, v in form_data.items():
            if k.lower().strip() == key.lower().strip():
                return v
    return ""


def reserve_response_id(response_id: str) -> bool:
    """
    Try to insert just the response_id immediately.
    Returns True if successful (first time).
    Returns False if it already exists (duplicate).
    """
    if not response_id:
        return True  # No ID to check, allow through
    
    try:
        # Check first
        result = supabase.table("leads") \
            .select("id") \
            .eq("response_id", response_id) \
            .execute()
        
        if len(result.data) > 0:
            return False  # Already exists — duplicate
            
        # Insert a placeholder row immediately to "claim" this response_id
        supabase.table("leads").insert({
            "response_id": response_id,
            "report_sent": False,
            "name": "_processing_"
        }).execute()
        return True  # Successfully reserved
        
    except Exception as e:
        print(f"⚠️ reserve_response_id error: {e}")
        return True  # Allow through if unsure

def save_lead(form_data: dict, response_id: str = "", report_sent: bool = False):
    print("📋 Form data keys received:", list(form_data.keys()))

    record = {
        "name": find_value(form_data, "Contact Person Name", "Contact person name"),
        "email": find_value(form_data, "Email Address", "Email address"),
        "company": find_value(form_data, "Company name", "Company Name"),
        "industry": find_value(form_data, "Industry"),
        "employees": find_value(form_data, "Company size"),
        "revenue": find_value(form_data, "Annual Revenue"),
        "website": find_value(form_data, "Website"),
        "mobile": find_value(form_data, "Mobile Number"),
        "response_id": response_id,
        "raw_answers": json.dumps(form_data),
        "report_sent": report_sent
    }

    if response_id:
        # Update the placeholder row we reserved earlier
        result = supabase.table("leads") \
            .update(record) \
            .eq("response_id", response_id) \
            .execute()
    else:
        result = supabase.table("leads").insert(record).execute()

    print(f"✅ Lead saved: {record['name']} — {record['email']}")
    return result