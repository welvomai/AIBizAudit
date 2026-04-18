import csv
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


HEADER_ALIASES = {
    "sno": "sno",
    "products": "product_name",
    "product": "product_name",
    "product name": "product_name",
    "usp": "usp",
    "product demo samples": "product_demo_samples",
    "level": "level",
    "what does it do": "what_does_it_do",
    "what does it do?": "what_does_it_do",
    "value benefits": "value_benefits",
    "user flows": "user_flows",
    "agent name": "agent_name",
    "set up days": "setup_days",
    "setup days": "setup_days",
    "pre requisites": "prerequisites",
    "prerequisites": "prerequisites",
}

CATALOG_PATH_CANDIDATES = (
    "data/welvom_products.xlsx",
    "data/welvom_product_list.xlsx",
    "data/product_catalog.xlsx",
    "data/products.xlsx",
    "data/welvom_products.csv",
    "data/welvom_product_list.csv",
    "data/product_catalog.csv",
    "data/products.csv",
    "data/welvom_products.json",
    "data/welvom_product_list.json",
    "data/product_catalog.json",
    "data/products.json",
    "welvom_products.xlsx",
    "welvom_product_list.xlsx",
    "product_catalog.xlsx",
    "products.xlsx",
    "welvom_products.csv",
    "welvom_product_list.csv",
    "product_catalog.csv",
    "products.csv",
    "welvom_products.json",
    "welvom_product_list.json",
    "product_catalog.json",
    "products.json",
)

CATALOG_EXTENSIONS = {".xlsx", ".csv", ".json"}

STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "our", "you",
    "are", "can", "will", "not", "but", "all", "per", "via", "than", "into", "their",
    "them", "has", "have", "had", "was", "were", "what", "does", "built", "ready",
    "solution", "solutions", "product", "products", "agent", "level", "days",
}

DEPARTMENT_HINTS = {
    "Leadership & Strategy": {"dashboard", "mis", "leadership", "management", "reporting", "forecast", "decision", "analytics", "command", "board", "kpi"},
    "Customer Experience & Support": {"customer", "support", "ticket", "service", "complaint", "crm", "response", "sla", "helpdesk", "case", "feedback"},
    "Human Resources & Workforce": {"hr", "employee", "hiring", "recruitment", "onboarding", "attendance", "leave", "policy", "payroll", "workforce"},
    "Sales & Marketing": {"sales", "marketing", "lead", "campaign", "pipeline", "proposal", "conversion", "outreach", "followup", "nurture", "crm"},
    "Operations & Process Efficiency": {"operations", "workflow", "process", "approval", "dispatch", "procurement", "inventory", "production", "quality", "task", "escalation"},
    "Finance & Administration": {"finance", "invoice", "payment", "reconciliation", "receivable", "cash", "expense", "accounts", "approval", "payable", "variance"},
    "Technology & IT Infrastructure": {"integration", "api", "cloud", "infrastructure", "uptime", "monitoring", "security", "system", "data", "automation", "observability"},
}


def _normalize_header(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()
    return HEADER_ALIASES.get(cleaned, cleaned.replace(" ", "_"))


def _normalize_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _preferred_catalog_path() -> Path | None:
    configured = os.getenv("WELVOM_PRODUCT_CATALOG_PATH", "").strip()
    if configured:
        path = Path(configured)
        return path if path.exists() else None

    for candidate in CATALOG_PATH_CANDIDATES:
        path = Path(candidate)
        if path.exists():
            return path

    # Fallback: discover recent confidential uploads with non-standard names.
    discovered = []
    for base_dir in (Path("data"), Path(".")):
        if not base_dir.exists() or not base_dir.is_dir():
            continue
        for file_path in base_dir.iterdir():
            if not file_path.is_file() or file_path.suffix.lower() not in CATALOG_EXTENSIONS:
                continue
            lowered_name = file_path.name.lower()
            if any(token in lowered_name for token in ("welvom", "product", "catalog", "solution")):
                discovered.append(file_path)

    if discovered:
        discovered.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        return discovered[0]

    return None


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        xml_bytes = archive.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ET.fromstring(xml_bytes)
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values = []
    for item in root.findall("x:si", namespace):
        text_parts = [node.text or "" for node in item.findall(".//x:t", namespace)]
        values.append("".join(text_parts))
    return values


def _xlsx_sheet_path(archive: zipfile.ZipFile) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    main_ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}

    first_sheet = workbook.find("x:sheets/x:sheet", main_ns)
    if first_sheet is None:
        raise ValueError("No sheets found in Excel catalog")

    relationship_id = first_sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    for relationship in rels.findall("r:Relationship", rel_ns):
        if relationship.attrib.get("Id") == relationship_id:
            target = relationship.attrib.get("Target", "")
            return f"xl/{target}" if not target.startswith("xl/") else target
    raise ValueError("Could not resolve first sheet in Excel catalog")


