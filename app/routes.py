from fastapi import APIRouter, Request, BackgroundTasks
import time
from app.ai import analyze_business
from app.pdf import generate_pdf
from app.email_sender import send_report_email, send_lead_notification
from app.database import save_lead, reserve_response_id
import re

router = APIRouter()


def parse_tally_data(data: dict) -> dict:
    fields = data.get("data", {}).get("fields", [])
    parsed = {}

    for field in fields:
        label = field.get("label", "")
        value = field.get("value", "")
        field_type = field.get("type", "")

        if field_type in (
            "DROPDOWN", "MULTIPLE_CHOICE", "CHECKBOXES",
            "CHECKBOX", "MULTI_SELECT", "RANKING"
        ) and value:
            options = field.get("options", [])
            selected_ids = value if isinstance(value, list) else [value]
            matched = [o.get("text", "") for o in options if o.get("id") in selected_ids]
            value = ", ".join(matched) if matched else ", ".join(str(v) for v in selected_ids)

        elif isinstance(value, list):
            value = ", ".join([str(v) for v in value])

        parsed[label] = value

    # Reconstruct checkbox sub-fields into readable values
    checkbox_groups = {}
    keys_to_remove = []

    for key, val in parsed.items():
        match = re.match(r'^(.+?)\s+\((.+)\)$', key)
        if match:
            group_name = match.group(1)
            option_name = match.group(2)
            if str(val).lower() in ("true", "1", "yes"):
                if group_name not in checkbox_groups:
                    checkbox_groups[group_name] = []
                checkbox_groups[group_name].append(option_name)
                keys_to_remove.append(key)

    for group_name, options in checkbox_groups.items():
        parsed[group_name] = ", ".join(options)

    for key in keys_to_remove:
        parsed.pop(key, None)

    return parsed


def process_submission(parsed: dict, response_id: str):
    """Full pipeline — runs in background via FastAPI BackgroundTasks."""
    try:
        start_time = time.time()

        print("⏳ Step 2: Saving lead to database...")
        save_lead(parsed, response_id=response_id, report_sent=True)
        print("✅ Step 2: Lead saved")

        print("⏳ Step 3: Sending to Mistral AI...")
        analysis = analyze_business(parsed)
        print("✅ Step 3: AI analysis done")

        print("⏳ Step 4: Generating PDF...")
        pdf_bytes = generate_pdf(analysis, parsed)
        print("✅ Step 4: PDF generated")

        print("⏳ Step 5: Sending emails...")
        user_email = parsed.get("Email Address", "")
        company_name = parsed.get("Company name", "Client")
        send_report_email(user_email, company_name, pdf_bytes)
        send_lead_notification(parsed, pdf_bytes)
        print("✅ Step 5: Emails sent")

        elapsed = time.time() - start_time
        print(f"🎉 All done! Total time: {elapsed:.1f} seconds")

    except Exception as e:
        print(f"❌ Error in process_submission: {e}")
        import traceback
        traceback.print_exc()


@router.post("/webhook/tally")
async def tally_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    response_id = data.get("data", {}).get("responseId", "")

    # Reserve the response_id IMMEDIATELY — blocks all retries at the door
    from app.database import reserve_response_id
    if not reserve_response_id(response_id):
        print(f"⚠️ Duplicate ignored: {response_id}")
        return {"status": "already processed"}

    parsed = parse_tally_data(data)
    print("✅ Step 1: Form data received")

    background_tasks.add_task(process_submission, parsed, response_id)

    return {"status": "received"}