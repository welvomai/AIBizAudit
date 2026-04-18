import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from mistralai.client import MistralClient
from mistralai.models.chat_completion import ChatMessage
from app.product_catalog import (
    curate_department_recommendations,
    product_catalog_status,
)

load_dotenv()

_api_key = os.getenv("MISTRAL_API_KEY")
client = MistralClient(api_key=_api_key, timeout=180, max_retries=5) if _api_key else None


def _stringify_form_data(form_data: dict) -> str:
    lines = []
    for key, value in form_data.items():
        clean_value = value if value not in (None, "") else "Not provided"
        lines.append(f"- {key}: {clean_value}")
    return "\n".join(lines)


def _extract_json_block(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in AI response")
    return text[start : end + 1]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parts = [part.strip("- ").strip() for part in value.split("\n")]
        return [part for part in parts if part]
    return [str(value).strip()]


def _first_value(form_data: dict, *possible_keys: str, default: str = "Not provided") -> str:
    for key in possible_keys:
        if key in form_data and str(form_data[key]).strip():
            return str(form_data[key]).strip()
        for existing_key, value in form_data.items():
            if existing_key.lower().strip() == key.lower().strip() and str(value).strip():
                return str(value).strip()
    return default



_PLACEHOLDER_MARKERS = (
    "assumption based on available data",
    "assessment in progress",
    "to be refined",
    "pending ai analysis",
    "to be estimated by analyst",
    "not provided",
    "n/a",
)


def _is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip().lower()
        if not text:
            return True
        return any(marker in text for marker in _PLACEHOLDER_MARKERS)
    if isinstance(value, list):
        return not any(not _is_placeholder(item) for item in value)
    if isinstance(value, dict):
        return not bool(value)
    return False


def _prefer_meaningful(value: Any, fallback: Any) -> Any:
    return fallback if _is_placeholder(value) else value


def _extract_number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    matches = re.findall(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not matches:
        return default
    return float(matches[0])


def _estimate_employee_count(company_size: str) -> int:
    text = str(company_size or "").strip().lower()
    values = [int(match) for match in re.findall(r"\d+", text)]
    if len(values) >= 2:
        return max(5, round((values[0] + values[1]) / 2))
    if len(values) == 1:
        return max(5, values[0])
    if "1-10" in text or "small" in text:
        return 8
    if "11-50" in text:
        return 30
    if "51-200" in text or "medium" in text:
        return 125
    if "201-500" in text:
        return 350
    if "500+" in text or "enterprise" in text:
        return 700
    return 75


def _estimate_revenue_lakh(revenue_text: str) -> float:
    text = str(revenue_text or "").strip().lower().replace("rs.", "").replace("rs", "")
    values = [float(match) for match in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))]
    if not values:
        return 300.0
    midpoint = sum(values[:2]) / 2 if len(values) >= 2 else values[0]
    if "cr" in text or "crore" in text:
        return midpoint * 100
    if "lakh" in text or text.endswith("l"):
        return midpoint
    if midpoint > 100000:
        return midpoint / 100000
    return midpoint


def _format_inr(amount_inr: float) -> str:
    lakh = amount_inr / 100000
    if lakh >= 100:
        return f"Rs. {lakh / 100:.1f}Cr"
    if lakh >= 1:
        return f"Rs. {lakh:.1f}L"
    return f"Rs. {amount_inr:,.0f}"


def _format_inr_range(low_inr: float, high_inr: float) -> str:
    return f"{_format_inr(low_inr)}-{_format_inr(high_inr)}"


def _round_to_nearest_five(value: float) -> int:
    return int(round(value / 5.0) * 5)


def _size_factor(employee_count: int) -> float:
    if employee_count <= 20:
        return 0.7
    if employee_count <= 50:
        return 0.85
    if employee_count <= 200:
        return 1.0
    if employee_count <= 500:
        return 1.2
    return 1.4


def _loaded_hourly_cost(form_data: dict) -> float:
    industry = _first_value(form_data, "Industry", default="Business").lower()
    base_salary_lakh = {
        "manufacturing": 4.8,
        "retail": 4.2,
        "healthcare": 5.6,
        "logistics": 4.6,
        "technology": 8.5,
        "it": 8.5,
        "education": 4.4,
        "finance": 7.2,
    }
    annual_salary_lakh = 5.5
    for key, value in base_salary_lakh.items():
        if key in industry:
            annual_salary_lakh = value
            break
    return annual_salary_lakh * 100000 * 1.3 / 2080


def _estimated_business_case(form_data: dict) -> dict:
    employee_count = _estimate_employee_count(_first_value(form_data, "Company size", default="51-200"))
    revenue_lakh = _estimate_revenue_lakh(_first_value(form_data, "Annual Revenue", default="1-5Cr"))
    hourly_cost = _loaded_hourly_cost(form_data)
    scale = _size_factor(employee_count)

    annual_cost_low = 12 * hourly_cost * _round_to_nearest_five(285 * scale)
    annual_cost_high = 12 * hourly_cost * _round_to_nearest_five(415 * scale)
    annual_savings_low = annual_cost_low * 1.25
    annual_savings_high = annual_cost_high * 1.45
    investment_lakh = max(6.0, min(revenue_lakh * 0.035, 24.0))
    investment_inr = investment_lakh * 100000
    monthly_savings_mid = ((annual_savings_low + annual_savings_high) / 2) / 12
    break_even_months = max(4, min(10, round(investment_inr / monthly_savings_mid)))
    roi_low = round((annual_savings_low - investment_inr) / investment_inr * 100)
    roi_high = round((annual_savings_high - investment_inr) / investment_inr * 100)
    roi_low = max(20, roi_low)
    roi_high = max(roi_low + 10, roi_high)
    three_year_net = round(((annual_savings_low + annual_savings_high) / 2) * 3 - investment_inr)

    return {
        "employee_count": employee_count,
        "revenue_lakh": revenue_lakh,
        "annual_loss_estimate": _format_inr_range(annual_cost_low, annual_cost_high),
        "annual_savings_estimate": _format_inr_range(annual_savings_low, annual_savings_high),
        "investment_required": _format_inr(investment_inr),
        "break_even": f"{break_even_months}-{break_even_months + 1} months",
        "year_one_roi": f"{roi_low}-{roi_high}%",
        "three_year_impact": _format_inr(three_year_net),
    }


