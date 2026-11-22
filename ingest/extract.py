"""
BuildCheck motherboard extraction.

Turns a parsed manual (from ingest.parse) into a staged MotherboardRecord that a
reviewer can approve. Two methods feed it:

  1. Deterministic: regexes over the spec pages. Fast, free, confidence 1.0.
  2. LLM: fills whatever the regexes missed (or everything, in --mode llm).

Every value from either method then goes through the same checks:
  - the evidence quote must really appear on the cited page (catches hallucinations)
  - the value must appear inside its own evidence quote
  - the finished record must pass the MotherboardRecord validators in schemas.py
A value that fails is dropped to None with the reason recorded, so the reviewer
sees a gap instead of a wrong number.

Usage:
    py -m ingest.extract data/parsed/z890.json                  # hybrid (default)
    py -m ingest.extract data/parsed/z890.json --mode deterministic
    py -m ingest.extract data/parsed/z890.json --mode llm
    py -m ingest.extract data/parsed/z890.json --mode compare   # regex vs LLM, side by side
    py -m ingest.extract data/parsed/z890.json --pages 7 8 9    # override page selection
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ValidationError, create_model

from ingest.parse import ParsedDocument, evidence_in_page, find_relevant_pages
from ingest.schemas import ExtractedRecord, MotherboardRecord

FIELDS = [
    "manufacturer", "model", "socket", "chipset", "form_factor",
    "memory_type", "memory_slots", "max_memory_gb", "max_memory_speed_mts",
]
INT_FIELDS = {"memory_slots", "max_memory_gb", "max_memory_speed_mts"}

# What the LLM is told about each field. Kept short: small local models do better
# with one plain sentence per field than with long instructions.
FIELD_HINTS = {
    "manufacturer": "Board manufacturer, e.g. GIGABYTE, ASUS, MSI, ASRock.",
    "model": "Exact motherboard model name as printed, e.g. 'Z890 EAGLE WIFI7 PLUS'.",
    "socket": "CPU socket, e.g. 'LGA1851' or 'AM5'.",
    "chipset": "Chipset code only, e.g. 'Z890' or 'B650'.",
    "form_factor": "One of: E-ATX, ATX, Micro-ATX, Mini-ITX.",
    "memory_type": "DDR4 or DDR5.",
    "memory_slots": "Number of memory DIMM slots, as a number.",
    "max_memory_gb": "Maximum total system memory in GB, as a number.",
    "max_memory_speed_mts": "Highest memory speed listed in MT/s, as a number.",
}

MAX_PROMPT_CHARS = 14_000   # roughly 4k tokens of manual text; fits a 16k context window


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class FieldValue:
    value: object
    page: Optional[int]
    evidence: Optional[str]
    confidence: float
    method: str                       # "deterministic" or "llm"

    def as_sourced(self) -> dict:
        return {"value": self.value, "page": self.page,
                "evidence": self.evidence, "confidence": self.confidence}


@dataclass
class ExtractionResult:
    record: Optional[ExtractedRecord]
    fields: dict[str, Optional[FieldValue]]
    problems: list[str] = field(default_factory=list)
    pages_used: list[int] = field(default_factory=list)
    backend: Optional[str] = None

    def to_json(self) -> dict:
        return {
            "record": self.record.model_dump(mode="json") if self.record else None,
            "field_methods": {k: (v.method if v else None) for k, v in self.fields.items()},
            "problems": self.problems,
            "pages_used": self.pages_used,
            "backend": self.backend,
        }


# ---------------------------------------------------------------------------
# 1. Deterministic extraction
# Evidence is always the exact matched text, so it passes the evidence check by
# construction. Regexes allow \s+ between words because pdfplumber wraps lines.
# ---------------------------------------------------------------------------

_MANUFACTURERS = {"gigabyte": "Gigabyte", "asus": "ASUS", "msi": "MSI", "asrock": "ASRock", "biostar": "Biostar"}

_PATTERNS: dict[str, re.Pattern] = {
    # "LGA1851 socket" (Gigabyte) or "Processor socket AM5" (MSI)
    "socket": re.compile(r"((?:FC)?LGA\s?-?\d{4}|AM[45])\s+socket|socket\s+((?:FC)?LGA\s?-?\d{4}|AM[45])\b", re.I),
    "chipset": re.compile(r"(?:Intel®?|AMD)\s+([ABHXZ]\d{3}E?)\s+(?:Express\s+)?Chipset", re.I),
    "form_factor": re.compile(r"(E-ATX|Micro[- ]ATX|Mini[- ]ITX|ATX)\s+Form\s+Factor", re.I),
    "memory_speed": re.compile(r"(DDR[45])\s+((?:\d{4,5}\s*/\s*)*\d{4,5})\s*MT/s", re.I),
    # "4 x DDR5 DIMM sockets" (Gigabyte) or "4x DDR5 memory slots" (MSI)
    "memory_slots": re.compile(r"(\d)\s*x\s*(DDR[45])\s+(?:DIMM|memory)\s+(?:sockets|slots)", re.I),
    "max_memory_gb": re.compile(r"(?:sockets|slots)\**,?\s+supporting\s+up\s+to\s+(\d{2,4})\s*GB", re.I),
}

_FORM_FACTORS = {"e-atx": "E-ATX", "atx": "ATX", "micro-atx": "Micro-ATX", "micro atx": "Micro-ATX",
                 "mini-itx": "Mini-ITX", "mini itx": "Mini-ITX"}


def _det(value, page, match_text) -> FieldValue:
    return FieldValue(value, page, match_text, 1.0, "deterministic")


def extract_deterministic(doc: ParsedDocument, pages: list[int]) -> dict[str, FieldValue]:
    found: dict[str, FieldValue] = {}

    for page in pages:                    # spec pages come first (see choose_pages)
        text = doc.page_text(page)

        if "socket" not in found and (m := _PATTERNS["socket"].search(text)):
            found["socket"] = _det((m.group(1) or m.group(2)).replace(" ", ""), page, m.group(0))
        if "chipset" not in found and (m := _PATTERNS["chipset"].search(text)):
            found["chipset"] = _det(m.group(1).upper(), page, m.group(0))
        if "form_factor" not in found and (m := _PATTERNS["form_factor"].search(text)):
            found["form_factor"] = _det(_FORM_FACTORS[m.group(1).lower()], page, m.group(0))
        if "memory_type" not in found and (m := _PATTERNS["memory_speed"].search(text)):
            speeds = [int(s) for s in re.findall(r"\d{4,5}", m.group(2))]
            found["memory_type"] = _det(m.group(1).upper(), page, m.group(0))
            found["max_memory_speed_mts"] = _det(max(speeds), page, m.group(0))
        if "memory_slots" not in found and (m := _PATTERNS["memory_slots"].search(text)):
            found["memory_slots"] = _det(int(m.group(1)), page, m.group(0))
            if "memory_type" not in found:
                found["memory_type"] = _det(m.group(2).upper(), page, m.group(0))
        if "max_memory_gb" not in found and (m := _PATTERNS["max_memory_gb"].search(text)):
            found["max_memory_gb"] = _det(int(m.group(1)), page, m.group(0))

    # Manufacturer: first known brand name anywhere in the document.
    brand_re = re.compile(r"\b(" + "|".join(_MANUFACTURERS) + r")\b", re.I)
    for p in doc.pages:
        if m := brand_re.search(p.text):
            found["manufacturer"] = _det(_MANUFACTURERS[m.group(1).lower()], p.page, m.group(0))
            break

    # Model: first line of the cover page. Confidence < 1 because one manual often
    # covers several variants (this Z890 manual covers WIFI7 PLUS and PLUS).
    cover = doc.pages[0].text if doc.pages else ""
    if any(w in cover.lower() for w in ("manual", "user guide")):
        first = next((ln.strip() for ln in cover.splitlines() if ln.strip()), None)
        if first:
            found["model"] = FieldValue(first, 1, first, 0.9, "deterministic")

    return found


# ---------------------------------------------------------------------------
# 2. LLM extraction
# ---------------------------------------------------------------------------

class LLMValue(BaseModel):
    value: Optional[str]
    page: Optional[int]
    evidence: Optional[str]
    confidence: float


SYSTEM_PROMPT = """You extract PC motherboard specifications from manufacturer manuals.
Rules:
- Use ONLY the manual text provided. Never use outside knowledge.
- For each field, "evidence" must be a short phrase (under 150 characters) copied EXACTLY,
  character for character, from the manual text.
