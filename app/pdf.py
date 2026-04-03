import os
import re
from datetime import datetime
from weasyprint import HTML
from jinja2 import Template

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "report.html")

def convert_to_html(text: str) -> str:
    lines = text.split('\n')
    html_lines = []
    in_table = False
    in_ul = False
    in_ol = False
    pending_heading = None  # holds h2/h3 until we know what follows

    def flush_pending():
        """Write any pending heading that wasn't followed by a table."""
        nonlocal pending_heading
        if pending_heading:
            html_lines.append(pending_heading)
            pending_heading = None

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if in_ul: html_lines.append('</ul>'); in_ul = False
            if in_ol: html_lines.append('</ol>'); in_ol = False
            if in_table: html_lines.append('</table>'); in_table = False
            flush_pending()
            continue

        # Skip separator lines
        if re.match(r'^[-=]{3,}$', stripped):
            continue

        # Clean markdown
        stripped = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', stripped)
        stripped = re.sub(r'\*(.*?)\*', r'<em>\1</em>', stripped)
        stripped = re.sub(r'#{1,6}\s*', '', stripped)
        stripped = re.sub(r'\[(.*?)\]\(.*?\)', r'\1', stripped)

        # SECTION headers — force page break before each (except first)
        if re.match(r'^SECTION\s+\d+', stripped, re.IGNORECASE):
            flush_pending()
            if in_ul: html_lines.append('</ul>'); in_ul = False
            if in_ol: html_lines.append('</ol>'); in_ol = False
            if in_table: html_lines.append('</table>'); in_table = False
            is_first = not any('section-header' in l for l in html_lines)
            css = 'section-header no-break' if is_first else 'section-header'
            html_lines.append(f'<div class="{css}">{stripped}</div>')
            continue

        # Numbered subsections like 1.1, 2.3 — store as pending
        if re.match(r'^\d+\.\d+\s+', stripped):
            flush_pending()
            if in_ul: html_lines.append('</ul>'); in_ul = False
            if in_ol: html_lines.append('</ol>'); in_ol = False
            if in_table: html_lines.append('</table>'); in_table = False
            pending_heading = f'<h2>{stripped}</h2>'
            continue

        # ALL CAPS headings — store as pending
        clean_check = re.sub(r'<[^>]+>', '', stripped)
        if clean_check.isupper() and len(clean_check) > 4 and not clean_check.startswith('-'):
            flush_pending()
            if in_ul: html_lines.append('</ul>'); in_ul = False
            if in_ol: html_lines.append('</ol>'); in_ol = False
            if in_table: html_lines.append('</table>'); in_table = False
            pending_heading = f'<h3>{stripped}</h3>'
            continue

        # Tables — wrap heading+table in a no-break div
        if '|' in stripped and stripped.count('|') >= 2:
            if not in_table:
                # If there's a pending heading, start the wrapper div with it
                if pending_heading:
                    html_lines.append('<div class="keep-together">')
                    html_lines.append(pending_heading)
                    pending_heading = None
                html_lines.append('<table>')
                in_table = True
            if re.match(r'^[\-\|\ :]+$', stripped):
                continue
            cells = [c.strip() for c in stripped.split('|') if c.strip()]
            if not cells:
                continue
            prev = html_lines[-1] if html_lines else ''
            if prev == '<table>':
                row = ''.join([f'<th>{c}</th>' for c in cells])
                html_lines.append(f'<tr>{row}</tr>')
            else:
                is_total = cells[0].upper().startswith('TOTAL')
                row_class = ' class="total-row"' if is_total else ''
                row = ''.join([f'<td>{c}</td>' for c in cells])
                html_lines.append(f'<tr{row_class}>{row}</tr>')
            continue
        else:
            if in_table:
                html_lines.append('</table>')
                # Close the keep-together wrapper if it was opened
                if any('<div class="keep-together">' in l for l in html_lines[-20:]):
                    html_lines.append('</div>')
                in_table = False

        # Numbered lists
        if re.match(r'^\d+\.\s+', stripped):
            flush_pending()
            if not in_ol:
                if in_ul: html_lines.append('</ul>'); in_ul = False
                html_lines.append('<ol>')
                in_ol = True
            content = re.sub(r'^\d+\.\s+', '', stripped)
            html_lines.append(f'<li>{content}</li>')
            continue
        else:
            if in_ol:
                html_lines.append('</ol>')
                in_ol = False

        # Bullet points
        if re.match(r'^[-*•]\s+', stripped):
            flush_pending()
            if not in_ul:
                html_lines.append('<ul>')
                in_ul = True
            content = re.sub(r'^[-*•]\s+', '', stripped)
            html_lines.append(f'<li>{content}</li>')
            continue
        else:
            if in_ul:
                html_lines.append('</ul>')
                in_ul = False

        # Highlight boxes
        clean = re.sub(r'<[^>]+>', '', stripped).upper()
        if any(w in clean for w in ['URGENT', 'CRITICAL', 'RED FLAG', 'WARNING']):
            flush_pending()
            html_lines.append(f'<div class="highlight-box red">{stripped}</div>')
            continue
        if any(w in clean for w in ['TOTAL ANNUAL', 'NET BENEFIT', 'ROI:', 'BREAK-EVEN']):
            flush_pending()
            html_lines.append(f'<div class="highlight-box green">{stripped}</div>')
            continue

        # Default paragraph
        flush_pending()
        html_lines.append(f'<p>{stripped}</p>')

    # Close any open tags
    if in_ul: html_lines.append('</ul>')
    if in_ol: html_lines.append('</ol>')
    if in_table:
        html_lines.append('</table>')
        html_lines.append('</div>')
    flush_pending()

    return '\n'.join(html_lines)  

def generate_pdf(analysis_text: str, form_data: dict) -> bytes:

    company_name = form_data.get("Company name", "Your Business")
    industry = form_data.get("Industry", "Business")
    employees = form_data.get("Company size", "N/A")
    contact_name = form_data.get("Contact Person Name", "")
    date = datetime.now().strftime("%B %d, %Y")

    # Replace any placeholders the AI left in
    analysis_text = analysis_text.replace("[Business Name]", company_name)
    analysis_text = analysis_text.replace("[Insert Date]", date)
    analysis_text = analysis_text.replace("[ABC]", contact_name)
    analysis_text = analysis_text.replace("[Your Name]", "Welvom AI Team")
    analysis_text = analysis_text.replace("[Contact Person]", contact_name)

    report_html = convert_to_html(analysis_text)

    with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
        template_str = f.read()

    template = Template(template_str)
    rendered_html = template.render(
        company_name=company_name,
        industry=industry,
        employees=employees,
        date=date,
        report_html=report_html
    )

    pdf_bytes = HTML(string=rendered_html).write_pdf()
    return pdf_bytes