def _default_department(title: str, form_data: dict) -> dict:
    company_size = _first_value(form_data, "Company size")
    industry = _first_value(form_data, "Industry")
    crm_usage = _first_value(form_data, "CRM system usage", "CRM usage level")
    automation_level = _first_value(form_data, "Workflow automation level")
    cloud_adoption = _first_value(form_data, "Cloud adoption")
    operational_efficiency = _first_value(form_data, "Operational efficiency")
    response_time = _first_value(form_data, "Average lead response time (hours)", "Response time to operational issues")
    turnover = _first_value(form_data, "Annual employee turnover rate (%)")
    conversion = _first_value(form_data, "Lead to customer conversion rate (%)")
    reporting_frequency = _first_value(form_data, "Financial reporting frequency")
    uptime = _first_value(form_data, "System uptime(%)")
    employee_count = _estimate_employee_count(company_size)
    scale = _size_factor(employee_count)
    hourly_cost = _loaded_hourly_cost(form_data)
    response_hours = max(1.0, _extract_number(response_time, 4.0))
    turnover_pct = _extract_number(turnover, 18.0) or 18.0
    conversion_pct = _extract_number(conversion, 40.0) or 40.0
    ops_response = max(2.0, _extract_number(_first_value(form_data, "Response time to operational issues"), 12.0))
    uptime_pct = _extract_number(uptime, 97.0) or 97.0
    time_to_hire = max(10.0, _extract_number(_first_value(form_data, "Average time to hire (days)", "Average time to hire(days)"), 30.0))
    sales_cycle_days = max(7.0, _extract_number(_first_value(form_data, "Average sales cycle length (days)"), 25.0))

    profiles = {
        "Leadership & Strategy": {
            "hours": (30, 45),
            "cost_multiplier": 1.45,
            "impact_multiplier": 1.35,
            "current_state_assessment": [
                f"Leadership reporting is still assembled manually across a {company_size} {industry.lower()} business, which slows weekly decision-making and pushes issue visibility into month-end reviews.",
                f"With {automation_level.lower()} workflow automation, management is spending too much executive time reconciling numbers instead of acting on exceptions."
            ],
            "industry_comparison": f"Industry-average {industry.lower()} firms of this size typically close operating reviews within 24-48 hours; best-in-class teams run daily dashboard-led reviews with same-day variance alerts and top-quartile decision support.",
            "benchmark_comparison": "Current leadership visibility is behind industry average and well outside best-in-class performance on live MIS, variance alerts, and escalation discipline.",
            "kpis": [
                {"metric": "Decision turnaround", "current": "3-5 days", "target_12_months": "<24 hours"},
                {"metric": "Leadership dashboard coverage", "current": "45-55%", "target_12_months": "90%+"},
                {"metric": "Strategic initiative tracking", "current": "Manual", "target_12_months": "Automated weekly visibility"},
            ],
            "recommendations": {
                "immediate": [
                    "Replace manual review packs with a single operating dashboard covering sales, production, cash, and service exceptions.",
                    "Set automated variance alerts for missed targets so leadership is not dependent on end-of-week summaries."
                ],
                "short_term": [
                    "Automate board and management MIS generation from ERP, CRM, and finance data instead of manual consolidation.",
                    "Introduce AI-generated decision briefs that flag bottlenecks, exceptions, and commercial risks every week."
                ],
                "medium_term": [
                    "Build a leadership command center with forecast, margin, delivery, and capacity signals refreshed daily.",
                    "Use predictive planning models to prioritise initiatives by payback, risk, and execution capacity."
                ],
            },
            "measurable_outcomes": [
                "Cut management reporting effort by 35-45% and recover 8-12 leadership hours each month.",
                "Reduce delayed decision-making and execution slippage worth 1.2-1.5x the direct labour savings over 12 months."
            ],
        },
        "Customer Experience & Support": {
            "hours": (40, 60),
            "cost_multiplier": 1.15,
            "impact_multiplier": 1.6,
            "current_state_assessment": [
                f"Customer handling is anchored around {crm_usage.lower()} CRM maturity, which means follow-ups, complaint tracking, and case visibility are fragmented.",
                f"A lead response time of {response_hours:.0f} hours is outside the standard for high-conversion teams and is creating avoidable revenue leakage."
            ],
            "industry_comparison": f"Industry-average response time for well-run teams is typically 1-2 hours; best-in-class operators respond in under 30 minutes with SLA-driven routing and closed-loop follow-up.",
            "benchmark_comparison": "Current responsiveness is behind industry average and significantly behind best-in-class support teams, especially on first response discipline, CRM hygiene, and escalation visibility.",
            "kpis": [
                {"metric": "Lead / case response time", "current": f"{response_hours:.0f} hours", "target_12_months": "<1 hour"},
                {"metric": "CRM maturity", "current": crm_usage, "target_12_months": "Advanced with workflow automation"},
                {"metric": "Customer retention consistency", "current": "82-86%", "target_12_months": "90%+"},
            ],
            "recommendations": {
                "immediate": [
                    "Set up automated lead assignment, reminder sequences, and escalation rules inside the CRM.",
                    "Create a daily ageing report for open enquiries, unresolved complaints, and at-risk accounts."
                ],
                "short_term": [
                    "Deploy AI-assisted response drafting and ticket classification to shorten first-touch and follow-up time.",
                    "Standardise service SLAs by channel so the team stops operating through inbox-driven firefighting."
                ],
                "medium_term": [
                    "Add churn-risk and repeat-issue detection so support and sales teams can intervene before revenue drops.",
                    "Integrate CRM, support, and feedback data into one customer health view for leadership review."
                ],
            },
            "measurable_outcomes": [
                "Reduce first-response delay by 60-75% and recover 15-25 service hours per month.",
                "Lift conversion and retention discipline enough to create commercial upside beyond direct support cost savings."
            ],
        },
        "Human Resources & Workforce": {
            "hours": (28, 42),
            "cost_multiplier": 1.0,
            "impact_multiplier": 1.3,
            "current_state_assessment": [
                f"HR is operating with inconsistent workflows across hiring, onboarding, attendance, and employee query handling, which creates avoidable coordination loss in a {company_size} company.",
                f"Turnover pressure of {turnover_pct:.0f}% and a hiring cycle near {time_to_hire:.0f} days indicate weak workforce visibility and unnecessary process drag."
            ],
            "industry_comparison": f"Industry-average HR teams of this size run with 15-20% lower admin load than manual environments; best-in-class teams automate screening, onboarding, policy queries, and attendance workflows.",
            "benchmark_comparison": "Current HR operations are around industry average for basic administration but behind best-in-class on time-to-hire, employee service speed, and workforce analytics.",
            "kpis": [
                {"metric": "Employee turnover", "current": f"{turnover_pct:.0f}%", "target_12_months": f"{max(10, turnover_pct - 4):.0f}-{max(12, turnover_pct - 2):.0f}%"},
                {"metric": "Time to hire", "current": f"{time_to_hire:.0f} days", "target_12_months": f"{max(18, time_to_hire - 8):.0f}-{max(20, time_to_hire - 5):.0f} days"},
                {"metric": "HR process automation", "current": _first_value(form_data, "Payroll processing", "Attendance and leave management", default="Semi-automated"), "target_12_months": "Automated core workflows"},
            ],
            "recommendations": {
                "immediate": [
                    "Automate candidate screening, interview coordination, and joining checklists to remove email-heavy handoffs.",
                    "Launch a shared HR service tracker for onboarding, policy queries, leave approvals, and payroll exceptions."
                ],
                "short_term": [
                    "Deploy AI policy assistants and templated employee communications to cut repetitive HR support load.",
                    "Standardise workforce dashboards covering attrition, open roles, attendance exceptions, and onboarding completion."
                ],
                "medium_term": [
                    "Integrate recruitment, attendance, payroll, and performance data to build a single workforce operating view.",
                    "Use predictive attrition signals to identify teams with rising retention risk before exits increase."
                ],
            },
            "measurable_outcomes": [
                "Recover 10-18 HR hours per month and shorten hiring cycle time by 20-25%.",
                "Reduce preventable hiring and onboarding friction that typically creates 1.2-1.4x the direct admin cost in hidden productivity loss."
            ],
        },
        "Sales & Marketing": {
            "hours": (45, 70),
            "cost_multiplier": 1.2,
            "impact_multiplier": 1.8,
            "current_state_assessment": [
                f"A conversion rate around {conversion_pct:.0f}% shows demand, but the commercial engine is losing value through inconsistent follow-up, weak attribution, and basic CRM discipline.",
                f"With a sales cycle of {sales_cycle_days:.0f} days and {crm_usage.lower()} CRM usage, pipeline movement is slower and less predictable than required."
            ],
            "industry_comparison": f"Industry-average revenue teams run with tighter funnel tracking and shorter follow-up intervals; best-in-class teams automate lead scoring, outreach cadence, and campaign-to-revenue attribution.",
            "benchmark_comparison": "Current sales operations are behind industry average on funnel visibility and well behind best-in-class on response discipline, CRM cleanliness, and forecast predictability.",
            "kpis": [
                {"metric": "Lead-to-customer conversion", "current": f"{conversion_pct:.0f}%", "target_12_months": f"{conversion_pct + 8:.0f}-{conversion_pct + 12:.0f}%"},
                {"metric": "Sales cycle", "current": f"{sales_cycle_days:.0f} days", "target_12_months": f"{max(14, sales_cycle_days - 6):.0f}-{max(16, sales_cycle_days - 4):.0f} days"},
                {"metric": "Marketing ROI visibility", "current": _first_value(form_data, "Marketing ROI tracking", default="Partial"), "target_12_months": "Campaign-level tracking"},
            ],
            "recommendations": {
                "immediate": [
                    "Enforce automated CRM stage updates, lead ownership, and same-day follow-up rules for every qualified enquiry.",
                    "Set a weekly pipeline hygiene review that flags stagnant deals, duplicate records, and unworked leads."
                ],
                "short_term": [
                    "Deploy AI-assisted lead scoring, proposal drafting, and next-best-action prompts for sales teams.",
                    "Connect campaign data to pipeline and order outcomes so marketing spend can be reallocated by conversion performance."
                ],
                "medium_term": [
                    "Introduce forecast models that combine deal health, response speed, and historical close patterns.",
                    "Automate nurture journeys for cold, warm, and repeat-buy segments to increase throughput without adding headcount."
                ],
            },
            "measurable_outcomes": [
                "Recover 18-28 commercial hours per month and improve conversion by 8-12 points within 12 months.",
                "Create revenue upside materially larger than direct labour savings by reducing lead leakage and shortening sales cycle time."
            ],
        },
        "Operations & Process Efficiency": {
            "hours": (65, 95),
            "cost_multiplier": 1.1,
            "impact_multiplier": 1.7,
            "current_state_assessment": [
                f"Operations reflect {automation_level.lower()} automation and an efficiency score around {operational_efficiency}, leaving too many manual handoffs across issue tracking, approvals, and status follow-up.",
                f"An operational response time of roughly {ops_response:.0f} hours signals preventable delay in escalation handling, especially where cycle-time discipline matters."
            ],
            "industry_comparison": f"Industry-average operators at this scale usually remove 20-30% of coordination effort through workflow controls; best-in-class teams automate routing, exception handling, and live production visibility.",
            "benchmark_comparison": "Current operations are behind industry average on response speed and significantly behind best-in-class on cross-functional workflow orchestration, SLA discipline, and exception management.",
            "kpis": [
                {"metric": "Operational efficiency", "current": str(operational_efficiency), "target_12_months": "4.2 / 5"},
                {"metric": "Workflow automation level", "current": automation_level, "target_12_months": "High"},
                {"metric": "Issue response time", "current": f"{ops_response:.0f} hours", "target_12_months": f"{max(3, ops_response * 0.5):.0f}-{max(4, ops_response * 0.65):.0f} hours"},
            ],
            "recommendations": {
                "immediate": [
                    "Map the top three delay-prone workflows across production, dispatch, procurement, and quality exception handling.",
                    "Introduce automated routing and SLA timers for approvals, incident ownership, and overdue task escalation."
                ],
                "short_term": [
                    "Deploy AI-assisted issue triage and root-cause tagging so repeat bottlenecks stop being managed manually.",
                    "Create a live operations dashboard covering ageing tasks, blocked orders, exception counts, and turnaround time."
                ],
                "medium_term": [
                    "Integrate ERP, production, and service workflows so cross-functional teams work from one operational queue.",
                    "Use predictive alerts for delays, stock exceptions, and handoff failures before they hit customer delivery."
                ],
            },
            "measurable_outcomes": [
                "Recover 25-40 operations hours per month and reduce avoidable coordination load by 30-40%.",
                "Lower delay-related cost and execution leakage at a scale that usually exceeds direct labour savings by 1.5-1.8x."
            ],
        },
        "Finance & Administration": {
            "hours": (32, 48),
            "cost_multiplier": 1.15,
            "impact_multiplier": 1.4,
            "current_state_assessment": [
                f"Finance is working off a {reporting_frequency.lower()} cadence, which is compliant but too slow for tight cash, margin, and variance control.",
                "Manual approvals, reconciliations, and reporting cycles are consuming disproportionate time in a function that should be rules-driven and exception-led."
            ],
            "industry_comparison": f"Industry-average finance teams are now moving toward weekly dashboard-led control; best-in-class teams automate reconciliations, approval workflows, and variance commentary.",
            "benchmark_comparison": "Current finance maturity is around industry average for basic reporting but behind best-in-class on close speed, forecast visibility, and approval automation.",
            "kpis": [
                {"metric": "Forecast accuracy", "current": _first_value(form_data, "Cash flow forecasting accuracy", "Revenue forecasting method", default="70-75%"), "target_12_months": "85-90%"},
                {"metric": "Reporting cycle", "current": reporting_frequency, "target_12_months": "Weekly dashboard + month-end close in 3-4 days"},
                {"metric": "Approval automation", "current": _first_value(form_data, "Expense approval system", default="Manual / email-driven"), "target_12_months": "Workflow-driven approvals"},
            ],
            "recommendations": {
                "immediate": [
                    "Automate approval chains for expenses, purchase requests, and payment exceptions instead of relying on inbox follow-up.",
                    "Set up a weekly cash, receivable, payable, and margin control dashboard for finance leadership."
                ],
                "short_term": [
                    "Deploy reconciliation workflows with exception flags so the team stops rechecking routine entries manually.",
                    "Use AI-generated commentary for month-end variance, overdue receivables, and margin erosion analysis."
                ],
                "medium_term": [
                    "Connect finance, procurement, and sales data to improve working-capital visibility and forecast accuracy.",
                    "Implement predictive cash-flow and receivables-risk views to reduce month-end surprises."
                ],
            },
            "measurable_outcomes": [
                "Recover 12-20 finance hours per month and shorten reporting cycle time by 25-35%.",
                "Reduce variance, approval delay, and reconciliation drag that typically creates 1.3-1.5x the direct admin cost in working-capital friction."
            ],
        },
        "Technology & IT Infrastructure": {
            "hours": (34, 52),
            "cost_multiplier": 1.25,
            "impact_multiplier": 1.45,
            "current_state_assessment": [
                f"Technology maturity is constrained by {cloud_adoption.lower()} cloud adoption and {automation_level.lower()} IT automation, which means integration work is still manual and slow to scale.",
                f"System uptime around {uptime_pct:.1f}% is serviceable, but it leaves room for avoidable disruption compared with modern operating standards."
            ],
            "industry_comparison": f"Industry-average IT teams are moving toward API-led integration and monitored workflows; best-in-class teams run with 99.5%+ uptime, strong observability, and reusable automation layers.",
            "benchmark_comparison": "Current IT maturity is behind best-in-class on uptime, integration speed, and automation depth, which limits how quickly the business can scale AI use cases.",
            "kpis": [
                {"metric": "System uptime", "current": f"{uptime_pct:.1f}%", "target_12_months": "99.5%+"},
                {"metric": "Cloud adoption", "current": cloud_adoption, "target_12_months": "Near-full for target workloads"},
                {"metric": "Integration maturity", "current": _first_value(form_data, "System integration level", default="Partial"), "target_12_months": "API-led and monitored"},
            ],
            "recommendations": {
                "immediate": [
                    "Document the highest-friction integrations and automate monitoring for uptime, sync failure, and data lag exceptions.",
                    "Prioritise one reusable integration layer instead of maintaining isolated point-to-point fixes."
                ],
                "short_term": [
                    "Deploy automation for user provisioning, routine support requests, and system health alerting.",
                    "Standardise data movement between CRM, ERP, finance, and reporting systems to reduce manual sync dependency."
                ],
                "medium_term": [
                    "Build governed APIs and event-based workflows so new AI automations do not rely on brittle manual interventions.",
                    "Introduce observability, access control, and audit trails that let the business scale automation without increasing risk."
                ],
            },
            "measurable_outcomes": [
                "Recover 12-22 IT hours per month and reduce repeat support and integration rework materially.",
                "Lower downtime and data-lag risk in a way that protects both operational throughput and future AI implementation velocity."
            ],
        },
    }

    default_profile = {
        "hours": (30, 45),
        "cost_multiplier": 1.0,
        "impact_multiplier": 1.25,
        "current_state_assessment": [
            f"{title} shows visible improvement opportunity based on the submitted assessment.",
            "AI-enabled process standardization should improve speed, visibility, and consistency in this function."
        ],
        "industry_comparison": f"{industry} peers with stronger digital operating systems typically outperform this function on speed, visibility, and controllability.",
        "benchmark_comparison": f"This function is likely below the {industry} industry average and best-in-class benchmarks in speed, automation, and decision support.",
        "benchmark_metrics": {
            "current_metric": "Current performance estimate based on the reported operating maturity.",
            "industry_average": "Comparable mid-market firms typically achieve stronger metric discipline in this area.",
            "best_in_class": "Best-in-class peers operate with automated workflows, tighter SLAs, and consistent targets.",
            "peer_percentile": "Approximately bottom 40-50% of same-segment peers.",
        },
        "kpis": [
            {"metric": "Current maturity", "current": "Assumption based on available data", "target_12_months": "Structured improvement"},
            {"metric": "Automation coverage", "current": "Partial / manual", "target_12_months": "High"},
            {"metric": "Process visibility", "current": "Limited", "target_12_months": "Dashboard driven"},
        ],
        "recommendations": {
            "immediate": [
                f"Map the current {title.lower()} workflow and identify the top 2 manual bottlenecks.",
                "Introduce a single KPI view so leaders can review this function weekly."
            ],
            "short_term": [
                "Add workflow automation or AI assistance to the highest-friction recurring task.",
                "Standardize ownership, SLAs, and data capture across the process."
            ],
            "medium_term": [
                "Integrate this function with the broader CRM / ERP / reporting stack.",
                "Use AI-driven forecasting, alerts, or prioritization to improve throughput."
            ],
        },
        "measurable_outcomes": [
            "Reduce manual effort and reporting lag within 90 days.",
            "Improve decision quality and service consistency over the next 12 months."
        ],
    }
    profile = {**default_profile, **profiles.get(title, {})}
    hours_low = _round_to_nearest_five(profile["hours"][0] * scale)
    hours_high = _round_to_nearest_five(profile["hours"][1] * scale)
    annual_cost_low = hours_low * 12 * hourly_cost * profile["cost_multiplier"]
    annual_cost_high = hours_high * 12 * hourly_cost * profile["cost_multiplier"]
    annual_impact_low = annual_cost_low * profile["impact_multiplier"]
    annual_impact_high = annual_cost_high * profile["impact_multiplier"]

    return {
        "title": title,
        "current_state_assessment": profile["current_state_assessment"],
        "industry_comparison": profile["industry_comparison"],
        "benchmark_comparison": profile["benchmark_comparison"],
        "benchmark_metrics": profile["benchmark_metrics"],
        "hours_lost_per_month": f"{hours_low}-{hours_high} hours",
        "annual_cost_impact": _format_inr_range(annual_cost_low, annual_cost_high),
        "kpis": profile["kpis"],
        "recommendations": profile["recommendations"],
        "measurable_outcomes": profile["measurable_outcomes"],
        "annual_impact": f"{_format_inr_range(annual_impact_low, annual_impact_high)} from labour recovery, faster cycle time, and reduced leakage.",
    }


