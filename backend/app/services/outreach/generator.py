"""OpenAI subject/body generation — same prompt, schema and signature as the Sheet automation's email_generator.py."""
from __future__ import annotations

import json
import re

import httpx

from app.core.config import get_settings

OPENAI_URL = "https://api.openai.com/v1/responses"

EMAIL_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}


def signature_for(brand_name: str, s) -> str:
    """Exact brand contact footer, added outside the AI-generated content."""
    lines = [s.signature_name, s.signature_title, brand_name]
    lines = [x for x in lines if x]
    if s.phone:
        lines.append(f"Phone: {s.phone}")
    if s.cc_email:
        lines.append(f"Email: {s.cc_email}")
    if s.website:
        website = s.website.removeprefix("https://").removeprefix("http://").rstrip("/")
        lines.append(f"Website: {website}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("OpenAI returned no final text; the draft was not generated")
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("OpenAI response did not contain a JSON object")
    return json.loads(cleaned[start: end + 1])


def _output_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    parts = []
    for item in data.get("output", []) or []:
        if item.get("type") == "message":
            for c in item.get("content", []) or []:
                if c.get("type") == "output_text":
                    parts.append(c.get("text", ""))
    return "".join(parts)


def build_request(model: str, brand_name: str, brand_facts: str, person_name: str, details: str) -> dict:
    return {
        "model": model,
        "max_output_tokens": 1000,
        "reasoning": {"effort": "minimal"},
        "text": {"format": {"type": "json_schema", "name": "outreach_email", "strict": True,
                            "schema": EMAIL_RESPONSE_SCHEMA}},
        "instructions": (
            f"You write compliant first-contact business outreach for {brand_name}. "
            "Use only the recipient name and details supplied by the user plus these approved facts: "
            f"{brand_facts} Do not invent achievements, years, client counts, locations, "
            "partnerships or personal facts."
        ),
        "input": (
            f"Recipient name: {person_name or 'Not provided'}\n"
            f"Recipient details: {details or 'No additional details provided'}\n\n"
            "Requirements:\n"
            "- Subject must be short and natural.\n"
            "- Body must be approximately 80 to 130 words before the signature.\n"
            "- Use one simple call to action.\n"
            "- Avoid aggressive language and avoid attachments.\n"
            "- End with: If this is not relevant, please reply and we will not contact you again.\n"
            "- Do not add a name, job title, phone number, email address, website, or signature. "
            "The application adds the approved signature separately.\n"
            "- Do not include a subject line inside the body."
        ),
    }


def openai_key(db=None) -> str:
    """Server env var OPENAI_API_KEY first, otherwise the key the Super Admin saved in the app."""
    key = get_settings().openai_api_key
    if key or db is None:
        return key
    from app.models import OutreachGlobal
    from app.services.crypto import decrypt
    g = db.get(OutreachGlobal, 1)
    return decrypt(g.openai_key_ciphertext) if g and g.openai_key_ciphertext else ""


def generate_email(model: str, brand_name: str, settings, person_name: str, details: str, key: str | None = None) -> tuple[str, str]:
    key = key if key is not None else get_settings().openai_api_key
    if not key:
        raise RuntimeError("No OpenAI API key yet. Add it under Email Outreach → Automation.")
    resp = httpx.post(OPENAI_URL, json=build_request(model, brand_name, settings.brand_facts, person_name, details),
                      headers={"Authorization": f"Bearer {key}"}, timeout=45)
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            msg = resp.text[:200]
        raise RuntimeError(f"OpenAI API {resp.status_code}: {msg[:300]}")
    data = _extract_json(_output_text(resp.json()))
    subject = str(data.get("subject", "")).strip()
    body = str(data.get("body", "")).strip()
    if not subject or not body:
        raise ValueError("OpenAI returned an empty subject or body")
    return subject, f"{body}\n\n{signature_for(brand_name, settings)}"