def _xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_path = _xlsx_sheet_path(archive)
        sheet_root = ET.fromstring(archive.read(sheet_path))

    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows = []
    for row in sheet_root.findall(".//x:sheetData/x:row", namespace):
        values = []
        expected_col = 0
        for cell in row.findall("x:c", namespace):
            reference = cell.attrib.get("r", "")
            letters = re.match(r"([A-Z]+)", reference)
            if letters:
                col_index = 0
                for letter in letters.group(1):
                    col_index = col_index * 26 + (ord(letter) - 64)
                col_index -= 1
                while expected_col < col_index:
                    values.append("")
                    expected_col += 1

            cell_type = cell.attrib.get("t")
            value_node = cell.find("x:v", namespace)
            inline_text = "".join(node.text or "" for node in cell.findall(".//x:is/x:t", namespace))
            if inline_text:
                value = inline_text
            elif value_node is None or value_node.text is None:
                value = ""
            elif cell_type == "s":
                index = int(value_node.text)
                value = shared_strings[index] if 0 <= index < len(shared_strings) else ""
            else:
                value = value_node.text

            values.append(_normalize_value(value))
            expected_col += 1
        if any(values):
            rows.append(values)
    return rows


def _records_from_rows(rows: list[list[str]]) -> list[dict[str, str]]:
    if not rows:
        return []

    headers = [_normalize_header(cell) for cell in rows[0]]
    records = []
    for row in rows[1:]:
        record = {}
        for index, header in enumerate(headers):
            if not header:
                continue
            record[header] = _normalize_value(row[index] if index < len(row) else "")
        if any(record.values()):
            records.append(_normalize_record(record))
    return [record for record in records if record.get("product_name")]


def _normalize_record(record: dict[str, str]) -> dict[str, str]:
    normalized = {
        "sno": record.get("sno", ""),
        "product_name": record.get("product_name", ""),
        "usp": record.get("usp", ""),
        "product_demo_samples": record.get("product_demo_samples", ""),
        "level": record.get("level", ""),
        "what_does_it_do": record.get("what_does_it_do", ""),
        "value_benefits": record.get("value_benefits", ""),
        "user_flows": record.get("user_flows", ""),
        "agent_name": record.get("agent_name", ""),
        "setup_days": record.get("setup_days", ""),
        "prerequisites": record.get("prerequisites", ""),
    }
    normalized["search_blob"] = " ".join(
        value for key, value in normalized.items() if key not in {"sno", "setup_days"} and value
    ).lower()
    return normalized


def load_product_catalog() -> dict[str, Any]:
    path = _preferred_catalog_path()
    if path is None:
        return {"path": None, "products": [], "status": "missing"}

    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        products = _records_from_rows(rows)
    elif suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            payload = payload.get("products", [])
        products = [_normalize_record({_normalize_header(k): _normalize_value(v) for k, v in item.items()}) for item in payload if isinstance(item, dict)]
        products = [record for record in products if record.get("product_name")]
    elif suffix == ".xlsx":
        products = _records_from_rows(_xlsx_rows(path))
    else:
        return {"path": str(path), "products": [], "status": "unsupported"}

    return {"path": str(path), "products": products, "status": "loaded"}


def _tokenize(text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 2 and token not in STOPWORDS
    }


