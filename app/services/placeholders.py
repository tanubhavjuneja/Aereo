from __future__ import annotations

import re
from typing import Iterable, List

PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z][a-zA-Z0-9_]*)\}")

#: Placeholders always available in e-mail templates and uploaded PDF templates.
BUILTIN_PLACEHOLDERS = (
    "event_name",
    "event_date",
    "certificate_id",
    "certificate_code",
    "verification_url",
    "recipient",
)


def render_placeholders(template: str, context: dict[str, str]) -> str:
    """Substitute ``{key}`` placeholders; unknown keys are left untouched."""
    return PLACEHOLDER_RE.sub(lambda match: context.get(match.group(1), match.group(0)), template)


def extract_placeholders(text: str) -> List[str]:
    """Return the unique placeholders found in ``text``, in order of appearance."""
    seen: dict[str, None] = {}
    for match in PLACEHOLDER_RE.finditer(text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def build_context(job, certificate, base_url: str = "") -> dict[str, str]:
    """Context shared by PDF rendering and e-mail subject/body templates."""
    context = {key: str(value) for key, value in (certificate.data or {}).items()}
    context.update(
        {
            "event_name": job.event_name,
            "event_date": job.event_date.isoformat() if job.event_date else "",
            "certificate_id": certificate.id,
            "certificate_code": certificate.certificate_code,
            "recipient": certificate.recipient_name,
            "verification_url": f"{base_url.rstrip('/')}/verify/{certificate.verify_token}" if base_url else "",
        }
    )
    return context


def uncovered_placeholders(placeholders: Iterable[str], field_keys: Iterable[str]) -> List[str]:
    """Placeholders that match neither a declared field nor a builtin."""
    known = set(field_keys) | set(BUILTIN_PLACEHOLDERS)
    return [placeholder for placeholder in placeholders if placeholder not in known]
