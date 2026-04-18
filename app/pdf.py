import os
from io import BytesIO
from datetime import datetime

from jinja2 import Template

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "report.html")

SECTION_HEADINGS = {
    "sec-1": "Section 1: Executive Briefing",
    "sec-2": "Section 2: Comprehensive Department Analysis",
    "sec-3": "Section 3: Department Benchmark Comparison",
    "sec-4": "Section 4: Leakage Scorecard",
    "sec-5": "Section 5: Cost Reduction Opportunities",
    "sec-6": "Section 6: 90-180 Day Implementation Roadmap",
    "sec-7": "Section 7: 12-Month ROI Projections",
    "sec-8": "Section 8: Strategic Recommendations",
    "sec-9": "Section 9: Risk Mitigation Plan",
    "sec-10": "Section 10: Future Vision",
    "sec-11": "Section 11: Next Steps & Call to Action",
}


def _normalize_text(value: str) -> str:
    return " ".join((value or "").split()).lower()


def _extract_section_pages(pdf_bytes: bytes) -> dict:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise RuntimeError("pypdf is required to build the report table of contents") from exc

    reader = PdfReader(BytesIO(pdf_bytes))
    normalized_headings = {
        section_id: _normalize_text(heading)
        for section_id, heading in SECTION_HEADINGS.items()
    }
    section_pages = {}

    # Pages 1-2 are cover and index; section labels appear there and would create false matches.
    for page_number, pdf_page in enumerate(reader.pages, start=1):
        if page_number <= 2:
            continue
        page_text = _normalize_text(pdf_page.extract_text() or "")
        for section_id, normalized_heading in normalized_headings.items():
            if section_id in section_pages:
                continue
            if normalized_heading and normalized_heading in page_text:
                section_pages[section_id] = page_number

    return section_pages


def _render_pdf_bytes(playwright, html: str) -> bytes:
    browser = playwright.chromium.launch(headless=True)
    try:
        # Approximate A4 at 96dpi for stable print-layout calculations.
        page = browser.new_page(viewport={"width": 794, "height": 1123})
        page.emulate_media(media="print")
        page.set_content(html, wait_until="networkidle")
        return page.pdf(
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            scale=1,
        )
    finally:
        browser.close()


def generate_pdf(report_data: dict, form_data: dict) -> bytes:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError("playwright is required to generate PDF reports") from exc

    company = report_data.get("company") or {}
    overview = report_data.get("overview") or {}

    company_name = company.get("name") or form_data.get("Company name", "Your Business")
    industry = company.get("industry") or form_data.get("Industry", "Business")
    employees = company.get("size") or form_data.get("Company size", "N/A")
    date = datetime.now().strftime("%B %d, %Y")

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as file_handle:
        template = Template(file_handle.read())

    base_context = {
        "report": report_data,
        "company_name": company_name,
        "industry": industry,
        "employees": employees,
        "date": date,
        "overall_score": overview.get("overall_score", "N/A"),
        "readiness_level": overview.get("readiness_level", "Emerging"),
    }

    with sync_playwright() as playwright:
        # First pass: render with placeholders to discover the real PDF page numbers.
        first_pass_html = template.render(**base_context, toc_pages={})
        first_pass_pdf = _render_pdf_bytes(playwright, first_pass_html)

        # Second pass: render TOC with page numbers extracted from the first pass PDF.
        toc_pages = _extract_section_pages(first_pass_pdf)
        final_html = template.render(**base_context, toc_pages=toc_pages)
        pdf_bytes = _render_pdf_bytes(playwright, final_html)

    return pdf_bytes