def _default_calculations(form_data: dict) -> dict:
    company_size = _first_value(form_data, "Company size")
    industry = _first_value(form_data, "Industry")
    revenue = _first_value(form_data, "Annual Revenue")
    business_case = _estimated_business_case(form_data)
    return {
        "manhour_analysis_intro": f"For a {company_size} {industry.lower()} business in the {revenue} band, the largest automation upside usually sits in reporting, coordination, follow-up, approvals, and data reconciliation.",
        "current_time_breakdown": [
            {"department": "Leadership & Reporting", "manual_hours_per_week": "10-14", "manual_hours_per_year": "520-728", "assumption": "Manual review packs and status consolidation"},
            {"department": "Customer / Sales Follow-up", "manual_hours_per_week": "12-18", "manual_hours_per_year": "624-936", "assumption": "Lead response, reminders, CRM updates"},
            {"department": "HR Administration", "manual_hours_per_week": "8-12", "manual_hours_per_year": "416-624", "assumption": "Hiring coordination, onboarding, payroll queries"},
            {"department": "Operations Coordination", "manual_hours_per_week": "14-20", "manual_hours_per_year": "728-1040", "assumption": "Task tracking, issue escalation, handoffs"},
            {"department": "Finance / Admin", "manual_hours_per_week": "8-12", "manual_hours_per_year": "416-624", "assumption": "Approvals, reconciliation, reporting"},
        ],
        "automation_opportunities": [
            {"department": "Leadership & Reporting", "manual_task": "KPI pack creation", "hours_saved_per_week": "6-8", "automation_percentage": "55-65%", "annual_hours_saved": "312-416", "financial_value": "Rs. 3-5L"},
            {"department": "Customer / Sales", "manual_task": "Lead response and CRM updates", "hours_saved_per_week": "7-10", "automation_percentage": "60-70%", "annual_hours_saved": "364-520", "financial_value": "Rs. 4-6L"},
            {"department": "HR", "manual_task": "Screening and onboarding coordination", "hours_saved_per_week": "4-6", "automation_percentage": "45-55%", "annual_hours_saved": "208-312", "financial_value": "Rs. 2-4L"},
            {"department": "Operations", "manual_task": "Issue routing and task follow-up", "hours_saved_per_week": "6-9", "automation_percentage": "40-50%", "annual_hours_saved": "312-468", "financial_value": "Rs. 4-6L"},
            {"department": "Finance", "manual_task": "Approval and reporting workflows", "hours_saved_per_week": "4-6", "automation_percentage": "45-55%", "annual_hours_saved": "208-312", "financial_value": "Rs. 2-4L"},
        ],
        "summary_table": [
            {"department": "Leadership & Reporting", "weekly_hours_saved": "6-8", "annual_hours_saved": "312-416", "financial_value": "Rs. 3-5L"},
            {"department": "Customer / Sales", "weekly_hours_saved": "7-10", "annual_hours_saved": "364-520", "financial_value": "Rs. 4-6L"},
            {"department": "HR", "weekly_hours_saved": "4-6", "annual_hours_saved": "208-312", "financial_value": "Rs. 2-4L"},
            {"department": "Operations", "weekly_hours_saved": "6-9", "annual_hours_saved": "312-468", "financial_value": "Rs. 4-6L"},
            {"department": "Finance", "weekly_hours_saved": "4-6", "annual_hours_saved": "208-312", "financial_value": "Rs. 2-4L"},
        ],
        "overall_automation_percentage": "45-60%",
        "total_hours_lost_per_month": "275-360 hours",
        "fte_equivalent": "1.6-2.2 FTE",
        "automation_rationale": [
            "Automation should target high-volume manual handoffs and repetitive reporting tasks to free up leadership time for strategic decisions.",
            "Ai-enabled follow-up and routing can reduce wasted effort across customer, operations, and finance workflows.",
            "Standardizing data capture and approval flows is the most reliable way to realize the projected hours saved.",
        ],
        "key_assumptions": [
            "Current manual work estimates are based on standard midsize business operations in the reported industry.",
            "Automation value assumes the business adopts the recommended workflows and data standards within the first 90 days.",
            "Financial savings are driven by recovered labour time and faster decision cycles, not just software licensing.",
        ],
        "cost_reduction_opportunities": [
            {"category": "Process simplification", "description": "Reduce overhead in manual approvals, data reconciliation, and repeated reporting tasks.", "estimated_value": "Rs. 5-8L"},
            {"category": "CRM and customer follow-up", "description": "Automate response and tracking workflows to lower lost opportunity costs.", "estimated_value": "Rs. 4-6L"},
            {"category": "HR administration", "description": "Reduce manual HR coordination, attendance follow-up, and onboarding support work.", "estimated_value": "Rs. 2-4L"},
            {"category": "Finance and reconciliation", "description": "Cut repeated finance review cycles and manual reconciliation through intelligent workflows.", "estimated_value": "Rs. 3-5L"},
        ],
        "total_annual_savings_projection": "Rs. 14-23L from automation and process efficiency gains.",
        "investment_required": [
            {"item": "AI orchestration platform", "estimated_cost": "Rs. 3-5L", "purpose": "Create a centralized automation and data workflow layer."},
            {"item": "CRM and customer workflow upgrades", "estimated_cost": "Rs. 2-4L", "purpose": "Improve lead handling, follow-up, and service escalation."},
            {"item": "Reporting and analytics build", "estimated_cost": "Rs. 2-3L", "purpose": "Establish dashboards for execution and performance monitoring."},
            {"item": "Change and adoption support", "estimated_cost": "Rs. 1-2L", "purpose": "Ensure process owners adopt new automation workflows."},
        ],
        "monthly_savings_accrual": [
            {"month": "Month 1", "estimated_savings": "Rs. 0.5-1L", "commentary": "Foundational work and early automation pilots begin to capture savings."},
            {"month": "Month 2", "estimated_savings": "Rs. 1.5-2L", "commentary": "Initial automations and reporting improvements start reducing manual effort."},
            {"month": "Month 3", "estimated_savings": "Rs. 2.5-3.5L", "commentary": "Operational teams adopt workflows and productivity uplift grows."},
            {"month": "Month 4", "estimated_savings": "Rs. 3.5-4.5L", "commentary": "Core systems are connected and savings become more predictable."},
            {"month": "Month 5", "estimated_savings": "Rs. 4.5-5.5L", "commentary": "Workflow automation is steadily reducing manual follow-up and reporting chores."},
            {"month": "Month 6", "estimated_savings": "Rs. 5.5-6.5L", "commentary": "Savings acceleration is visible as teams move to analytics-driven decision making."},
        ],
        "break_even_point": business_case["break_even"],
        "net_roi_year_one": business_case["year_one_roi"],
        "three_year_impact": business_case["three_year_impact"],
    }


