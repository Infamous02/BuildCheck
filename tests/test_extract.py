"""Tests for ingest.extract. Run from the repo root with: py -m pytest -v

No LLM is needed: FakeClient returns canned JSON so the validation logic can be
tested on its own. The last test runs on the real parsed Z890 manual if present.
"""
from pathlib import Path

import pytest

from ingest.extract import (FIELDS, check_field, extract_deterministic, extract_motherboard,
                            FieldValue)
from ingest.llm import coerce_json
from ingest.parse import ParsedDocument, ParsedPage

COVER = "Z890 EAGLE WIFI7 PLUS\n(Z890 EAGLE WIFI7 P)\nUser's Manual\nRev. 1002"
SPECS = """2-2 Product Specifications
CPU • LGA1851 socket: Support for Intel® Core™ Ultra Processors
(Go to GIGABYTE's website for the latest CPU support list.)
Chipset • Intel® Z890 Express Chipset
Memory • Support for DDR5 7200/6400/5600 MT/s memory modules
• 4 x DDR5 DIMM sockets supporting up to 256 GB (64 GB single DIMM capacity)
of system memory"""
FORM = "Operating System • Support for Windows 11 64-bit\nForm Factor • ATX Form Factor; 30.5cm x 24.4cm"


def make_doc(*texts: str) -> ParsedDocument:
    pages = [ParsedPage(page=i, text=t) for i, t in enumerate(texts, start=1)]
    return ParsedDocument(doc_id="test", source_file="test.pdf", page_count=len(pages), pages=pages)


def llm_field(value, page, evidence, confidence=0.9):
    return {"value": value, "page": page, "evidence": evidence, "confidence": confidence}


class FakeClient:
    name = "fake"

    def __init__(self, response: dict):
        self.response = response
        self.asked_for: list[str] = []

    def extract_json(self, system, user, schema):
        self.asked_for = list(schema.model_fields)
        # Fill any field the test didn't specify with "not stated".
        return {n: self.response.get(n, llm_field(None, None, None, 0)) for n in self.asked_for}


# ------------------------------------------------------------ deterministic
def test_deterministic_reads_all_spec_fields():
    doc = make_doc(COVER, SPECS, FORM)
    res = extract_motherboard(doc, "deterministic", pages=[2, 3])
    b = res.record.data
    assert b.manufacturer.value == "Gigabyte"
    assert b.model.value == "Z890 EAGLE WIFI7 PLUS"
    assert b.socket.value == "LGA 1851"           # normalized by schemas.py
    assert b.chipset.value == "Z890"
    assert b.form_factor.value == "ATX"
    assert b.memory_type.value == "DDR5"
    assert b.memory_slots.value == 4
    assert b.max_memory_gb.value == 256
    assert b.max_memory_speed_mts.value == 7200   # highest of 7200/6400/5600
    assert res.record.method == "deterministic"


def test_deterministic_evidence_is_on_the_cited_page():
    doc = make_doc(COVER, SPECS, FORM)
    for name, fv in extract_deterministic(doc, [2, 3]).items():
        assert check_field(doc, name, fv) is None, name


def test_missing_field_is_reported_not_guessed():
    doc = make_doc(COVER, SPECS)                  # no form factor page
    res = extract_motherboard(doc, "deterministic", pages=[2])
    assert res.record.data.form_factor.value is None
    assert any(p.startswith("form_factor:") for p in res.problems)
    assert "form_factor" in res.record.low_confidence_fields()


# ------------------------------------------------------------ hybrid / LLM
def test_hybrid_only_asks_llm_for_missing_fields():
    doc = make_doc(COVER, SPECS, FORM)
    client = FakeClient({"form_factor": llm_field("ATX", 3, "ATX Form Factor")})
    res = extract_motherboard(doc, "hybrid", client=client, pages=[2])   # page 3 not searched by regex
    assert client.asked_for == ["form_factor"]
    assert res.fields["form_factor"].method == "llm"
    assert res.record.data.form_factor.value == "ATX"
    assert res.record.method == "llm"


