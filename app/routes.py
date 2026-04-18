import re
import threading
import time

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import jinja2

from app.ai import analyze_business
from app.database import reserve_response_id, save_lead
from app.email_sender import send_lead_notification, send_report_email
from app.pdf import generate_pdf

router = APIRouter()

# Setup Jinja2 environment for templates
jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader("app/templates"),
    autoescape=jinja2.select_autoescape(['html', 'xml']),
    enable_async=True
)

def get_jinja_template(template_name: str):
    """Get Jinja2 template"""
    return jinja_env.get_template(template_name)


def find_value(form_data: dict, *possible_keys) -> str:
    for key in possible_keys:
        if key in form_data:
            return form_data[key]
        for existing_key, value in form_data.items():
            if existing_key.lower().strip() == key.lower().strip():
                return value
    return ""


def parse_tally_data(data: dict) -> dict:
    fields = data.get("data", {}).get("fields", [])
    parsed = {}

    for field in fields:
        label = field.get("label", "")
        value = field.get("value", "")
        field_type = field.get("type", "")

        if field_type in (
            "DROPDOWN",
            "MULTIPLE_CHOICE",
            "CHECKBOXES",
            "CHECKBOX",
            "MULTI_SELECT",
            "RANKING",
        ) and value:
            options = field.get("options", [])
            selected_ids = value if isinstance(value, list) else [value]
            matched = [option.get("text", "") for option in options if option.get("id") in selected_ids]
            value = ", ".join(matched) if matched else ", ".join(str(item) for item in selected_ids)
        elif isinstance(value, list):
            value = ", ".join(str(item) for item in value)

        parsed[label] = value

    checkbox_groups = {}
    keys_to_remove = []

    for key, value in parsed.items():
        match = re.match(r"^(.+?)\s+\((.+)\)$", key)
        if not match:
            continue

        group_name = match.group(1)
        option_name = match.group(2)
        if str(value).lower() in ("true", "1", "yes"):
            checkbox_groups.setdefault(group_name, []).append(option_name)
            keys_to_remove.append(key)

    for group_name, options in checkbox_groups.items():
        parsed[group_name] = ", ".join(options)

    for key in keys_to_remove:
        parsed.pop(key, None)

    return parsed


def process_submission(parsed: dict, response_id: str):
    """Run the full submission pipeline in the background."""
    try:
        start_time = time.time()

        print("[Step 2] Saving lead to database...")
        save_lead(parsed, response_id=response_id, report_sent=True)
        print("[Step 2] Lead saved")

        print("[Step 3] Sending to Mistral AI...")
        analysis = analyze_business(parsed)
        print("[Step 3] AI analysis done")

        print("[Step 4] Generating PDF...")
        pdf_bytes = generate_pdf(analysis, parsed)
        print("[Step 4] PDF generated")

        print("[Step 5] Sending emails...")
        user_email = find_value(parsed, "Email address", "Email Address")
        company_name = find_value(parsed, "Company name", "Company Name") or "Client"
        send_report_email(user_email, company_name, pdf_bytes)
        send_lead_notification(parsed, pdf_bytes)
        print("[Step 5] Emails sent")

        elapsed = time.time() - start_time
        print(f"[Done] Total time: {elapsed:.1f} seconds")

    except Exception as exc:
        print(f"[Error] process_submission failed: {exc}")
        import traceback

        traceback.print_exc()


@router.post("/webhook/tally")
async def tally_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    response_id = data.get("data", {}).get("responseId", "")

    if not reserve_response_id(response_id):
        print(f"[Warn] Duplicate ignored: {response_id}")
        return {"status": "already processed"}

    parsed = parse_tally_data(data)
    print("[Step 1] Form data received")

    background_tasks.add_task(process_submission, parsed, response_id)

    return {"status": "received"}


# ============= PREMIUM UI QUESTIONNAIRE ROUTES =============

@router.get("/questionnaire", response_class=HTMLResponse)
async def questionnaire():
    """Serve the premium business assessment questionnaire"""
    try:
        template = get_jinja_template("questionnaire.html")
        html_content = await template.render_async()
        return html_content
    except Exception as e:
        print(f"Error rendering questionnaire: {e}")
        return f"<h1>Error</h1><p>{str(e)}</p>"


