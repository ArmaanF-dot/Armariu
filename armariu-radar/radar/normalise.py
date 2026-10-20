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
