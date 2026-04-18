import os
import base64
import resend
from dotenv import load_dotenv

load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY")

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
BOSS_EMAIL = os.getenv("BOSS_EMAIL")


def send_report_email(to_email: str, company_name: str, pdf_bytes: bytes):
    """Send the PDF report to the business owner."""

    if not to_email:
        raise ValueError("Missing recipient email for report delivery")

    encoded_pdf = base64.b64encode(pdf_bytes).decode()

    params = {
        "from": SENDER_EMAIL,
        "to": [to_email],
        "subject": f"Your AI Transformation Report is Ready — {company_name}",
        "html": f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px;
                    margin: 0 auto; background: #ffffff;">

          <!-- Header -->
          <div style="background: linear-gradient(135deg, #0f0c29, #302b63);
                      padding: 40px 40px 30px 40px; text-align: center;">
            <h1 style="color: white; font-size: 24px; margin: 0;">
              Welvom AI
            </h1>
            <p style="color: #a0aec0; margin: 8px 0 0 0; font-size: 14px;">
              AI Transformation Consulting
            </p>
          </div>

          <!-- Body -->
          <div style="padding: 40px;">
            <h2 style="color: #1a1a2e; font-size: 20px;">
              Your AI Audit Report is Ready! 🎉
            </h2>
            <p style="color: #4a5568; line-height: 1.7;">
              Thank you for completing the
              <strong>Welvom AI Business Audit</strong>.
            </p>
            <p style="color: #4a5568; line-height: 1.7;">
              We have analyzed your business operations and prepared a
              personalized <strong>AI Transformation Audit Report</strong>
              for <strong>{company_name}</strong>.
            </p>
            <p style="color: #4a5568; line-height: 1.7;">
              Your report includes:
            </p>
            <ul style="color: #4a5568; line-height: 2;">
              <li>Executive Summary & Critical Findings</li>
              <li>Department-by-Department Analysis</li>
              <li>Man-Hour Savings Breakdown</li>
              <li>Cost Reduction Opportunities</li>
              <li>90-180 Day Implementation Roadmap</li>
              <li>12-Month ROI Projections</li>
            </ul>

            <div style="text-align: center; margin: 36px 0;">
              <p style="color: #4a5568; font-size: 14px;">
                Please find your full report attached to this email as a PDF.
              </p>
            </div>

            <p style="color: #4a5568; line-height: 1.7;">
              Our team will be in touch shortly to walk you through
              the findings and discuss how we can implement these
              recommendations for your business.
            </p>

            <p style="color: #4a5568; margin-top: 30px;">
              Best regards,<br>
              <strong>The Welvom AI Team</strong><br>
              <a href="https://www.welvom.com"
                 style="color: #3182ce;">www.welvom.com</a>
            </p>
          </div>

          <!-- Footer -->
          <div style="background: #f7fafc; padding: 20px 40px;
                      text-align: center; border-top: 1px solid #e2e8f0;">
            <p style="color: #a0aec0; font-size: 12px; margin: 0;">
              © 2025 Welvom AI Consulting ·
              <a href="https://www.welvom.com"
                 style="color: #a0aec0;">www.welvom.com</a>
            </p>
          </div>

        </div>
        """,
        "attachments": [
            {
                "filename": f"Welvom_AI_Report_{company_name}.pdf",
                "content": encoded_pdf,
            }
        ]
    }

    try:
      response = resend.Emails.send(params)
      print(f"✅ Report email sent to {to_email} — ID: {response['id']}")
      return response
    except Exception as exc:
      print(f"[Warn] Report email not sent: {exc}")
      return None


def send_lead_notification(form_data: dict, pdf_bytes: bytes):
    """Send lead notification + PDF to Welvom (boss)."""

    encoded_pdf = base64.b64encode(pdf_bytes).decode()

    name = form_data.get("Contact Person Name", form_data.get("Contact person name", "Unknown"))
    email = form_data.get("Email Address", form_data.get("Email address", "Unknown"))
    company = form_data.get("Company name", form_data.get("Company Name", "Unknown"))
    industry = form_data.get("Industry", "Unknown")
    employees = form_data.get("Company size", "Unknown")

    # Build answers table rows
    answers_rows = "".join([
        f"""<tr>
              <td style="padding: 8px 12px; border: 1px solid #e2e8f0;
                         font-weight: 600; background: #f7fafc;
                         width: 35%; color: #1a1a2e;">{k}</td>
              <td style="padding: 8px 12px; border: 1px solid #e2e8f0;
                         color: #4a5568;">{v}</td>
            </tr>"""
        for k, v in form_data.items()
    ])

    params = {
        "from": SENDER_EMAIL,
        "to": [BOSS_EMAIL],
        "subject": f"🔥 New Lead: {name} from {company} ({industry})",
        "html": f"""
        <div style="font-family: Arial, sans-serif; max-width: 700px;
                    margin: 0 auto;">

          <!-- Header -->
          <div style="background: linear-gradient(135deg, #0f0c29, #302b63);
                      padding: 30px 40px;">
            <h1 style="color: white; font-size: 20px; margin: 0;">
              🔥 New AI Audit Lead
            </h1>
            <p style="color: #a0aec0; margin: 6px 0 0 0; font-size: 14px;">
              A new business has submitted the AI audit form
            </p>
          </div>

          <!-- Lead Summary -->
          <div style="padding: 30px 40px; background: #ebf8ff;
                      border-left: 4px solid #3182ce;">
            <h2 style="color: #1a1a2e; margin: 0 0 12px 0; font-size: 18px;">
              {name}
            </h2>
            <p style="margin: 4px 0; color: #4a5568;">
              🏢 <strong>Company:</strong> {company}
            </p>
            <p style="margin: 4px 0; color: #4a5568;">
              📧 <strong>Email:</strong>
              <a href="mailto:{email}" style="color: #3182ce;">{email}</a>
            </p>
            <p style="margin: 4px 0; color: #4a5568;">
              🏭 <strong>Industry:</strong> {industry}
            </p>
            <p style="margin: 4px 0; color: #4a5568;">
              👥 <strong>Employees:</strong> {employees}
            </p>
          </div>

          <!-- Full Answers Table -->
          <div style="padding: 30px 40px;">
            <h3 style="color: #1a1a2e; margin-bottom: 16px;">
              Full Audit Responses
            </h3>
            <table style="width: 100%; border-collapse: collapse;
                          font-size: 13px;">
              {answers_rows}
            </table>
          </div>

          <!-- Note -->
          <div style="padding: 0 40px 30px 40px;">
            <p style="color: #718096; font-size: 13px;">
              The AI audit report has been automatically generated and
              sent to <strong>{email}</strong>.
              The report is also attached to this email for your reference.
            </p>
          </div>

        </div>
        """,
        "attachments": [
            {
                "filename": f"Welvom_AI_Report_{company}.pdf",
                "content": encoded_pdf,
            }
        ]
    }

    try:
      response = resend.Emails.send(params)
      print(f"✅ Lead notification sent to boss — ID: {response['id']}")
      return response
    except Exception as exc:
      print(f"[Warn] Lead notification not sent: {exc}")
      return None