def _default_actions() -> dict:
    return {
        "roadmap": [
            {"phase": "Phase 1", "timeline": "Days 0-30", "focus": "Foundation", "actions": ["Audit workflows and data sources", "Prioritize 3 high-friction processes", "Define baseline KPIs"]},
            {"phase": "Phase 2", "timeline": "Days 31-60", "focus": "Quick Wins", "actions": ["Launch response and reporting automations", "Standardize CRM / ticketing hygiene", "Train process owners"]},
            {"phase": "Phase 3", "timeline": "Days 61-90", "focus": "Core Systems", "actions": ["Connect core systems", "Automate approvals and routing", "Roll out management dashboards"]},
            {"phase": "Phase 4", "timeline": "Days 91-120", "focus": "Integration", "actions": ["Sync customer, finance, and ops data", "Improve alerting and escalation flows", "Add role-based reporting"]},
            {"phase": "Phase 5", "timeline": "Days 121-150", "focus": "Optimization", "actions": ["Refine prompts and automations", "Tune KPIs against outcomes", "Expand high-performing use cases"]},
            {"phase": "Phase 6", "timeline": "Days 151-180", "focus": "Scaling", "actions": ["Scale across departments", "Harden governance and controls", "Prepare 12-month expansion plan"]},
        ],
        "strategic_recommendations": {
            "immediate_actions": [
                "Stabilize data capture and process ownership",
                "Pick the first two automations with fastest payback",
                "Create a weekly executive scorecard",
                "Assign clear owners for rollout and adoption",
            ],
            "short_term_priorities": [
                "Modernize CRM and follow-up workflows",
                "Reduce reporting and coordination overhead",
                "Improve workforce and service visibility",
                "Establish campaign and pipeline analytics",
            ],
            "medium_term_initiatives": [
                "Integrate core systems for shared visibility",
                "Introduce AI-assisted forecasting and prioritization",
                "Expand automation into finance and HR",
                "Standardize KPIs across business units",
            ],
            "long_term_vision": [
                "Operate from a unified, AI-assisted control layer",
                "Use predictive insights to improve throughput and retention",
                "Lower manual dependence across back-office functions",
                "Build scalable operating discipline for the next growth stage",
            ],
        },
        "risk_mitigation_plan": [
            "Establish governance and change management for each AI use case",
            "Protect sensitive data through access controls and secure integrations",
            "Validate outcomes with human review on high-impact decisions",
        ],
        "future_vision": [
            "Operate with AI-enabled workflows across customer, operations, finance, and HR",
            "Shift from reactive execution to predictive and prioritized decision-making",
            "Use data-driven insights to increase revenue, reduce cost, and improve staff productivity",
        ],
        "immediate_action_checklist": [
            "Confirm owners for the AI transformation program",
            "Baseline current KPIs before rollout",
            "Select the first automation use cases",
            "Prepare source systems and access",
            "Schedule a 90-day implementation kickoff",
        ],
        "partnership_opportunities": [
            "Workflow automation design and deployment",
            "Reporting and dashboard modernization",
            "AI operating model and change management support",
        ],
    }


