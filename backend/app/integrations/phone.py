"""Phone numbers. WhatsApp wants E.164; clients are stored the way people write them."""

import re


def digits(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def to_e164(raw: str, country_code: str = "972") -> str | None:
    """+972501234567 from 050-123-4567, 972501234567, +972 (50) 123 4567, 00972501234567."""
    d = digits(raw)
    if d.startswith("00"):
        d = d[2:]
    elif d.startswith("0"):
        d = country_code + d[1:]
    if not 9 <= len(d) <= 15 or (d.startswith(country_code) and len(d) - len(country_code) < 8):
        return None
    return "+" + d


def same_number(a: str, b: str, country_code: str = "972") -> bool:
    ea, eb = to_e164(a, country_code), to_e164(b, country_code)
    return ea is not None and ea == eb
