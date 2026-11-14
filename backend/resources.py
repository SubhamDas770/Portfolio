from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError


DATA_DIRECTORY = Path(__file__).resolve().parent / "data"


def load_profile_data() -> dict[str, Any]:
    """Load the structured facts, LinkedIn PDF text, and communication style."""
    facts_path = DATA_DIRECTORY / "facts.json"
    style_path = DATA_DIRECTORY / "style.txt"
    linkedin_path = DATA_DIRECTORY / "linkedin.pdf"

    try:
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to load profile facts from {facts_path.name}.") from error
    if not isinstance(facts, dict):
        raise RuntimeError(f"{facts_path.name} must contain a JSON object.")

    try:
        style = style_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError(f"Unable to load communication style from {style_path.name}.") from error
    if not style:
        raise RuntimeError(f"{style_path.name} is empty.")

    try:
        reader = PdfReader(str(linkedin_path))
        if reader.is_encrypted:
            raise RuntimeError(f"{linkedin_path.name} is encrypted and cannot be read.")
        linkedin_text = "\n\n".join(
            page.extract_text(extraction_mode="layout") or ""
            for page in reader.pages
        ).strip()
    except (OSError, PdfReadError, ValueError) as error:
        raise RuntimeError(f"Unable to read LinkedIn profile from {linkedin_path.name}.") from error
    if not linkedin_text:
        raise RuntimeError(f"No readable text was found in {linkedin_path.name}.")

    return {
        "facts": facts,
        "linkedin": linkedin_text,
        "style": style,
    }


def format_facts(facts: dict[str, Any]) -> str:
    """Format the JSON profile as readable prompt context without dropping fields."""
    return json.dumps(facts, ensure_ascii=False, indent=2)