def _normalize_report(report: dict, form_data: dict) -> dict:
    company_name = form_data.get("Company name", "Your Business")
    industry = form_data.get("Industry", "Business")
    contact_name = form_data.get("Contact person name") or form_data.get("Contact Person Name") or "Business Leader"
    years_in_business = (
        form_data.get("Years in business")
        or form_data.get("Years In Business")
        or "Not provided"
    )

    overview = report.get("overview") or {}
    executive = report.get("executive") or {}
    assessment = report.get("assessment") or {}
    calculations = report.get("calculations") or {}
    actions = report.get("actions") or {}
    default_actions = _default_actions()
    default_calculations = _default_calculations(form_data)
    default_report = _partial_base_report(form_data)
    default_overview = default_report.get("overview") or {}
    default_executive = default_report.get("executive") or {}

    departments = []
    for department in assessment.get("departments") or []:
        default_department = _default_department(department.get("title", "Department Analysis"), form_data)
        recommendations = department.get("recommendations") or {}
        departments.append(
            {
                "title": department.get("title", "Department Analysis"),
                "current_state_assessment": _prefer_meaningful(_as_list(department.get("current_state_assessment")), default_department.get("current_state_assessment", [])),
                "industry_comparison": _prefer_meaningful(department.get("industry_comparison"), default_department.get("industry_comparison", "")),
                "benchmark_comparison": _prefer_meaningful(department.get("benchmark_comparison"), default_department.get("benchmark_comparison", "")),
                "benchmark_metrics": {
                    "current_metric": _prefer_meaningful(
                        department.get("benchmark_metrics", {}).get("current_metric"),
                        default_department.get("benchmark_metrics", {}).get("current_metric", "Current performance estimate based on the reported operating maturity.")
                    ),
                    "industry_average": _prefer_meaningful(
                        department.get("benchmark_metrics", {}).get("industry_average"),
                        default_department.get("benchmark_metrics", {}).get("industry_average", "Comparable mid-market firms typically achieve stronger metric discipline in this area.")
                    ),
                    "best_in_class": _prefer_meaningful(
                        department.get("benchmark_metrics", {}).get("best_in_class"),
                        default_department.get("benchmark_metrics", {}).get("best_in_class", "Best-in-class peers operate with automated workflows, tighter SLAs, and consistent targets.")
                    ),
                    "peer_percentile": _prefer_meaningful(
                        department.get("benchmark_metrics", {}).get("peer_percentile"),
                        default_department.get("benchmark_metrics", {}).get("peer_percentile", "Approximately bottom 40-50% of same-segment peers.")
                    ),
                },
                "hours_lost_per_month": _prefer_meaningful(department.get("hours_lost_per_month"), default_department.get("hours_lost_per_month", "Not estimated")),
                "annual_cost_impact": _prefer_meaningful(department.get("annual_cost_impact"), default_department.get("annual_cost_impact", "Not estimated")),
                "kpis": department.get("kpis") or default_department.get("kpis", []),
                "recommendations": {
                    "immediate": _prefer_meaningful(_as_list(recommendations.get("immediate")), default_department.get("recommendations", {}).get("immediate", [])),
                    "short_term": _prefer_meaningful(_as_list(recommendations.get("short_term")), default_department.get("recommendations", {}).get("short_term", [])),
                    "medium_term": _prefer_meaningful(_as_list(recommendations.get("medium_term")), default_department.get("recommendations", {}).get("medium_term", [])),
                },
                "measurable_outcomes": _prefer_meaningful(_as_list(department.get("measurable_outcomes")), default_department.get("measurable_outcomes", [])),
                "annual_impact": _prefer_meaningful(department.get("annual_impact"), default_department.get("annual_impact", "Not estimated")),
            }
        )
    if not departments:
        departments = [
            _default_department(title, form_data)
            for title in [
                "Leadership & Strategy",
                "Customer Experience & Support",
                "Human Resources & Workforce",
                "Sales & Marketing",
                "Operations & Process Efficiency",
                "Finance & Administration",
                "Technology & IT Infrastructure",
            ]
        ]

    strategic = actions.get("strategic_recommendations") or {}

    normalized = {
        "company": {
            "name": company_name,
            "industry": industry,
            "size": form_data.get("Company size", "Not provided"),
            "revenue": form_data.get("Annual Revenue", "Not provided"),
            "contact_name": contact_name,
            "email": form_data.get("Email address") or form_data.get("Email Address") or "Not provided",
            "website": form_data.get("Website", "Not provided"),
            "years_in_business": years_in_business,
        },
        "overview": {
            "readiness_level": _prefer_meaningful(overview.get("readiness_level"), default_overview.get("readiness_level", "Emerging")),
            "overall_score": _prefer_meaningful(overview.get("overall_score"), default_overview.get("overall_score", "N/A")),
            "headline": _prefer_meaningful(overview.get("headline"), default_overview.get("headline", f"{company_name} has meaningful AI transformation potential across multiple business functions.")),
            "annual_loss_estimate": _prefer_meaningful(overview.get("annual_loss_estimate"), default_overview.get("annual_loss_estimate", "Estimated based on company scale and operational profile")),
            "annual_savings_estimate": _prefer_meaningful(overview.get("annual_savings_estimate"), default_overview.get("annual_savings_estimate", "Estimated based on automation and process improvement potential")),
            "break_even": _prefer_meaningful(overview.get("break_even"), default_overview.get("break_even", "Estimated 4-10 months depending on scope")),
            "year_one_roi": _prefer_meaningful(overview.get("year_one_roi"), default_overview.get("year_one_roi", "Estimated 25-45%")),
            "competitive_position": _prefer_meaningful(overview.get("competitive_position"), default_overview.get("competitive_position", "The business is currently at mid-market maturity with clear upside to best-in-class operating discipline.")),
            "financial_bridge": _prefer_meaningful(overview.get("financial_bridge"), default_overview.get("financial_bridge", {
                "current_loss": "Estimated current loss from manual work and coordination inefficiencies.",
                "recoverable_automation": "Estimated recoverable value from workflow automation and better execution control.",
                "additional_upside": "Additional upside from improved retention, faster closure, and lower rework.",
                "total_improvement_potential": "Aggregate improvement potential from operations, sales, and support efficiency gains.",
            })),
            "industry_insight": _prefer_meaningful(overview.get("industry_insight"), default_overview.get("industry_insight", "For this sector, the highest value drivers are customer response speed, workflow reliability, and tighter execution discipline.")),
            "strengths": _prefer_meaningful(_as_list(overview.get("strengths")), default_overview.get("strengths", [])),
            "priority_gaps": _prefer_meaningful(_as_list(overview.get("priority_gaps")), default_overview.get("priority_gaps", [])),
        },
        "executive": {
            "leadership_letter_title": _prefer_meaningful(executive.get("leadership_letter_title"), default_executive.get("leadership_letter_title", "Letter from Welvom AI Leadership")),
            "leadership_letter": _prefer_meaningful(_as_list(executive.get("leadership_letter")), default_executive.get("leadership_letter", [])),
            "brutal_insight": _prefer_meaningful(executive.get("brutal_insight"), default_executive.get("brutal_insight", "The current operating profile suggests a significant hidden drag on productivity that is slowing growth and leadership focus.")),
            "executive_summary": _prefer_meaningful(_as_list(executive.get("executive_summary")), default_executive.get("executive_summary", [])),
            "business_snapshot": executive.get("business_snapshot") or default_executive.get("business_snapshot") or {},
            "risk_of_inaction": _prefer_meaningful(executive.get("risk_of_inaction"), default_executive.get("risk_of_inaction", "If the current operating gaps persist, execution drag will compound and erode commercial momentum over the next 12 months.")),
            "critical_findings": executive.get("critical_findings") or default_executive.get("critical_findings") or [],
        },
        "assessment": {
            "departments": departments,
        },
        "calculations": {
            "manhour_analysis_intro": calculations.get("manhour_analysis_intro", default_calculations["manhour_analysis_intro"]),
            "current_time_breakdown": calculations.get("current_time_breakdown") or default_calculations["current_time_breakdown"],
            "automation_opportunities": calculations.get("automation_opportunities") or default_calculations["automation_opportunities"],
            "summary_table": calculations.get("summary_table") or default_calculations["summary_table"],
            "overall_automation_percentage": calculations.get("overall_automation_percentage", default_calculations["overall_automation_percentage"]),
            "total_hours_lost_per_month": calculations.get("total_hours_lost_per_month", default_calculations["total_hours_lost_per_month"]),
            "fte_equivalent": calculations.get("fte_equivalent", default_calculations["fte_equivalent"]),
            "automation_rationale": calculations.get("automation_rationale") or default_calculations["automation_rationale"],
            "key_assumptions": calculations.get("key_assumptions") or default_calculations["key_assumptions"],
            "cost_reduction_opportunities": calculations.get("cost_reduction_opportunities") or default_calculations["cost_reduction_opportunities"],
            "total_annual_savings_projection": calculations.get("total_annual_savings_projection", default_calculations["total_annual_savings_projection"]),
            "investment_required": calculations.get("investment_required") or default_calculations["investment_required"],
            "monthly_savings_accrual": calculations.get("monthly_savings_accrual") or default_calculations["monthly_savings_accrual"],
            "break_even_point": calculations.get("break_even_point", default_calculations.get("break_even_point", "To be estimated by analyst")),
            "net_roi_year_one": calculations.get("net_roi_year_one", default_calculations.get("net_roi_year_one", "To be estimated by analyst")),
            "three_year_impact": calculations.get("three_year_impact", default_calculations.get("three_year_impact", "To be estimated by analyst")),
        },
        "actions": {
            "roadmap": actions.get("roadmap") or default_actions["roadmap"],
            "strategic_recommendations": {
                "immediate_actions": _as_list(strategic.get("immediate_actions")) or default_actions["strategic_recommendations"]["immediate_actions"],
                "short_term_priorities": _as_list(strategic.get("short_term_priorities")) or default_actions["strategic_recommendations"]["short_term_priorities"],
                "medium_term_initiatives": _as_list(strategic.get("medium_term_initiatives")) or default_actions["strategic_recommendations"]["medium_term_initiatives"],
                "long_term_vision": _as_list(strategic.get("long_term_vision")) or default_actions["strategic_recommendations"]["long_term_vision"],
            },
            "risk_mitigation_plan": _as_list(actions.get("risk_mitigation_plan")) or default_actions["risk_mitigation_plan"],
            "future_vision": _as_list(actions.get("future_vision")) or default_actions["future_vision"],
            "immediate_action_checklist": _as_list(actions.get("immediate_action_checklist")) or default_actions["immediate_action_checklist"],
            "partnership_opportunities": _as_list(actions.get("partnership_opportunities")) or default_actions["partnership_opportunities"],
        },
    }

    snapshot = normalized["executive"]["business_snapshot"]
    if not snapshot:
        normalized["executive"]["business_snapshot"] = {
            "Company name": company_name,
            "Industry": industry,
            "Company size": form_data.get("Company size", "Not provided"),
            "Annual revenue": form_data.get("Annual Revenue", "Not provided"),
            "Years in business": years_in_business,
        }

    return normalized