def test_hallucinated_evidence_is_rejected():
    doc = make_doc(COVER, SPECS, FORM)
    client = FakeClient({"form_factor": llm_field("Micro-ATX", 3, "Micro-ATX Form Factor")})
    res = extract_motherboard(doc, "hybrid", client=client, pages=[2])
    assert res.record.data.form_factor.value is None
    assert any("not found on page 3" in p for p in res.problems)


def test_value_must_appear_in_its_own_evidence():
    doc = make_doc(COVER, SPECS, FORM)
    # Real quote, wrong number: the classic LLM slip.
    client = FakeClient({"max_memory_gb": llm_field("128", 2, "supporting up to 256 GB")})
    res = extract_motherboard(doc, "llm", client=client, pages=[2, 3])
    assert res.record.data.max_memory_gb.value is None
    assert any("does not appear in its evidence" in p for p in res.problems)


def test_schema_validator_drops_implausible_value():
    odd = SPECS.replace("4 x DDR5 DIMM", "3 x DDR5 DIMM")
    doc = make_doc(COVER, odd, FORM)
    res = extract_motherboard(doc, "deterministic", pages=[2, 3])
    assert res.record.data.memory_slots.value is None       # 3 slots fails _slots_range
    assert any("memory_slots: failed schema check" in p for p in res.problems)
    assert res.record.data.memory_type.value == "DDR5"      # rest of the record survives


def test_llm_failure_keeps_deterministic_results():
    class Broken:
        name = "broken"
        def extract_json(self, *a):
            raise ConnectionError("LM Studio server not running")
    doc = make_doc(COVER, SPECS)
    res = extract_motherboard(doc, "hybrid", client=Broken(), pages=[2])
    assert res.record.data.socket.value == "LGA 1851"
    assert any("LLM step failed" in p for p in res.problems)


def test_llm_speed_list_takes_highest():
    doc = make_doc(COVER, SPECS, FORM)
    client = FakeClient({"max_memory_speed_mts": llm_field("7200/6400/5600", 2, "DDR5 7200/6400/5600 MT/s")})
    res = extract_motherboard(doc, "llm", client=client, pages=[2, 3])
    assert res.record.data.max_memory_speed_mts.value == 7200


# ------------------------------------------------------------ coerce_json
def test_coerce_json_handles_reasoning_text():
    raw = '* Task: extract {thinking about braces}\nAnswer:\n{"memory_type": "DDR5", "slots": 4}'
    assert coerce_json(raw) == {"memory_type": "DDR5", "slots": 4}
    assert coerce_json({"a": 1}) == {"a": 1}
    with pytest.raises(ValueError):
        coerce_json("no json here")


# ------------------------------------------------------------ real manual
REAL = Path("data/parsed/z890.json")


@pytest.mark.skipif(not REAL.exists(), reason="run ingest.parse on the Z890 manual first")
def test_real_z890_manual_deterministic():
    doc = ParsedDocument.from_json(REAL)
    res = extract_motherboard(doc, "deterministic")
    b = res.record.data
    assert b.socket.value == "LGA 1851"
    assert b.chipset.value == "Z890"
    assert b.form_factor.value == "ATX"
    assert b.memory_type.value == "DDR5"
    assert b.memory_slots.value == 4
    assert b.max_memory_gb.value == 256
    assert b.max_memory_speed_mts.value == 7200
    assert "Z890 EAGLE" in b.model.value
    assert all(res.fields[n] is not None for n in FIELDS), res.problems


# ------------------------------------------------------------ normalization
@pytest.mark.parametrize("raw, expected", [
    ("GIGABYTE", "Gigabyte"), ("gigabyte", "Gigabyte"), ("Gigabyte Technology", "Gigabyte"),
    ("asus", "ASUS"), ("ASRock", "ASRock"), ("SomeNewBrand", "SomeNewBrand"),
])
def test_normalize_manufacturer(raw, expected):
    from ingest.schemas import normalize_manufacturer
    assert normalize_manufacturer(raw) == expected


def test_llm_and_regex_manufacturer_agree():
    doc = make_doc(COVER, SPECS, FORM)
    client = FakeClient({"manufacturer": llm_field("GIGABYTE", 2, "GIGABYTE")})
    res = extract_motherboard(doc, "llm", client=client, pages=[2, 3])
    assert res.record.data.manufacturer.value == "Gigabyte"
