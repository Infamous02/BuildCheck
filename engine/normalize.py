"""Normalization helpers shared by the database loaders.

Manufacturers (and the Module 4 extractor) write the same value many ways.
Everything goes through these functions before it reaches Postgres, so the
rules engine only ever compares clean values:

    sockets       LGA1851, LGA1700, AM5, AM4   (uppercase, no spaces)
    memory type   DDR4 / DDR5
    speeds        integer MT/s
    form factors  ATX, mATX, ITX, E-ATX        (PSU: ATX / SFX)

Note: ingest/schemas.py writes sockets as "LGA 1851" (with a space) and board
form factors as "Micro-ATX" / "Mini-ITX". Those are accepted here and converted.
"""
from __future__ import annotations

import re

_SOCKET_RE = re.compile(r"^\s*(FCLGA|LGA|AM)\s*-?\s*(\d{1,4})\s*$", re.IGNORECASE)


def normalize_socket(raw: str) -> str:
    """'LGA 1851', 'lga-1851', 'FCLGA1851' -> 'LGA1851'; ' am5 ' -> 'AM5'."""
    m = _SOCKET_RE.match(str(raw))
    if not m:
        raise ValueError(f"Unrecognized socket: {raw!r}")
    family = "LGA" if m.group(1).upper() in ("LGA", "FCLGA") else "AM"
    return f"{family}{m.group(2)}"


def normalize_mem_type(raw: str) -> str:
    """'ddr5', 'DDR5 SDRAM', 'DDR-5', 'DDR5-6400' -> 'DDR5'."""
    m = re.search(r"DDR\s*-?\s*([45])", str(raw), re.IGNORECASE)
    if not m:
        raise ValueError(f"Unrecognized memory type: {raw!r}")
    return f"DDR{m.group(1)}"


def parse_speed(raw: str | int) -> int:
    """'DDR5-6400', '6400MHz', '6400 MT/s', 6400 -> 6400."""
    if isinstance(raw, int):
        return raw
    text = re.sub(r"DDR\s*-?\s*[45]", "", str(raw), flags=re.IGNORECASE)   # drop the '5' in DDR5
    m = re.search(r"(\d{3,5})", text)
    if not m:
        raise ValueError(f"No memory speed found in {raw!r}")
    return int(m.group(1))


_BOARD_FF = {
    "atx": "ATX",
    "matx": "mATX", "micro-atx": "mATX", "microatx": "mATX", "uatx": "mATX", "m-atx": "mATX",
    "itx": "ITX", "mini-itx": "ITX", "miniitx": "ITX",
    "e-atx": "E-ATX", "eatx": "E-ATX", "extended-atx": "E-ATX",
}


def normalize_form_factor(raw: str) -> str:
    """'Micro-ATX', 'micro atx', 'mATX' -> 'mATX'; 'Mini-ITX' -> 'ITX'; 'EATX' -> 'E-ATX'."""
    key = re.sub(r"\s+", "-", str(raw).strip().lower())
    if key not in _BOARD_FF:
        raise ValueError(f"Unrecognized board form factor: {raw!r}")
    return _BOARD_FF[key]


def normalize_psu_form_factor(raw: str) -> str:
    """'atx', 'ATX 3.1' -> 'ATX'; 'sfx', 'SFX-L' -> 'SFX'."""
    text = str(raw).strip().upper()
    if text.startswith("SFX"):
        return "SFX"
    if text.startswith("ATX"):
        return "ATX"
    raise ValueError(f"Unrecognized PSU form factor: {raw!r}")


def parse_pg_array(raw: str) -> list[str]:
    """'{ATX,mATX,ITX}' (Postgres array literal, as written in the seed CSVs) -> ['ATX', 'mATX', 'ITX']."""
    text = str(raw).strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return [item.strip().strip('"') for item in text.split(",") if item.strip()]
