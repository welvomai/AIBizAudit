import os
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from dotenv import load_dotenv

load_dotenv()

client = MistralClient(api_key=os.getenv("MISTRAL_API_KEY"))

def analyze_business(form_data: dict) -> str:

    answers_text = "\n".join([
        f"- {key}: {value}"
        for key, value in form_data.items()
    ])

    prompt = f"""
You are a senior AI business consultant producing a formal, data-rich AI Transformation Audit Report for a business. 
This report will be sent as a PDF to the business owner and must look and feel like a premium consulting deliverable.

The business has answered the following audit questions:

{answers_text}

---

Generate a comprehensive AI Transformation Audit Report with ALL of the following sections. 
Be specific, use real numbers, estimate costs and savings based on industry benchmarks, 
and tailor every section to the business's specific industry and answers.

---

SECTION 1: EXECUTIVE BRIEFING

1.1 LETTER FROM WELVOM AI LEADERSHIP
Write a personalized letter addressed to the business owner by name (use their name from the form data).
Mention their industry, company name, and 3 specific problems you found.
Include:
- A statement about the current state of AI in their industry
- Total estimated annual losses from inefficiencies (calculate a specific Rs. figure)
- The transformation opportunity with ROI numbers
- Monthly cost of inaction (specific Rs. figure)

1.2 EXECUTIVE SUMMARY
Include:
- Business snapshot table (company name, industry, employees, location)
- Critical findings dashboard listing the top 5-6 urgent issues with:
  * Current state
  * Industry benchmark
  * Gap
  * Annual financial impact in Rs.
- A TOTAL ESTIMATED ANNUAL LOSS figure
- List of strengths to build on

---

SECTION 2: COMPREHENSIVE DEPARTMENT ANALYSIS

Analyze ALL of these departments that are relevant to the business. For each one:
- Write CURRENT STATE ASSESSMENT with specific observations from their answers
- Show INDUSTRY COMPARISON with benchmarks
- List KEY PERFORMANCE INDICATORS (current baseline vs 12-month targets)
- Give ACTIONABLE RECOMMENDATIONS split into: Immediate (Week 1-4), Short-Term (Months 2-3), Medium-Term (Months 4-6)
- Show MEASURABLE OUTCOMES with Rs. impact

Departments to cover:
2.1 Leadership & Strategy
2.2 Customer Experience & Support
2.3 Human Resources & Workforce
2.4 Sales & Marketing
2.5 Operations & Process Efficiency
2.6 Finance & Administration
2.7 Technology & IT Infrastructure

---

SECTION 3: MAN-HOUR SAVINGS ANALYSIS

3.1 CURRENT TIME INVESTMENT BREAKDOWN
Estimate current weekly manual hours by department based on their employee count and industry.
List each department with estimated hours per week for manual tasks.
Calculate: Total Weekly Manual Hours and Total Annual Manual Hours.

3.2 AI AUTOMATION POTENTIAL BY DEPARTMENT
For each department, list:
- Each manual task
- Hours saved per week
- Percentage automation
- Annual hours saved
- Financial value in Rs.

3.3 SUMMARY TABLE
Create a table with columns:
Department | Weekly Hrs Saved | Annual Hrs Saved | Financial Value (Rs.)
Include a TOTAL row at the bottom.
State the overall automation percentage.

---

SECTION 4: COST REDUCTION OPPORTUNITIES

4.1 Direct cost savings with specific Rs. figures
4.2 Operational expense reduction
4.3 Total Annual Savings Projection with a clear number

---

SECTION 5: 90-180 DAY IMPLEMENTATION ROADMAP

Break into 6 phases:
- Phase 1 (Days 0-30): Foundation
- Phase 2 (Days 31-60): Quick Wins
- Phase 3 (Days 61-90): Core Systems
- Phase 4 (Days 91-120): Integration
- Phase 5 (Days 121-150): Optimization
- Phase 6 (Days 151-180): Scaling

For each phase list 3-5 specific actions.

---

SECTION 6: 12-MONTH ROI PROJECTIONS

6.1 Investment required (specific Rs. figures per tool/system)
6.2 Monthly savings accrual (month by month)
6.3 Break-even point (which month)
6.4 Net ROI at end of Year 1
6.5 3-year financial impact

---

SECTION 7: STRATEGIC RECOMMENDATIONS

7.1 Immediate Actions (Week 1)
7.2 Short-Term Priorities (Months 1-3)
7.3 Medium-Term Initiatives (Months 4-6)
7.4 Long-Term Vision (Months 7-12)

---

SECTION 8: NEXT STEPS & CALL TO ACTION

8.1 Immediate Action Checklist (5-7 items)
8.2 Partnership Opportunities with Welvom AI
8.3 Contact details:
    Email: contact@welvom.com
    Website: www.welvom.com
    Phone: +91 XXXXXXXXXX

---

IMPORTANT FORMATTING RULES:
- Use clear section headings exactly as shown above
- Use bullet points with - for lists
- Use ALL CAPS for section titles
- For tables use this exact format:
  Column1 | Column2 | Column3
  --------------------------------
  Value1   | Value2   | Value3
- Always give specific Rs. figures
- Always compare to industry benchmarks
- Always include a 12-month target alongside every current metric
- Tailor everything to their specific industry
- Tone should be professional, urgent, and data-driven
- Do not cut any section short
"""

    response = client.chat(
        model="mistral-large-latest",
        messages=[
            ChatMessage(role="user", content=prompt)
        ]
    )

    return response.choices[0].message.content