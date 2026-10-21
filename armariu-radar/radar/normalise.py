"""Lender-name normalisation so 'Barclays Bank PLC' and 'BARCLAYS BANK Plc.' are one key."""
from __future__ import annotations

import re
import unicodedata

# Legal-form suffixes stripped from the END of a name only.
_SUFFIXES = {"LIMITED", "LTD", "PLC", "LLP", "LP", "INC", "LLC", "SARL", "GMBH", "NV", "BV"}


def normalise_name(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode("ascii")
    s = s.upper().replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    tokens = s.split()
    stripped = list(tokens)
    while stripped and stripped[-1] in _SUFFIXES:
        stripped.pop()
    return " ".join(stripped or tokens)


_FAMILY_TRIM = [
    re.compile(r"\s+(LIMITED\s+|LTD\s+|PLC\s+|LLP\s+)?(COMPANY NUMBER|CRN)\b.*$"),
    re.compile(r"\s+(LIMITED\s+|LTD\s+)?TRADING AS\b.*$"),
    re.compile(r"\s+(PLC\s+|LIMITED\s+)?AS LENDER$"),
    re.compile(r"(\s+SPV)?\s*\d+$"),
    re.compile(r"\s+SPV$"),
    re.compile(r"\s+(LIMITED|LTD|PLC|LLP)$"),
]


def family_key(norm: str) -> str:
    """'FORTYS CAPITAL 6' and 'FORTYS CAPITAL' -> 'FORTYS CAPITAL'; 'MS LENDING SPV1' -> 'MS LENDING'.
    Short stems ('SBF', 'PP') are left alone so unrelated names never merge."""
    s = norm
    for _ in range(3):
        for rx in _FAMILY_TRIM:
            s = rx.sub("", s).strip()
    return s if len(s) >= 6 else norm
