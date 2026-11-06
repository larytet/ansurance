"""Identifiers and Israeli ID number validation."""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Time-ordered UUID (RFC 9562 v7), so primary keys sort by creation time."""
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 62) & 0xFFF) << 64
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)
    return uuid.UUID(int=value)


def normalize_id_number(raw: str) -> str | None:
    """Digits only, left-padded to 9. None when it cannot be an ID number."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not 5 <= len(digits) <= 9 or len(digits) != len(raw.strip().replace("-", "")):
        return None
    return digits.zfill(9)


def is_valid_israeli_id(raw: str) -> bool:
    """9 digits, last is a Luhn-style check digit (weights 1,2,1,2,...)."""
    nid = normalize_id_number(raw)
    if nid is None:
        return False
    total = 0
    for i, ch in enumerate(nid):
        n = int(ch) * (1 if i % 2 == 0 else 2)
        total += n - 9 if n > 9 else n
    return total % 10 == 0