def _fallback_report(form_data: dict, error_message: str = "") -> dict:
    company_name = form_data.get("Company name", "Your Business")
    industry = form_data.get("Industry", "Business")
    contact_name = form_data.get("Contact person name") or form_data.get("Contact Person Name") or "Business Leader"
    base_report = _partial_base_report(form_data)
    base_report["executive"]["leadership_letter"] = [
        f"Dear {contact_name},",
        f"We prepared this advisory view for {company_name} in the {industry} sector using the submitted business assessment.",
        "The AI narrative layer was unavailable or incomplete, so this version uses internal benchmark and financial modeling logic to keep the report decision-useful instead of returning placeholders.",
    ]
    base_report["executive"]["critical_findings"] = [
        {
            "area": "AI narrative generation",
            "current_state": "The model response did not return a complete structured report.",
            "benchmark": "A fully populated consulting report with tailored narrative in every section.",
            "gap": error_message or "AI output unavailable",
            "annual_impact": "No business-data loss. The financial estimates in this version come from fallback modeling rather than the narrative layer.",
        }
    ]
    return _normalize_report(base_report, form_data)


def _partial_base_report(form_data: dict) -> dict:
    company_name = _first_value(form_data, "Company name", "Company Name", default="Your Business")
    industry = _first_value(form_data, "Industry", default="Business")
    contact_name = _first_value(form_data, "Contact Person Name", "Contact person name", default="Business Leader")
    years_in_business = _first_value(form_data, "Years in business", "Years In Business", default="Not provided")
    business_case = _estimated_business_case(form_data)
    automation_level = _first_value(form_data, "Workflow automation level", default="Partial")
    crm_usage = _first_value(form_data, "CRM system usage", "CRM usage level", default="Basic")
    cloud_adoption = _first_value(form_data, "Cloud adoption", default="Partial")

    return {
        "overview": {
            "readiness_level": "Moderate",
            "overall_score": "63/100",
            "headline": f"{company_name} is leaving measurable value on the table because {automation_level.lower()} automation, {crm_usage.lower()} CRM maturity, and fragmented reporting are slowing execution across core functions.",
            "annual_loss_estimate": business_case["annual_loss_estimate"],
            "annual_savings_estimate": business_case["annual_savings_estimate"],
            "break_even": business_case["break_even"],
            "year_one_roi": business_case["year_one_roi"],
            "competitive_position": f"{company_name} is at industry average on basic digital adoption, but below best-in-class peers on workflow automation, response speed, and management visibility.",
            "financial_bridge": {
                "current_loss": f"The business is estimated to be losing {business_case['annual_loss_estimate']} annually to manual coordination and reporting work.",
                "recoverable_automation": f"Approximately {business_case['annual_savings_estimate']} can be recovered through targeted automation and workflow control.",
                "additional_upside": "Further upside comes from better customer retention, faster decision-making, and lower rework across support and operations.",
                "total_improvement_potential": f"Total improvement potential is meaningful once the automation case is combined with execution discipline and governance.",
            },
            "industry_insight": f"Within {industry}, the critical value drivers are faster case handling, tighter customer follow-up, and stronger execution discipline across sales and support.",
            "strengths": [
                f"Established operating base in {industry} with enough scale to justify automation investment.",
                f"Existing {crm_usage.lower()} system usage provides a starting point for better customer and revenue workflows.",
                f"{cloud_adoption} cloud adoption gives the business a practical path to connect reporting, workflows, and AI tooling.",
            ],
            "priority_gaps": [
                "Manual reporting and cross-functional coordination are still slowing decision velocity.",
                "Commercial and support workflows lack the response discipline expected from high-performing teams.",
                "Automation maturity is not yet strong enough to support predictable scale without adding admin overhead.",
            ],
        },
        "executive": {
            "leadership_letter_title": "AI Transformation Opportunity Summary",
            "leadership_letter": [
                f"Dear {contact_name},",
                f"{company_name} has a clear AI and automation case, but the current operating model is still carrying avoidable manual work, delayed decisions, and revenue leakage.",
                f"The business is absorbing roughly {business_case['annual_loss_estimate']} in annual inefficiency, with a recoverable opportunity of {business_case['annual_savings_estimate']} over the next 12 months.",
                f"The objective is to remove friction from leadership, customer, operations, finance, and workforce workflows fast enough to reach break-even in {business_case['break_even']}.",
            ],
            "executive_summary": [
                f"{company_name} is ready for a focused AI program, but the current operating stack is still too manual to scale cleanly.",
                "The largest value pools are in response speed, workflow routing, reporting automation, and exception-led management visibility.",
                f"A phased implementation must target quick-win process control first, then system integration and forecasting depth to deliver {business_case['year_one_roi']} year-one ROI.",
            ],
            "business_snapshot": {
                "Company name": company_name,
                "Industry": industry,
                "Company size": _first_value(form_data, "Company size", default="Not provided"),
                "Annual revenue": _first_value(form_data, "Annual Revenue", default="Not provided"),
                "Years in business": years_in_business,
            },
            "industry_insight": f"Within {industry}, the critical value drivers are faster case handling, tighter customer follow-up, and stronger execution discipline across sales and support.",
            "brutal_insight": f"The business is carrying a hidden productivity drain equivalent to multiple full-time resources, which is slowing growth and leadership focus.",
            "risk_of_inaction": "If current operating gaps persist, execution drag will compound and reduce commercial momentum over the next 12 months.",
            "critical_findings": [
                {
                    "area": "Operational efficiency",
                    "current_state": f"{automation_level} automation with visible reliance on manual follow-up and issue coordination",
                    "benchmark": "Industry-average operators automate 20-30% more workflow routing; best-in-class run live exception-led operations",
                    "gap": "Execution speed and coordination discipline are below modern operating standards",
                    "annual_impact": "Rs. 8-12L from recovered operational capacity and lower execution leakage",
                },
                {
                    "area": "Customer and revenue workflows",
                    "current_state": f"{crm_usage} CRM usage, but limited automation in follow-up, service routing, and pipeline control",
                    "benchmark": "Best teams enforce sub-1-hour response, guided follow-up, and campaign-to-revenue visibility",
                    "gap": "The business is leaving value on the table through slow response and incomplete funnel visibility",
                    "annual_impact": "Rs. 6-10L from better conversion discipline, faster response, and reduced lead leakage",
                },
                {
                    "area": "Management visibility",
                    "current_state": "Leadership visibility still depends too heavily on manually assembled updates and uneven process data",
                    "benchmark": "Industry leaders review daily dashboards with automated variance alerts and weekly AI-backed summaries",
                    "gap": "Decision quality is being limited by reporting lag and fragmented signals",
                    "annual_impact": "Rs. 4-7L from faster decision-making and fewer missed escalations",
                },
                {
                    "area": "Technology enablement",
                    "current_state": f"{cloud_adoption} cloud adoption with integration and automation maturity still short of scalable best practice",
                    "benchmark": "Best-in-class environments run 99.5%+ uptime, reusable integrations, and governed automation layers",
                    "gap": "System maturity is good enough to start, but not yet strong enough to scale AI without cleanup",
                    "annual_impact": "Rs. 3-5L from lower support rework, fewer sync issues, and faster rollout of automation use cases",
                },
            ],
        },
        "assessment": {"departments": []},
        "calculations": _default_calculations(form_data),
        "actions": _default_actions(),
    }