@router.get("/submit")
async def submit():
    """Backward-compatible route for older landing page links."""
    return RedirectResponse(url="/questionnaire", status_code=307)


@router.post("/submit-assessment")
async def submit_assessment(request: Request, background_tasks: BackgroundTasks):
    """Handle questionnaire form submission"""
    try:
        form_data = await request.form()
        
        # Convert form data to dict
        data_dict = {}
        for key in form_data:
            values = form_data.getlist(key)
            # If multiple values (checkboxes), join them; otherwise take single value
            data_dict[key] = ", ".join(values) if len(values) > 1 else values[0] if values else ""
        
        # Extract email for response ID
        email = find_value(data_dict, "Email address", "Email Address", "email_address")
        response_id = f"assess_{int(time.time())}_{email.split('@')[0]}"
        
        # Reserve the response ID to prevent duplicates
        if not reserve_response_id(response_id):
            print(f"[Warn] Duplicate submission ignored: {response_id}")
            return {"status": "already processed"}
        
        print("[Step 1] Assessment form data received")
        print(f"[Step 1] Email: {email}")
        
        # Process fully outside the request lifecycle so submission success is not tied to report generation.
        threading.Thread(target=process_assessment, args=(data_dict, response_id), daemon=True).start()
        
        # Return success response
        return {"status": "received", "message": "Your assessment has been submitted successfully. We will send you a detailed report shortly."}
        
    except Exception as e:
        print(f"[Error] Form submission failed: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}


def process_assessment(data: dict, response_id: str):
    """Process the assessment in the background"""
    import sys
    import logging
    
    # Setup logging to file for background task
    logging.basicConfig(
        filename='background_task.log',
        level=logging.DEBUG,
        format='%(asctime)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    try:
        start_time = time.time()
        
        logger.info(f"[Step 2] Processing assessment {response_id}...")
        print(f"[Step 2] Processing assessment {response_id}...", flush=True)
        sys.stdout.flush()
        
        logger.info(f"[Step 2] Saving to database...")
        print(f"[Step 2] Saving to database...", flush=True)
        sys.stdout.flush()
        
        save_lead(data, response_id=response_id, report_sent=False)
        logger.info("[Step 2] Assessment saved")
        print("[Step 2] Assessment saved", flush=True)
        sys.stdout.flush()
        
        print("[Step 3] Sending to Mistral AI for analysis...")
        sys.stdout.flush()
        logger.info("[Step 3] Sending to Mistral AI for analysis...")
        analysis = analyze_business(data)
        logger.info("[Step 3] AI analysis complete")
        print("[Step 3] AI analysis complete")
        sys.stdout.flush()
        
        logger.info("[Step 4] Generating PDF report...")
        print("[Step 4] Generating PDF report...")
        sys.stdout.flush()
        pdf_bytes = generate_pdf(analysis, data)
        logger.info("[Step 4] PDF generated")
        print("[Step 4] PDF generated")
        sys.stdout.flush()
        
        logger.info("[Step 5] Sending emails...")
        print("[Step 5] Sending emails...")
        sys.stdout.flush()
        email = find_value(data, "Email address", "Email Address", "email_address")
        company_name = find_value(data, "Company name", "Company Name", "company_name") or 'Valued Client'
        
        try:
            send_report_email(email, company_name, pdf_bytes)
        except Exception as exc:
            logger.warning(f"[Warn] Report email skipped: {exc}")
            print(f"[Warn] Report email skipped: {exc}")

        try:
            send_lead_notification(data, pdf_bytes)
        except Exception as exc:
            logger.warning(f"[Warn] Lead notification skipped: {exc}")
            print(f"[Warn] Lead notification skipped: {exc}")

        logger.info("[Step 5] Email stage complete")
        print("[Step 5] Email stage complete")
        sys.stdout.flush()
        
        elapsed = time.time() - start_time
        logger.info(f"[Done] Assessment processed in {elapsed:.1f} seconds")
        print(f"[Done] Assessment processed in {elapsed:.1f} seconds")
        sys.stdout.flush()
        
    except Exception as exc:
        logger.error(f"[Error] process_assessment failed: {exc}", exc_info=True)
        print(f"[Error] process_assessment failed: {exc}", flush=True)
        sys.stdout.flush()
        import traceback
        traceback.print_exc()