def _product_score(product: dict[str, str], context_tokens: set[str], department_title: str) -> int:
    score = 0
    search_tokens = _tokenize(product.get("search_blob", ""))
    score += len(search_tokens & context_tokens) * 3

    department_tokens = DEPARTMENT_HINTS.get(department_title, set())
    score += len(search_tokens & department_tokens) * 2

    setup_days = re.findall(r"\d+", product.get("setup_days", ""))
    if setup_days:
        score += max(0, 10 - min(int(setup_days[0]), 10))

    if product.get("level", "").strip().lower() in {"ready", "built", "high", "production"}:
        score += 2

    if setup_days and int(setup_days[0]) <= 30:
        score += 2
    return score


def _strong_signal_text(form_data: dict[str, Any], department_title: str) -> str:
    signal_fragments = [department_title]
    for key, value in form_data.items():
        if not value:
            continue
        key_text = str(key).lower()
        value_text = str(value)
        if any(token in key_text for token in ("challenge", "pain", "problem", "gap", "issue", "bottleneck", "workflow", "response", "delay")):
            # Duplicate high-signal fields so ranking aligns with stated pain points.
            signal_fragments.extend([value_text, value_text])
        else:
            signal_fragments.append(value_text)
    return " ".join(signal_fragments)


def matched_products_for_department(
    department_title: str,
    form_data: dict[str, Any],
    department: dict[str, Any] | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    catalog = load_product_catalog()
    products = catalog.get("products") or []
    if not products:
        return []

    context_parts = [_strong_signal_text(form_data, department_title)]
    if department:
        for value in department.values():
            if isinstance(value, dict):
                context_parts.extend(str(item) for item in value.values() if item)
            elif isinstance(value, list):
                context_parts.extend(str(item) for item in value if item)
            elif value:
                context_parts.append(str(value))
    context_tokens = _tokenize(" ".join(context_parts))

    ranked = sorted(
        products,
        key=lambda product: (_product_score(product, context_tokens, department_title), product.get("product_name", "")),
        reverse=True,
    )
    matches = [product for product in ranked if _product_score(product, context_tokens, department_title) > 0]
    return matches[:limit]


def _clean_sentence(text: str, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip(" .;:-")
    return cleaned if cleaned else fallback


def _sanitize_catalog_text(text: str, fallback: str) -> str:
    cleaned = _clean_sentence(text, fallback)
    # Remove emoji/checkmark-like symbols and uncommon decorative characters.
    cleaned = re.sub(r"[^\w\s.,;:()/%&+\-]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or fallback


def _short_phrase(text: str, fallback: str, max_len: int = 90) -> str:
    cleaned = _sanitize_catalog_text(text, fallback)
    # Prefer the first strong phrase to avoid dumping long spreadsheet cells.
    first_chunk = re.split(r"[.;\n]| and |,", cleaned, maxsplit=1)[0].strip()
    if not first_chunk:
        first_chunk = cleaned
    return first_chunk


def _catalog_safe_text(text: str, fallback: str, max_len: int = 140) -> str:
    return _sanitize_catalog_text(text, fallback)


def _setup_timeline_text(setup_days: str) -> str:
    text = _sanitize_catalog_text(str(setup_days or ""), "")
    if not text:
        return ""
    if re.search(r"\bday(s)?\b", text, flags=re.IGNORECASE):
        return text
    return f"{text} days"


def product_prompt_context(
    section_name: str,
    form_data: dict[str, Any],
    department_title: str | None = None,
) -> str:
    catalog = load_product_catalog()
    if not catalog.get("products"):
        return ""

    if department_title:
        products = matched_products_for_department(department_title, form_data, limit=4)
    else:
        products = matched_products_for_department("Operations & Process Efficiency", form_data, limit=2)
        seen = {item.get("product_name") for item in products}
        for title in DEPARTMENT_HINTS:
            for product in matched_products_for_department(title, form_data, limit=2):
                if product.get("product_name") not in seen:
                    products.append(product)
                    seen.add(product.get("product_name"))
                if len(products) >= 8:
                    break
            if len(products) >= 8:
                break

    if not products:
        return ""

    lines = [
        "WELVOM PRODUCT CATALOG CONTEXT:",
        "Use Welvom products as the primary solutions whenever they fit the identified gap.",
        "Do not invent product names that are not in the catalog below.",
        "If no catalog product fits a gap, keep the recommendation generic for that gap only.",
    ]
    for product in products:
        lines.append(
            f"- {product['product_name']}: USP={product.get('usp') or 'Not stated'}; "
            f"Solves={product.get('what_does_it_do') or 'Not stated'}; "
            f"Benefits={product.get('value_benefits') or 'Not stated'}; "
            f"Flows={product.get('user_flows') or 'Not stated'}; "
            f"Setup={product.get('setup_days') or 'Not stated'} days; "
            f"Prerequisites={product.get('prerequisites') or 'Not stated'}."
        )
    return "\n".join(lines)


def curate_department_recommendations(department: dict[str, Any], form_data: dict[str, Any]) -> dict[str, Any]:
    matches = matched_products_for_department(department.get("title", ""), form_data, department, limit=3)
    if not matches:
        return department

    recommendations = department.get("recommendations") or {}
    curated = {"immediate": [], "short_term": [], "medium_term": []}

    immediate = matches[0]
    immediate_solution = _short_phrase(immediate.get("what_does_it_do", ""), "address the core workflow gap")
    immediate_value = _short_phrase(immediate.get("value_benefits", ""), "faster execution and better visibility")
    immediate_bullet = (
        f"Deploy {immediate['product_name']} as the primary solution for this function. "
        f"Use it to {immediate_solution.lower()} and deliver {immediate_value.lower()}."
    )
    timeline = _setup_timeline_text(immediate.get("setup_days", ""))
    if timeline:
        immediate_bullet += f" Estimated rollout: {timeline}."
    curated["immediate"].append(immediate_bullet)

    if len(matches) > 1:
        short_term = matches[1]
        short_flow = _short_phrase(short_term.get("user_flows", ""), "the next high-friction workflow")
        short_value = _short_phrase(short_term.get("value_benefits", ""), "service consistency and cycle-time gains")
        short_bullet = (
            f"Roll out {short_term['product_name']} in phase two to strengthen {short_flow.lower()}."
        )
        short_bullet += f" Expected value: {short_value}."
        timeline = _setup_timeline_text(short_term.get("setup_days", ""))
        if timeline:
            short_bullet += f" Estimated rollout: {timeline}."
        curated["short_term"].append(short_bullet)

    if len(matches) > 2:
        medium_term = matches[2]
        medium_fit = _short_phrase(medium_term.get("what_does_it_do", ""), "scale automation depth across adjacent processes")
        medium_bullet = (
            f"Scale with {medium_term['product_name']} to expand automation maturity across adjacent workflows. "
            f"Primary fit: {medium_fit}."
        )
        timeline = _setup_timeline_text(medium_term.get("setup_days", ""))
        if timeline:
            medium_bullet += f" Estimated rollout: {timeline}."
        curated["medium_term"].append(medium_bullet)

    for phase in ("immediate", "short_term", "medium_term"):
        if not curated[phase]:
            existing = list(recommendations.get(phase) or [])
            if existing:
                curated[phase].append(existing[0])

    department["recommendations"] = curated
    department["matched_products"] = [
        {
            "product_name": product.get("product_name", ""),
            "usp": _catalog_safe_text(product.get("usp", ""), "Outcome-focused automation support"),
            "what_does_it_do": _catalog_safe_text(product.get("what_does_it_do", ""), "Improves workflow execution and control"),
            "value_benefits": _catalog_safe_text(product.get("value_benefits", ""), "Faster cycle time and better visibility"),
            "setup_days": product.get("setup_days", ""),
        }
        for product in matches
    ]
    return department


def product_catalog_status() -> dict[str, Any]:
    catalog = load_product_catalog()
    return {
        "status": catalog.get("status"),
        "path": catalog.get("path"),
        "count": len(catalog.get("products") or []),
    }