def _call_model(prompt: str, max_tokens: int) -> dict:
    response = client.chat(
        model="mistral-large-latest",
        messages=[ChatMessage(role="user", content=prompt)],
        temperature=0.2,
        max_tokens=max_tokens,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or ""
    return json.loads(_extract_json_block(content))


def _section_prompt(
    answers_text: str,
    section_name: str,
    schema_text: str,
    extra_rules: str = "",
    quality_rules: str = "",
) -> str:
    return f"""
You are a senior business analyst and AI transformation consultant creating a premium consulting report for a client.

Use the submitted business assessment below to produce the `{section_name}` section only.
Return ONLY valid JSON. Do not wrap it in markdown. Do not include commentary before or after the JSON.

BUSINESS ASSESSMENT:
{answers_text}

Output schema:
{schema_text}

Rules:
- Follow the Numbers Enforcement Rule: include exact values, precise ranges, and ROI math wherever relevant.
- Follow the Consulting Tone Rule: write in a confident, advisory, priority-driven voice with no passive hedging.
- Follow the No Repetition Rule: do not restate the submitted answers as a narrative summary.
- Write with the quality of a paid consulting deliverable, not a generic AI summary.
- Be detailed, concrete, and commercially useful.
- IMPORTANT: Do NOT leave any section vague.
- IMPORTANT: Always estimate numbers even if data is missing.
- IMPORTANT: Convert all inefficiencies into financial impact.
- Use specific numbers and Rs. estimates in every section, even when data is incomplete.
- Every inefficiency must be mapped to hours lost, cost impact, and annualized business impact.
- Use benchmarks, targets, timelines, and quantified outcomes wherever possible.
- Make every recommendation clearly tied to the client's submitted business assessment.
- Prefer explicit reasoning, examples, and realistic assumptions over vague generalities.
- Write like a senior consultant: direct, assertive, and clear about risks.
- Avoid generic language, platitudes, or repeated phrasing.
- Each department analysis must include unique problems, specific AI solutions, and quantified impact.
- Do not repeat the same problem statement, recommendation, or impact logic across departments.
- Compare every meaningful metric with both industry average and best-in-class standards wherever feasible.
- If exact source data is unavailable, use a realistic estimate and label it as an explicit estimate with supporting assumptions; never return placeholder phrases.
- Do not use words such as likely, probably, appears, could, should, may, or might in conclusions.
- If you need to qualify an estimate, use "based on available data" only as supporting language, not as a placeholder.
- Every model output must use either a concrete number or a precise range for hours, rupees, ROI, and break-even.
- Benchmarks must be explicit rather than implied and should reference percentile or ranked performance when comparing to industry average and best-in-class.
- If a field appears in the schema, populate it with meaningful content; do not omit it or leave it as a placeholder.
- Always populate the structured fields such as financial_bridge, brutal_insight, risk_of_inaction, and industry_insight when they are requested.
- If a response is missing, generate the best credible estimate with a clear assumption statement; never use placeholder text.
- Do not leave arrays empty; populate every requested list with strong, relevant points.
{extra_rules}
{quality_rules}
"""


def analyze_business(form_data: dict) -> dict:
    if not client:
        return _fallback_report(form_data, "Missing Mistral API configuration")

    answers_text = _stringify_form_data(form_data)
    report = {}
    catalog_state = product_catalog_status()

    try:
        overview_and_executive = _call_model(
            _section_prompt(
                answers_text,
                "overview_and_executive",
                """
{
  "overview": {
    "readiness_level": "string",
    "overall_score": "string",
    "headline": "string",
    "annual_loss_estimate": "string",
    "annual_savings_estimate": "string",
    "break_even": "string",
    "year_one_roi": "string",
    "competitive_position": "string",
    "financial_bridge": {
      "current_loss": "string",
      "recoverable_automation": "string",
      "additional_upside": "string",
      "total_improvement_potential": "string"
    },
    "industry_insight": "string",
    "strengths": ["string", "string", "string"],
    "priority_gaps": ["string", "string", "string"]
  },
  "executive": {
    "leadership_letter_title": "string",
    "leadership_letter": ["string", "string", "string"],
    "brutal_insight": "string",
    "executive_summary": ["string", "string", "string"],
    "business_snapshot": {
      "Company name": "string",
      "Industry": "string",
      "Company size": "string",
      "Annual revenue": "string",
      "Years in business": "string"
    },
    "risk_of_inaction": "string",
    "critical_findings": [
      {"area": "string", "current_state": "string", "benchmark": "string", "gap": "string", "annual_impact": "string"},
      {"area": "string", "current_state": "string", "benchmark": "string", "gap": "string", "annual_impact": "string"},
      {"area": "string", "current_state": "string", "benchmark": "string", "gap": "string", "annual_impact": "string"},
      {"area": "string", "current_state": "string", "benchmark": "string", "gap": "string", "annual_impact": "string"}
    ]
  }
}
""",
                "Use exactly 3 strengths, 3 priority_gaps, 3 executive_summary bullets, and 4 critical_findings.",
                """
- The headline should feel boardroom-ready and outcome-led.
- The leadership letter should sound personalized, premium, and urgent.
- Critical findings must include commercial impact, not just operational observations.
- Competitive position should compare this business with industry peers.
- Use realistic Indian business language for revenue, savings, and impact.
- Include a one-line brutal insight in executive_summary that quantifies the productivity equivalent of current inefficiency.
- Include a financial bridge explanation showing current inefficiency loss, recoverable automation, additional upside, and total improvement potential.
- Add industry-specific depth tied to the submitted Industry, such as ticket resolution SLAs, onboarding or deployment delays, support backlog impact, or churn risk in high-value accounts.
- Add a risk-of-inaction statement quantifying inefficiency growth if no changes are made.
""",
            ),
            max_tokens=1300,
        )
        report.update(overview_and_executive)
    except Exception:
        pass

    departments = []
    department_titles = [
        "Leadership & Strategy",
        "Customer Experience & Support",
        "Human Resources & Workforce",
        "Sales & Marketing",
        "Operations & Process Efficiency",
        "Finance & Administration",
        "Technology & IT Infrastructure",
    ]
    for title in department_titles:
        try:
            department_section = _call_model(
                _section_prompt(
                    answers_text,
                    f"department_analysis_{title}",
                    """
{
  "department": {
    "title": "{title}",
    "current_state_assessment": ["string", "string"],
    "industry_comparison": "string",
    "benchmark_comparison": "string",
    "benchmark_metrics": {
      "current_metric": "string",
      "industry_average": "string",
      "best_in_class": "string",
      "peer_percentile": "string"
    },
    "hours_lost_per_month": "string",
    "annual_cost_impact": "string",
    "kpis": [
      {"metric": "string", "current": "string", "target_12_months": "string"},
      {"metric": "string", "current": "string", "target_12_months": "string"},
      {"metric": "string", "current": "string", "target_12_months": "string"}
    ],
    "recommendations": {
      "immediate": ["string", "string"],
      "short_term": ["string", "string"],
      "medium_term": ["string", "string"]
    },
    "measurable_outcomes": ["string", "string"],
    "annual_impact": "string"
  }
}
""",
                    "Return exactly one department object matching the requested title.",
                    f"""
- Write this as a serious consulting assessment for the {title} function.
- Make the 2 current_state_assessment bullets specific to the submitted answers.
- Make the industry_comparison specific to typical industry performance for this function.
- Make the benchmark_comparison explicit and compare performance to industry average and best-in-class.
- hours_lost_per_month must be a numeric estimate or range, not a vague sentence.
- annual_cost_impact must convert lost hours into a realistic Rs. annual cost range.
- Quantify hours lost, annual cost impact, and convert inefficiencies into financial terms.
- The 3 KPIs must be realistic, relevant, and measurable within 12 months.
- Immediate actions should feel executable in 30 days, short_term actions in 60-90 days, and medium_term actions in 4-6 months.
- Measurable outcomes should include quantified operational or financial upside.
- annual_impact should contain a realistic Rs. range or a quantified business effect.
- Ensure this department narrative is distinct from other functions and does not repeat the same problem statements.
- Recommendations must solve the specific problems identified in this department, not generic automation themes.
- Benchmark comparisons must include current metric, industry average/range, best-in-class target, and a percentile or ranking statement such as "bottom 35% of peers".
- Populate benchmark_metrics with explicit current metric, industry average, best-in-class target, and peer percentile comparisons.
- Return all listed fields exactly as shown in the schema. Do not omit any field or return null/empty values.
- If a field cannot be confirmed from the assessment, provide a realistic estimate with concrete numbers or ranges.
- Do not use placeholder language for required fields; every listed field must contain report-ready content.
- Include one industry-specific operational insight using the submitted Industry, such as ticket SLA risk, deployment delay, onboarding friction, backlog impact, or churn pressure.
""",
                ),
                max_tokens=800,
            )
            departments.append(department_section.get("department") or _default_department(title, form_data))
        except Exception:
            departments.append(_default_department(title, form_data))
    report["assessment"] = {"departments": departments}

    calculations = report.get("calculations") or {}
    default_calculations = _default_calculations(form_data)
    try:
        time_and_automation = _call_model(
            _section_prompt(
                answers_text,
                "time_and_automation",
                """
{
  "calculations": {
    "manhour_analysis_intro": "string",
    "total_hours_lost_per_month": "string",
    "fte_equivalent": "string",
    "current_time_breakdown": [
      {"department": "string", "manual_hours_per_week": "string", "manual_hours_per_year": "string", "assumption": "string"}
    ],
    "automation_opportunities": [
      {"department": "string", "manual_task": "string", "hours_saved_per_week": "string", "automation_percentage": "string", "annual_hours_saved": "string", "financial_value": "string"}
    ],
    "summary_table": [
      {"department": "string", "weekly_hours_saved": "string", "annual_hours_saved": "string", "financial_value": "string"}
    ],
    "automation_rationale": ["string", "string", "string"],
    "key_assumptions": ["string", "string", "string"],
    "overall_automation_percentage": "string"
  }
}
""",
                "Use exactly 5 rows each in current_time_breakdown, automation_opportunities, and summary_table.",
                """
- Focus on realistic manual work patterns for this business size and industry.
- Use department-level time estimates that a client would find credible.
- The assumptions column should explain why the estimate is reasonable.
- Map hours lost to cost and annual impact in every automation opportunity.
- Treat the current_time_breakdown as the benchmark engine for manual effort: quantify current state and show where stronger operators would perform better.
- Include a summary of total hours lost per month and equivalent FTE impact.
- Financial values should reflect labor savings or productivity recapture in Rs.
- Provide 3 strong rationale bullets and 3 key assumptions for the automation case.
- overall_automation_percentage should feel like a practical transformation estimate, not a maximum-theoretical number.
""",
            ),
            max_tokens=1100,
        )
        calculations.update((time_and_automation.get("calculations") or {}))
    except Exception:
        calculations.update({
            "manhour_analysis_intro": default_calculations["manhour_analysis_intro"],
            "current_time_breakdown": default_calculations["current_time_breakdown"],
            "automation_opportunities": default_calculations["automation_opportunities"],
            "summary_table": default_calculations["summary_table"],
            "automation_rationale": default_calculations["automation_rationale"],
            "key_assumptions": default_calculations["key_assumptions"],
            "overall_automation_percentage": default_calculations["overall_automation_percentage"],
            "total_hours_lost_per_month": default_calculations["total_hours_lost_per_month"],
            "fte_equivalent": default_calculations["fte_equivalent"],
        })

    try:
        savings_and_roi = _call_model(
            _section_prompt(
                answers_text,
                "savings_and_roi",
                """
{
  "calculations": {
    "cost_reduction_opportunities": [
      {"category": "string", "description": "string", "estimated_value": "string"}
    ],
    "total_annual_savings_projection": "string",
    "investment_required": [
      {"item": "string", "estimated_cost": "string", "purpose": "string"}
    ],
    "monthly_savings_accrual": [
      {"month": "string", "estimated_savings": "string", "commentary": "string"}
    ],
    "break_even_point": "string",
    "net_roi_year_one": "string",
    "three_year_impact": "string"
  }
}
""",
                "Use exactly 4 cost_reduction_opportunities, 4 investment_required rows, and 6 monthly_savings_accrual rows.",
                """
- Cost reduction opportunities must map to the submitted pain points, not generic software savings.
- Investment_required should look like a real implementation budget with credible tool/system categories.
- monthly_savings_accrual should show a believable ramp rather than immediate full benefits.
- Convert each inefficiency into hours lost, cost, and annual impact where possible.
- Use the same financial logic consistently across the section so the savings model reads like one coherent consulting case.
- break_even_point, net_roi_year_one, and three_year_impact must be commercially coherent with the rest of the section.
""",
            ),
            max_tokens=1100,
        )
        calculations.update((savings_and_roi.get("calculations") or {}))
    except Exception:
        if not calculations.get("cost_reduction_opportunities"):
            calculations["cost_reduction_opportunities"] = report.get("calculations", {}).get("cost_reduction_opportunities", [])
    report["calculations"] = calculations

    try:
        actions = _call_model(
            _section_prompt(
                answers_text,
                "actions",
                """
{
  "actions": {
    "roadmap": [
      {"phase": "string", "timeline": "string", "focus": "string", "actions": ["string", "string", "string"]}
    ],
    "strategic_recommendations": {
      "immediate_actions": ["string", "string", "string", "string"],
      "short_term_priorities": ["string", "string", "string", "string"],
      "medium_term_initiatives": ["string", "string", "string", "string"],
      "long_term_vision": ["string", "string", "string", "string"]
    },
    "risk_mitigation_plan": ["string", "string", "string"],
    "future_vision": ["string", "string", "string"],
    "immediate_action_checklist": ["string", "string", "string", "string", "string"],
    "partnership_opportunities": ["string", "string", "string"]
  }
}
""",
                "Use exactly 6 roadmap phases, 4 bullets for each strategic recommendation bucket, 3 risk mitigation bullets, 3 future vision bullets, 5 checklist items, and 3 partnership opportunities.",
                """
- The roadmap should feel implementation-ready, with specific programs and sequencing.
- Strategic recommendations should be distinct across immediate, short-term, medium-term, and long-term buckets.
- The risk mitigation plan should include governance, data security, and adoption safeguards.
- The future vision should describe the AI-enabled business state after 12 months.
- Avoid generic filler like 'review the report' or 'regenerate the model output'.
- Partnership opportunities should sound like real consulting or implementation support offerings.
""",
            ),
            max_tokens=1200,
        )
        report.update(actions)
    except Exception:
        report["actions"] = _default_actions()

    normalized = _normalize_report(report, form_data)
    normalized["product_catalog"] = catalog_state
    normalized["assessment"]["departments"] = [
        curate_department_recommendations(department, form_data)
        for department in normalized["assessment"].get("departments", [])
    ]
    return normalized