- "page" is the number from the === PAGE n === header above the evidence.
- If the manual does not state a field, set value, page and evidence to null and confidence to 0.
- confidence is 0 to 1: how sure you are the value is correct and stated in the text."""


def build_llm_schema(field_names: list[str]) -> type[BaseModel]:
    return create_model("MotherboardExtraction", **{name: (LLMValue, ...) for name in field_names})


def build_user_prompt(doc: ParsedDocument, pages: list[int], field_names: list[str]) -> str:
    parts, used = [], 0
    for page in pages:
        block = f"=== PAGE {page} ===\n{doc.page_text(page).strip()}\n"
        if used + len(block) > MAX_PROMPT_CHARS:
            break
        parts.append(block)
        used += len(block)
    fields_txt = "\n".join(f"- {name}: {FIELD_HINTS[name]}" for name in field_names)
    return f"Fields to extract:\n{fields_txt}\n\nManual text:\n\n" + "\n".join(parts)


def _to_int(name: str, raw) -> int:
    if isinstance(raw, int):
        return raw
    nums = [int(n) for n in re.findall(r"\d+", str(raw))]
    if not nums:
        raise ValueError(f"no number in {raw!r}")
    if name == "max_memory_speed_mts":          # '7200/6400/5600' -> 7200
        speeds = [n for n in nums if 1000 <= n <= 20000]
        return max(speeds or nums)
    return nums[0]


def extract_llm(client, doc: ParsedDocument, pages: list[int], field_names: list[str]) -> dict[str, FieldValue]:
    schema = build_llm_schema(field_names)
    raw = client.extract_json(SYSTEM_PROMPT, build_user_prompt(doc, pages, field_names), schema)
    parsed = schema.model_validate(raw)

    out: dict[str, FieldValue] = {}
    for name in field_names:
        item: LLMValue = getattr(parsed, name)
        if item.value is None or str(item.value).strip().lower() in ("", "null", "none"):
            continue
        value: object = item.value.strip()
        if name in INT_FIELDS:
            try:
                value = _to_int(name, value)
            except ValueError:
                continue
        conf = min(max(float(item.confidence), 0.0), 1.0)
        out[name] = FieldValue(value, item.page, item.evidence, conf, "llm")
    return out


# ---------------------------------------------------------------------------
# 3. Validation
# ---------------------------------------------------------------------------

def _squash(s: str) -> str:
    return re.sub(r"[\s\-]+", "", str(s)).lower()


def check_field(doc: ParsedDocument, name: str, fv: FieldValue) -> Optional[str]:
    """Return a problem description, or None if the value is supported by its evidence."""
    if fv.page is None or not fv.evidence:
        return f"{name}: no page/evidence given"
    if not evidence_in_page(doc, fv.page, fv.evidence):
        return f"{name}: evidence {fv.evidence[:60]!r} not found on page {fv.page}"
    if _squash(str(fv.value)) not in _squash(fv.evidence):
        # e.g. LLM says memory_slots=4 but quotes a sentence with no 4 in it
        return f"{name}: value {fv.value!r} does not appear in its evidence"
    return None


def build_record(doc: ParsedDocument, fields: dict[str, Optional[FieldValue]],
                 method: str, problems: list[str]) -> ExtractedRecord:
    """Assemble the record; fields that fail schema validators are nulled and reported."""
    data = {n: (fields[n].as_sourced() if fields.get(n) else {"value": None, "confidence": 0.0}) for n in FIELDS}
    for _ in range(len(FIELDS) + 1):
        try:
            board = MotherboardRecord(**data)
            return ExtractedRecord(doc_id=doc.doc_id, source_file=doc.source_file, method=method, data=board)
        except ValidationError as e:
            bad = {err["loc"][0] for err in e.errors() if err["loc"] and err["loc"][0] in data}
            if not bad:
                raise
            for name in bad:
                msg = next(err["msg"] for err in e.errors() if err["loc"] and err["loc"][0] == name)
                problems.append(f"{name}: failed schema check ({msg}); value {data[name].get('value')!r} dropped")
                data[name] = {"value": None, "confidence": 0.0}
                fields[name] = None
    raise RuntimeError("could not build a valid record")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

SPEC_SPAN = 4   # spec tables often run across several pages ("Continued on next column")


def find_spec_pages(doc: ParsedDocument) -> list[int]:
    """The 'Specifications' section: first page headed with it, plus the next few pages."""
    for p in doc.pages:
        head = " ".join(p.text.strip().splitlines()[:2]).lower()
        if "specifications" in head:
            return list(range(p.page, min(p.page + SPEC_SPAN, doc.page_count + 1)))
    return []


def choose_pages(doc: ParsedDocument, override: list[int] | None = None) -> list[int]:
    """Spec section first, then other keyword-ranked pages. Order matters: the LLM
    prompt is cut at MAX_PROMPT_CHARS, so the most useful pages go in first."""
    if override:
        return list(dict.fromkeys(override))
    ranked = [p for p, _ in find_relevant_pages(doc, top_n=6)]
    return list(dict.fromkeys(find_spec_pages(doc) + ranked))


def extract_motherboard(doc: ParsedDocument, mode: str = "hybrid", client=None,
                        pages: list[int] | None = None) -> ExtractionResult:
    if mode not in ("hybrid", "deterministic", "llm"):
        raise ValueError(f"unknown mode {mode!r}")
    pages = choose_pages(doc, pages)
    problems: list[str] = []
    fields: dict[str, Optional[FieldValue]] = {n: None for n in FIELDS}

    candidates: dict[str, FieldValue] = {}
    if mode in ("hybrid", "deterministic"):
        candidates.update(extract_deterministic(doc, pages))

    # Validate deterministic values first so the LLM can be asked for anything that failed.
    for name, fv in candidates.items():
        if (p := check_field(doc, name, fv)) is None:
            fields[name] = fv
        else:
            problems.append(p)

    missing = [n for n in FIELDS if fields[n] is None]
    if mode in ("hybrid", "llm") and missing:
        if client is None:
            from ingest.llm import get_client
            client = get_client()
        llm_pages = list(dict.fromkeys([1] + pages))     # cover page carries the model name
        try:
            for name, fv in extract_llm(client, doc, llm_pages, missing).items():
                if (p := check_field(doc, name, fv)) is None:
                    fields[name] = fv
                else:
                    problems.append(p + " [llm]")
        except Exception as e:                    # local server down, bad JSON, etc.
            problems.append(f"LLM step failed: {type(e).__name__}: {e}")

    for name in FIELDS:
        if fields[name] is None and not any(p.startswith(name + ":") for p in problems):
            problems.append(f"{name}: not found")

    method = "llm" if any(fv and fv.method == "llm" for fv in fields.values()) else "deterministic"
    record = build_record(doc, fields, method, problems)
    return ExtractionResult(record, fields, problems, pages, getattr(client, "name", None))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_result(res: ExtractionResult) -> None:
    print(f"Pages used: {res.pages_used}   LLM backend: {res.backend or '-'}\n")
    print(f"{'field':<22}{'value':<26}{'page':<6}{'method':<15}conf")
    print("-" * 75)
    for name in FIELDS:
        fv = res.fields.get(name)
        if fv is None:
            print(f"{name:<22}{'—':<26}")
            continue
        val = res.record.data.__getattribute__(name).value if res.record else fv.value
        print(f"{name:<22}{str(val)[:25]:<26}{fv.page!s:<6}{fv.method:<15}{fv.confidence:.2f}")
    if res.problems:
        print("\nProblems:")
        for p in res.problems:
            print(f"  - {p}")
    if res.record:
        print(f"\nReview first: {res.record.low_confidence_fields() or 'nothing flagged'}")


def _compare(doc: ParsedDocument, pages: list[int] | None) -> None:
    det = extract_motherboard(doc, "deterministic", pages=pages)
    llm = extract_motherboard(doc, "llm", pages=pages)
    print(f"{'field':<22}{'deterministic':<26}{'llm':<26}match")
    print("-" * 80)
    agree = 0
    for name in FIELDS:
        a = getattr(det.record.data, name).value
        b = getattr(llm.record.data, name).value
        ok = a is not None and a == b
        agree += ok
        print(f"{name:<22}{str(a)[:25]:<26}{str(b)[:25]:<26}{'yes' if ok else 'NO'}")
    print(f"\nAgreement: {agree}/{len(FIELDS)} fields   LLM backend: {llm.backend}")
    if llm.problems:
        print("LLM problems:")
        for p in llm.problems:
            print(f"  - {p}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract a motherboard record from a parsed manual.")
    ap.add_argument("parsed", type=Path, help="Parsed JSON from ingest.parse")
    ap.add_argument("--mode", choices=["hybrid", "deterministic", "llm", "compare"], default="hybrid")
    ap.add_argument("--pages", type=int, nargs="*", help="Page numbers to use instead of auto-ranking")
    ap.add_argument("--out", type=Path, help="Staged output path (default: data/staged/<name>.json)")
    args = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")

    doc = ParsedDocument.from_json(args.parsed)
    print(f"{doc.source_file}  ({doc.page_count} pages, doc_id {doc.doc_id[:12]}...)\n")

    if args.mode == "compare":
        _compare(doc, args.pages)
        return

    res = extract_motherboard(doc, args.mode, pages=args.pages)
    _print_result(res)

    out = args.out or Path("data") / "staged" / f"{args.parsed.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res.to_json(), indent=2), encoding="utf-8")
    print(f"\nStaged for review: {out}")


if __name__ == "__main__":
    main()
