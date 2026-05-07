"""Normalize phone numbers for contributor login (simple E.164-style)."""
import re


def normalize_phone_e164(raw: str) -> str:
    """Keep digits only and optional leading + for international."""
    s = (raw or "").strip()
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if s.startswith("+") and digits:
        return "+" + digits
    return digits
